"""
SSH onboarding helpers: deploy key, rotate keypair, least-priv user (Debian family).

Target for automated least-priv / sudoers: Raspberry Pi OS and Ubuntu (Debian-based).
HAOS and other specialised systems get copy-paste guidance only — not remote provision.
"""
from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, Optional

import paramiko

from ..models import Server
from ..security import encryption
from . import ssh as ssh_service


# Placeholders that are not real OpenSSH public keys
_PLACEHOLDER_PUB = (
    "(password auth - no public key)",
    "(provided with private key - test connection to verify)",
)


@dataclass
class OnboardingResult:
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, **self.details}


def is_real_public_key(pub: Optional[str]) -> bool:
    if not pub or not str(pub).strip():
        return False
    s = str(pub).strip()
    if s in _PLACEHOLDER_PUB:
        return False
    parts = s.split()
    return len(parts) >= 2 and parts[0].startswith("ssh-")


def normalize_public_key(pub: str) -> str:
    return " ".join(pub.strip().split())


def public_key_identity(pub: str) -> str:
    """Type + key material (no comment) for matching authorized_keys lines."""
    parts = normalize_public_key(pub).split()
    if len(parts) < 2:
        raise ValueError("Invalid public key")
    return f"{parts[0]} {parts[1]}"


def public_key_from_private(priv_openssh: str, comment: str = "piherder-uploaded") -> str:
    """Extract OpenSSH public key line from a private key (RSA / Ed25519 / ECDSA)."""
    buf = StringIO(priv_openssh.strip())
    last_err: Exception | None = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            buf.seek(0)
            key = cls.from_private_key(buf)
            return f"{key.get_name()} {key.get_base64()} {comment}"
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Could not parse private key: {last_err}")


def _pkey_from_private(priv_openssh: str) -> paramiko.PKey:
    buf = StringIO(priv_openssh.strip())
    last_err: Exception | None = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            buf.seek(0)
            return cls.from_private_key(buf)
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Could not load private key: {last_err}")


def connect_with_auth(
    server: Server,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    private_key_plain: Optional[str] = None,
    allow_stored_password: bool = False,
) -> paramiko.SSHClient:
    """
    Connect with explicit credentials. Does not fall back to agent/host keys.
    If private_key_plain is None and server has encrypted key, use it only when
    password is also None and caller wants stored key (see get_ssh_client).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = None
    if private_key_plain:
        pkey = _pkey_from_private(private_key_plain)

    pw = password
    if pw is None and allow_stored_password and server.ssh_password_encrypted:
        pw = encryption.decrypt_str(server.ssh_password_encrypted)

    user = (username or server.ssh_username or "").strip()
    host = server.hostname or server.ip_address
    if not host:
        client.close()
        raise RuntimeError("Server has no hostname")
    if not user:
        client.close()
        raise RuntimeError("Server has no SSH username")

    try:
        client.connect(
            hostname=host,
            port=server.ssh_port or 22,
            username=user,
            pkey=pkey,
            password=pw,
            timeout=ssh_service.SSH_OPTS["timeout"],
            banner_timeout=ssh_service.SSH_OPTS["banner_timeout"],
            auth_timeout=ssh_service.SSH_OPTS["auth_timeout"],
            look_for_keys=False,
            allow_agent=False,
        )
        return client
    except Exception as e:
        client.close()
        raise RuntimeError(f"SSH connect failed to {host}: {e}") from e


def test_connection_detail(server: Server) -> OnboardingResult:
    try:
        client = ssh_service.get_ssh_client(server)
        try:
            status, out, err = ssh_service.run_command(
                client,
                "echo 'PiHerder SSH test OK' && hostname && date",
                timeout=20,
            )
        finally:
            client.close()
        if status == 0:
            lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
            return OnboardingResult(
                ok=True,
                message="SSH connection OK",
                details={"hostname_line": lines[1] if len(lines) > 1 else (lines[0] if lines else "")},
            )
        return OnboardingResult(
            ok=False,
            message=(err or out or f"Remote command failed (exit {status})")[:240],
        )
    except Exception as e:
        return OnboardingResult(ok=False, message=str(e)[:240])


def install_authorized_key(
    client: paramiko.SSHClient,
    public_key: str,
    *,
    home_dir: Optional[str] = None,
) -> dict[str, Any]:
    """
    Idempotently append public_key to ~/.ssh/authorized_keys (or $home_dir/.ssh).
    Returns {installed, already_present, path}.
    """
    if not is_real_public_key(public_key):
        raise ValueError("No valid public key to install")

    key_line = normalize_public_key(public_key)
    identity = public_key_identity(key_line)
    key_b64 = base64.b64encode(key_line.encode()).decode("ascii")
    id_b64 = base64.b64encode(identity.encode()).decode("ascii")

    if home_dir:
        home_q = shlex.quote(home_dir.rstrip("/"))
        base = f"export HOME={home_q}; "
    else:
        base = ""

    # Portable shell: no bash-only features required
    script = base + f"""
