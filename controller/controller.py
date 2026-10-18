import os
import time

import httpx

from policy import decide

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8000")
REQUESTED_MODE = os.getenv("CONTROLLER_MODE", "dry-run").strip().lower()
CHECK_INTERVAL = 10  # seconds between checks

# Only these modes exist for now. Anything else (including "enforce") means OFF,
# because an unknown setting must never lead to action.
SUPPORTED_MODES = ("off", "dry-run")
MODE = REQUESTED_MODE if REQUESTED_MODE in SUPPORTED_MODES else "off"


def active_incidents():
    """Open incidents that the detector created (they have a fingerprint)."""
    response = httpx.get(f"{ORDER_SERVICE_URL}/incidents", timeout=5.0)
    return [
        incident for incident in response.json()["incidents"]
        if incident.get("status") == "open" and incident.get("fingerprint")
    ]


def get_diagnosis(incident_id):
    response = httpx.get(
        f"{ORDER_SERVICE_URL}/investigate/{incident_id}", timeout=10.0
    )
    return response.json()


def record(incident_id, decision, outcome):
    httpx.post(
        f"{ORDER_SERVICE_URL}/audit",
        json={
            "incident_id": incident_id,
            "mode": MODE,
            "decision": decision["decision"],
            "action": decision["action"],
            "service": decision["service"],
            "target_version": decision["target_version"],
            "reason": decision["reason"],
            "outcome": outcome,
        },
        timeout=5.0,
    )


def describe(decision):
    if decision["decision"] == "act":
        target = f" to {decision['target_version']}" if decision["target_version"] else ""
        return f"would {decision['action']} {decision['service']}{target}"
    if decision["decision"] == "wait":
        return "would wait"
    return "would escalate to a human"


def outcome_text(decision):
    if decision["decision"] == "act":
        return "Not executed (dry-run)"
    if decision["decision"] == "wait":
        return "No action"
    return "Needs a human"


def process(incident, last_logged):
    incident_id = incident["incident_id"]
    diagnosis = get_diagnosis(incident_id)

    # Dry-run never acts, so there is no action history yet.
    decision = decide(diagnosis, attempts=[], service_actions_last_hour=0)

    signature = (
        decision["decision"], decision["action"],
        decision["service"], decision["reason"],
    )
    if last_logged.get(incident_id) == signature:
        return  # same decision as last time: don't repeat it

    last_logged[incident_id] = signature
    record(incident_id, decision, outcome_text(decision))
    print(
        f"[{MODE}] incident {incident_id[:8]}: {describe(decision)}. "
        f"Reason: {decision['reason']}",
        flush=True,
    )


def main():
    if REQUESTED_MODE != MODE:
        print(
            f"Mode '{REQUESTED_MODE}' is not available yet, so the "
            "controller is OFF.",
            flush=True,
        )
    print(f"Controller started in {MODE} mode.", flush=True)

    last_logged = {}

    while True:
        try:
            if MODE != "off":
                incidents = active_incidents()
                open_ids = {i["incident_id"] for i in incidents}

                for gone in list(last_logged):
                    if gone not in open_ids:
                        print(f"incident {gone[:8]} is no longer open.", flush=True)
                        del last_logged[gone]

                for incident in incidents:
                    process(incident, last_logged)

        except Exception as error:
            print(f"Check failed: {error}", flush=True)

        time.sleep(CHECK_INTERVAL)


main()