#!/usr/bin/env bash
# Run the stock Caddyfile and check that client-supplied IP headers do not
# reach the upstream. CI does not run this. Docs: docs/QA_v1.15.0.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CADDY_IMAGE="${CADDY_IMAGE:-caddy:2@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e}"
NET="ph-caddy-spoof"
WEB="ph-caddy-spoof-web"
PROXY="ph-caddy-spoof-caddy"
CLIENT="ph-caddy-spoof-client"
WORKDIR="$(mktemp -d)"
FAKE_CF="203.0.113.50"
FAKE_TRUE="203.0.113.51"
FAKE_REAL="198.51.100.20"
FAKE_XFF="198.51.100.21"

cleanup() {
  docker rm -f "$CLIENT" "$PROXY" "$WEB" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not available"
  exit 2
fi

mkdir -p "$WORKDIR/certs"
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$WORKDIR/certs/privkey.pem" \
  -out "$WORKDIR/certs/fullchain.pem" \
  -days 1 \
  -subj "/CN=piherder.example.com" >/dev/null 2>&1

cat >"$WORKDIR/stub.py" <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({k: v for k, v in self.headers.items()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _fmt, *_args):
        return

HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
PY

docker network create "$NET" >/dev/null
docker run -d --name "$WEB" --network "$NET" --network-alias web \
  -v "$WORKDIR/stub.py:/stub.py:ro" \
  python:3.12-alpine python /stub.py >/dev/null

docker run -d --name "$PROXY" --network "$NET" \
  -v "$ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v "$WORKDIR/certs:/certs:ro" \
  -e PIHERDER_HOSTNAME=piherder.example.com \
  -e PIHERDER_PUBLIC_URL=https://piherder.example.com \
  "$CADDY_IMAGE" >/dev/null

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if docker logs "$PROXY" 2>&1 | grep -q "server running"; then
    ready=1
    break
  fi
  sleep 0.5
done
if [ "$ready" != 1 ]; then
  docker logs "$PROXY" 2>&1 | tail -40
  echo "caddy did not start"
  exit 1
fi

PROXY_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$PROXY")"
docker run -d --name "$CLIENT" --network "$NET" curlimages/curl:8.10.1 sleep 30 >/dev/null
CLIENT_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CLIENT")"
docker exec "$CLIENT" curl \
  --silent --show-error --insecure \
  --resolve "piherder.example.com:443:${PROXY_IP}" \
  -H "Host: piherder.example.com" \
  -H "CF-Connecting-IP: ${FAKE_CF}" \
  -H "True-Client-IP: ${FAKE_TRUE}" \
  -H "X-Real-IP: ${FAKE_REAL}" \
  -H "X-Forwarded-For: ${FAKE_XFF}" \
  "https://piherder.example.com/" >"$WORKDIR/upstream.json"
python3 - "$WORKDIR/upstream.json" "$CLIENT_IP" "$FAKE_CF" "$FAKE_TRUE" "$FAKE_REAL" "$FAKE_XFF" <<'PY'
import json
import sys

path, client_ip, fake_cf, fake_true, fake_real, fake_xff = sys.argv[1:]
raw = json.load(open(path, encoding="utf-8"))
headers = {str(k).lower(): str(v) for k, v in raw.items()}
print("upstream", json.dumps(headers, sort_keys=True))
print("client_ip", client_ip)
spoofed = {fake_cf, fake_true, fake_real, fake_xff}
for name in ("cf-connecting-ip", "true-client-ip"):
    if headers.get(name):
        raise SystemExit(f"{name} reached upstream: {headers[name]}")
for name in ("x-real-ip", "x-forwarded-for"):
    value = headers.get(name, "")
    if value != client_ip:
        raise SystemExit(f"{name}={value!r}, want {client_ip}")
    if value in spoofed:
        raise SystemExit(f"{name} kept a client-supplied address")
print("spoof check ok")
PY