set -e
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
AUTH="$HOME/.ssh/authorized_keys"
touch "$AUTH"
chmod 600 "$AUTH"
KEY=$(printf '%s' '{key_b64}' | base64 -d)
IDENT=$(printf '%s' '{id_b64}' | base64 -d)
if grep -F "$IDENT" "$AUTH" >/dev/null 2>&1; then
  echo ALREADY_PRESENT
  exit 0
fi
printf '%s\\n' "$KEY" >> "$AUTH"
chmod 600 "$AUTH"
echo INSTALLED
"""
    status, out, err = ssh_service.run_command(client, script, timeout=30)
    text = (out or "") + (err or "")
    if status != 0:
        raise RuntimeError(f"Failed to install authorized key: {(err or out or text)[:300]}")
    already = "ALREADY_PRESENT" in text
    return {
        "installed": not already,
        "already_present": already,
        "path": "~/.ssh/authorized_keys" if not home_dir else f"{home_dir.rstrip('/')}/.ssh/authorized_keys",
    }


def remove_authorized_key(client: paramiko.SSHClient, public_key: str) -> bool:
    """Remove lines matching the key identity from authorized_keys. Returns True if file touched."""
    if not is_real_public_key(public_key):
        return False
    identity = public_key_identity(public_key)
    id_b64 = base64.b64encode(identity.encode()).decode("ascii")
    script = f"""
set -e
AUTH="$HOME/.ssh/authorized_keys"
if [ ! -f "$AUTH" ]; then
  echo NO_FILE
  exit 0
fi
IDENT=$(printf '%s' '{id_b64}' | base64 -d)
if ! grep -F "$IDENT" "$AUTH" >/dev/null 2>&1; then
  echo NOT_FOUND
  exit 0
fi
TMP=$(mktemp)
grep -Fv "$IDENT" "$AUTH" > "$TMP" || true
mv "$TMP" "$AUTH"
chmod 600 "$AUTH"
echo REMOVED
"""
    status, out, err = ssh_service.run_command(client, script, timeout=30)
    if status != 0:
        raise RuntimeError(f"Failed to remove old key: {(err or out)[:300]}")
    return "REMOVED" in ((out or "") + (err or ""))


def build_key_install_script(public_key: str, username: str = "") -> str:
    """Copy-paste script for the remote host (any OpenSSH; not distro-specific)."""
    if not is_real_public_key(public_key):
        return "# No public key stored for this server yet.\n"
    key = normalize_public_key(public_key)
    user_note = f"  # as user {username}" if username else ""
    return f"""# PiHerder — install SSH public key{user_note}
# Run on the target host (or: ssh user@host 'bash -s' < this_script.sh)
set -e
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
KEY={shlex.quote(key)}
if ! grep -Fq "$(echo "$KEY" | awk '{{print $1" "$2}}')" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  printf '%s\\n' "$KEY" >> "$HOME/.ssh/authorized_keys"
  echo "Installed public key."
else
  echo "Public key already present."
