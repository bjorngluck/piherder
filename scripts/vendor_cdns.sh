#!/usr/bin/env bash
#
# Vendor external CDNs into app/static/ for fully offline / air-gapped deployments.
#
# - For local development: just run the script (the app has a good CSS fallback).
# - For `docker build`: this step must succeed for Tailwind Play.
#   The Dockerfile will fail hard if tailwind.js is missing/invalid after this script.
#
# This protects open-source users who build the image themselves.
#
# Run this manually for local development:
#   bash scripts/vendor_cdns.sh
#
# The Dockerfile runs it automatically during image build.
#
set -euo pipefail

STATIC_DIR="app/static"
mkdir -p "$STATIC_DIR"

echo "==> Vendoring frontend CDNs for offline use..."

download() {
  local name="$1"
  local url="$2"
  local dest="$3"

  echo "  - $name"

  local curl_opts="-fL --retry 3 --retry-delay 2 --max-time 30"

  # Allow forcing insecure mode (useful for corporate proxies, bad CAs, etc.)
  if [[ "${VENDOR_INSECURE:-}" == "1" || "${VENDOR_CDN_INSECURE:-}" == "1" ]]; then
    curl_opts="$curl_opts -k"
    echo "    (using --insecure because VENDOR_INSECURE=1)"
  fi

  if curl $curl_opts -o "$dest" "$url" 2>/dev/null; then
    echo "    ✓ downloaded"
    return 0
  fi

  # If we didn't already try insecure, retry with -k as a last resort for cert problems
  if [[ "$curl_opts" != *"-k"* ]]; then
    echo "    ! Normal download failed. Retrying once with --insecure (common with corporate proxies / SSL inspection)..."
    if curl -fL -k --retry 2 --max-time 30 -o "$dest" "$url" 2>/dev/null; then
      echo "    ✓ downloaded (with --insecure)"
      echo "    WARNING: Used --insecure. The file was retrieved without full certificate validation."
      return 0
    fi
  fi

  echo "    ⚠️  FAILED to download $name. Removing partial file."
  rm -f "$dest"
  return 0   # do not fail the whole build
}

# Tailwind Play CDN (provides JIT + allows the inline tailwind.config in base.html)
download "Tailwind Play" \
  "https://cdn.tailwindcss.com" \
  "$STATIC_DIR/tailwind.js"

# HTMX (exact version the templates were written against)
download "HTMX 1.9.12" \
  "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js" \
  "$STATIC_DIR/htmx.min.js"

# Alpine.js v3 (pinned to a known stable release)
download "Alpine.js 3.13.5" \
  "https://unpkg.com/alpinejs@3.13.5/dist/cdn.min.js" \
  "$STATIC_DIR/alpine.min.js"

echo ""
echo "==> Current vendored files:"
ls -lh "$STATIC_DIR"/*.js 2>/dev/null || echo "  (none — will rely on fallback CSS)"

# Post-download validation (especially important for Tailwind Play)
if [[ -f "$STATIC_DIR/tailwind.js" ]]; then
  size=$(stat -c%s "$STATIC_DIR/tailwind.js" 2>/dev/null || stat -f%z "$STATIC_DIR/tailwind.js" 2>/dev/null || echo 0)
  is_bad=false

  if [[ $size -lt 100000 ]]; then
    echo "    ⚠️  tailwind.js looks too small ($size bytes). Probably blocked or incomplete."
    is_bad=true
  fi

  if grep -qiE "pi-hole|blocked|this file is copyright|ad block" "$STATIC_DIR/tailwind.js" 2>/dev/null; then
    echo "    ⚠️  tailwind.js was blocked by Pi-hole (or similar)!"
    echo "       Whitelist 'cdn.tailwindcss.com' in Pi-hole, then re-run this script."
    is_bad=true
  fi

  if ! grep -q "tailwind" "$STATIC_DIR/tailwind.js" 2>/dev/null; then
    echo "    ⚠️  tailwind.js does not appear to be the real Play CDN."
    is_bad=true
  fi

  if $is_bad; then
    rm -f "$STATIC_DIR/tailwind.js"
  else
    echo "    ✓ tailwind.js looks valid ($size bytes)"
  fi
fi

echo ""
echo "Done."

if [[ ! -f "$STATIC_DIR/tailwind.js" ]]; then
  echo ""
  echo "Tailwind Play is missing."
  echo ""
  echo "For local development you can still run the app (strong CSS fallback exists)."
  echo ""
  echo "However, 'docker build' will now FAIL HARD if tailwind.js is missing."
  echo "This is deliberate for open-source consumers who build the image themselves."
  echo ""
  echo "Common fixes:"
  echo "  1. Whitelist 'cdn.tailwindcss.com' in Pi-hole (most common in this project)"
  echo "  2. VENDOR_INSECURE=1 bash scripts/vendor_cdns.sh"
  echo "  3. curl -kL -o app/static/tailwind.js https://cdn.tailwindcss.com"
fi

echo "For full JS features, re-run this script (or rebuild the image) with internet access."
