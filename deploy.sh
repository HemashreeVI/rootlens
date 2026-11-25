#!/usr/bin/env bash
# Bash version of deploy.ps1 (macOS, Linux, Codespaces):  ./deploy.sh payment-service v2
# Updates .env, recreates only that service, waits for /version, then records the deployment.
set -euo pipefail
cd "$(dirname "$0")"

service="${1:-}"; version="${2:-}"
case "$service" in
  user-service)    port=8001; key=USER_SERVICE_VERSION ;;
  payment-service) port=8003; key=PAYMENT_SERVICE_VERSION ;;
  *) echo "usage: ./deploy.sh <user-service|payment-service> <vN>"; exit 1 ;;
esac
[[ "$version" =~ ^v[0-9]+$ ]] || { echo "version must look like v1, v2…"; exit 1; }

# 1. Which version is running now?
previous=$(curl -fsS "http://localhost:$port/version" 2>/dev/null \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('version','none'))" 2>/dev/null || echo none)
if [[ "$previous" == "$version" ]]; then
  echo "$service is already running $version. Nothing to do."; exit 0
fi
echo "Deploying $service $version (currently $previous)..."

# 2. Update the release manifest (.env). Docker Compose reads it automatically.
touch .env
grep -v "^$key=" .env > .env.tmp || true
echo "$key=$version" >> .env.tmp
mv .env.tmp .env

# 3. Release: recreate only this service's container.
docker compose up -d --no-deps --force-recreate "$service" || { echo "Deploy FAILED."; exit 1; }

# 4. Wait until the service reports the new version.
ready=false
for _ in $(seq 1 20); do
  sleep 1
  running=$(curl -fsS "http://localhost:$port/version" 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
  if [[ "$running" == "$version" ]]; then ready=true; break; fi
done
$ready || echo "WARNING: $service did not report $version within 20 seconds."

# 5. Record the deployment.
curl -fsS -X POST "http://localhost:8002/deployments" -H "Content-Type: application/json" \
  -d "{\"service\":\"$service\",\"version\":\"$version\",\"previous_version\":\"$previous\",\"deployed_by\":\"deploy.sh\"}" >/dev/null
echo "Done. $service $previous -> $version (recorded)."
