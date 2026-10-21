FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY database.py .
COPY investigator.py .
COPY dashboard.py .

# opentelemetry-instrument starts the app with tracing switched on, so every
# request is recorded and sent to Jaeger. No code changes are needed.
CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
