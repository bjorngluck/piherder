#!/usr/bin/env bash
# Hard demo reset: optional compose volume wipe + re-seed.
# Prefer in-container re-seed for day-to-day; use --wipe for empty volumes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

WIPE=0
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.demo.yml)

usage() {
  cat <<EOF
Usage: $(basename "$0") [--wipe]

  --wipe   docker compose down -v then up (destroys Postgres volume)
  default  re-seed in running web container (force)

Requires demo overlay (PIHERDER_DEMO_MODE) and a running web service for non-wipe.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --wipe) WIPE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage; exit 1 ;;
  esac
done

if [[ "$WIPE" -eq 1 ]]; then
  echo "==> Wipe volumes and restart demo stack"
  "${COMPOSE[@]}" down -v
  "${COMPOSE[@]}" up -d
  echo "==> Waiting for web health..."
  for i in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T web curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  # Lifespan auto-seeds when empty; force anyway for password refresh
  "${COMPOSE[@]}" exec -T web python scripts/demo_seed/seed.py --force || \
    "${COMPOSE[@]}" exec -T web python -m scripts.demo_seed.seed --force || true
  echo "==> Done (wipe + seed)"
  exit 0
fi

echo "==> Force re-seed in web container"
if docker compose ps --status running 2>/dev/null | grep -q piherder-web; then
  docker compose exec -T web python scripts/demo_seed/seed.py --force
else
  echo "web not running — starting with demo overlay..."
  "${COMPOSE[@]}" up -d
  sleep 5
  "${COMPOSE[@]}" exec -T web python scripts/demo_seed/seed.py --force
fi
echo "==> Done"