fi
chmod 600 "$HOME/.ssh/authorized_keys"
"""


def _stored_password(server: Server) -> Optional[str]:
    if not server.ssh_password_encrypted:
        return None
    try:
        return encryption.decrypt_str(server.ssh_password_encrypted)
    except Exception:
        return None


def _ensure_server_key_material(server: Server) -> tuple[str, str]:
    """Return (public_key, private_key_plain). Raises if incomplete."""
    if not server.ssh_private_key_encrypted:
        raise RuntimeError("No SSH private key on this server — generate or upload a key first")
    priv = ssh_service.get_private_key_plain(server)
    pub = server.ssh_public_key
    if not is_real_public_key(pub):
        pub = public_key_from_private(priv, comment=f"piherder@{server.hostname or server.name}")
    return normalize_public_key(pub), priv


def deploy_public_key(
    server: Server,
    *,
    password_override: Optional[str] = None,
) -> OnboardingResult:
    """
    Install server's public key on the remote host and verify key-only login.
    Uses existing key auth if it already works; otherwise password (override or stored).
    """
    try:
        pub, priv = _ensure_server_key_material(server)
    except Exception as e:
        return OnboardingResult(ok=False, message=str(e))

    # Prefer key-only path when it already works
    key_ok = False
    try:
        client = connect_with_auth(server, private_key_plain=priv, password=None)
        try:
            install_info = install_authorized_key(client, pub)
        finally:
            client.close()
        key_ok = True
        return OnboardingResult(
            ok=True,
            message="SSH key auth already works; authorized_keys checked",
            details={
                "already_auth": True,
                "public_key": pub,
                **install_info,
            },
        )
    except Exception:
        key_ok = False

    password = (password_override or "").strip() or _stored_password(server)
    if not password:
        return OnboardingResult(
            ok=False,
            message="Key auth failed and no password available. Enter a one-time password or store one in Edit, then retry Deploy.",
            details={"need_password": True},
        )

    try:
        client = connect_with_auth(server, private_key_plain=None, password=password)
        try:
            install_info = install_authorized_key(client, pub)
        finally:
            client.close()
    except Exception as e:
        return OnboardingResult(ok=False, message=f"Password session failed: {e}")

    # Verify key-only
    try:
        vclient = connect_with_auth(server, private_key_plain=priv, password=None)
        vclient.close()
    except Exception as e:
        return OnboardingResult(
            ok=False,
            message=f"Key was written but key-only login still fails: {e}",
            details={**install_info, "public_key": pub, "verify_failed": True},
        )

    return OnboardingResult(
        ok=True,
        message="SSH public key deployed and key-only login verified",
        details={
            "already_auth": False,
            "public_key": pub,
            **install_info,
        },
    )


def rotate_keypair(
    server: Server,
    *,
    password_override: Optional[str] = None,
) -> OnboardingResult:
    """
    Generate new keypair, install new pubkey, verify with new private key,
    return material for DB swap. Does not modify the Server row.
    On verify failure, leaves DB unchanged; new pubkey may remain on host for retry.
    """
    if not server.ssh_private_key_encrypted:
        return OnboardingResult(ok=False, message="No existing private key to rotate from")

    old_pub = server.ssh_public_key if is_real_public_key(server.ssh_public_key) else None
    old_priv = ssh_service.get_private_key_plain(server)
    if not old_pub:
        try:
            old_pub = public_key_from_private(old_priv, comment=f"piherder@{server.hostname or 'old'}")
        except Exception:
            old_pub = None

    comment = f"piherder-{re.sub(r'[^a-zA-Z0-9._-]+', '-', server.name or 'server')}-rotated"
    new_pub, new_priv = ssh_service.generate_keypair(comment=comment)

    password = (password_override or "").strip() or None
    client = None
    # Prefer current key; fall back to password
    try:
        client = connect_with_auth(server, private_key_plain=old_priv, password=None)
    except Exception:
        pw = password or _stored_password(server)
        if not pw:
            return OnboardingResult(
                ok=False,
                message="Cannot connect with current key and no password available for rotation",
                details={"need_password": True},
            )
        try:
            client = connect_with_auth(server, private_key_plain=None, password=pw)
        except Exception as e:
            return OnboardingResult(ok=False, message=f"Connect for rotation failed: {e}")

    try:
        install_info = install_authorized_key(client, new_pub)
    except Exception as e:
        client.close()
        return OnboardingResult(ok=False, message=f"Failed to install new public key: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass

    # Verify with NEW key only
    try:
        vclient = connect_with_auth(server, private_key_plain=new_priv, password=None)
        vclient.close()
    except Exception as e:
        return OnboardingResult(
            ok=False,
            message=f"New key installed but verification failed — DB not updated: {e}",
            details={"verify_failed": True, "new_public_key": new_pub, **install_info},
        )

    # Remove old public key (best-effort) using new key session
    removed_old = False
    if old_pub and public_key_identity(old_pub) != public_key_identity(new_pub):
        try:
            rclient = connect_with_auth(server, private_key_plain=new_priv, password=None)
            try:
                removed_old = remove_authorized_key(rclient, old_pub)
            finally:
                rclient.close()
        except Exception:
            removed_old = False

    return OnboardingResult(
        ok=True,
        message="SSH key rotated and verified",
        details={
            "new_public_key": new_pub,
            "new_private_key": new_priv,
            "old_public_key": old_pub,
            "removed_old": removed_old,
            **install_info,
        },
    )


# ---------------------------------------------------------------------------
# Least-privilege user — Debian / Pi OS / Ubuntu only
# ---------------------------------------------------------------------------

DEBIAN_FAMILY_IDS = frozenset({"debian", "ubuntu", "raspbian", "linuxmint", "pop"})
HAOS_GUIDANCE = """# Home Assistant OS (HAOS) — PiHerder host setup
# Automated least-priv user + sudoers is not supported on HAOS.
#
# Required dependencies:
#   1. Terminal & SSH add-on enabled (Settings → Add-ons)
#      - SSH as root (or the add-on user) with key auth
#      - Install PiHerder public key (Deploy key / install script above)
#   2. rsync package installed on the host (backups use plain rsync, no sudo)
#      - Exact install steps: see wiki when published; package must be on PATH
#
# What PiHerder does on HAOS:
#   - Detects HAOS via os-release + `ha` CLI; auto-marks the server
#   - OS update check/apply via: ha supervisor|core|os info/update
#   - Backups via plain rsync when the package is present
#   - Does NOT manage Docker Compose fleet or apt packages on HAOS
#
# If you need a non-root user, create it with your platform's tools and
# re-point the server SSH username in PiHerder Edit.
"""


def build_compose_tree_acl_script(
    service_user: str,
    compose_owner: str,
    compose_dir: str = "docker",
) -> str:
    """
    Option B host setup: let least-priv user traverse another user's home and
    read/write the compose tree (Pi OS / Ubuntu). Run as root on the target.
    """
    svc = re.sub(r"[^a-z0-9_-]", "", (service_user or "piherder").lower()) or "piherder"
    owner = re.sub(r"[^a-z0-9_-]", "", (compose_owner or "bjorn").lower()) or "bjorn"
    # compose_dir may be absolute or relative under owner's home
    if compose_dir.startswith("/"):
        tree = compose_dir.rstrip("/") or f"/home/{owner}/docker"
        home = f"/home/{owner}"
    else:
        rel = compose_dir.strip("/") or "docker"
        home = f"/home/{owner}"
        tree = f"{home}/{rel}"
    return f"""#!/bin/bash
