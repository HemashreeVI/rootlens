from datetime import datetime, timedelta, timezone

import policy
import safety

NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)


def decision(action="rollback_deployment", service="payment-service", target="v1"):
    return {
        "decision": "act", "action": action, "service": service,
        "target_version": target, "reason": "test",
    }


def diagnosis(cause="payment-service is returning errors", version="v2"):
    trigger = None
    if version:
        trigger = {
            "service": "payment-service", "version": version,
            "previous_version": "v1",
        }
    return {"likely_cause": cause, "trigger": trigger}


def live(ratio=0.45, payment="v2", user="v1"):
    return {
        "failure_ratio": ratio,
        "versions": {"payment-service": payment, "user-service": user},
    }


def full_diagnosis():
    return {
        "likely_cause": "payment-service is returning errors",
        "confidence": "high",
        "root_cause_service": "payment-service",
        "ranked_causes": [{
            "cause": "payment-service is returning errors",
            "service": "payment-service", "score": 1.0,
        }],
        "trigger": {
            "service": "payment-service", "version": "v2",
            "previous_version": "v1", "seconds_before_detection": 60,
            "strength": "strong",
        },
    }


def audit(action="rollback_deployment", service="payment-service",
          incident="inc-1", mode="enforce", decision_kind="act",
          outcome="Executed (verification pending)", seconds_ago=90):
    return {
        "incident_id": incident, "mode": mode, "decision": decision_kind,
        "action": action, "service": service, "outcome": outcome,
        "logged_at": (NOW - timedelta(seconds=seconds_ago)).isoformat(),
    }


# ---------- pre-flight ----------

def test_rollback_passes_when_everything_checks_out():
    result = safety.preflight(decision(), diagnosis(), live(), 120)
    assert result["ok"], result["reason"]


def test_restart_does_not_need_a_version_match():
    result = safety.preflight(
        decision(action="restart_service", target=None),
        diagnosis(), live(payment="v9"), 120,
    )
    assert result["ok"], result["reason"]


def test_forbidden_action_is_refused():
    result = safety.preflight(decision(action="run_shell"), diagnosis(), live(), 120)
    assert not result["ok"]


def test_forbidden_service_is_refused():
    result = safety.preflight(
        decision(service="order-service"), diagnosis(), live(), 120
    )
    assert not result["ok"]


def test_stale_incident_is_refused():
    result = safety.preflight(decision(), diagnosis(), live(), 2000)
    assert not result["ok"]
    assert "too old" in result["reason"]


def test_no_live_data_is_refused():
    result = safety.preflight(decision(), diagnosis(), live(ratio=None), 120)
    assert not result["ok"]
    assert "no live traffic data" in result["reason"].lower()


def test_already_recovered_is_refused():
    result = safety.preflight(decision(), diagnosis(), live(ratio=0.05), 120)
    assert not result["ok"]
    assert "already recovered" in result["reason"]


def test_failure_exactly_at_the_threshold_still_counts():
    result = safety.preflight(decision(), diagnosis(), live(ratio=0.3), 120)
    assert result["ok"], result["reason"]


def test_version_changed_since_diagnosis_is_refused():
    result = safety.preflight(decision(), diagnosis(), live(payment="v1"), 120)
    assert not result["ok"]
    assert "running v1" in result["reason"]


def test_rollback_target_must_be_an_earlier_version():
    same = safety.preflight(decision(target="v2"), diagnosis(), live(), 120)
    later = safety.preflight(decision(target="v3"), diagnosis(), live(), 120)
    assert not same["ok"]
    assert not later["ok"]
    assert "not an earlier version" in same["reason"]


def test_rollback_needs_a_release_to_undo():
    result = safety.preflight(decision(), diagnosis(version=None), live(), 120)
    assert not result["ok"]
    assert "no release" in result["reason"].lower()


def test_unreadable_version_is_refused_when_not_down():
    result = safety.preflight(decision(), diagnosis(), live(payment=None), 120)
    assert not result["ok"]
    assert "cannot be read" in result["reason"]


