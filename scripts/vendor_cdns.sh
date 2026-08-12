#!/usr/bin/env bash
#
# Vendor HTMX / Alpine / xterm into app/static/ for offline / air-gapped images.
#
# Tailwind is **compiled** (scripts/build-tailwind.sh → app/static/css/tailwind.css)
# and committed. This script does **not** download Tailwind Play.
#
#   bash scripts/vendor_cdns.sh
#   docker build runs this automatically
#
set -euo pipefail

STATIC_DIR="app/static"
mkdir -p "$STATIC_DIR"

echo "==> Vendoring frontend JS for offline use..."

download() {
  local name="$1"
  local url="$2"
  local dest="$3"

  echo "  - $name"

  local curl_opts="-fL --retry 3 --retry-delay 2 --max-time 30"

  if [[ "${VENDOR_INSECURE:-}" == "1" || "${VENDOR_CDN_INSECURE:-}" == "1" ]]; then
    curl_opts="$curl_opts -k"
    echo "    (using --insecure because VENDOR_INSECURE=1)"
  fi

  if curl $curl_opts -o "$dest" "$url" 2>/dev/null; then
    echo "    ✓ downloaded"
    return 0
  fi

  if [[ "$curl_opts" != *"-k"* ]]; then
    echo "    ! Normal download failed. Retrying once with --insecure..."
    if curl -fL -k --retry 2 --max-time 30 -o "$dest" "$url" 2>/dev/null; then
      echo "    ✓ downloaded (with --insecure)"
      echo "    WARNING: Used --insecure. The file was retrieved without full certificate validation."
      return 0
    fi
  fi

  echo "    ⚠️  FAILED to download $name. Removing partial file."
  rm -f "$dest"
  return 0
}

# HTMX (exact version the templates were written against)
download "HTMX 1.9.12" \
  "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js" \
  "$STATIC_DIR/htmx.min.js"

# Alpine.js v3 (pinned to a known stable release)
download "Alpine.js 3.13.5" \
  "https://unpkg.com/alpinejs@3.13.5/dist/cdn.min.js" \
  "$STATIC_DIR/alpine.min.js"

# xterm.js (web SSH console — must stay same-origin for CSP)
mkdir -p "$STATIC_DIR/vendor/xterm"
download "xterm.js 5.5.0" \
  "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js" \
  "$STATIC_DIR/vendor/xterm/xterm.min.js"
download "xterm.css 5.5.0" \
  "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css" \
  "$STATIC_DIR/vendor/xterm/xterm.min.css"
download "xterm addon-fit 0.10.0" \
  "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js" \
  "$STATIC_DIR/vendor/xterm/addon-fit.min.js"

echo ""
echo "==> Current vendored files:"
ls -lh "$STATIC_DIR"/*.js 2>/dev/null || echo "  (none — HTMX/Alpine missing)"
ls -lh "$STATIC_DIR/vendor/xterm/" 2>/dev/null || true

if [[ ! -f "$STATIC_DIR/css/tailwind.css" ]]; then
  echo ""
  echo "WARNING: app/static/css/tailwind.css is missing."
  echo "Compile it with: bash scripts/build-tailwind.sh"
  echo "docker build will fail without that committed file."
fi

echo ""
echo "Done."