# PiHerder — share compose tree with least-priv SSH user (Option B)
# Target: Debian / Raspberry Pi OS / Ubuntu
# Lets {svc!r} manage stacks under {tree!r} (owned by {owner!r}).
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "Re-run as root: sudo bash $0"
  exec sudo bash "$0" "$@"
fi

SERVICE_USER={shlex.quote(svc)}
OWNER_HOME={shlex.quote(home)}
TREE={shlex.quote(tree)}

# docker group (socket access)
if getent group docker >/dev/null 2>&1; then
  usermod -aG docker "$SERVICE_USER" || true
else
  echo "WARNING: docker group missing"
fi

# Traverse owner home (execute-only on home is enough; does not list contents)
if [ -d "$OWNER_HOME" ]; then
  chmod o+x "$OWNER_HOME" || chmod 711 "$OWNER_HOME" || true
fi

if [ ! -d "$TREE" ]; then
  echo "ERROR: compose tree not found: $TREE"
  exit 1
fi

if command -v setfacl >/dev/null 2>&1; then
  setfacl -R -m "u:${{SERVICE_USER}}:rwx" "$TREE"
  setfacl -R -d -m "u:${{SERVICE_USER}}:rwx" "$TREE"
  echo "ACLs granted on $TREE for $SERVICE_USER"
else
  # Fallback: shared group (less precise)
  GROUP="piherder-compose"
  groupadd -f "$GROUP"
  usermod -aG "$GROUP" "$SERVICE_USER"
  usermod -aG "$GROUP" "{owner}" || true
  chgrp -R "$GROUP" "$TREE"
  chmod -R g+rwX "$TREE"
  find "$TREE" -type d -exec chmod g+s {{}} +
  echo "Group $GROUP applied on $TREE (install acl package for setfacl next time)"
