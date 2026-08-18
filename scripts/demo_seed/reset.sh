#!/usr/bin/env bash
# Thin wrapper → scripts/demo-maintain.sh (preferred for VPS cron).
#   ./scripts/demo_seed/reset.sh          # data-side force re-seed
#   ./scripts/demo_seed/reset.sh --wipe   # volume wipe + redeploy + seed
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MAINT="$ROOT/scripts/demo-maintain.sh"

if [[ ! -x "$MAINT" ]]; then
  echo "missing $MAINT" >&2
  exit 1
fi

case "${1:-}" in
  --wipe)
    exec "$MAINT" redeploy --wipe
    ;;
  -h|--help)
    cat <<EOF
Usage: $(basename "$0") [--wipe]

  (default)  force re-seed (keeps volumes) — same as demo-maintain.sh data-reset
  --wipe     compose down -v + up + seed — same as demo-maintain.sh redeploy --wipe

VPS schedule / logging: docs/DEMO_SITE.md § Cron · scripts/cron.d/piherder-demo.example
EOF
    exit 0
    ;;
  "")
    exec "$MAINT" data-reset
    ;;
  *)
    echo "Unknown arg: $1" >&2
    exit 2
    ;;
esac
