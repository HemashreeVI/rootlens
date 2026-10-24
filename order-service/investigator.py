import json
import re

import topology

# Both come from services.yaml (see common/topology.py).
# The entry service gives up waiting for a dependency after this long.
TIMEOUT_SECONDS = float(topology.entry()["timeout_seconds"])

# Who depends on whom. Used to work out which services are impacted.
DEPENDENCY_GRAPH = topology.dependency_graph()

# How close a deployment must be to the incident to count as a strong link.
STRONG_SECONDS = 300
MODERATE_SECONDS = 900

# "v2", "v2.1", "1.10.3": an optional "v", then numbers separated by dots.
# Keep in step with controller/policy.py.
VERSION_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")


def _load_evidence(incident):
    """Read the saved evidence (stored as JSON text). Returns {} if none."""
    raw = incident.get("evidence_json")

    if not raw:
        return {}

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _dependencies(evidence):
    """Return per-dependency evidence. Converts older single-service evidence."""
    if "dependencies" in evidence:
        return evidence["dependencies"] or {}

    if "user_service_up" in evidence:
        rates = evidence.get("order_status_rates_per_second") or {}
        return {
            "user-service": {
                "up": evidence.get("user_service_up"),
                "own_error_ratio": evidence.get("user_service_error_ratio"),
                "failure_rates_per_second": {
                    "error": rates.get("503", 0),
                    "timeout": rates.get("504", 0),
                },
            }
        }

    return {}


def _failure_split(info):
    """Return (timeouts, errors, total) failure rates for one dependency."""
    rates = info.get("failure_rates_per_second") or {}
    timeouts = rates.get("timeout", 0)
    errors = rates.get("error", 0) + rates.get("unreachable", 0)
    return timeouts, errors, timeouts + errors


def _age_text(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} seconds"
    return f"{seconds // 60} min {seconds % 60} s"


def _trigger_strength(seconds):
    if seconds <= STRONG_SECONDS:
        return "strong"
    if seconds <= MODERATE_SECONDS:
        return "moderate"
    return "weak"


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


def _find_trigger(evidence, service):
    """The most recent deployment of this service, if any, with its strength."""
    candidates = [
        d for d in (evidence.get("recent_deployments") or [])
        if d.get("service") == service
    ]

    if not candidates:
        return None

    latest = min(
        candidates,
        key=lambda d: d.get("seconds_before_detection", 10 ** 9)
    )
    seconds = latest.get("seconds_before_detection", 0)

    current = parse_version(latest.get("version"))
    previous = parse_version(latest.get("previous_version"))
    is_rollback = (
        current is not None and previous is not None and current < previous
    )

    return {
        "service": latest.get("service"),
        "version": latest.get("version"),
        "previous_version": latest.get("previous_version"),
        "deployed_at": latest.get("deployed_at"),
        "seconds_before_detection": seconds,
        "strength": _trigger_strength(seconds),
        "kind": "rollback" if is_rollback else "release",
    }


def _next_step(trigger):
    default = "Review the ranked causes and the service logs."

    if not trigger:
        return default

    previous = trigger.get("previous_version")
    age = _age_text(trigger["seconds_before_detection"])

    if trigger.get("kind") == "rollback":
        return (
            f"{trigger['service']} was rolled back from {previous} to "
            f"{trigger['version']} {age} before detection, so a new release "
            "does not explain this failure. Investigate manually and do not "
            "roll back again."
        )

    if previous and previous != "none":
        return (
            f"Roll back {trigger['service']} from {trigger['version']} to "
            f"{previous}: it was deployed {age} before the failures were "
            "detected. In enforce mode RootLens does this automatically."
        )

    return (
        f"{trigger['service']} {trigger['version']} was deployed {age} "
        "before detection, but no earlier version is recorded, so there is "
        "nothing to roll back to. Investigate manually."
    )

