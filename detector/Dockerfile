FROM python:3.12-slim

WORKDIR /app

COPY detector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared with the other RootLens parts: what app to watch (services.yaml).
COPY common/topology.py services.yaml ./
COPY detector/detector.py .

CMD ["python", "-u", "detector.py"]