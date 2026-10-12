
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

app = FastAPI(title="RootLens User Service")

# Count the requests handled by this service.
REQUEST_COUNT = Counter(
    "rootlens_user_requests_total",
    "Total number of requests handled by User Service",
    ["endpoint", "method", "status"]
)


@app.get("/")
def home():
    REQUEST_COUNT.labels(
        endpoint="/",
        method="GET",
        status="200"
    ).inc()

    return {"message": "Hello from RootLens User Service"}


@app.get("/health")
def health():
    REQUEST_COUNT.labels(
        endpoint="/health",
        method="GET",
        status="200"
    ).inc()

    return {"status": "healthy"}


@app.get("/simulate-failure")
def simulate_failure():
    REQUEST_COUNT.labels(
        endpoint="/simulate-failure",
        method="GET",
        status="503"
    ).inc()

    raise HTTPException(
        status_code=503,
        detail="Simulated User Service failure"
    )


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
