"""Safety checks for enforce mode.

Everything here is a pure function: no network and no Docker. That makes every
rule easy to test.
"""
from datetime import datetime

from policy import cause_kind, is_allowed, parse_version

# ---- pre-flight ----
MAX_INCIDENT_AGE_SECONDS = 1800
LIVE_FAILURE_THRESHOLD = 0.3

# ---- verification ----
RECOVERY_THRESHOLD = 0.1
CONSECUTIVE_OK = 3
VERIFY_TIMEOUT_SECONDS = 120



def _no(reason):
    return {"ok": False, "reason": reason}


def preflight(decision, diagnosis, live, incident_age_seconds):
    """Is it safe to carry out this decision right now?

    decision: what policy.decide returned (action, service, target_version)
    diagnosis: the result of /investigate/{incident_id}
    live: {"failure_ratio": float or None, "versions": {service: version or None}}
    """
    action = decision.get("action")
    service = decision.get("service")

    if not is_allowed(action, service):
        return _no(f"{action} on {service} is not allowed.")

    if incident_age_seconds > MAX_INCIDENT_AGE_SECONDS:
        minutes = int(incident_age_seconds // 60)
        return _no(
            f"The incident is {minutes} minutes old, which is too old to act "
            "on automatically."
        )

    ratio = live.get("failure_ratio")
    if ratio is None:
        return _no(
            "There is no live traffic data, so the failure cannot be confirmed."
        )

    if ratio < LIVE_FAILURE_THRESHOLD:
        return _no(
            f"The live failure ratio is {ratio:.0%}, so the system has "
            "already recovered."
        )

    if action == "rollback_deployment":
        trigger = diagnosis.get("trigger") or {}
        expected = trigger.get("version")
        target = decision.get("target_version")
        live_version = (live.get("versions") or {}).get(service)

        if not expected or not target:
            return _no("There is no release to roll back.")

        target_number = parse_version(target)
        expected_number = parse_version(expected)
        if (
            target_number is None
            or expected_number is None
            or target_number >= expected_number
        ):
            return _no(f"{target} is not an earlier version than {expected}.")

        if live_version is None:
            if cause_kind(diagnosis) != "down":
                return _no(f"The live version of {service} cannot be read.")
        elif live_version != expected:
            return _no(
                f"{service} is running {live_version}, not {expected}, so "
                "the situation has changed since the diagnosis."
            )

    return {"ok": True, "reason": "All pre-flight checks passed."}


def judge_recovery(readings, elapsed_seconds):
    """Did the fix work? readings are failure ratios, oldest first.

    Returns {"state": "recovered" | "pending" | "failed" | "inconclusive",
             "reason": "..."}
    """
    recent = readings[-CONSECUTIVE_OK:]

    if len(recent) == CONSECUTIVE_OK and all(
        r is not None and r < RECOVERY_THRESHOLD for r in recent
    ):
        return {
            "state": "recovered",
            "reason": (
                f"The failure ratio stayed below {RECOVERY_THRESHOLD:.0%} "
                f"for {CONSECUTIVE_OK} checks in a row."
            ),
        }

    if elapsed_seconds < VERIFY_TIMEOUT_SECONDS:
        return {"state": "pending", "reason": "Still checking."}

    latest = readings[-1] if readings else None

    if latest is None:
        return {
            "state": "inconclusive",
            "reason": "There was no traffic data to confirm recovery.",
        }

    if latest < RECOVERY_THRESHOLD:
        return {
            "state": "inconclusive",
            "reason": (
                f"The failure ratio dropped to {latest:.0%}, but not for long "
                "enough to confirm recovery."
            ),
        }

    return {
        "state": "failed",
        "reason": (
            f"The failure ratio was still {latest:.0%} after "
            f"{int(elapsed_seconds)}s."
        ),
    }


def counts_as_attempt(outcome):
    """An action that ran (or tried to run) counts toward the limits."""
    text = outcome or ""
    return text.startswith("Executed") or text.startswith("Execution failed")


def _age_seconds(logged_at, now):
    try:
        when = datetime.fromisoformat(logged_at)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - when).total_seconds())


def attempts_from_audit(entries, incident_id, now):
    """Actions already taken for this incident, in the form policy.decide wants."""
    attempts = []

    for entry in entries:
        if entry.get("incident_id") != incident_id:
            continue
        if entry.get("mode") != "enforce" or entry.get("decision") != "act":
            continue
        if not counts_as_attempt(entry.get("outcome")):
            continue

        attempts.append({
            "action": entry.get("action"),
            "age_seconds": _age_seconds(entry.get("logged_at"), now),
        })

    return attempts


def actions_last_hour(entries, service, now):
    """How many automatic actions this service has had in the last hour."""
    count = 0

    for entry in entries:
        if entry.get("service") != service:
            continue
        if entry.get("mode") != "enforce" or entry.get("decision") != "act":
            continue
        if not counts_as_attempt(entry.get("outcome")):
            continue
        if _age_seconds(entry.get("logged_at"), now) < 3600:
            count += 1

    return count