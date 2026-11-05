# RootLens hosted demo: the whole lab in one container (see demo/run.py).
# The normal setup is still `docker compose up`; this image is only for a free web host.
FROM python:3.12-slim

ARG PROMETHEUS_VERSION=3.5.0
ADD https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz /tmp/prometheus.tar.gz
RUN tar -xzf /tmp/prometheus.tar.gz -C /opt \
 && mv /opt/prometheus-${PROMETHEUS_VERSION}.linux-amd64/prometheus /usr/local/bin/prometheus \
 && rm -rf /tmp/prometheus.tar.gz /opt/prometheus-*

RUN pip install --no-cache-dir fastapi uvicorn httpx prometheus-client

WORKDIR /app
COPY user-service/app.py user-service/
COPY payment-service/app.py payment-service/
COPY order-service/*.py order-service/
COPY detector/detector.py detector/
COPY controller/*.py controller/
COPY traffic.py ./
COPY demo/ demo/

ENV PROMETHEUS_BIN=/usr/local/bin/prometheus PORT=10000
EXPOSE 10000
CMD ["python", "-u", "demo/run.py"]
