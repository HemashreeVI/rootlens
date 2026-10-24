from fastapi import FastAPI
from fastapi.responses import Response, JSONResponse, HTMLResponse
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST
)
import os
import json
import time
import httpx
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel
from typing import Optional
from dashboard import DASHBOARD_HTML, build_overview

from database import (
    initialize_database,
    save_incident,
    get_incidents,
    find_open_incident,
    update_incident_status,
    save_evidence,
    save_deployment,
    get_deployments,
    save_audit,
    get_audit
)
from investigator import investigate_incident

app = FastAPI(title="RootLens Order Service")

# Count requests handled by Order Service.
REQUEST_COUNT = Counter(
    "rootlens_order_requests_total",
    "Total number of requests handled by Order Service",
    ["endpoint", "method", "status"]
)

# How long /orders requests take, including the dependency calls.
ORDER_LATENCY = Histogram(
    "rootlens_order_request_duration_seconds",
    "Duration of Order Service requests in seconds",
    ["endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 3.0, 3.5, 5.0]
)

# Records WHICH dependency failed and HOW (timeout, unreachable, error).
DEPENDENCY_FAILURES = Counter(
    "rootlens_order_dependency_failures_total",
    "Failures when the Order Service calls a dependency",
    ["dependency", "kind"]
)

initialize_database()

USER_SERVICE_URL = os.getenv(
    "USER_SERVICE_URL",
    "http://localhost:8001"
)

PAYMENT_SERVICE_URL = os.getenv(
    "PAYMENT_SERVICE_URL",
    "http://localhost:8003"
)


@app.get("/")
def home():
    REQUEST_COUNT.labels(
        endpoint="/",
        method="GET",
        status="200"
    ).inc()

    return {"message": "Hello from RootLens Order Service"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML

@app.get("/api/overview")
async def api_overview():
    return await build_overview()


@app.get("/health")
def health():
    REQUEST_COUNT.labels(
        endpoint="/health",
        method="GET",
        status="200"
    ).inc()

    return {"status": "healthy"}


async def call_dependency(client, name, url):
    """Call one dependency. Returns (status_for_order, body)."""
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        DEPENDENCY_FAILURES.labels(dependency=name, kind="timeout").inc()
        return 504, {"error": f"{name} timed out"}
    except httpx.HTTPError:
        DEPENDENCY_FAILURES.labels(dependency=name, kind="unreachable").inc()
        return 503, {"error": f"{name} unreachable"}

    if response.status_code >= 500:
        DEPENDENCY_FAILURES.labels(dependency=name, kind="error").inc()
        return 503, {
            "error": f"{name} returned an error",
            "dependency_status": response.status_code
        }

    return 200, response.json()


@app.get("/orders")
async def place_order():
    endpoint = "/orders"
    start = time.perf_counter()

    async with httpx.AsyncClient(timeout=3.0) as client:
        status, user_body = await call_dependency(
            client, "user-service", f"{USER_SERVICE_URL}/users/1"
        )

        if status == 200:
            status, payment_body = await call_dependency(
                client, "payment-service", f"{PAYMENT_SERVICE_URL}/pay/1"
            )
            body = (
                {"order": "accepted", "user": user_body, "payment": payment_body}
                if status == 200 else payment_body
            )
        else:
            body = user_body

    ORDER_LATENCY.labels(endpoint=endpoint).observe(
        time.perf_counter() - start
    )
    REQUEST_COUNT.labels(
        endpoint=endpoint,
        method="GET",
        status=str(status)
    ).inc()

    return JSONResponse(status_code=status, content=body)


@app.get("/check-user-service")
async def check_user_service():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"{USER_SERVICE_URL}/health"
            )
            response.raise_for_status()

            REQUEST_COUNT.labels(
                endpoint="/check-user-service",
                method="GET",
                status="200"
            ).inc()

            return {
                "order_service": "healthy",
                "user_service": response.json()
            }

    except httpx.HTTPError:
        REQUEST_COUNT.labels(
            endpoint="/check-user-service",
            method="GET",
            status="200"
        ).inc()

        return {
            "order_service": "running",
            "user_service": "unavailable"
        }


@app.get("/test-failed-dependency")
async def test_failed_dependency():
    endpoint = "/simulate-failure"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"{USER_SERVICE_URL}{endpoint}"
            )

            if response.status_code >= 500:
                incident = {
                    "incident_id": str(uuid4()),
                    "detected_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "incident_detected": True,
                    "service": "user-service",
                    "endpoint": endpoint,
                    "http_status": response.status_code,
                    "message": "Dependency failure detected"
                }

                save_incident(incident)

                REQUEST_COUNT.labels(
                    endpoint="/test-failed-dependency",
                    method="GET",
                    status="200"
                ).inc()

                return incident

            REQUEST_COUNT.labels(
                endpoint="/test-failed-dependency",
                method="GET",
                status=str(response.status_code)
            ).inc()

            return {
                "incident_detected": False,
                "http_status": response.status_code
            }

    except httpx.HTTPError as error:
        incident = {
            "incident_id": str(uuid4()),
            "detected_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "incident_detected": True,
            "service": "user-service",
            "endpoint": endpoint,
            "message": "Dependency request failed",
            "details": str(error)
        }

        save_incident(incident)

        REQUEST_COUNT.labels(
            endpoint="/test-failed-dependency",
            method="GET",
            status="200"
        ).inc()

        return incident