def _describe_facts(evidence):
    """Turn the raw numbers into plain-English observations."""
    facts = []

    for name, info in sorted(_dependencies(evidence).items()):
        up = info.get("up")
        if up == 1:
            facts.append(f"{name} is reachable (up = 1).")
        elif up == 0:
            facts.append(f"{name} is NOT reachable (up = 0).")
        else:
            facts.append(f"No 'up' data for {name}.")

        own_err = info.get("own_error_ratio")
        if own_err is None:
            facts.append(f"{name} reported no request metrics.")
        else:
            facts.append(f"{name} is failing {own_err:.0%} of its own requests.")

        timeouts, errors, total = _failure_split(info)
        if total > 0:
            facts.append(
                f"Order Service saw {timeouts}/s timeouts and {errors}/s "
                f"errors when calling {name}."
            )

    rates = evidence.get("order_status_rates_per_second") or {}
    for status, rate in sorted(rates.items()):
        facts.append(f"Order requests with status {status}: {rate}/second.")

    p95 = evidence.get("order_p95_latency_seconds")
    if p95 is not None:
        facts.append(f"Order requests: 95th percentile latency is {p95:.2f}s.")

    for d in evidence.get("recent_deployments") or []:
        facts.append(
            f"{d.get('service')} was deployed as {d.get('version')} "
            f"(from {d.get('previous_version')}) "
            f"{_age_text(d.get('seconds_before_detection', 0))} "
            "before the incident was detected."
        )

    return facts


def _score_dependency(name, info, p95):
    """Score the three possible causes for one dependency."""
    up = info.get("up")
    own_err = info.get("own_error_ratio")
    timeouts, errors, total = _failure_split(info)

    # No failures when calling this dependency: it is not implicated.
    if total <= 0:
        return []

    rates = info.get("failure_rates_per_second") or {}
    unreachable = rates.get("unreachable", 0)
    share_timeout = timeouts / total
    share_error = errors / total

    results = []

    # ---------- Cause 1: the dependency is down ----------
    score, support, against = 0.0, [], []
    if up == 0:
        score += 0.6
        support.append(f"{name} 'up' metric is 0.")
        if own_err is None:
            score += 0.2
            support.append(f"{name} is reporting no request metrics.")
    elif up == 1:
        against.append(f"{name} 'up' metric is 1 (it is running).")
    if unreachable / total >= 0.5:
        score += 0.2
        support.append(f"Order Service could not connect to {name}.")
    results.append((f"{name} is down", "down", name, score, support, against))

    # ---------- Cause 2: the dependency is returning errors ----------
    score, support, against = 0.0, [], []
    if up == 1:
        score += 0.25
        support.append(f"{name} is running.")
    elif up == 0:
        against.append(f"{name} is not running, so errors are not its own.")
    if own_err is not None and own_err >= 0.2:
        score += 0.45
        support.append(f"{name} is failing {own_err:.0%} of its own requests.")
    elif up == 1:
        against.append(f"{name} reports few or no errors of its own.")
    if share_error >= 0.8:
        score += 0.15
        support.append(f"Almost all failures calling {name} are error responses.")
    if p95 is not None and p95 < TIMEOUT_SECONDS * 0.7:
        score += 0.15
        support.append("Responses are not unusually slow.")
    elif p95 is not None:
        against.append("Responses are very slow, which points away from plain errors.")
    results.append((f"{name} is returning errors", "errors", name, score, support, against))

    # ---------- Cause 3: the dependency is too slow ----------
    score, support, against = 0.0, [], []
    if up == 1:
        score += 0.2
        support.append(f"{name} is running.")
    if share_timeout >= 0.5:
        score += 0.4
        support.append(f"Most failures calling {name} are timeouts.")
        if p95 is not None and p95 >= TIMEOUT_SECONDS * 0.8:
            score += 0.3
            support.append(
                f"p95 latency is {p95:.2f}s, close to the "
                f"{TIMEOUT_SECONDS:.0f}s timeout."
            )
    else:
        against.append(f"Few or no timeouts when calling {name}.")
    if up == 1 and (own_err is None or own_err < 0.1):
        score += 0.1
        support.append(f"{name} is not reporting errors of its own.")
    if up == 0:
        score = max(0.0, score - 0.4)
        against.append(f"{name} is not reachable, so it cannot be 'just slow'.")
    results.append((f"{name} is too slow (timeouts)", "slow", name, score, support, against))

    return results


