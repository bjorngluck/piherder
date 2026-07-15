#!/usr/bin/env bash
# Refresh uv.lock and exported pip requirement pins for Docker/CI.
# Requires: uv (https://docs.astral.sh/uv/) and network access to PyPI.
#
# Usage (from repo root):
#   ./scripts/refresh-lockfiles.sh
#   # or: bash scripts/refresh-lockfiles.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not found on PATH. Install: https://docs.astral.sh/uv/getting-started/installation/" >&2
  echo "hint: docker run --rm -v \"\$PWD\":/app -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv lock" >&2
  exit 1
fi

echo "==> uv lock (resolve pyproject.toml → uv.lock)"
uv lock

echo "==> export requirements.lock.txt (runtime + [dev], with hashes)"
uv export --frozen --extra dev --no-emit-project -o requirements.lock.txt

echo "==> export requirements.runtime.lock.txt (runtime only, with hashes)"
uv export --frozen --no-emit-project -o requirements.runtime.lock.txt

echo "==> done"
echo "    uv.lock  requirements.lock.txt  requirements.runtime.lock.txt"
echo "Commit all three after intentional dependency bumps. Rebuild images to pick up pins."
