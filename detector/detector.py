import os
import time
from datetime import datetime, timezone

import httpx

import topology

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8000")

CHECK_INTERVAL = 10        # seconds between checks
THRESHOLD = 0.3            # failure fraction that counts as "bad"
RECOVERY_THRESHOLD = 0.1   # failure fraction that counts as "healthy again"
BREACHES_TO_OPEN = 3       # bad checks in a row before opening an incident
OKS_TO_RESOLVE = 3         # healthy checks in a row before resolving

# Deployments older than this are not included as evidence.
RECENT_DEPLOY_WINDOW_SECONDS = 1800

# Which app to watch comes from services.yaml (see common/topology.py).
ENTRY = topology.entry()
ENTRY_SELECTOR = 'endpoint="' + ENTRY["endpoint"] + '"'

FINGERPRINT = ENTRY["service"] + ":error_ratio"

# The failure fraction of entry requests: this is what triggers incidents.
QUERY = (
    "sum(rate(" + ENTRY["requests_metric"] + "{" + ENTRY_SELECTOR
    + ',status=~"5.."}[1m]))'
    + " / "
    + "sum(rate(" + ENTRY["requests_metric"] + "{" + ENTRY_SELECTOR + "}[1m]))"
)

# Every service behind the entry, and where its own metrics live.
DEPENDENCIES = {
    name: {"metric": info["requests_metric"], "endpoint": info["endpoint"]}
    for name, info in topology.services().items()
}

ORDER_STATUS_QUERY = (
    "sum by (status) "
    "(rate(" + ENTRY["requests_metric"] + "{" + ENTRY_SELECTOR + "}[1m]))"
)

ORDER_P95_QUERY = (
    "histogram_quantile(0.95, sum by (le) "
    "(rate(" + ENTRY["latency_metric"] + "_bucket"
    "{" + ENTRY_SELECTOR + "}[1m])))"
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
        "sum by (kind) (rate(" + ENTRY["dependency_failures_metric"]
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


def collect_recent_deployments(now):
    """Ask the Order Service which releases happened recently."""
    response = httpx.get(f"{ORDER_SERVICE_URL}/deployments", timeout=5.0)

    recent = []
    for item in response.json()["deployments"]:
        deployed_at = datetime.fromisoformat(item["deployed_at"])
        age = (now - deployed_at).total_seconds()

        if 0 <= age <= RECENT_DEPLOY_WINDOW_SECONDS:
            recent.append({
                "service": item["service"],
                "version": item["version"],
                "previous_version": item.get("previous_version"),
                "deployed_at": item["deployed_at"],
                "seconds_before_detection": round(age),
            })

    return recent


def collect_evidence(ratio):
    """Gather facts about the whole system at the moment of the incident."""
    now = datetime.now(timezone.utc)

    evidence = {
        "collected_at": now.isoformat(),
        "order_failure_ratio": ratio,
        "dependencies": {},
        "recent_deployments": [],
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

    try:
        evidence["recent_deployments"] = collect_recent_deployments(now)
    except Exception as error:
        evidence["deployments_error"] = str(error)

    return evidence


def open_incident(value):
    evidence = collect_evidence(value)

    response = httpx.post(
        f"{ORDER_SERVICE_URL}/incidents",
        json={
            "service": ENTRY["service"],
            "endpoint": ENTRY["endpoint"],
            "fingerprint": FINGERPRINT,
            "message": (
                f"{ENTRY.get('display_name', ENTRY['service'])} failure "
                f"ratio {value:.2f} is above {THRESHOLD}"
            ),
            "evidence": evidence
        },
        timeout=5.0
    )
    response.raise_for_status()
    print(f"OPEN incident -> created={response.json()['created']}", flush=True)
    print(f"Evidence: {evidence}", flush=True)


def resolve_incident():
    response = httpx.post(
        f"{ORDER_SERVICE_URL}/incidents/resolve",
        json={"fingerprint": FINGERPRINT},
        timeout=5.0
    )
    response.raise_for_status()
    print(f"RESOLVE incident -> {response.json()}", flush=True)


def next_step(state, value):
    """Update the counters for one reading and say what to report, if anything.

    state: {"breaches": int, "oks": int, "reported": None | "open" | "resolved"}
    Returns "open", "resolve" or None. The caller marks the report as done
    (state["reported"]) only once it has succeeded, so a failed report is
    simply tried again on the next check instead of being lost.
    """
    state["breaches"] = state["breaches"] + 1 if value >= THRESHOLD else 0
    state["oks"] = state["oks"] + 1 if value < RECOVERY_THRESHOLD else 0

    if state["breaches"] >= BREACHES_TO_OPEN and state["reported"] != "open":
        return "open"

    if state["oks"] >= OKS_TO_RESOLVE and state["reported"] != "resolved":
        return "resolve"

    return None


def main():
    # "reported" starts unknown, so after a restart the detector re-reports
    # whatever it sees. Order Service ignores duplicates, so that is safe.
    state = {"breaches": 0, "oks": 0, "reported": None}

    print("Detector started.", flush=True)

    while True:
        try:
            value = get_error_ratio()

            if value is None:
                print("No data from Prometheus yet.", flush=True)
            else:
                print(f"failure ratio = {value:.2f}", flush=True)

                step = next_step(state, value)

                if step == "open":
                    open_incident(value)
                    state["reported"] = "open"

                if step == "resolve":
                    resolve_incident()
                    state["reported"] = "resolved"

        except Exception as error:
            print(f"Check failed: {error}", flush=True)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