def _score_causes(evidence):
    """Score every cause for every dependency, best first."""
    p95 = evidence.get("order_p95_latency_seconds")
    scored = []

    for name, info in _dependencies(evidence).items():
        scored.extend(_score_dependency(name, info, p95))

    ranked = []
    for cause, kind, service, value, support, against in sorted(
        scored, key=lambda item: item[3], reverse=True
    ):
        ranked.append({
            "cause": cause,
            "kind": kind,
            "service": service,
            "score": round(value, 2),
            "supporting_evidence": support,
            "contradicting_evidence": against,
        })

    return ranked


def _confidence(ranked):
    top = ranked[0]["score"]
    second = ranked[1]["score"] if len(ranked) > 1 else 0

    if top >= 0.75 and (top - second) >= 0.3:
        return "high"
    if top >= 0.5:
        return "medium"
    return "low"


def _impacted_services(culprit):
    """Services that depend on the culprit and are hurt by its failure."""
    return sorted(
        service for service, deps in DEPENDENCY_GRAPH.items()
        if culprit in deps
    )


def _legacy_investigation(incident):
    """Used for older incidents that have no saved evidence."""
    service = incident.get("service", "unknown")
    endpoint = incident.get("endpoint")
    status = incident.get("http_status")
    message = incident.get("message", "")

    observed = []

    if endpoint:
        observed.append(f"The failed request targeted endpoint {endpoint}.")

    if status == 503:
        observed.append("The service returned HTTP 503 (Service Unavailable).")

    if "Dependency failure detected" in message:
        observed.append("The failure was detected during a dependency request.")

    if "Dependency request failed" in message:
        observed.append(
            "The dependency request failed at the network or HTTP-client level."
        )

    if endpoint == "/simulate-failure" and status == 503:
        likely_cause = (
            "The known simulation endpoint deliberately returned HTTP 503. "
            "This is a test failure, not evidence of an unexpected outage."
        )
        confidence = "high"
    elif status == 503:
        likely_cause = (
            "The service reported temporary unavailability. The exact "
            "cause is not established by the available evidence."
        )
        confidence = "limited"
    else:
        likely_cause = (
            "The available evidence is insufficient to identify the root cause."
        )
        confidence = "low"

    return {
        "incident_id": incident.get("incident_id"),
        "affected_service": service,
        "failed_endpoint": endpoint,
        "observed_facts": observed,
        "ranked_causes": [],
        "likely_cause": likely_cause,
        "cause_kind": None,
        "confidence": confidence,
        "trigger": None,
        "recommended_next_step": (
            "Inspect service logs and verify recovery before attempting remediation."
        ),
    }


def investigate_incident(incident):
    evidence = _load_evidence(incident)

    if not evidence:
        return _legacy_investigation(incident)

    ranked = _score_causes(evidence)
    notes = []
    trigger = None

    if not ranked or ranked[0]["score"] < 0.4:
        likely_cause = "Insufficient evidence to name a cause."
        kind = None
        confidence = "low"
        root_service = None
    else:
        likely_cause = ranked[0]["cause"]
        kind = ranked[0]["kind"]
        confidence = _confidence(ranked)
        root_service = ranked[0]["service"]

        if len(ranked) > 1:
            second = ranked[1]
            if second["score"] >= 0.6 and second["service"] != root_service:
                notes.append(
                    "More than one dependency appears to be failing: "
                    f"{root_service} and {second['service']}."
                )

        trigger = _find_trigger(evidence, root_service)

        if trigger and trigger["strength"] == "weak":
            notes.append(
                f"{trigger['service']} {trigger['version']} was deployed "
                f"{_age_text(trigger['seconds_before_detection'])} before "
                "detection; the timing is only a weak link to this failure."
            )

    return {
        "incident_id": incident.get("incident_id"),
        "affected_service": incident.get("service", "unknown"),
        "failed_endpoint": incident.get("endpoint"),
        "observed_facts": _describe_facts(evidence),
        "ranked_causes": ranked,
        "likely_cause": likely_cause,
        "cause_kind": kind,
        "confidence": confidence,
        "root_cause_service": root_service,
        "impacted_services": _impacted_services(root_service) if root_service else [],
        "trigger": trigger,
        "notes": notes,
        "recommended_next_step": _next_step(trigger),
    }