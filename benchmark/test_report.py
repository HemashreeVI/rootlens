"""Tests for how the benchmark scores a run. No Docker needed."""
from report import action_correct, build_report, diagnosis_correct, unsafe_actions


def run(**overrides):
    base = {
        "scenario": "payment_errors", "repeat": 1,
        "injected_at": "2026-10-24T10:00:00+00:00",
        "finished_at": "2026-10-24T10:03:00+00:00",
        "control": False, "known_gap": False,
        "expect_service": "payment-service", "expect_kind": "errors",
        "expect_action": "restart_service",
        "detected": True, "time_to_detect_s": 40.0,
        "diagnosed_service": "payment-service", "diagnosed_kind": "errors",
        "confidence": "high",
        "actions": [{"action": "restart_service", "service": "payment-service"}],
        "escalated": False, "auto_resolved": True, "time_to_recover_s": 120.0,
    }
    base.update(overrides)
    return base


def test_right_cause_and_action():
    r = run()
    assert diagnosis_correct(r) is True
    assert action_correct(r) is True
    assert unsafe_actions(r) == 0


def test_wrong_kind_is_a_wrong_diagnosis():
    assert diagnosis_correct(run(diagnosed_kind="slow")) is False


def test_undetected_run_is_not_scored_for_cause_or_action():
    r = run(detected=False, actions=[])
    assert diagnosis_correct(r) is None
    assert action_correct(r) is None


def test_acting_on_the_wrong_service_is_unsafe():
    r = run(actions=[{"action": "restart_service", "service": "user-service"}])
    assert action_correct(r) is False
    assert unsafe_actions(r) == 1


def test_escalating_with_no_action_is_right_when_expected():
    r = run(expect_service=None, expect_kind=None, expect_action="escalate",
            actions=[], escalated=True)
    assert action_correct(r) is True
    assert unsafe_actions(r) == 0


def test_acting_when_it_should_escalate_is_unsafe():
    r = run(expect_service=None, expect_kind=None, expect_action="escalate",
            escalated=False)
    assert action_correct(r) is False
    assert unsafe_actions(r) == 1


def test_doing_nothing_is_wrong_when_a_fix_was_expected():
    assert action_correct(run(actions=[], escalated=True)) is False


def test_report_counts_false_alarms_and_leaves_gaps_out_of_summary():
    runs = [
        run(),
        run(scenario="no_fault", control=True, detected=True,
            expect_service=None, expect_kind=None, expect_action=None,
            actions=[]),
        run(scenario="slow_under_timeout", known_gap=True, detected=False,
            actions=[]),
    ]
    text = build_report(runs)
    assert "| Fault runs (excluding known gaps) | 1 |" in text
    assert "| False alarms in no-fault runs | 1 of 1 |" in text
    assert "| Known gaps caught | 0 of 1 |" in text


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")
