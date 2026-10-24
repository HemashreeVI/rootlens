import json

from investigator import investigate_incident


def run(evidence):
    incident = {"incident_id": "test", "evidence_json": json.dumps(evidence)}
    return investigate_incident(incident)


def dep(up, own_error_ratio, timeout=0.0, error=0.0, unreachable=0.0):
    rates = {}
    if timeout:
        rates["timeout"] = timeout
    if error:
        rates["error"] = error
    if unreachable:
        rates["unreachable"] = unreachable
    return {
        "up": up,
        "own_error_ratio": own_error_ratio,
        "failure_rates_per_second": rates,
    }


def deploy(service, version, previous, seconds):
    return {
        "service": service,
        "version": version,
        "previous_version": previous,
        "deployed_at": "2026-10-17T10:00:00+00:00",
        "seconds_before_detection": seconds,
    }


HEALTHY = dep(1.0, 0.0)


def payment_errors_evidence(deployments=None):
    evidence = {
        "order_p95_latency_seconds": 0.05,
        "dependencies": {
            "user-service": HEALTHY,
            "payment-service": dep(1.0, 0.5, error=1.0),
        },
    }
    if deployments is not None:
        evidence["recent_deployments"] = deployments
    return evidence


def test_user_errors():
    r = run({
        "order_p95_latency_seconds": 0.04,
        "dependencies": {
            "user-service": dep(1.0, 0.48, error=2.0),
            "payment-service": HEALTHY,
        },
    })
    assert r["likely_cause"] == "user-service is returning errors"
    assert r["confidence"] == "high"
    assert r["root_cause_service"] == "user-service"
    assert r["impacted_services"] == ["order-service"]


def test_user_slow():
    r = run({
        "order_p95_latency_seconds": 4.88,
        "dependencies": {
            "user-service": dep(1.0, 0.0, timeout=0.3),
            "payment-service": HEALTHY,
        },
    })
    assert r["likely_cause"] == "user-service is too slow (timeouts)"
    assert r["confidence"] == "high"


def test_user_down():
    r = run({
        "order_p95_latency_seconds": 3.48,
        "dependencies": {
            "user-service": dep(0.0, None, timeout=0.3),
            "payment-service": HEALTHY,
        },
    })
    assert r["likely_cause"] == "user-service is down"
    assert r["confidence"] == "high"


def test_payment_errors():
    r = run(payment_errors_evidence())
    assert r["likely_cause"] == "payment-service is returning errors"
    assert r["confidence"] == "high"
    assert r["root_cause_service"] == "payment-service"


def test_payment_slow():
    r = run({
        "order_p95_latency_seconds": 3.2,
        "dependencies": {
            "user-service": HEALTHY,
            "payment-service": dep(1.0, 0.0, timeout=0.3),
        },
    })
    assert r["likely_cause"] == "payment-service is too slow (timeouts)"
    assert r["confidence"] == "high"


def test_payment_down():
    r = run({
        "order_p95_latency_seconds": 3.4,
        "dependencies": {
            "user-service": HEALTHY,
            "payment-service": dep(0.0, None, timeout=0.3),
        },
    })
    assert r["likely_cause"] == "payment-service is down"
    assert r["confidence"] == "high"


def test_old_evidence_format_still_works():
    r = run({
        "user_service_up": 0.0,
        "order_status_rates_per_second": {"504": 0.309},
        "user_service_error_ratio": None,
        "order_p95_latency_seconds": 3.48,
    })
    assert r["likely_cause"] == "user-service is down"
    assert r["confidence"] == "high"


def test_both_dependencies_failing():
    r = run({
        "order_p95_latency_seconds": 0.05,
        "dependencies": {
            "user-service": dep(1.0, 0.5, error=1.0),
            "payment-service": dep(1.0, 0.5, error=1.0),
        },
    })
    assert r["confidence"] != "high"
    assert r["notes"]


def test_recent_release_is_strong_trigger_with_rollback():
    r = run(payment_errors_evidence([deploy("payment-service", "v2", "v1", 60)]))
    assert r["likely_cause"] == "payment-service is returning errors"
    assert r["confidence"] == "high"
    assert r["trigger"]["version"] == "v2"
    assert r["trigger"]["strength"] == "strong"
    assert "Roll back payment-service from v2 to v1" in r["recommended_next_step"]


def test_old_release_is_only_a_weak_trigger():
    r = run(payment_errors_evidence([deploy("payment-service", "v2", "v1", 1500)]))
    assert r["trigger"]["strength"] == "weak"
    assert r["notes"]


def test_release_of_a_different_service_is_not_blamed():
    r = run(payment_errors_evidence([deploy("user-service", "v2", "v1", 60)]))
    assert r["trigger"] is None
    assert any("user-service was deployed" in fact for fact in r["observed_facts"])


def test_release_without_previous_version_cannot_roll_back():
    r = run(payment_errors_evidence([deploy("payment-service", "v2", "none", 60)]))
    assert r["trigger"]["strength"] == "strong"
    assert "nothing to roll back to" in r["recommended_next_step"]


def test_no_deployments_means_no_trigger():
    r = run(payment_errors_evidence([]))
    assert r["trigger"] is None


def test_a_rollback_is_not_rolled_back_again():
    r = run(payment_errors_evidence([deploy("payment-service", "v1", "v2", 60)]))
    assert r["trigger"]["kind"] == "rollback"
    assert "do not roll back again" in r["recommended_next_step"]
    assert "Roll back payment-service from" not in r["recommended_next_step"]


def test_normal_release_trigger_is_marked_as_release():
    r = run(payment_errors_evidence([deploy("payment-service", "v2", "v1", 60)]))
    assert r["trigger"]["kind"] == "release"


def test_cause_kind_is_reported_as_a_field():
    r = run(payment_errors_evidence())
    assert r["cause_kind"] == "errors"
    assert all("kind" in cause for cause in r["ranked_causes"])


def test_insufficient_evidence_has_no_cause_kind():
    r = run({"dependencies": {"user-service": HEALTHY,
                              "payment-service": HEALTHY}})
    assert r["cause_kind"] is None


def test_dotted_versions_compare_as_numbers():
    # v1.10 is older than v2; stripping digits used to read it as 110.
    r = run(payment_errors_evidence([deploy("payment-service", "v2", "v1.10", 60)]))
    assert r["trigger"]["kind"] == "release"

    r = run(payment_errors_evidence([deploy("payment-service", "v1.10", "v2", 60)]))
    assert r["trigger"]["kind"] == "rollback"


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")
    print(f"{len(tests)} tests passed.")