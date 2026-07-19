#!/usr/bin/env bash
# Start E2E compose set (docker-compose.e2e.yml) under project piherder.
# Same folder / project as main — not a separate stack card.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PIHERDER_E2E_HOST_PORT:-18000}"
export PIHERDER_E2E_HOST_PORT="$PORT"

echo "Starting e2e compose set (web on host port ${PORT}, project piherder)…"
docker compose \
  -p piherder \
  -f docker-compose.e2e.yml \
  up -d --build

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

echo "ERROR: e2e-web did not become healthy in time" >&2
docker compose -p piherder -f docker-compose.e2e.yml logs --tail=80 e2e-web || true
exit 1