@app.get("/incidents")
def list_incidents():
    incidents = get_incidents()

    for item in incidents:
        if item.get("evidence_json"):
            item["evidence"] = json.loads(item["evidence_json"])

    REQUEST_COUNT.labels(
        endpoint="/incidents",
        method="GET",
        status="200"
    ).inc()

    return {
        "total_incidents": len(incidents),
        "incidents": incidents
    }


class IncidentReport(BaseModel):
    service: str
    message: str
    fingerprint: str
    endpoint: Optional[str] = None
    http_status: Optional[int] = None
    evidence: Optional[dict] = None


class ResolveRequest(BaseModel):
    fingerprint: str


@app.post("/incidents")
def report_incident(report: IncidentReport):
    # If this problem already has an open incident, don't create another.
    existing = find_open_incident(report.fingerprint)
    if existing:
        return {"created": False, "incident": existing}

    incident = {
        "incident_id": str(uuid4()),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "service": report.service,
        "endpoint": report.endpoint,
        "http_status": report.http_status,
        "message": report.message,
        "fingerprint": report.fingerprint
    }
    save_incident(incident)

    if report.evidence:
        save_evidence(incident["incident_id"], report.evidence)

    return {"created": True, "incident": incident}


@app.post("/incidents/resolve")
def resolve_incident(request: ResolveRequest):
    existing = find_open_incident(request.fingerprint)
    if not existing:
        return {"resolved": False}

    update_incident_status(
        existing["incident_id"],
        "resolved",
        datetime.now(timezone.utc).isoformat()
    )

    return {"resolved": True, "incident_id": existing["incident_id"]}


class DeploymentRecord(BaseModel):
    service: str
    version: str
    previous_version: Optional[str] = None
    deployed_by: Optional[str] = "unknown"


@app.post("/deployments")
def record_deployment(record: DeploymentRecord):
    deployment = {
        "deployment_id": str(uuid4()),
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "service": record.service,
        "version": record.version,
        "previous_version": record.previous_version,
        "deployed_by": record.deployed_by
    }
    save_deployment(deployment)

    return deployment


@app.get("/deployments")
def list_deployments():
    deployments = get_deployments()

    return {
        "total_deployments": len(deployments),
        "deployments": deployments
    }

class AuditEntry(BaseModel):
    incident_id: Optional[str] = None
    mode: str
    decision: str
    action: Optional[str] = None
    service: Optional[str] = None
    target_version: Optional[str] = None
    reason: Optional[str] = None
    outcome: Optional[str] = None


@app.post("/audit")
def record_audit(entry: AuditEntry):
    record = {
        "audit_id": str(uuid4()),
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "incident_id": entry.incident_id,
        "mode": entry.mode,
        "decision": entry.decision,
        "action": entry.action,
        "service": entry.service,
        "target_version": entry.target_version,
        "reason": entry.reason,
        "outcome": entry.outcome
    }
    save_audit(record)

    return record


@app.get("/audit")
def list_audit(incident_id: Optional[str] = None, limit: int = 100):
    entries = get_audit(incident_id, limit)

    return {
        "total_entries": len(entries),
        "entries": entries
    }


@app.get("/investigate/{incident_id}")
def investigate_saved_incident(incident_id: str):
    incidents = get_incidents()

    for incident in incidents:
        if incident["incident_id"] == incident_id:
            REQUEST_COUNT.labels(
                endpoint="/investigate/{incident_id}",
                method="GET",
                status="200"
            ).inc()

            return investigate_incident(incident)

    REQUEST_COUNT.labels(
        endpoint="/investigate/{incident_id}",
        method="GET",
        status="404"
    ).inc()

    return {
        "error": "Incident not found",
        "incident_id": incident_id
    }


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )