#!/usr/bin/env bash
#
# Compile Tailwind utilities → app/static/css/tailwind.css
#
# Requires Docker (node image) or a local `npx` + tailwindcss@3.
# The compiled file is committed so `docker compose build` stays air-gapped
# and does not need Tailwind Play or Node at image-build time.
#
# Re-run after adding new utility class names in templates / static JS:
#   bash scripts/build-tailwind.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SRC="app/static/css/tailwind-src.css"
OUT="app/static/css/tailwind.css"
CFG="tailwind.config.js"

if [[ ! -f "$SRC" || ! -f "$CFG" ]]; then
  echo "ERROR: missing $SRC or $CFG" >&2
  exit 1
fi

run_local() {
  npx --yes tailwindcss@3.4.17 -c "$CFG" -i "$SRC" -o "$OUT" --minify
}

run_docker() {
  docker run --rm \
    -v "$ROOT:/work" -w /work \
    node:22-bookworm-slim \
    bash -lc 'npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i app/static/css/tailwind-src.css -o app/static/css/tailwind.css --minify'
}

echo "==> Compiling Tailwind utilities → $OUT"
if command -v npx >/dev/null 2>&1; then
  run_local
elif command -v docker >/dev/null 2>&1; then
  echo "    (no local npx — using node:22-bookworm-slim)"
  run_docker
else
  echo "ERROR: need npx or docker to compile Tailwind." >&2
  exit 1
fi

size=$(wc -c < "$OUT" | tr -d ' ')
if [[ "${size:-0}" -lt 5000 ]]; then
  echo "ERROR: $OUT looks too small ($size bytes)" >&2
  exit 1
fi

echo "    ✓ wrote $OUT ($size bytes)"
echo "Commit this file with the template change that needed new utilities."