fi

echo "Done. In PiHerder set Docker base dir to: $TREE"
echo "Test as $SERVICE_USER: ssh $SERVICE_USER@host 'ls $TREE && cd $TREE && docker ps'"
"""


def preserve_docker_base_after_user_switch(
    docker_base_dir: str,
    previous_username: str,
    new_username: str,
) -> str:
    """
    When SSH username changes (least-priv re-point), convert ``~/…`` paths to an
    absolute path under the *previous* home so stacks stay discoverable.
    """
    base = (docker_base_dir or "~/docker").strip() or "~/docker"
    prev = (previous_username or "").strip()
    new = (new_username or "").strip()
    if not prev or not new or prev == new:
        return base
    if not base.startswith("~"):
        return base
    from .ssh import expand_remote_path
    return expand_remote_path(base, prev)


def detect_os_family(client: paramiko.SSHClient) -> dict[str, Any]:
    """Read /etc/os-release. Returns {id, id_like, name, debian_family, raw}."""
    status, out, err = ssh_service.run_command(
        client, "cat /etc/os-release 2>/dev/null || true", timeout=15
    )
    raw = out or ""
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            data[k.strip().lower()] = v.strip().strip('"').strip("'")
    os_id = (data.get("id") or "").lower()
    id_like = (data.get("id_like") or "").lower()
    likes = set(id_like.split())
    debian_family = (
        os_id in DEBIAN_FAMILY_IDS
        or "debian" in likes
        or "ubuntu" in likes
        or os_id == "raspbian"
    )
    # HAOS heuristic
    pretty = (data.get("pretty_name") or data.get("name") or "").lower()
    if "hassos" in pretty or "home assistant" in pretty or os_id in {"hassos", "haos"}:
        debian_family = False
    return {
        "id": os_id,
        "id_like": id_like,
        "name": data.get("pretty_name") or data.get("name") or os_id or "unknown",
        "debian_family": debian_family,
        "raw": raw[:500],
    }


def build_sudoers_content(
    username: str,
    *,
    backup: bool = True,
    docker: bool = False,
    os_patch: bool = False,
) -> str:
    """
    Debian/Ubuntu/Pi OS sudoers drop-in body (validated with visudo -cf when applied).
    Docker access is via group membership, not sudo docker, when possible.
    """
    user = username.strip()
    lines = [
        f"# PiHerder least-privilege — {user}",
        "# Target: Debian / Raspberry Pi OS / Ubuntu",
        "# Generated by PiHerder; review before install.",
        "",
    ]
    aliases: list[str] = []
    grants: list[str] = []

    if backup:
        aliases.append(
            "Cmnd_Alias PIHERDER_BACKUP = /usr/bin/rsync, /bin/rsync, "
            "/usr/bin/test, /bin/test, /usr/bin/true, /bin/true"
        )
        grants.append(f"{user} ALL=(root) NOPASSWD: PIHERDER_BACKUP")

    if os_patch:
        aliases.append(
            "Cmnd_Alias PIHERDER_APT = /usr/bin/apt, /usr/bin/apt-get, /usr/bin/dpkg, "
            "/usr/bin/apt-cache"
        )
        aliases.append(
            "Cmnd_Alias PIHERDER_REBOOT = /usr/sbin/reboot, /sbin/reboot, "
            "/bin/systemctl, /usr/bin/systemctl"
        )
        grants.append(f"{user} ALL=(root) NOPASSWD: PIHERDER_APT, PIHERDER_REBOOT")

    if docker:
        # Prefer docker group membership (provision script). Optional sudo paths for CLI only.
        aliases.append(
            "Cmnd_Alias PIHERDER_DOCKER = /usr/bin/docker, /usr/local/bin/docker, "
            "/usr/bin/docker-compose, /usr/local/bin/docker-compose"
        )
        grants.append(f"# Docker group is primary; optional NOPASSWD CLI:")
        grants.append(f"{user} ALL=(root) NOPASSWD: PIHERDER_DOCKER")

    lines.extend(aliases)
    if aliases:
        lines.append("")
    lines.extend(grants)
    lines.append("")
    return "\n".join(lines)


def build_least_priv_script(
    new_username: str,
    public_key: str,
    *,
    backup: bool = True,
    docker: bool = False,
    os_patch: bool = False,
) -> str:
    """
    Full copy-paste provision script for Debian / Pi OS / Ubuntu.
    Creates user, optional docker group, sudoers drop-in, authorized_keys.
    """
    user = re.sub(r"[^a-z0-9_-]", "", (new_username or "piherder").lower()) or "piherder"
    if user in {"root", "daemon", "nobody"}:
        user = "piherder"

    sudoers = build_sudoers_content(user, backup=backup, docker=docker, os_patch=os_patch)
    sudoers_b64 = base64.b64encode(sudoers.encode()).decode("ascii")
    key_block = ""
    if is_real_public_key(public_key):
        key_line = normalize_public_key(public_key)
        key_b64 = base64.b64encode(key_line.encode()).decode("ascii")
        key_block = f"""
