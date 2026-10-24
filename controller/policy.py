"""The rulebook: decides whether RootLens may act on a diagnosis, and how.

This module only DECIDES. It never touches Docker or the network, which makes
every rule easy to test.
"""
import re

# The only things the controller will ever be allowed to do.
ALLOWED_ACTIONS = {"rollback_deployment", "restart_service"}
ALLOWED_TARGETS = {"user-service", "payment-service"}

# The kinds of cause the investigator can name.
CAUSE_KINDS = {"down", "errors", "slow"}

MIN_CONFIDENCE = "high"
ACCEPTED_TRIGGER_STRENGTHS = {"strong", "moderate"}

MAX_ATTEMPTS_PER_INCIDENT = 2
COOLDOWN_SECONDS = 60
MAX_ACTIONS_PER_SERVICE_PER_HOUR = 3

# "v2", "v2.1", "1.10.3": an optional "v", then numbers separated by dots.
VERSION_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")

# Gentlest action first.
LADDER = ["rollback_deployment", "restart_service"]

# A cause from another service at or above this score counts as "also failing".
OTHER_SERVICE_SCORE = 0.6


def is_allowed(action, service):
    """The allowlist check. The executor must call this too."""
    return action in ALLOWED_ACTIONS and service in ALLOWED_TARGETS


def _decision(kind, reason, action=None, service=None, target_version=None):
    return {
        "decision": kind,
        "action": action,
        "service": service,
        "target_version": target_version,
        "reason": reason,
    }


def _escalate(reason):
    return _decision("escalate", reason)


def _act(action, service, reason, target_version=None):
    # Last line of defence: never return an action outside the allowlist.
    if not is_allowed(action, service):
        return _escalate(f"Blocked: {action} on {service} is not allowed.")
    return _decision("act", reason, action, service, target_version)


def cause_kind(diagnosis):
    """The kind of cause the investigator named: "down", "errors" or "slow".

    Read from the structured "cause_kind" field, never from the wording of
    "likely_cause", so rewording a sentence cannot change what the robot does.
    """
    kind = (diagnosis or {}).get("cause_kind")
    return kind if kind in CAUSE_KINDS else None


def parse_version(version):
    """Turn "v1.10.2" into (1, 10, 2) so versions compare correctly.

    Trailing zeros are dropped, so "v2" and "v2.0" are the same version.
    Returns None for anything that is not a plain version number.
    """
    match = VERSION_PATTERN.match(str(version or "").strip())
    if not match:
        return None

    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _rollback_target(trigger, service):
    """Return (previous_version, None) if a rollback makes sense, else (None, why not)."""
    if not trigger:
        return None, "there is no recent deployment of this service"

    if trigger.get("service") != service:
        return None, "the recent deployment was for a different service"

    if trigger.get("strength") not in ACCEPTED_TRIGGER_STRENGTHS:
        return None, "the deployment is too old to be blamed"

    previous = trigger.get("previous_version")
    previous_number = parse_version(previous)
    current_number = parse_version(trigger.get("version"))

    if previous in (None, "", "none") or previous_number is None:
        return None, "no previous version is recorded"

    if current_number is None or previous_number >= current_number:
        return None, "the latest deployment was itself a rollback"

    return previous, None


def _other_service_failing(diagnosis, root):
    for cause in diagnosis.get("ranked_causes") or []:
        if (
            cause.get("service") not in (None, root)
            and cause.get("score", 0) >= OTHER_SERVICE_SCORE
        ):
            return True
    return False


def decide(diagnosis, attempts=None, service_actions_last_hour=0):
    """Decide what to do about one diagnosis.

    diagnosis: the result of /investigate/{incident_id}
    attempts: actions already taken for this incident, e.g.
              [{"action": "rollback_deployment", "age_seconds": 75}]
    service_actions_last_hour: actions taken on the culprit in the last hour
    """
    attempts = attempts or []
    root = diagnosis.get("root_cause_service")

    if not root or not (diagnosis.get("ranked_causes") or []):
        return _escalate("The investigation did not name a clear cause.")

    if root not in ALLOWED_TARGETS:
        return _escalate(f"{root} is not a service the controller may act on.")

    confidence = diagnosis.get("confidence")
    if confidence != MIN_CONFIDENCE:
        return _escalate(
            f"Confidence is {confidence}; the controller only acts on "
            f"{MIN_CONFIDENCE} confidence."
        )

    if _other_service_failing(diagnosis, root):
        return _escalate(
            "More than one service appears to be failing, which is too "
            "uncertain to act on automatically."
        )

    cause = diagnosis.get("likely_cause")
    if cause_kind(diagnosis) is None:
        return _escalate(
            f"Unrecognised kind of cause: {diagnosis.get('cause_kind')!r}."
        )

    if len(attempts) >= MAX_ATTEMPTS_PER_INCIDENT:
        return _escalate(
            f"Already tried {len(attempts)} actions for this incident without "
            "recovery."
        )

    if service_actions_last_hour >= MAX_ACTIONS_PER_SERVICE_PER_HOUR:
        return _escalate(
            f"{root} has already had {service_actions_last_hour} automatic "
            "actions in the last hour."
        )

    if attempts:
        newest = min(a.get("age_seconds", 0) for a in attempts)
        if newest < COOLDOWN_SECONDS:
            return _decision(
                "wait",
                f"The last action was {int(newest)}s ago; waiting "
                f"{COOLDOWN_SECONDS}s before trying anything else.",
            )

    tried = {a.get("action") for a in attempts}
    trigger = diagnosis.get("trigger")
    target, why_not = _rollback_target(trigger, root)

    for action in LADDER:
        if action in tried:
            continue

        if action == "rollback_deployment":
            if target is None:
                continue
            return _act(
                action,
                root,
                f"{cause} (high confidence), and {trigger['version']} was "
                f"deployed {trigger['seconds_before_detection']}s before "
                f"detection, so roll back to {target}.",
                target_version=target,
            )

        if action == "restart_service":
            if "rollback_deployment" in tried:
                why = "the rollback did not restore health"
            elif target is None:
                why = f"rolling back is not possible because {why_not}"
            else:
                why = "restarting is the next step"
            return _act(
                action,
                root,
                f"{cause} (high confidence); {why}, so restart the service.",
            )

    return _escalate("Every permitted action has already been tried.")