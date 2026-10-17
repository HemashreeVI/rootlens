import asyncio
import random

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI(title="RootLens User Service")

REQUEST_COUNT = Counter(
    "rootlens_user_requests_total",
    "Total number of requests handled by User Service",
    ["endpoint", "method", "status"]
)

# Current failure-injection settings (the "failure lab").
chaos = {
    "error_rate": 0.0,       # fraction of /users requests that fail (0 to 1)
    "latency_seconds": 0.0,  # delay added to every /users request
}


@app.get("/")
def home():
    REQUEST_COUNT.labels(endpoint="/", method="GET", status="200").inc()
    return {"message": "Hello from RootLens User Service"}


@app.get("/health")
def health():
    # Stays healthy during injected failures, like many real services
    # whose health check passes while real requests are failing.
    REQUEST_COUNT.labels(endpoint="/health", method="GET", status="200").inc()
    return {"status": "healthy"}


@app.get("/users/{user_id}")
async def get_user(user_id: int):
    if chaos["latency_seconds"] > 0:
        await asyncio.sleep(chaos["latency_seconds"])

    if random.random() < chaos["error_rate"]:
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