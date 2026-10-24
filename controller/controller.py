"""The RootLens controller (the "robot").

Every few seconds it looks at open incidents and, for each one:
  1. asks the rulebook (policy.py) what to do,
  2. in enforce mode: runs the pre-flight checks (safety.py), carries out the
     fix (executor.py), then watches the live numbers to verify it worked,
  3. writes every decision to the audit log,
  4. sends Telegram notices to the service's owner.

Modes (CONTROLLER_MODE):
  off      do nothing
  dry-run  decide and record, but never act
  enforce  decide, act, and verify
Any other value means OFF, because an unknown setting must never lead to action.
"""
import os
import time
from datetime import datetime, timezone

import httpx

import executor
import notify
import policy
import safety
import topology
from policy import decide

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8000")
CHECK_INTERVAL = 10  # seconds between checks
RETRY_SECONDS = 60   # wait this long before retrying a failed notification

SUPPORTED_MODES = ("off", "dry-run", "enforce")


def resolve_mode(requested):
    mode = (requested or "").strip().lower()
    return mode if mode in SUPPORTED_MODES else "off"


REQUESTED_MODE = os.getenv("CONTROLLER_MODE", "dry-run").strip().lower()
MODE = resolve_mode(REQUESTED_MODE)


# ---------- talking to order-service ----------

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


def get_audit_entries():
    """Recent audit entries; used to count earlier attempts."""
    response = httpx.get(
        f"{ORDER_SERVICE_URL}/audit", params={"limit": 500}, timeout=5.0
    )
    return response.json()["entries"]


def live_state():
    """The live failure ratio and the versions running right now."""
    try:
        data = httpx.get(f"{ORDER_SERVICE_URL}/api/overview", timeout=10.0).json()
    except Exception:
        return {"failure_ratio": None, "versions": {}}

    services = data.get("services") or {}
    return {
        "failure_ratio": data.get("failure_ratio"),
        "versions": {
            name: (info or {}).get("version") for name, info in services.items()
        },
    }


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


# ---------- wording ----------

def describe(decision):
    if decision["decision"] == "act":
        target = f" to {decision['target_version']}" if decision["target_version"] else ""
        prefix = "would " if MODE == "dry-run" else ""
        return f"{prefix}{decision['action']} {decision['service']}{target}"
    if decision["decision"] == "wait":
        return "wait"
    return "escalate to a human"


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


def short(incident_id):
    return incident_id[:8]


# ---------- enforce mode ----------

def carry_out(incident_id, decision, diagnosis, age, entry, now):
    """Pre-flight, then execute. Returns (outcome, counted_as_attempt)."""
    check = safety.preflight(decision, diagnosis, live_state(), age)
    if not check["ok"]:
        return f"Refused: {check['reason']}", False

    print(
        f"[enforce] incident {short(incident_id)}: carrying out "
        f"{describe(decision)}...",
        flush=True,
    )
    result = executor.execute(decision)
    counted = safety.counts_as_attempt(result["outcome"])

    if counted:
        # Watch the live numbers before deciding anything else.
        entry["verify"] = {"started": now, "readings": [], "decision": decision}

    return result["outcome"], counted


def verify_step(incident_id, entry, now):
    """One verification check after a fix. Returns the verdict."""
    check = entry["verify"]
    check["readings"].append(live_state()["failure_ratio"])
    verdict = safety.judge_recovery(check["readings"], now - check["started"])

    if verdict["state"] == "pending":
        return verdict

    entry["verify"] = None
    print(
        f"[enforce] incident {short(incident_id)}: verification "
        f"{verdict['state']}. {verdict['reason']}",
        flush=True,
    )

    if verdict["state"] != "recovered":
        notify.notify("fix_failed", {
            "incident_id": incident_id,
            "service": entry.get("service"),
            "cause": entry.get("cause"),
            "robot": notify.robot_text(check["decision"], MODE),
            "reason": verdict["reason"],
            "mode": MODE,
            "dashboard_url": os.getenv("DASHBOARD_URL", ""),
        })

    return verdict


# ---------- one incident ----------

def process(incident, tracked):
    incident_id = incident["incident_id"]
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
        "verify": None,
    })

    # A fix is being checked: do nothing else until we know whether it worked.
    if entry.get("verify"):
        verify_step(incident_id, entry, now)
        return

    diagnosis = get_diagnosis(incident_id)
    entry["service"] = diagnosis.get("root_cause_service")
    entry["cause"] = diagnosis.get("likely_cause")

    if MODE == "enforce":
        audit = get_audit_entries()
        now_dt = datetime.now(timezone.utc)
        attempts = safety.attempts_from_audit(audit, incident_id, now_dt)
        recent = safety.actions_last_hour(
            audit, diagnosis.get("root_cause_service"), now_dt
        )
        decision = decide(diagnosis, attempts=attempts, service_actions_last_hour=recent)
    else:
        decision = decide(diagnosis, attempts=[], service_actions_last_hour=0)

    outcome = outcome_text(decision)
    acted = False
    if MODE == "enforce" and decision["decision"] == "act":
        outcome, acted = carry_out(incident_id, decision, diagnosis, age, entry, now)

    # 1. Audit log: every real action, plus every change of decision.
    #    (A "wait" reason contains a ticking clock, so it is left out.)
    signature = (
        decision["decision"], decision["action"], decision["service"],
        "" if decision["decision"] == "wait" else decision["reason"],
        outcome.split(":")[0],
    )
    if acted or entry["signature"] != signature:
        entry["signature"] = signature
        record(incident_id, decision, outcome)
        print(
            f"[{MODE}] incident {short(incident_id)}: {describe(decision)}. "
            f"Reason: {decision['reason']} Outcome: {outcome}",
            flush=True,
        )

    # 2. Notifications.
    plan = notify.plan_notice(entry, decision["decision"], age, now)
    handled = True

    if plan == "mute":
        entry["muted"] = True
        print(
            f"incident {short(incident_id)} is too old to announce; "
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

    if acted and not entry.get("muted"):
        info = notice_info(incident_id, diagnosis, decision, age)
        info["result"] = outcome
        notify.notify("acted", info)


def announce_resolved(incident_id, entry):
    if entry.get("verify"):
        print(
            f"incident {short(incident_id)} closed while the fix was being "
            "verified: the fix worked.",
            flush=True,
        )

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
            f"Mode '{REQUESTED_MODE}' is not a known mode, so the "
            "controller is OFF.",
            flush=True,
        )
    print(f"Controller started in {MODE} mode.", flush=True)
    if MODE == "enforce":
        for name in sorted(policy.ALLOWED_TARGETS):
            actions = ", ".join(sorted(topology.allowed_actions(name)))
            print(f"ENFORCE: the robot may {actions} on {name}.", flush=True)

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
                        print(f"incident {short(gone)} is no longer open.", flush=True)
                        announce_resolved(gone, entry)

                for incident in incidents:
                    process(incident, tracked)

        except Exception as error:
            print(f"Check failed: {error}", flush=True)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()