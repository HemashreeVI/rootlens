from policy import ALLOWED_ACTIONS, decide, is_allowed


def trigger(service="payment-service", version="v2", previous="v1",
            seconds=60, strength="strong"):
    return {
        "service": service,
        "version": version,
        "previous_version": previous,
        "seconds_before_detection": seconds,
        "strength": strength,
    }


def diagnosis(cause="payment-service is returning errors",
              service="payment-service", confidence="high",
              trig=None, ranked=None):
    if ranked is None:
        ranked = [{"cause": cause, "service": service, "score": 1.0}]
    return {
        "likely_cause": cause,
        "confidence": confidence,
        "root_cause_service": service,
        "trigger": trig,
        "ranked_causes": ranked,
    }


def attempt(action, age):
    return {"action": action, "age_seconds": age}


def test_recent_release_means_rollback():
    d = decide(diagnosis(trig=trigger()))
    assert d["decision"] == "act"
    assert d["action"] == "rollback_deployment"
    assert d["service"] == "payment-service"
    assert d["target_version"] == "v1"


def test_no_release_means_restart():
    d = decide(diagnosis())
    assert d["decision"] == "act"
    assert d["action"] == "restart_service"


def test_down_with_recent_release_rolls_back_first():
    d = decide(diagnosis(cause="payment-service is down", trig=trigger()))
    assert d["action"] == "rollback_deployment"


def test_down_without_release_restarts():
    d = decide(diagnosis(cause="payment-service is down"))
    assert d["action"] == "restart_service"


def test_slow_service_restarts_without_release():
    d = decide(diagnosis(cause="user-service is too slow (timeouts)",
                         service="user-service"))
    assert d["action"] == "restart_service"
    assert d["service"] == "user-service"


def test_medium_confidence_escalates():
    d = decide(diagnosis(confidence="medium", trig=trigger()))
    assert d["decision"] == "escalate"


def test_low_confidence_escalates():
    d = decide(diagnosis(confidence="low"))
    assert d["decision"] == "escalate"


def test_two_services_failing_escalates():
    ranked = [
        {"cause": "payment-service is returning errors",
         "service": "payment-service", "score": 1.0},
        {"cause": "user-service is returning errors",
         "service": "user-service", "score": 1.0},
    ]
    d = decide(diagnosis(trig=trigger(), ranked=ranked))
    assert d["decision"] == "escalate"
    assert "More than one" in d["reason"]


def test_forbidden_service_escalates():
    d = decide(diagnosis(cause="order-service is returning errors",
                         service="order-service"))
    assert d["decision"] == "escalate"


def test_unknown_cause_escalates():
    d = decide(diagnosis(cause="payment-service is on fire"))
    assert d["decision"] == "escalate"


def test_no_clear_cause_escalates():
    d = decide({"likely_cause": "Insufficient evidence to name a cause.",
                "confidence": "low", "root_cause_service": None,
                "ranked_causes": []})
    assert d["decision"] == "escalate"


def test_a_rollback_is_never_rolled_back():
    d = decide(diagnosis(trig=trigger(version="v1", previous="v2")))
    assert d["action"] == "restart_service"
    assert "rollback" in d["reason"]


def test_old_release_is_not_blamed():
    d = decide(diagnosis(trig=trigger(strength="weak", seconds=1500)))
    assert d["action"] == "restart_service"


def test_release_of_another_service_is_ignored():
    d = decide(diagnosis(trig=trigger(service="user-service")))
    assert d["action"] == "restart_service"


def test_missing_previous_version_means_restart():
    d = decide(diagnosis(trig=trigger(previous="none")))
    assert d["action"] == "restart_service"


def test_after_failed_rollback_try_restart():
    d = decide(diagnosis(trig=trigger()),
               attempts=[attempt("rollback_deployment", 90)])
    assert d["decision"] == "act"
    assert d["action"] == "restart_service"


def test_cooldown_means_wait():
    d = decide(diagnosis(trig=trigger()),
               attempts=[attempt("rollback_deployment", 20)])
    assert d["decision"] == "wait"
    assert d["action"] is None


def test_attempt_limit_escalates():
    d = decide(diagnosis(trig=trigger()),
               attempts=[attempt("rollback_deployment", 200),
                         attempt("restart_service", 100)])
    assert d["decision"] == "escalate"


def test_hourly_limit_escalates():
    d = decide(diagnosis(), service_actions_last_hour=3)
    assert d["decision"] == "escalate"


def test_nothing_left_to_try_escalates():
    d = decide(diagnosis(), attempts=[attempt("restart_service", 100)])
    assert d["decision"] == "escalate"


def test_allowlist():
    assert is_allowed("restart_service", "payment-service")
    assert is_allowed("rollback_deployment", "user-service")
    assert not is_allowed("run_shell", "payment-service")
    assert not is_allowed("restart_service", "order-service")
    assert not is_allowed("restart_service", "prometheus")


def test_never_returns_a_forbidden_action():
    causes = [
        ("payment-service is returning errors", "payment-service"),
        ("user-service is down", "user-service"),
        ("user-service is too slow (timeouts)", "user-service"),
        ("order-service is down", "order-service"),
        ("something strange", "payment-service"),
    ]
    triggers = [None, trigger(), trigger(version="v1", previous="v2"),
                trigger(strength="weak"), trigger(previous="none")]
    histories = [[], [attempt("rollback_deployment", 90)],
                 [attempt("restart_service", 10)]]

    for cause, service in causes:
        for confidence in ("high", "medium", "low"):
            for trig in triggers:
                for history in histories:
                    d = decide(
                        diagnosis(cause=cause, service=service,
                                  confidence=confidence, trig=trig),
                        attempts=history,
                    )
                    assert d["decision"] in ("act", "wait", "escalate")
                    if d["decision"] == "act":
                        assert d["action"] in ALLOWED_ACTIONS
                        assert is_allowed(d["action"], d["service"])


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")