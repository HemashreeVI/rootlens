import json

# The Order Service gives up waiting for a dependency after this long.
TIMEOUT_SECONDS = 3.0

# Who depends on whom. Used to work out which services are impacted.
DEPENDENCY_GRAPH = {
    "order-service": ["user-service", "payment-service"],
}


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
    results.append((f"{name} is down", name, score, support, against))

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
    results.append((f"{name} is returning errors", name, score, support, against))

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
    results.append((f"{name} is too slow (timeouts)", name, score, support, against))

    return results


def _score_causes(evidence):
    """Score every cause for every dependency, best first."""
    p95 = evidence.get("order_p95_latency_seconds")
    scored = []

    for name, info in _dependencies(evidence).items():
        scored.extend(_score_dependency(name, info, p95))

    ranked = []
    for cause, service, value, support, against in sorted(
        scored, key=lambda item: item[2], reverse=True
    ):
        ranked.append({
            "cause": cause,
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
        "confidence": confidence,
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

    if not ranked or ranked[0]["score"] < 0.4:
        likely_cause = "Insufficient evidence to name a cause."
        confidence = "low"
        root_service = None
    else:
        likely_cause = ranked[0]["cause"]
        confidence = _confidence(ranked)
        root_service = ranked[0]["service"]

        if len(ranked) > 1:
            second = ranked[1]
            if second["score"] >= 0.6 and second["service"] != root_service:
                notes.append(
                    "More than one dependency appears to be failing: "
                    f"{root_service} and {second['service']}."
                )

    return {
        "incident_id": incident.get("incident_id"),
        "affected_service": incident.get("service", "unknown"),
        "failed_endpoint": incident.get("endpoint"),
        "observed_facts": _describe_facts(evidence),
        "ranked_causes": ranked,
        "likely_cause": likely_cause,
        "confidence": confidence,
        "root_cause_service": root_service,
        "impacted_services": _impacted_services(root_service) if root_service else [],
        "notes": notes,
        "recommended_next_step": (
            "Review the ranked causes. Automated remediation is not "
            "implemented yet."
        ),
    }