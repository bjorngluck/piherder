#!/bin/bash
# PiHerder — cleanup least-privilege service user on the *host*
# Supported: Debian, Raspberry Pi OS, Ubuntu
#
# What this does NOT do (by design):
#   - Does not stop/remove Docker containers, images, or volumes
#   - Does not delete compose projects or media/data under other users
#   - Does not touch PiHerder application DB (use "Remove from PiHerder" in the UI)
#
# Usage (as root on the target host):
#   sudo bash cleanup-piherder-user.sh
#   USER_NAME=piherder REMOVE_USER=1 sudo -E bash cleanup-piherder-user.sh
#
# Env:
#   USER_NAME      service account (default: piherder)
#   REMOVE_USER    1 = userdel -r after other steps (default: 0)
#   REMOVE_SUDOERS 1 = remove /etc/sudoers.d/piherder-$USER_NAME (default: 1)
#   REMOVE_DOCKER  1 = remove from docker group (default: 1)
#   DRY_RUN        1 = print actions only (default: 0)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-run as root (or: sudo bash $0)"
  exec sudo -E bash "$0" "$@"
fi

USER_NAME="${USER_NAME:-piherder}"
REMOVE_USER="${REMOVE_USER:-0}"
REMOVE_SUDOERS="${REMOVE_SUDOERS:-1}"
REMOVE_DOCKER="${REMOVE_DOCKER:-1}"
DRY_RUN="${DRY_RUN:-0}"

run() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: $*"
  else
    eval "$@"
  fi
}

echo "PiHerder host cleanup for user: $USER_NAME"
echo "  REMOVE_SUDOERS=$REMOVE_SUDOERS REMOVE_DOCKER=$REMOVE_DOCKER REMOVE_USER=$REMOVE_USER DRY_RUN=$DRY_RUN"

case "$USER_NAME" in
  root|daemon|nobody|sync|halt|shutdown)
    echo "ERROR: refusing to clean protected system user: $USER_NAME"
    exit 1
    ;;
esac

if [ "$REMOVE_SUDOERS" = "1" ]; then
  DROPIN="/etc/sudoers.d/piherder-${USER_NAME}"
  if [ -e "$DROPIN" ]; then
    run "rm -f $(printf %q "$DROPIN")"
    echo "Removed $DROPIN"
  else
    echo "No sudoers drop-in at $DROPIN (ok)"
  fi
  for f in /etc/sudoers.d/piherder /etc/sudoers.d/*piherder*; do
    [ -e "$f" ] || continue
    case "$f" in
      *"${USER_NAME}"*|*/piherder) run "rm -f $(printf %q "$f")"; echo "Removed $f";;
    esac
  done 2>/dev/null || true
fi

if [ "$REMOVE_DOCKER" = "1" ]; then
  if id "$USER_NAME" >/dev/null 2>&1 && getent group docker >/dev/null 2>&1; then
    if id -nG "$USER_NAME" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
      run "gpasswd -d $(printf %q "$USER_NAME") docker" || true
      echo "Removed $USER_NAME from docker group"
    else
      echo "$USER_NAME not in docker group (ok)"
    fi
  fi
fi

if getent group piherder-compose >/dev/null 2>&1; then
  if id "$USER_NAME" >/dev/null 2>&1 && id -nG "$USER_NAME" 2>/dev/null | tr ' ' '\n' | grep -qx piherder-compose; then
    run "gpasswd -d $(printf %q "$USER_NAME") piherder-compose" || true
    echo "Removed $USER_NAME from piherder-compose group"
  fi
fi

# Optional ACL cleanup — uncomment and set COMPOSE_TREE if you used Option B:
# COMPOSE_TREE=/home/bjorn/docker
# if command -v setfacl >/dev/null 2>&1 && [ -n "${COMPOSE_TREE:-}" ] && [ -d "$COMPOSE_TREE" ]; then
#   setfacl -R -x "u:${USER_NAME}" "$COMPOSE_TREE" 2>/dev/null || true
#   setfacl -R -d -x "u:${USER_NAME}" "$COMPOSE_TREE" 2>/dev/null || true
#   echo "Removed ACLs for $USER_NAME on $COMPOSE_TREE (best-effort)"
# fi

if [ "$REMOVE_USER" = "1" ]; then
  if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "User $USER_NAME does not exist (ok)"
  else
    run "pkill -u $(printf %q "$USER_NAME")" || true
    sleep 0.5 || true
    if command -v deluser >/dev/null 2>&1; then
      run "deluser --remove-home $(printf %q "$USER_NAME")" || run "userdel -r $(printf %q "$USER_NAME")" || true
    else
      run "userdel -r $(printf %q "$USER_NAME")" || true
    fi
    if id "$USER_NAME" >/dev/null 2>&1; then
      echo "WARNING: user $USER_NAME still present — remove manually if needed"
    else
      echo "Deleted user $USER_NAME (home removed when possible)"
    fi
  fi
else
  echo "Left user $USER_NAME in place (set REMOVE_USER=1 to delete account + home)."
  echo "authorized_keys under that home were not modified."
fi

echo "Done. Host Docker stacks and data were not touched."
echo "If this host is still in PiHerder, remove it from the UI (or it will keep trying to connect)."
