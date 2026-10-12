
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
import os
import httpx
from datetime import datetime, timezone
from uuid import uuid4

from database import (
    initialize_database,
    save_incident,
    get_incidents
)
from investigator import investigate_incident

app = FastAPI(title="RootLens Order Service")

# Count requests handled by Order Service.
REQUEST_COUNT = Counter(
    "rootlens_order_requests_total",
    "Total number of requests handled by Order Service",
    ["endpoint", "method", "status"]
)

initialize_database()

USER_SERVICE_URL = os.getenv(
    "USER_SERVICE_URL",
    "http://localhost:8001"
)


@app.get("/")
def home():
    REQUEST_COUNT.labels(
        endpoint="/",
        method="GET",
        status="200"
    ).inc()

    return {"message": "Hello from RootLens Order Service"}


@app.get("/health")
def health():
    REQUEST_COUNT.labels(
        endpoint="/health",
        method="GET",
        status="200"
    ).inc()

    return {"status": "healthy"}


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

    REQUEST_COUNT.labels(
        endpoint="/incidents",
        method="GET",
        status="200"
    ).inc()

    return {
        "total_incidents": len(incidents),
        "incidents": incidents
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
