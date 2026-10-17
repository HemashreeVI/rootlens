import asyncio
import os
import random

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI(title="RootLens User Service")

SERVICE_NAME = "user-service"
VERSION = os.getenv("SERVICE_VERSION", "v1")

# Planted bugs: what each release does wrong. v1 is healthy.
BUGS = {
    "v2": {"latency_seconds": 4.0},
}

REQUEST_COUNT = Counter(
    "rootlens_user_requests_total",
    "Total number of requests handled by User Service",
    ["endpoint", "method", "status"]
)

# Current failure-injection settings (the "failure lab").
chaos = {
    "error_rate": 0.0,
    "latency_seconds": 0.0,
}


@app.get("/")
def home():
    REQUEST_COUNT.labels(endpoint="/", method="GET", status="200").inc()
    return {"message": "Hello from RootLens User Service"}


@app.get("/health")
def health():
    REQUEST_COUNT.labels(endpoint="/health", method="GET", status="200").inc()
    return {"status": "healthy"}


@app.get("/version")
def version():
    return {"service": SERVICE_NAME, "version": VERSION}


@app.get("/users/{user_id}")
async def get_user(user_id: int):
    bug = BUGS.get(VERSION, {})
    latency = max(chaos["latency_seconds"], bug.get("latency_seconds", 0.0))
    error_rate = max(chaos["error_rate"], bug.get("error_rate", 0.0))

    if latency > 0:
        await asyncio.sleep(latency)

    if random.random() < error_rate:
        REQUEST_COUNT.labels(
            endpoint="/users/{user_id}", method="GET", status="503"
        ).inc()
        return JSONResponse(
            status_code=503,
            content={"error": "Injected failure"}
        )

    REQUEST_COUNT.labels(
        endpoint="/users/{user_id}", method="GET", status="200"
    ).inc()
    return {"user_id": user_id, "name": f"User {user_id}"}


@app.get("/simulate-failure")
def simulate_failure():
    REQUEST_COUNT.labels(
        endpoint="/simulate-failure", method="GET", status="503"
    ).inc()
    return JSONResponse(
        status_code=503,
        content={"error": "Simulated failure"}
    )


# ---- Failure lab controls (not counted in request metrics) ----

@app.post("/chaos/errors")
def set_error_rate(rate: float = Query(..., ge=0, le=1)):
    chaos["error_rate"] = rate
    return chaos


@app.post("/chaos/latency")
def set_latency(seconds: float = Query(..., ge=0, le=30)):
    chaos["latency_seconds"] = seconds
    return chaos


@app.post("/chaos/reset")
def reset_chaos():
    chaos["error_rate"] = 0.0
    chaos["latency_seconds"] = 0.0
    return chaos


@app.get("/chaos/status")
def chaos_status():
    return chaos


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )