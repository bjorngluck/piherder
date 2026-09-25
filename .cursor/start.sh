#!/usr/bin/env bash
# PiHerder — Cloud Agent per-boot start. Brings up PostgreSQL + Redis and
# ensures the app role/database exist. Idempotent and safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Starting PostgreSQL and Redis…"
sudo service postgresql start || true
sudo service redis-server start || true

echo "==> Waiting for PostgreSQL to accept connections…"
for _ in $(seq 1 30); do
  if pg_isready -h 127.0.0.1 -q; then break; fi
  sleep 1
done
pg_isready -h 127.0.0.1 -q || { echo "PostgreSQL did not become ready" >&2; exit 1; }

echo "==> Ensuring 'piherder' role and database…"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='piherder'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE piherder LOGIN PASSWORD 'piherder';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='piherder'" | grep -q 1 \
  || sudo -u postgres createdb -O piherder piherder

echo "==> Waiting for Redis…"
for _ in $(seq 1 30); do
  if redis-cli ping >/dev/null 2>&1; then break; fi
  sleep 1
done
redis-cli ping >/dev/null 2>&1 || { echo "Redis did not become ready" >&2; exit 1; }

echo "==> start complete (PostgreSQL + Redis up)."
