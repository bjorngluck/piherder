#!/usr/bin/env bash
# Optional helper: obtain a Let's Encrypt (ACME) certificate with Certbot in Docker,
# then print paths ready for PiHerder Catalog → Certificates → Upload PEM.
#
# This does NOT talk to the PiHerder API. It educates and produces PEMs on disk.
#
# Usage:
#   ./scripts/obtain-acme-cert.sh --email you@example.com -d example.com --staging
#   ./scripts/obtain-acme-cert.sh --email you@example.com -d example.com -d www.example.com
#   ./scripts/obtain-acme-cert.sh --email you@example.com -d example.com -d '*.example.com' --dns-manual --staging
#   ./scripts/obtain-acme-cert.sh --email you@example.com -d app.example.com --webroot /var/www/html --staging
#
# Docs: wiki/integrations/certificates-obtain-acme.md
#
# Requirements: docker (or podman with docker-compatible CLI), outbound HTTPS to Let's Encrypt.
set -euo pipefail

EMAIL=""
DOMAINS=()
STAGING=0
DNS_MANUAL=0
WEBROOT=""
OUT_DIR=""
CERTBOT_IMAGE="${CERTBOT_IMAGE:-docker.io/certbot/certbot:latest}"
AGREE_TOS=0

usage() {
  cat <<'EOF'
PiHerder optional helper: obtain a Let's Encrypt (ACME) certificate with Certbot
in Docker, then print PEM paths for Catalog → Certificates → Upload PEM.

Does not call the PiHerder API — education + PEMs on disk only.

Usage:
  ./scripts/obtain-acme-cert.sh --email you@example.com -d example.com --dns-manual --staging
  ./scripts/obtain-acme-cert.sh --email you@example.com -d example.com -d www.example.com --dns-manual
  ./scripts/obtain-acme-cert.sh --email you@example.com -d app.example.com --webroot /var/www/html --staging

Options:
  --email ADDR          Account email (required)
  -d, --domain NAME     Domain (repeatable; first is primary name for output folder)
  --staging             Use Let's Encrypt staging (recommended until the flow works)
  --dns-manual          DNS-01 manual challenge (you create TXT records)
  --webroot PATH        HTTP-01 webroot on the host (mounted into the container)
  --out DIR             Output directory for copied PEMs (default: ./acme-out/<primary>)
  --image IMAGE         Certbot image (default: certbot/certbot:latest)
  --agree-tos           Pass --agree-tos (also auto-set for webroot / after manual start)
  -h, --help            Show this help

Environment:
  CERTBOT_IMAGE         Override default image

Docs: wiki/integrations/certificates-obtain-acme.md
After success: upload fullchain.pem + privkey.pem in PiHerder.
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --email) EMAIL="${2:-}"; shift 2 ;;
    -d|--domain) DOMAINS+=("${2:-}"); shift 2 ;;
    --staging) STAGING=1; shift ;;
    --dns-manual) DNS_MANUAL=1; shift ;;
    --webroot) WEBROOT="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --image) CERTBOT_IMAGE="${2:-}"; shift 2 ;;
    --agree-tos) AGREE_TOS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

