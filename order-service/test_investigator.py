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


HEALTHY = dep(1.0, 0.0)


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
    r = run({
        "order_p95_latency_seconds": 0.05,
        "dependencies": {
            "user-service": HEALTHY,
            "payment-service": dep(1.0, 0.5, error=1.0),
        },
    })
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


if __name__ == "__main__":
    tests = [
        test_user_errors, test_user_slow, test_user_down,
        test_payment_errors, test_payment_slow, test_payment_down,
        test_old_evidence_format_still_works, test_both_dependencies_failing,
    ]
    for test in tests:
        test()
        print(f"PASS  {test.__name__}")