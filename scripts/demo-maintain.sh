#!/usr/bin/env bash
# PiHerder public demo — host maintenance (data reset / clean redeploy).
#
# Intended for the **demo VPS only**. Do not run against a real fleet DB.
# Install schedule yourself (example: /etc/cron.d/piherder-demo) — this script
# does not install cron.
#
# Usage (from repo root or any cwd):
#   ./scripts/demo-maintain.sh data-reset     # force re-seed (no volume wipe)
#   ./scripts/demo-maintain.sh redeploy       # git pull + rebuild + recreate + seed
#   ./scripts/demo-maintain.sh redeploy --wipe  # also docker compose down -v
#
# Env (optional):
#   DEMO_ROOT          repo path (default: parent of scripts/)
#   DEMO_GIT_BRANCH    expected branch for redeploy (default: current branch)
#   DEMO_SKIP_PULL=1   skip git pull on redeploy
#   DEMO_REAPPLY_FW=1  after redeploy, sudo ./scripts/demo-docker-user.sh
#   DEMO_WAN / DEMO_COMPOSE_NET / DEMO_ADMIN_IP  passed to demo-docker-user.sh
#   PIHERDER_IMAGE     image tag after build (default: piherder:demo)
#   DEMO_BUILD_NETWORK docker build --network (default: host — required when
#                      the Docker bridge has no egress / no PyPI)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${DEMO_ROOT:-$ROOT}"
cd "$ROOT"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s %s\n' "$(ts)" "$*"; }

LOCK_FILE="${DEMO_LOCK_FILE:-/tmp/piherder-demo-maintain.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another demo-maintain run holds $LOCK_FILE — exiting"
  exit 0
fi

compose_cmd() {
  local files=(-f docker-compose.yml -f docker-compose.demo.yml)
  if [[ -f docker-compose.demo-ports.yml ]]; then
    files+=(-f docker-compose.demo-ports.yml)
  fi
  docker compose "${files[@]}" "$@"
}

wait_web_healthy() {
  local i
  log "waiting for web /health ..."
  for i in $(seq 1 90); do
    if compose_cmd exec -T web curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
      log "web healthy"
      return 0
    fi
    sleep 2
  done
  log "ERROR: web did not become healthy in time"
  return 1
}

force_seed() {
  log "force re-seed"
  compose_cmd exec -T web python scripts/demo_seed/seed.py --force
}

cmd_data_reset() {
  log "==> data-reset (force seed, no volume wipe)"
  if ! compose_cmd ps --status running 2>/dev/null | grep -qE 'web|piherder-web'; then
    log "web not running — starting stack"
    compose_cmd up -d
    wait_web_healthy
  fi
  force_seed
  log "==> data-reset done"
}

cmd_redeploy() {
  local wipe=0
  for arg in "$@"; do
    case "$arg" in
      --wipe) wipe=1 ;;
      -h|--help)
        echo "Usage: $0 redeploy [--wipe]"
        exit 0
        ;;
      *)
        log "unknown redeploy arg: $arg" >&2
        exit 2
        ;;
    esac
  done

  log "==> redeploy (clean containers; wipe_volumes=$wipe)"

  if [[ "${DEMO_SKIP_PULL:-0}" != "1" ]]; then
    if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
      log "ERROR: working tree dirty — refuse git pull. Commit/stash on VPS or set DEMO_SKIP_PULL=1"
      git status --short || true
      exit 1
    fi
    if [[ -n "${DEMO_GIT_BRANCH:-}" ]]; then
      local cur
      cur="$(git rev-parse --abbrev-ref HEAD)"
      if [[ "$cur" != "$DEMO_GIT_BRANCH" ]]; then
        log "ERROR: on branch '$cur', expected DEMO_GIT_BRANCH='$DEMO_GIT_BRANCH'"
        exit 1
      fi
    fi
    log "git pull --ff-only"
    git pull --ff-only
  else
    log "skip git pull (DEMO_SKIP_PULL=1)"
  fi

  # Restricted Docker egress: compose build uses the bridge and hangs on
  # "Installing build dependencies". Same as the host recipe:
  #   DOCKER_BUILDKIT=1 docker build --network=host -t piherder:demo .
  local image="${PIHERDER_IMAGE:-piherder:demo}"
  local net="${DEMO_BUILD_NETWORK:-host}"
  export PIHERDER_IMAGE="$image"
  log "docker build --network=${net} -t ${image}"
  DOCKER_BUILDKIT=1 docker build --network="$net" -t "$image" .

  if [[ "$wipe" -eq 1 ]]; then
    log "compose down -v (destroys Postgres/Redis volumes)"
    compose_cmd down -v
    log "compose up -d --no-build (image ${image})"
    compose_cmd up -d --no-build --remove-orphans
  else
    log "compose up -d --no-build --force-recreate (image ${image})"
    compose_cmd up -d --no-build --force-recreate --remove-orphans
  fi

  wait_web_healthy
  # Empty DB auto-seeds; --force refreshes password/pack even if lifespan already ran
  force_seed || true

  if [[ "${DEMO_REAPPLY_FW:-0}" == "1" ]]; then
    log "re-apply DOCKER-USER firewall (DEMO_REAPPLY_FW=1)"
    if [[ "$(id -u)" -eq 0 ]]; then
      WAN="${DEMO_WAN:-eth0}" COMPOSE_NET="${DEMO_COMPOSE_NET:-172.18.0.0/16}" \
        ADMIN_IP="${DEMO_ADMIN_IP:-}" \
        "$ROOT/scripts/demo-docker-user.sh"
    elif sudo -n true 2>/dev/null; then
      sudo -n env \
        WAN="${DEMO_WAN:-eth0}" \
        COMPOSE_NET="${DEMO_COMPOSE_NET:-172.18.0.0/16}" \
        ADMIN_IP="${DEMO_ADMIN_IP:-}" \
        "$ROOT/scripts/demo-docker-user.sh"
    else
      log "WARN: cannot re-apply firewall (need root or passwordless sudo)"
    fi
  fi

  log "==> redeploy done"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <data-reset|redeploy> [options]

  data-reset          Force re-seed synthetic fleet (keeps volumes)
  redeploy [--wipe]   git pull --ff-only, docker build --network=host
                      -t piherder:demo, compose up --no-build, force seed
                      --wipe also runs compose down -v (empty Postgres)

Does not install cron. See docs/DEMO_SITE.md § Cron.
EOF
}

main() {
  local action="${1:-}"
  shift || true
  case "$action" in
    data-reset|reset|seed)
      cmd_data_reset "$@"
      ;;
    redeploy|deploy)
      cmd_redeploy "$@"
      ;;
    -h|--help|"")
      usage
      exit 0
      ;;
    *)
      log "unknown action: $action" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
