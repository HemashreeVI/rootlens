import notify


def info(**overrides):
    base = {
        "service": "payment-service",
        "cause": "payment-service is returning errors",
        "confidence": "high",
        "trigger": "payment-service v2 was deployed 57s before detection (strong correlation)",
        "robot": "would roll back payment-service to v1",
        "reason": "Confidence is medium; the controller only acts on high confidence.",
        "mode": "dry-run",
        "dashboard_url": "http://localhost:8002/dashboard",
        "duration": "3m 12s",
    }
    base.update(overrides)
    return base


ENV = {
    "NOTIFY_CHAT_PAYMENT_SERVICE": "111",
    "NOTIFY_CHAT_DEFAULT": "999",
}


# ---------- who gets told ----------

def test_owner_gets_the_message():
    assert notify.recipients("payment-service", ENV) == ["111"]


def test_default_is_used_when_service_has_no_owner():
    assert notify.recipients("user-service", ENV) == ["999"]


def test_no_service_uses_default():
    assert notify.recipients(None, ENV) == ["999"]


def test_several_owners_without_duplicates():
    env = {"NOTIFY_CHAT_USER_SERVICE": "111, 222,111"}
    assert notify.recipients("user-service", env) == ["111", "222"]


def test_nobody_configured():
    assert notify.recipients("user-service", {}) == []


def test_deliverable_needs_token_and_recipient():
    assert not notify.deliverable("payment-service", ENV)
    assert notify.deliverable("payment-service", dict(ENV, TELEGRAM_BOT_TOKEN="t"))
    assert not notify.deliverable("payment-service", {"TELEGRAM_BOT_TOKEN": "t"})


# ---------- what the messages say ----------

def test_opened_message():
    text = notify.build_message("opened", info())
    assert "Incident: payment-service is returning errors" in text
    assert "Owner: payment-service" in text
    assert "Confidence: high" in text
    assert "Likely trigger" in text
    assert "Robot (dry-run): would roll back payment-service to v1" in text
    assert "Dashboard: http://localhost:8002/dashboard" in text


def test_needs_human_message_explains_why():
    text = notify.build_message("needs_human", info())
    assert "NEEDS A HUMAN" in text
    assert "Why the robot is not acting" in text
    assert "only acts on high confidence" in text


def test_reminder_message():
    text = notify.build_message("reminder", info(duration="15m 0s"))
    assert "Still open after 15m 0s" in text


def test_resolved_message_is_short():
    text = notify.build_message("resolved", info())
    assert "Resolved after 3m 12s" in text
    assert "Confidence" not in text


def test_acted_message_shows_the_result():
    text = notify.build_message("acted", info(
        mode="enforce", robot="roll back payment-service to v1",
        result="Executed: rolled back payment-service from v2 to v1",
    ))
    assert "RootLens acted: payment-service is returning errors" in text
    assert "Robot (enforce): roll back payment-service to v1" in text
    assert "Result: Executed: rolled back payment-service from v2 to v1" in text


def test_fix_failed_message_says_what_happens_next():
    text = notify.build_message("fix_failed", info(
        mode="enforce", reason="The failure ratio was still 48% after 120s.",
    ))
    assert "Fix did not work" in text
    assert "Check: The failure ratio was still 48% after 120s." in text
    assert "try the next step" in text


def test_unassigned_owner():
    text = notify.build_message("opened", info(service=None))
    assert "Owner: unassigned" in text


def test_long_messages_are_cut():
    text = notify.build_message("opened", info(cause="x" * 5000))
    assert len(text) <= notify.MAX_MESSAGE_CHARS


def test_trigger_text_for_a_release():
    trigger = {
        "service": "payment-service", "version": "v2", "previous_version": "v1",
        "seconds_before_detection": 57, "strength": "strong", "kind": "release",
    }
    assert notify.trigger_text(trigger) == (
        "payment-service v2 was deployed 57s before detection (strong correlation)"
    )


def test_trigger_text_for_a_rollback():
    trigger = {
        "service": "payment-service", "version": "v1", "previous_version": "v2",
        "seconds_before_detection": 60, "strength": "strong", "kind": "rollback",
    }
    text = notify.trigger_text(trigger)
    assert "rolled back to v1 (from v2)" in text


def test_no_trigger_gives_none():
    assert notify.trigger_text(None) is None


def test_robot_text():
    act = {"decision": "act", "action": "rollback_deployment",
           "service": "payment-service", "target_version": "v1"}
    assert notify.robot_text(act, "dry-run") == "would roll back payment-service to v1"
    assert notify.robot_text(act, "enforce") == "roll back payment-service to v1"
    restart = {"decision": "act", "action": "restart_service",
               "service": "user-service", "target_version": None}
    assert notify.robot_text(restart, "dry-run") == "would restart user-service"
    assert notify.robot_text({"decision": "escalate"}, "dry-run") == "escalated to a human"
    assert notify.robot_text({"decision": "wait"}, "dry-run") == "waiting before the next step"


def test_duration_text():
    assert notify.duration_text(45) == "45s"
    assert notify.duration_text(192) == "3m 12s"
    assert notify.duration_text(3900) == "1h 5m"


def test_notify_without_token_sends_nothing():
    assert notify.notify("opened", info(), environ={}) == 0


# ---------- when messages go out ----------

def test_new_incident_is_announced():
    assert notify.plan_notice({}, "act", 60, 1000.0) == "opened"


def test_new_incident_that_needs_a_human_says_so():
    assert notify.plan_notice({}, "escalate", 60, 1000.0) == "needs_human"


def test_old_incident_is_muted():
    assert notify.plan_notice({}, "act", 2000, 1000.0) == "mute"
    assert notify.plan_notice({"muted": True}, "act", 2000, 1000.0) is None


def test_decision_changing_to_escalate_sends_an_alert():
    entry = {"announced": True, "kind": "act", "last_notice": 990.0}
    assert notify.plan_notice(entry, "escalate", 100, 1000.0) == "needs_human"


def test_no_repeat_before_the_reminder_time():
    entry = {"announced": True, "kind": "escalate", "last_notice": 900.0}
    assert notify.plan_notice(entry, "escalate", 200, 1000.0) is None


def test_reminder_after_fifteen_minutes():
    entry = {"announced": True, "kind": "escalate", "last_notice": 0.0}
    assert notify.plan_notice(entry, "escalate", 2000, 1000.0) == "reminder"


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")