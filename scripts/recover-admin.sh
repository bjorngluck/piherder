#!/usr/bin/env bash
# Host wrapper: run sole-admin credential recovery inside the web container.
#
# Usage (from compose project root):
#   ./scripts/recover-admin.sh list
#   ./scripts/recover-admin.sh reset-access --email you@example.com --generate --yes
#   ./scripts/recover-admin.sh clear-2fa --email you@example.com --yes
#
# Requires: docker compose, running `web` service (same DATABASE_URL as the app).
# Docs: wiki/troubleshooting/locked-out.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx 'web'; then
  echo "ERROR: service 'web' is not running. Start the stack first:" >&2
  echo "  docker compose up -d web" >&2
  exit 1
fi

exec docker compose exec -T web python -m app.cli.recover_admin "$@"