[[ -n "$EMAIL" ]] || die "--email is required"
[[ ${#DOMAINS[@]} -gt 0 ]] || die "at least one --domain / -d is required"
if [[ -n "$WEBROOT" && "$DNS_MANUAL" -eq 1 ]]; then
  die "use either --webroot or --dns-manual, not both"
fi
if [[ -z "$WEBROOT" && "$DNS_MANUAL" -eq 0 ]]; then
  die "choose a challenge: --dns-manual or --webroot PATH"
fi

if ! command -v docker >/dev/null 2>&1; then
  die "docker not found. Install Docker, or run Certbot another way — see wiki/integrations/certificates-obtain-acme.md"
fi

PRIMARY="${DOMAINS[0]//\*/_}"
PRIMARY="${PRIMARY//\//_}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${ROOT}/.acme-certbot-config"
WORK_DIR="${ROOT}/.acme-certbot-work"
LOGS_DIR="${ROOT}/.acme-certbot-logs"
OUT_DIR="${OUT_DIR:-${ROOT}/acme-out/${PRIMARY}}"

mkdir -p "$CONFIG_DIR" "$WORK_DIR" "$LOGS_DIR" "$OUT_DIR"

DOMAIN_ARGS=()
for d in "${DOMAINS[@]}"; do
  DOMAIN_ARGS+=(-d "$d")
done

CERTBOT_ARGS=(
  certonly
  --config-dir /etc/letsencrypt
  --work-dir /var/lib/letsencrypt
  --logs-dir /var/log/letsencrypt
  --email "$EMAIL"
  --non-interactive
)
[[ "$AGREE_TOS" -eq 1 ]] && CERTBOT_ARGS+=(--agree-tos)
[[ "$STAGING" -eq 1 ]] && CERTBOT_ARGS+=(--staging)

if [[ "$DNS_MANUAL" -eq 1 ]]; then
  # manual DNS needs an interactive TTY for “press Enter after creating TXT”
  CERTBOT_ARGS+=(--manual --preferred-challenges dns --manual-public-ip-logging-ok)
  # drop non-interactive for manual pause
  NEW_ARGS=()
  for a in "${CERTBOT_ARGS[@]}"; do
    [[ "$a" == "--non-interactive" ]] && continue
    NEW_ARGS+=("$a")
  done
  CERTBOT_ARGS=("${NEW_ARGS[@]}")
  CERTBOT_ARGS+=(--agree-tos)
elif [[ -n "$WEBROOT" ]]; then
  [[ -d "$WEBROOT" ]] || die "webroot is not a directory: $WEBROOT"
  CERTBOT_ARGS+=(--webroot -w /webroot)
  CERTBOT_ARGS+=(--agree-tos)
fi

CERTBOT_ARGS+=("${DOMAIN_ARGS[@]}")

echo "==> PiHerder ACME helper (Certbot in Docker)"
echo "    image:   $CERTBOT_IMAGE"
echo "    email:   $EMAIL"
echo "    domains: ${DOMAINS[*]}"
if [[ "$STAGING" -eq 1 ]]; then
  echo "    server:  Let's Encrypt STAGING (not trusted by browsers — good for tests)"
else
  echo "    server:  Let's Encrypt PRODUCTION (rate limits apply)"
fi
echo "    config:  $CONFIG_DIR"
echo "    output:  $OUT_DIR"
echo

DOCKER_ARGS=(
  run --rm -it
  -v "${CONFIG_DIR}:/etc/letsencrypt"
  -v "${WORK_DIR}:/var/lib/letsencrypt"
  -v "${LOGS_DIR}:/var/log/letsencrypt"
)
if [[ -n "$WEBROOT" ]]; then
  DOCKER_ARGS+=(-v "${WEBROOT}:/webroot")
fi

# shellcheck disable=SC2086
docker "${DOCKER_ARGS[@]}" "$CERTBOT_IMAGE" "${CERTBOT_ARGS[@]}"

# Certbot names the lineage after the first domain (wildcards use the base name)
# Discover newest live lineage under config
LIVE_BASE="${CONFIG_DIR}/live"
if [[ ! -d "$LIVE_BASE" ]]; then
  die "Certbot finished but no live/ directory under $CONFIG_DIR"
fi

# Prefer exact first domain folder if present
LINEAGE=""
if [[ -d "${LIVE_BASE}/${DOMAINS[0]}" ]]; then
  LINEAGE="${LIVE_BASE}/${DOMAINS[0]}"
else
  # fallback: newest directory
  LINEAGE="$(find "$LIVE_BASE" -mindepth 1 -maxdepth 1 -type d ! -name 'README' | sort | tail -1 || true)"
fi
[[ -n "$LINEAGE" && -f "${LINEAGE}/fullchain.pem" && -f "${LINEAGE}/privkey.pem" ]] \
  || die "could not find fullchain.pem / privkey.pem under $LIVE_BASE"

cp -f "${LINEAGE}/fullchain.pem" "${OUT_DIR}/fullchain.pem"
cp -f "${LINEAGE}/privkey.pem" "${OUT_DIR}/privkey.pem"
chmod 600 "${OUT_DIR}/privkey.pem"
chmod 644 "${OUT_DIR}/fullchain.pem" 2>/dev/null || true

echo
echo "==> Success. PEMs ready for PiHerder:"
echo "    ${OUT_DIR}/fullchain.pem"
echo "    ${OUT_DIR}/privkey.pem"
echo
echo "Next:"
echo "  1. Catalog → Certificates → Upload PEM (paste or use these files)"
echo "  2. Add service maps → Deploy"
echo "  3. See wiki: integrations/certificates-obtain-acme.md"
if [[ "$STAGING" -eq 1 ]]; then
  echo
  echo "Note: staging certificates are not trusted by browsers."
  echo "Re-run without --staging when you are ready for production."
fi
echo
echo "Tip: keep ${CONFIG_DIR} if you will renew with the same account; otherwise delete when done."
echo "     Do not commit privkey.pem or ${CONFIG_DIR} to git."
