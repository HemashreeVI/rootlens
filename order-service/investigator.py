
def investigate_incident(incident):
    service = incident.get("service", "unknown")
    endpoint = incident.get("endpoint")
    status = incident.get("http_status")
    message = incident.get("message", "")

    evidence = []

    if endpoint:
        evidence.append(
            f"The failed request targeted endpoint {endpoint}."
        )

    if status == 503:
        evidence.append(
            "The service returned HTTP 503 (Service Unavailable)."
        )

    if "Dependency failure detected" in message:
        evidence.append(
            "The failure was detected during a dependency request."
        )

    if "Dependency request failed" in message:
        evidence.append(
            "The dependency request failed at the network "
            "or HTTP-client level."
        )

    if endpoint == "/simulate-failure" and status == 503:
        likely_cause = (
            "The known simulation endpoint deliberately returned "
            "HTTP 503. This is a test failure, not evidence of "
            "an unexpected production outage."
        )
        confidence = "high"
    elif status == 503:
        likely_cause = (
            "The service reported temporary unavailability. "
            "The exact underlying cause is not established "
            "by the available evidence."
        )
        confidence = "limited"
    else:
        likely_cause = (
            "The available evidence is insufficient "
            "to identify the root cause."
        )
        confidence = "low"

    return {
        "incident_id": incident.get("incident_id"),
        "affected_service": service,
        "failed_endpoint": endpoint,
        "evidence": evidence,
        "likely_cause": likely_cause,
        "confidence": confidence,
        "recommended_next_step": (
            "Inspect service logs and verify recovery "
            "before attempting remediation."
        )
    }