def test_unreadable_version_is_allowed_when_the_service_is_down():
    result = safety.preflight(
        decision(), diagnosis(cause="payment-service is down"),
        live(payment=None), 120,
    )
    assert result["ok"], result["reason"]


# ---------- verification ----------

def test_three_low_readings_mean_recovered():
    result = safety.judge_recovery([0.5, 0.05, 0.02, 0.0], 40)
    assert result["state"] == "recovered"


def test_high_readings_early_are_pending():
    assert safety.judge_recovery([0.5, 0.4, 0.45], 30)["state"] == "pending"


def test_fewer_than_three_readings_are_pending():
    assert safety.judge_recovery([0.0, 0.0], 20)["state"] == "pending"


def test_a_spike_resets_the_count():
    assert safety.judge_recovery([0.05, 0.05, 0.4], 60)["state"] == "pending"


def test_exactly_ten_percent_is_not_recovered():
    assert safety.judge_recovery([0.1, 0.1, 0.1], 60)["state"] == "pending"


def test_still_failing_after_the_timeout_is_failed():
    result = safety.judge_recovery([0.5] * 12, 125)
    assert result["state"] == "failed"
    assert "50%" in result["reason"]


def test_no_data_at_the_end_is_inconclusive():
    assert safety.judge_recovery([0.4, None, None], 130)["state"] == "inconclusive"


def test_dropping_too_late_is_inconclusive():
    assert safety.judge_recovery([0.5, 0.4, 0.05], 125)["state"] == "inconclusive"


# ---------- attempt history ----------

def test_only_executed_enforce_actions_for_this_incident_count():
    entries = [
        audit(),
        audit(mode="dry-run", outcome="Not executed (dry-run)"),
        audit(outcome="Skipped (pre-flight): already recovered"),
        audit(decision_kind="verify", outcome="Executed; recovery verified"),
        audit(incident="inc-2"),
        audit(action="restart_service", outcome="Execution failed: timeout"),
    ]
    attempts = safety.attempts_from_audit(entries, "inc-1", NOW)
    assert len(attempts) == 2


def test_attempt_age_is_measured():
    attempts = safety.attempts_from_audit([audit(seconds_ago=90)], "inc-1", NOW)
    assert attempts[0]["action"] == "rollback_deployment"
    assert attempts[0]["age_seconds"] == 90


def test_unreadable_timestamp_counts_as_just_now():
    entry = audit()
    entry["logged_at"] = "garbage"
    attempts = safety.attempts_from_audit([entry], "inc-1", NOW)
    assert attempts[0]["age_seconds"] == 0.0


def test_hourly_count_is_per_service_and_recent_only():
    entries = [
        audit(seconds_ago=600),
        audit(seconds_ago=4000),
        audit(service="user-service", seconds_ago=100),
        audit(mode="dry-run", outcome="Not executed (dry-run)", seconds_ago=100),
    ]
    assert safety.actions_last_hour(entries, "payment-service", NOW) == 1


# ---------- working together with the rulebook ----------

def test_history_makes_the_rulebook_try_a_restart():
    attempts = safety.attempts_from_audit([audit(seconds_ago=90)], "inc-1", NOW)
    result = policy.decide(full_diagnosis(), attempts=attempts)
    assert result["decision"] == "act"
    assert result["action"] == "restart_service"


def test_a_very_recent_attempt_makes_the_rulebook_wait():
    attempts = safety.attempts_from_audit([audit(seconds_ago=20)], "inc-1", NOW)
    result = policy.decide(full_diagnosis(), attempts=attempts)
    assert result["decision"] == "wait"


def test_a_fresh_rulebook_decision_passes_preflight():
    result_decision = policy.decide(full_diagnosis())
    result = safety.preflight(result_decision, full_diagnosis(), live(), 100)
    assert result_decision["action"] == "rollback_deployment"
    assert result["ok"], result["reason"]


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")