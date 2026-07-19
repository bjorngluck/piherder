#!/usr/bin/env bash
# Stop E2E compose set services (project piherder). Main stack left running.
# Pass --volumes to wipe e2e DB/data volumes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EXTRA=()
if [[ "${1:-}" == "--volumes" || "${1:-}" == "-v" ]]; then
  EXTRA+=(--volumes)
  echo "Removing e2e volumes (fresh DB next up)…"
fi

docker compose \
  -p piherder \
  -f docker-compose.e2e.yml \
  down "${EXTRA[@]}"
