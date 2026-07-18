#!/usr/bin/env bash
# Start isolated web+db+redis for Playwright E2E (project piherder-e2e).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PIHERDER_E2E_HOST_PORT:-18000}"
export PIHERDER_E2E_HOST_PORT="$PORT"

echo "Starting piherder-e2e (web on host port ${PORT})…"
docker compose \
  -f docker-compose.yml \
  -f docker-compose.e2e.yml \
  -p piherder-e2e \
  up -d --build db redis web

echo "Waiting for /health…"
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
    echo "Ready: http://127.0.0.1:${PORT}"
    echo "  export PIHERDER_E2E_BASE_URL=http://127.0.0.1:${PORT}"
    echo "  pytest e2e -q"
    exit 0
  fi
  sleep 2
done

echo "ERROR: web did not become healthy in time" >&2
docker compose -p piherder-e2e -f docker-compose.yml -f docker-compose.e2e.yml logs --tail=80 web || true
exit 1