# Install PiHerder public key for {user}
USER_HOME=$(getent passwd {user} | cut -d: -f6)
mkdir -p "$USER_HOME/.ssh"
chmod 700 "$USER_HOME/.ssh"
AUTH="$USER_HOME/.ssh/authorized_keys"
touch "$AUTH"
KEY=$(printf '%s' '{key_b64}' | base64 -d)
IDENT=$(echo "$KEY" | awk '{{print $1" "$2}}')
if ! grep -Fq "$IDENT" "$AUTH" 2>/dev/null; then
  printf '%s\\n' "$KEY" >> "$AUTH"
fi
chmod 600 "$AUTH"
chown -R {user}:{user} "$USER_HOME/.ssh"
"""

    docker_block = ""
    if docker:
        docker_block = f"""
if getent group docker >/dev/null 2>&1; then
  usermod -aG docker {user} || true
  echo "Added {user} to docker group (re-login required for group on interactive shells)."
else
  echo "WARNING: docker group not found — install Docker or skip container features."
fi
"""

    return f"""#!/bin/bash
# PiHerder least-privilege user setup
# Supported: Debian, Raspberry Pi OS, Ubuntu
# NOT for HAOS / Alpine / specialised images — use key deploy as root instead.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-run as root (or: sudo bash $0)"
  exec sudo -E bash "$0" "$@"
fi

if [ -f /etc/os-release ]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${{ID:-}} ${{ID_LIKE:-}}" in
    *debian*|*ubuntu*|*raspbian*) ;;
    *)
      echo "WARNING: This script is intended for Debian-based systems (Pi OS / Ubuntu)."
      echo "Detected: ${{PRETTY_NAME:-unknown}}. Continue only if you know the paths match."
      ;;
  esac
fi

USER_NAME={shlex.quote(user)}

if ! id "$USER_NAME" >/dev/null 2>&1; then
  if command -v adduser >/dev/null 2>&1; then
    adduser --disabled-password --gecos "PiHerder service" "$USER_NAME"
  else
    useradd -m -s /bin/bash "$USER_NAME"
  fi
  echo "Created user $USER_NAME"
else
  echo "User $USER_NAME already exists"
fi

{docker_block}

# Sudoers drop-in (validated)
DROPIN="/etc/sudoers.d/piherder-${{USER_NAME}}"
printf '%s' '{sudoers_b64}' | base64 -d > "${{DROPIN}}.tmp"
chmod 440 "${{DROPIN}}.tmp"
if command -v visudo >/dev/null 2>&1; then
  if ! visudo -cf "${{DROPIN}}.tmp"; then
    echo "ERROR: sudoers validation failed — not installing."
    rm -f "${{DROPIN}}.tmp"
    exit 1
  fi
