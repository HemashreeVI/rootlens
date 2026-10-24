FROM python:3.12-slim

WORKDIR /app

COPY order-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared with the other RootLens parts: what app to watch (services.yaml).
COPY common/topology.py services.yaml ./
COPY order-service/app.py order-service/database.py order-service/investigator.py order-service/dashboard.py ./

# opentelemetry-instrument starts the app with tracing switched on, so every
# request is recorded and sent to Jaeger. No code changes are needed.
CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
