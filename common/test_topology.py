"""Tests for reading and checking services.yaml."""
import copy

import topology


def good():
    return copy.deepcopy({
        "entry": {
            "service": "front", "requests_metric": "front_requests_total",
            "endpoint": "/buy", "latency_metric": "front_seconds",
            "dependency_failures_metric": "front_dep_failures_total",
            "timeout_seconds": 2,
        },
        "services": {
            "cart": {"requests_metric": "cart_total", "endpoint": "/cart",
                     "version_env": "CART_VERSION",
                     "actions": ["rollback_deployment", "restart_service"]},
            "search": {"requests_metric": "search_total", "endpoint": "/q",
                       "actions": ["restart_service"]},
            "ledger": {"requests_metric": "ledger_total", "endpoint": "/l"},
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
    del raw["entry"]["requests_metric"]
    rejected(raw, "requests_metric")


def test_missing_service_field_is_rejected():
    raw = good()
    del raw["services"]["cart"]["endpoint"]
    rejected(raw, "cart is missing: endpoint")


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


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")
