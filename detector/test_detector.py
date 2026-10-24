"""Tests for the detector's open/resolve decisions. No Prometheus needed."""
from detector import (
    BREACHES_TO_OPEN,
    OKS_TO_RESOLVE,
    RECOVERY_THRESHOLD,
    THRESHOLD,
    next_step,
)

BAD = THRESHOLD + 0.2
GOOD = RECOVERY_THRESHOLD / 2
IN_BETWEEN = (THRESHOLD + RECOVERY_THRESHOLD) / 2


def fresh():
    return {"breaches": 0, "oks": 0, "reported": None}


def feed(state, values, succeed=True):
    """Run readings through next_step. Returns the steps it asked for."""
    steps = []
    for value in values:
        step = next_step(state, value)
        steps.append(step)
        if step == "open" and succeed:
            state["reported"] = "open"
        if step == "resolve" and succeed:
            state["reported"] = "resolved"
    return steps


def test_opens_after_enough_bad_checks():
    steps = feed(fresh(), [BAD] * BREACHES_TO_OPEN)
    assert steps[-1] == "open"
    assert steps[:-1] == [None] * (BREACHES_TO_OPEN - 1)


def test_opens_only_once_while_still_failing():
    steps = feed(fresh(), [BAD] * (BREACHES_TO_OPEN + 5))
    assert steps.count("open") == 1


def test_failed_open_is_retried_on_next_check():
    # This is the bug that used to lose incidents: the report failed at
    # exactly the 3rd breach, and the 4th, 5th... never matched "== 3".
    state = fresh()
    steps = feed(state, [BAD] * BREACHES_TO_OPEN, succeed=False)
    assert steps[-1] == "open"
    assert state["reported"] is None

    assert next_step(state, BAD) == "open"
    assert next_step(state, BAD) == "open"


def test_failed_resolve_is_retried_on_next_check():
    state = fresh()
    feed(state, [BAD] * BREACHES_TO_OPEN)
    steps = feed(state, [GOOD] * OKS_TO_RESOLVE, succeed=False)
    assert steps[-1] == "resolve"

    assert next_step(state, GOOD) == "resolve"


def test_resolves_once_after_recovery():
    state = fresh()
    feed(state, [BAD] * BREACHES_TO_OPEN)
    steps = feed(state, [GOOD] * (OKS_TO_RESOLVE + 5))
    assert steps.count("resolve") == 1
    assert state["reported"] == "resolved"


def test_one_bad_check_resets_the_recovery_count():
    state = fresh()
    feed(state, [BAD] * BREACHES_TO_OPEN)
    steps = feed(state, [GOOD] * (OKS_TO_RESOLVE - 1) + [BAD] + [GOOD])
    assert "resolve" not in steps


def test_in_between_values_neither_open_nor_resolve():
    steps = feed(fresh(), [IN_BETWEEN] * 10)
    assert steps == [None] * 10


def test_after_restart_a_healthy_system_is_resolved_once():
    # The detector does not know whether an incident was left open before it
    # restarted, so it reports "resolved" once. Order Service ignores it if
    # nothing is open.
    steps = feed(fresh(), [GOOD] * (OKS_TO_RESOLVE + 3))
    assert steps.count("resolve") == 1


def test_a_second_outage_opens_again():
    state = fresh()
    feed(state, [BAD] * BREACHES_TO_OPEN + [GOOD] * OKS_TO_RESOLVE)
    steps = feed(state, [BAD] * BREACHES_TO_OPEN)
    assert steps[-1] == "open"


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")
