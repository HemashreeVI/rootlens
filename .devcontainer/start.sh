#!/usr/bin/env bash
# Starts the RootLens lab in a Codespace / dev container, then sends fake customer traffic.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "▶ Building and starting the RootLens lab (8 containers)…"
docker compose up -d --build

for i in $(seq 1 60); do
  curl -fsS http://localhost:8002/health >/dev/null 2>&1 && break
  sleep 3
done

# fake customers (about 5 requests per second), in the background
nohup python3 traffic.py > /tmp/traffic.log 2>&1 &

cat <<'MSG'
✅ RootLens is running. Open the Ports tab:
   8002 → /dashboard (live incidents) · 3000 Grafana · 16686 Jaeger · 9090 Prometheus

Try it:
   curl -X POST "http://localhost:8003/chaos/errors?rate=0.5"   # break payments → incident in ~30 s
   curl -X POST "http://localhost:8003/chaos/reset"             # heal it
   ./deploy.sh payment-service v2                               # ship a bad release
   ./deploy.sh payment-service v1                               # roll it back
MSG
