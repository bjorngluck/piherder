#!/usr/bin/env bash
# Stop and remove the isolated E2E compose project (volumes kept by default).
# Pass --volumes to wipe e2e DB/data (fresh seed on next up).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EXTRA=()
if [[ "${1:-}" == "--volumes" || "${1:-}" == "-v" ]]; then
  EXTRA+=(--volumes)
  echo "Removing e2e volumes (fresh DB next up)…"
fi

docker compose \
  -f docker-compose.yml \
  -f docker-compose.e2e.yml \
  -p piherder-e2e \
  down "${EXTRA[@]}"
