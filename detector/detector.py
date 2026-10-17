import os
import time
from datetime import datetime, timezone

import httpx

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8000")

CHECK_INTERVAL = 10        # seconds between checks
THRESHOLD = 0.3            # failure fraction that counts as "bad"
RECOVERY_THRESHOLD = 0.1   # failure fraction that counts as "healthy again"
BREACHES_TO_OPEN = 3       # bad checks in a row before opening an incident
OKS_TO_RESOLVE = 3         # healthy checks in a row before resolving

FINGERPRINT = "order-service:error_ratio"

# The failure fraction of Order requests: this is what triggers incidents.
QUERY = (
    'sum(rate(rootlens_order_requests_total{endpoint="/orders",status=~"5.."}[1m]))'
    ' / '
    'sum(rate(rootlens_order_requests_total{endpoint="/orders"}[1m]))'
)

# Every service the Order Service depends on, and where its own metrics live.
DEPENDENCIES = {
    "user-service": {
        "metric": "rootlens_user_requests_total",
        "endpoint": "/users/{user_id}",
    },
    "payment-service": {
        "metric": "rootlens_payment_requests_total",
        "endpoint": "/pay/{order_id}",
    },
}

ORDER_STATUS_QUERY = (
    'sum by (status) '
    '(rate(rootlens_order_requests_total{endpoint="/orders"}[1m]))'
)

ORDER_P95_QUERY = (
    'histogram_quantile(0.95, sum by (le) '
    '(rate(rootlens_order_request_duration_seconds_bucket'
    '{endpoint="/orders"}[1m])))'
)


def run_query(query):
    """Ask Prometheus a question. Returns the list of results."""
    response = httpx.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query},
        timeout=5.0
    )
    return response.json()["data"]["result"]


def query_number(query):
    """Return a single number, or None if there is no data."""
    results = run_query(query)

    if not results:
        return None

    value = float(results[0]["value"][1])

    if value != value:  # NaN, which happens when there is no traffic
        return None

    return value


def get_error_ratio():
    return query_number(QUERY)


def collect_dependency(name, info):
    """Ask the same questions about one dependency."""
    metric = info["metric"]
    selector = 'endpoint="' + info["endpoint"] + '"'

    own_error_query = (
        'sum(rate(' + metric + '{' + selector + ',status=~"5.."}[1m]))'
        + ' / '
        + 'sum(rate(' + metric + '{' + selector + '}[1m]))'
    )

    kinds_query = (
        'sum by (kind) (rate(rootlens_order_dependency_failures_total'
        + '{dependency="' + name + '"}[1m]))'
    )

    failure_rates = {}
    for item in run_query(kinds_query):
        failure_rates[item["metric"]["kind"]] = round(
            float(item["value"][1]), 3
        )

    return {
        "up": query_number('up{job="' + name + '"}'),
        "own_error_ratio": query_number(own_error_query),
        "failure_rates_per_second": failure_rates,
    }


def collect_evidence(ratio):
    """Gather facts about the whole system at the moment of the incident."""
    evidence = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "order_failure_ratio": ratio,
        "dependencies": {},
    }

    try:
        status_rates = {}
        for item in run_query(ORDER_STATUS_QUERY):
            status_rates[item["metric"]["status"]] = round(
                float(item["value"][1]), 3
            )
        evidence["order_status_rates_per_second"] = status_rates
        evidence["order_p95_latency_seconds"] = query_number(ORDER_P95_QUERY)
    except Exception as error:
        evidence["collection_error"] = str(error)

    for name, info in DEPENDENCIES.items():
        try:
            evidence["dependencies"][name] = collect_dependency(name, info)
        except Exception as error:
            evidence["dependencies"][name] = {"collection_error": str(error)}

    return evidence


def open_incident(value):
    evidence = collect_evidence(value)

    response = httpx.post(
        f"{ORDER_SERVICE_URL}/incidents",
        json={
            "service": "order-service",
            "endpoint": "/orders",
            "fingerprint": FINGERPRINT,
            "message": f"Order failure ratio {value:.2f} is above {THRESHOLD}",
            "evidence": evidence
        },
        timeout=5.0
    )
    print(f"OPEN incident -> created={response.json()['created']}", flush=True)
    print(f"Evidence: {evidence}", flush=True)


def resolve_incident():
    response = httpx.post(
        f"{ORDER_SERVICE_URL}/incidents/resolve",
        json={"fingerprint": FINGERPRINT},
        timeout=5.0
    )
    print(f"RESOLVE incident -> {response.json()}", flush=True)


def main():
    breaches = 0
    oks = 0

    print("Detector started.", flush=True)

    while True:
        try:
            value = get_error_ratio()

            if value is None:
                print("No data from Prometheus yet.", flush=True)
            else:
                print(f"failure ratio = {value:.2f}", flush=True)

                breaches = breaches + 1 if value >= THRESHOLD else 0
                oks = oks + 1 if value < RECOVERY_THRESHOLD else 0

                if breaches == BREACHES_TO_OPEN:
                    open_incident(value)

                if oks == OKS_TO_RESOLVE:
                    resolve_incident()

        except Exception as error:
            print(f"Check failed: {error}", flush=True)

        time.sleep(CHECK_INTERVAL)


main()