fi
mv "${{DROPIN}}.tmp" "$DROPIN"
chmod 440 "$DROPIN"
echo "Installed $DROPIN"
{key_block}
echo "Done. Point PiHerder SSH username to: $USER_NAME"
echo "Test: ssh -i <key> $USER_NAME@host"
"""


def build_piherder_user_cleanup_script(
    username: str = "piherder",
    *,
    remove_user: bool = False,
    compose_owner: str | None = None,
    compose_tree: str | None = None,
) -> str:
    """
    Host-side cleanup for a PiHerder least-priv account (Debian / Pi OS / Ubuntu).

    Safe defaults: remove sudoers drop-in and docker group membership only.
    Does **not** touch Docker stacks, volumes, or other users' data.
    Optional flags (via env when running) can delete the user account.
    """
    user = re.sub(r"[^a-z0-9_-]", "", (username or "piherder").lower()) or "piherder"
    if user in {"root", "daemon", "nobody", "bjorn"}:
        # Refuse destructive defaults on common primary accounts
        user = "piherder"

    owner = re.sub(r"[^a-z0-9_-]", "", (compose_owner or "").lower()) if compose_owner else ""
    tree = (compose_tree or "").strip()
    acl_hint = ""
    if owner and tree:
        acl_hint = f"""
# Optional ACL cleanup (only if you previously ran Option B share-compose script)
# COMPOSE_TREE={shlex.quote(tree)}
# COMPOSE_OWNER={shlex.quote(owner)}
# if command -v setfacl >/dev/null 2>&1 && [ -d "$COMPOSE_TREE" ]; then
#   setfacl -R -x "u:${{USER_NAME}}" "$COMPOSE_TREE" 2>/dev/null || true
#   setfacl -R -d -x "u:${{USER_NAME}}" "$COMPOSE_TREE" 2>/dev/null || true
#   echo "Removed ACLs for $USER_NAME on $COMPOSE_TREE (best-effort)"
# fi
"""

    remove_user_default = "1" if remove_user else "0"

    return f"""#!/bin/bash
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
#   USER_NAME      service account (default: {user})
#   REMOVE_USER    1 = userdel -r after other steps (default: {remove_user_default})
#   REMOVE_SUDOERS 1 = remove /etc/sudoers.d/piherder-$USER_NAME (default: 1)
#   REMOVE_DOCKER  1 = remove from docker group (default: 1)
#   DRY_RUN        1 = print actions only (default: 0)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-run as root (or: sudo bash $0)"
  exec sudo -E bash "$0" "$@"
fi

USER_NAME="${{USER_NAME:-{user}}}"
REMOVE_USER="${{REMOVE_USER:-{remove_user_default}}}"
REMOVE_SUDOERS="${{REMOVE_SUDOERS:-1}}"
REMOVE_DOCKER="${{REMOVE_DOCKER:-1}}"
DRY_RUN="${{DRY_RUN:-0}}"

run() {{
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: $*"
  else
    eval "$@"
  fi
}}

echo "PiHerder host cleanup for user: $USER_NAME"
echo "  REMOVE_SUDOERS=$REMOVE_SUDOERS REMOVE_DOCKER=$REMOVE_DOCKER REMOVE_USER=$REMOVE_USER DRY_RUN=$DRY_RUN"

# Guardrails
case "$USER_NAME" in
  root|daemon|nobody|sync|halt|shutdown)
    echo "ERROR: refusing to clean protected system user: $USER_NAME"
    exit 1
    ;;
esac

if [ "$REMOVE_SUDOERS" = "1" ]; then
  DROPIN="/etc/sudoers.d/piherder-${{USER_NAME}}"
  if [ -e "$DROPIN" ]; then
    run "rm -f $(printf %q "$DROPIN")"
    echo "Removed $DROPIN"
  else
    echo "No sudoers drop-in at $DROPIN (ok)"
  fi
  # Older / alternate names
  for f in /etc/sudoers.d/piherder /etc/sudoers.d/*piherder*; do
    [ -e "$f" ] || continue
    case "$f" in
      *"${{USER_NAME}}"*|*/piherder) run "rm -f $(printf %q "$f")"; echo "Removed $f";;
    esac
  done 2>/dev/null || true
fi

if [ "$REMOVE_DOCKER" = "1" ]; then
  if id "$USER_NAME" >/dev/null 2>&1 && getent group docker >/dev/null 2>&1; then
    if id -nG "$USER_NAME" 2>/dev/null | tr ' ' '\\n' | grep -qx docker; then
      run "gpasswd -d $(printf %q "$USER_NAME") docker" || true
      echo "Removed $USER_NAME from docker group"
    else
      echo "$USER_NAME not in docker group (ok)"
    fi
  fi
fi

