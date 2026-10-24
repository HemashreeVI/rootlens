"""Tests for reading and checking services.yaml."""
import copy

import topology


def good():
    return copy.deepcopy({
        "window": "2m",
        "entry": {
            "service": "front",
            "requests": 'calls_total{service="front"}',
            "errors": 'calls_total{service="front",status="ERROR"}',
            "latency_buckets": 'duration_ms_bucket{service="front"}',
            "latency_unit": "milliseconds",
            "timeout_seconds": 2,
        },
        "services": {
            "cart": {"requests": 'calls_total{service="cart"}',
                     "errors": 'calls_total{service="cart",status="ERROR"}',
                     "up": 'up{job="cart"}',
                     "caller_failures": 'calls_total{caller="front",callee="cart",status="ERROR"}',
                     "version_env": "CART_VERSION",
                     "actions": ["rollback_deployment", "restart_service"]},
            "search": {"requests": "search_total", "errors": "search_errors_total",
                       "caller_failures": 'fails_total{to="search"}',
                       "caller_failure_kind_label": "why",
                       "actions": ["restart_service"]},
            "ledger": {"requests": "ledger_total", "errors": "ledger_errors_total"},
        },
        "dependencies": {"front": ["cart", "search", "ledger"]},
    })


def rejected(raw, words):
    try:
        topology.validate(raw)
    except topology.ConfigError as error:
        assert words in str(error), str(error)
        return
    raise AssertionError(f"expected a ConfigError mentioning {words!r}")


def test_the_project_file_is_valid():
    t = topology.load()
    assert t["entry"]["service"] == "order-service"
    assert topology.controllable(t) == {"user-service", "payment-service"}


def test_a_different_app_loads():
    t = topology.validate(good())
    assert topology.entry(t)["service"] == "front"
    assert topology.dependency_graph(t) == {"front": ["cart", "search", "ledger"]}


def test_actions_are_per_service():
    t = topology.validate(good())
    assert topology.allowed_actions("cart", t) == {"rollback_deployment", "restart_service"}
    assert topology.allowed_actions("search", t) == {"restart_service"}
    assert topology.allowed_actions("ledger", t) == set()
    assert topology.allowed_actions("nobody", t) == set()
    assert topology.controllable(t) == {"cart", "search"}


def test_missing_entry_field_is_rejected():
    raw = good()
    del raw["entry"]["requests"]
    rejected(raw, "requests")


def test_missing_service_field_is_rejected():
    raw = good()
    del raw["services"]["cart"]["errors"]
    rejected(raw, "cart is missing: errors")


def test_unknown_action_is_rejected():
    # The config may narrow what the robot does, never invent new actions.
    raw = good()
    raw["services"]["search"]["actions"] = ["delete_database"]
    rejected(raw, "unknown actions")


def test_rollback_without_version_env_is_rejected():
    raw = good()
    raw["services"]["search"]["actions"] = ["rollback_deployment"]
    rejected(raw, "no version_env")


def test_unknown_dependency_is_rejected():
    raw = good()
    raw["dependencies"]["front"].append("ghost")
    rejected(raw, "ghost")


def test_no_services_is_rejected():
    raw = good()
    raw["services"] = {}
    rejected(raw, "at least one service")


def test_bad_latency_unit_is_rejected():
    raw = good()
    raw["entry"]["latency_unit"] = "hours"
    rejected(raw, "latency_unit")


def test_entry_queries_use_the_window_and_unit():
    q = topology.entry_queries(topology.validate(good()))
    assert q["failure_ratio"] == (
        'sum(rate(calls_total{service="front",status="ERROR"}[2m]))'
        ' / sum(rate(calls_total{service="front"}[2m]))'
    )
    assert q["p95_seconds"].endswith(") / 1000")
    assert "status_rates" not in q


def test_caller_failures_without_a_label_count_as_errors():
    q = topology.service_queries("cart", topology.validate(good()))
    assert q["caller_errors"] == (
        'sum(rate(calls_total{caller="front",callee="cart",status="ERROR"}[2m]))'
    )
    assert "caller_failures_by_kind" not in q
    assert q["up"] == 'up{job="cart"}'


def test_caller_failures_with_a_label_are_split_by_kind():
    q = topology.service_queries("search", topology.validate(good()))
    assert q["caller_failures_by_kind"] == (
        'sum by (why) (rate(fails_total{to="search"}[2m]))'
    )
    assert q["caller_timeouts"] == (
        'sum(rate(fails_total{to="search",why="timeout"}[2m]))'
    )
    assert "up" not in q


def test_optional_queries_can_be_left_out():
    q = topology.service_queries("ledger", topology.validate(good()))
    assert set(q) == {"error_ratio"}


def test_container_name_must_be_plain():
    raw = good()
    raw["services"]["search"]["container"] = "search"
    topology.validate(raw)
    for bad in ("--privileged", "a b", "x;rm -rf /", ""):
        raw = good()
        raw["services"]["search"]["container"] = bad
        rejected(raw, "invalid container name")


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")
