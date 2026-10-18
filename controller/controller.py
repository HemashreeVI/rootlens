import os
import time
from datetime import datetime, timezone

import httpx

import notify
from policy import decide

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8000")
REQUESTED_MODE = os.getenv("CONTROLLER_MODE", "dry-run").strip().lower()
CHECK_INTERVAL = 10  # seconds between checks
RETRY_SECONDS = 60   # wait this long before retrying a failed notification

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


def seconds_since(iso_time):
    started = datetime.fromisoformat(iso_time)
    return (datetime.now(timezone.utc) - started).total_seconds()


def notice_info(incident_id, diagnosis, decision, age):
    return {
        "incident_id": incident_id,
        "service": diagnosis.get("root_cause_service"),
        "cause": diagnosis.get("likely_cause"),
        "confidence": diagnosis.get("confidence"),
        "trigger": notify.trigger_text(diagnosis.get("trigger")),
        "robot": notify.robot_text(decision, MODE),
        "reason": decision["reason"],
        "mode": MODE,
        "dashboard_url": os.getenv("DASHBOARD_URL", ""),
        "duration": notify.duration_text(age),
    }


def process(incident, tracked):
    incident_id = incident["incident_id"]
    diagnosis = get_diagnosis(incident_id)

    # Dry-run never acts, so there is no action history yet.
    decision = decide(diagnosis, attempts=[], service_actions_last_hour=0)
    now = time.time()
    age = seconds_since(incident["detected_at"])

    entry = tracked.setdefault(incident_id, {
        "signature": None,
        "kind": None,
        "announced": False,
        "muted": False,
        "last_notice": 0.0,
        "last_attempt": 0.0,
        "started": incident["detected_at"],
    })
    entry["service"] = diagnosis.get("root_cause_service")
    entry["cause"] = diagnosis.get("likely_cause")

    # 1. Audit log: only when the decision changes.
    signature = (
        decision["decision"], decision["action"],
        decision["service"], decision["reason"],
    )
    if entry["signature"] != signature:
        entry["signature"] = signature
        record(incident_id, decision, outcome_text(decision))
        print(
            f"[{MODE}] incident {incident_id[:8]}: {describe(decision)}. "
            f"Reason: {decision['reason']}",
            flush=True,
        )

    # 2. Notifications.
    plan = notify.plan_notice(entry, decision["decision"], age, now)
    handled = True

    if plan == "mute":
        entry["muted"] = True
        print(
            f"incident {incident_id[:8]} is too old to announce; "
            "notifications are muted for it.",
            flush=True,
        )
    elif plan:
        handled = False
        if now - entry["last_attempt"] >= RETRY_SECONDS:
            entry["last_attempt"] = now
            info = notice_info(incident_id, diagnosis, decision, age)
            sent = notify.notify(plan, info)

            if sent > 0 or not notify.deliverable(entry["service"]):
                handled = True
                entry["announced"] = True
                entry["last_notice"] = now

    if handled:
        entry["kind"] = decision["decision"]


def announce_resolved(incident_id, entry):
    if not entry.get("announced") or entry.get("muted"):
        return

    notify.notify("resolved", {
        "incident_id": incident_id,
        "service": entry.get("service"),
        "cause": entry.get("cause"),
        "mode": MODE,
        "dashboard_url": os.getenv("DASHBOARD_URL", ""),
        "duration": notify.duration_text(seconds_since(entry["started"])),
    })


def main():
    if REQUESTED_MODE != MODE:
        print(
            f"Mode '{REQUESTED_MODE}' is not available yet, so the "
            "controller is OFF.",
            flush=True,
        )
    print(f"Controller started in {MODE} mode.", flush=True)

    if os.getenv("TELEGRAM_BOT_TOKEN"):
        print("Notifications: Telegram token found.", flush=True)
    else:
        print("Notifications: disabled (no TELEGRAM_BOT_TOKEN).", flush=True)

    tracked = {}

    while True:
        try:
            if MODE != "off":
                incidents = active_incidents()
                open_ids = {i["incident_id"] for i in incidents}

                for gone in list(tracked):
                    if gone not in open_ids:
                        entry = tracked.pop(gone)
                        print(f"incident {gone[:8]} is no longer open.", flush=True)
                        announce_resolved(gone, entry)

                for incident in incidents:
                    process(incident, tracked)

        except Exception as error:
            print(f"Check failed: {error}", flush=True)

        time.sleep(CHECK_INTERVAL)


main()