# Shared compose group fallback (Option B without setfacl)
if getent group piherder-compose >/dev/null 2>&1; then
  if id "$USER_NAME" >/dev/null 2>&1 && id -nG "$USER_NAME" 2>/dev/null | tr ' ' '\\n' | grep -qx piherder-compose; then
    run "gpasswd -d $(printf %q "$USER_NAME") piherder-compose" || true
    echo "Removed $USER_NAME from piherder-compose group"
  fi
fi
{acl_hint}
if [ "$REMOVE_USER" = "1" ]; then
  if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "User $USER_NAME does not exist (ok)"
  else
    # Kill leftover sessions best-effort
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
"""


def provision_least_priv_user(
    server: Server,
    new_username: str,
    *,
    backup: bool = True,
    docker: bool = False,
    os_patch: bool = False,
    password_override: Optional[str] = None,
    force_non_debian: bool = False,
) -> OnboardingResult:
    """
    Run least-priv provision on remote (Debian family). On success returns
    details including new_username for the router to re-point Server.ssh_username.
    """
    user = re.sub(r"[^a-z0-9_-]", "", (new_username or "piherder").lower()) or "piherder"
    if user in {"root", "daemon", "nobody"}:
        return OnboardingResult(ok=False, message=f"Username '{user}' is not allowed")

    try:
        pub, priv = _ensure_server_key_material(server)
    except Exception as e:
        return OnboardingResult(ok=False, message=str(e))

    password = (password_override or "").strip() or None
    try:
        client = connect_with_auth(server, private_key_plain=priv, password=None)
    except Exception:
        pw = password or _stored_password(server)
        if not pw:
            return OnboardingResult(
                ok=False,
                message="Cannot connect and no password available",
                details={"need_password": True},
            )
        try:
            client = connect_with_auth(server, private_key_plain=None, password=pw)
        except Exception as e:
            return OnboardingResult(ok=False, message=f"Connect failed: {e}")

    try:
        os_info = detect_os_family(client)
        if not os_info.get("debian_family") and not force_non_debian:
            return OnboardingResult(
                ok=False,
                message=(
                    f"Remote OS looks like {os_info.get('name')!r}, not Debian/Pi OS/Ubuntu. "
                    "Automated least-priv is phase-1 scoped to those. Use copy-paste script "
                    "only if appropriate, or Deploy key on HAOS as root."
                ),
                details={
                    "os": os_info,
                    "haos_guidance": HAOS_GUIDANCE,
                    "script": build_least_priv_script(
                        user, pub, backup=backup, docker=docker, os_patch=os_patch
                    ),
                },
            )

        script = build_least_priv_script(
            user, pub, backup=backup, docker=docker, os_patch=os_patch
        )
        script_b64 = base64.b64encode(script.encode()).decode("ascii")
        # Need passwordless sudo as current user to run provision, or be root
        remote = f"""
set -e
TMP=$(mktemp)
printf '%s' '{script_b64}' | base64 -d > "$TMP"
chmod 700 "$TMP"
if [ "$(id -u)" -eq 0 ]; then
  bash "$TMP"
else
  sudo -n bash "$TMP"
fi
RC=$?
rm -f "$TMP"
exit $RC
"""
        status, out, err = ssh_service.run_command(client, remote, timeout=120)
        if status != 0:
            return OnboardingResult(
                ok=False,
                message=(
                    f"Provision failed (exit {status}). Current user needs passwordless sudo "
                    f"(or root). {(err or out or '')[:280]}"
                ),
                details={"stdout": (out or "")[-500:], "stderr": (err or "")[-500:], "os": os_info},
            )
    finally:
        try:
            client.close()
        except Exception:
            pass

    # Verify key login as new user
    try:
        vclient = connect_with_auth(
            server, username=user, private_key_plain=priv, password=None
        )
        vclient.close()
    except Exception as e:
        return OnboardingResult(
            ok=False,
            message=(
                f"User may have been created but key login as {user!r} failed: {e}. "
                "Not re-pointing SSH username."
            ),
            details={"new_username": user, "verify_failed": True},
        )

    return OnboardingResult(
        ok=True,
        message=f"Provisioned least-priv user {user!r} and verified key login",
        details={
            "new_username": user,
            "previous_username": server.ssh_username,
            "os": os_info,
            "docker": docker,
            "os_patch": os_patch,
            "backup": backup,
        },
    )
