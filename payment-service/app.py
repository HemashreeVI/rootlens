import asyncio
import random

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI(title="RootLens Payment Service")

REQUEST_COUNT = Counter(
    "rootlens_payment_requests_total",
    "Total number of requests handled by Payment Service",
    ["endpoint", "method", "status"]
)

chaos = {
    "error_rate": 0.0,
    "latency_seconds": 0.0,
}


@app.get("/")
def home():
    REQUEST_COUNT.labels(endpoint="/", method="GET", status="200").inc()
    return {"message": "Hello from RootLens Payment Service"}


@app.get("/health")
def health():
    REQUEST_COUNT.labels(endpoint="/health", method="GET", status="200").inc()
    return {"status": "healthy"}


@app.get("/pay/{order_id}")
async def pay(order_id: int):
    if chaos["latency_seconds"] > 0:
        await asyncio.sleep(chaos["latency_seconds"])

    if random.random() < chaos["error_rate"]:
        REQUEST_COUNT.labels(
            endpoint="/pay/{order_id}", method="GET", status="503"
        ).inc()
        return JSONResponse(
            status_code=503,
            content={"error": "Injected failure"}
        )

    REQUEST_COUNT.labels(
        endpoint="/pay/{order_id}", method="GET", status="200"
    ).inc()
    return {"order_id": order_id, "payment": "approved"}


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