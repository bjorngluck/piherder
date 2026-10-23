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
    policy = ssh_service.attach_host_key_policy(client, server)

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
        ssh_service.persist_host_key_if_needed(server, policy.seen_key)
        return client
    except ssh_service.HostKeyMismatch:
        client.close()
        raise
    except Exception as e:
        client.close()
        raise RuntimeError(f"SSH connect failed to {host}: {e}") from e


def test_connection_detail(server: Server, identity=None) -> OnboardingResult:
    try:
        client = ssh_service.get_ssh_client(server, identity)
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
    except ssh_service.HostKeyMismatch as e:
        return OnboardingResult(ok=False, message=str(e)[:400])
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


def _ensure_server_key_material(server: Server, identity=None) -> tuple[str, str]:
    """Return (public_key, private_key_plain). Raises if incomplete."""
    enc = None
    pub = None
    if identity is not None:
        enc = getattr(identity, "private_key_encrypted", None)
        pub = getattr(identity, "public_key", None)
    if not enc:
        enc = server.ssh_private_key_encrypted
        pub = server.ssh_public_key
    if not enc:
        raise RuntimeError("No SSH private key on this server — generate or upload a key first")
    priv = encryption.decrypt_str(enc)
    if not is_real_public_key(pub):
        pub = public_key_from_private(priv, comment=f"piherder@{server.hostname or server.name}")
    return normalize_public_key(pub), priv


def deploy_public_key(
    server: Server,
    *,
    password_override: Optional[str] = None,
    identity=None,
) -> OnboardingResult:
    """
    Install public key on the remote host and verify key-only login.
    Uses existing key auth if it already works; otherwise password (override or stored).
    Privileged identities never use the stored fleet password.
    """
    try:
        pub, priv = _ensure_server_key_material(server, identity)
    except Exception as e:
        return OnboardingResult(ok=False, message=str(e))

    username = None
    if identity is not None:
        username = (getattr(identity, "username", None) or "").strip() or None
    privileged = (getattr(identity, "role", None) or "") == "privileged"

    # Prefer key-only path when it already works
    try:
        client = connect_with_auth(
            server, username=username, private_key_plain=priv, password=None
        )
        try:
            install_info = install_authorized_key(client, pub)
        finally:
            client.close()
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
        pass

    password = (password_override or "").strip() or None
    if not password and not privileged:
        password = _stored_password(server)
    if not password:
        return OnboardingResult(
            ok=False,
            message="Key auth failed and no password available. Enter a one-time password or store one in Edit, then retry Deploy.",
            details={"need_password": True},
        )

    try:
        client = connect_with_auth(
            server, username=username, private_key_plain=None, password=password
        )
        try:
            install_info = install_authorized_key(client, pub)
        finally:
            client.close()
    except Exception as e:
        return OnboardingResult(ok=False, message=f"Password session failed: {e}")

    # Verify key-only
    try:
        vclient = connect_with_auth(
            server, username=username, private_key_plain=priv, password=None
        )
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
    identity=None,
) -> OnboardingResult:
    """
    Generate new keypair, install new pubkey, verify with new private key,
    return material for DB swap. Does not modify the Server row.
    On verify failure, leaves DB unchanged; new pubkey may remain on host for retry.
    """
    try:
        old_pub_n, old_priv = _ensure_server_key_material(server, identity)
    except Exception as e:
        return OnboardingResult(ok=False, message=str(e) or "No existing private key to rotate from")

    old_pub = old_pub_n if is_real_public_key(old_pub_n) else None
    username = None
    if identity is not None:
        username = (getattr(identity, "username", None) or "").strip() or None
    privileged = (getattr(identity, "role", None) or "") == "privileged"
    if not old_pub:
        try:
            old_pub = public_key_from_private(old_priv, comment=f"piherder@{server.hostname or 'old'}")
        except Exception:
            old_pub = None

    comment = f"piherder-{re.sub(r'[^a-zA-Z0-9._-]+', '-', server.name or 'server')}-rotated"
    new_pub, new_priv = ssh_service.generate_keypair(comment=comment)

    password = (password_override or "").strip() or None
    client = None
    # Prefer current key; fall back to password (fleet stored password only)
    try:
        client = connect_with_auth(
            server, username=username, private_key_plain=old_priv, password=None
        )
    except Exception:
        pw = password if password else (None if privileged else _stored_password(server))
        if not pw:
            return OnboardingResult(
                ok=False,
                message="Cannot connect with current key and no password available for rotation",
                details={"need_password": True},
            )
        try:
            client = connect_with_auth(
                server, username=username, private_key_plain=None, password=pw
            )
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
        vclient = connect_with_auth(
            server, username=username, private_key_plain=new_priv, password=None
        )
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
            rclient = connect_with_auth(
                server, username=username, private_key_plain=new_priv, password=None
            )
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
    owner = re.sub(r"[^a-z0-9_-]", "", (compose_owner or "pi").lower()) or "pi"
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
            "/usr/bin/test, /bin/test, /usr/bin/true, /bin/true, "
            "/usr/bin/find, /bin/find, "
            "/usr/bin/chown, /bin/chown, /usr/bin/chmod, /bin/chmod, "
            "/var/lib/piherder/rclone"
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
        grants.append(
            "# Reboot uses: systemctl reboot --ignore-inhibitors "
            "(logind otherwise blocks while SSH/GUI sessions exist)"
        )

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


def build_privileged_user_script(username: str, public_key: str) -> str:
    """Copy-paste host setup for a break-glass / privileged SSH user.

    Not run from the herder. Creates the account, installs the herder public
    key, and a full sudoers drop-in. HAOS / specialised images: skip.
    """
    user = re.sub(r"[^a-z0-9_-]", "", (username or "piherder-admin").lower()) or "piherder-admin"
    if user in {"daemon", "nobody"}:
        user = "piherder-admin"
    sudoers = f"{user} ALL=(ALL) NOPASSWD: ALL\n"
    sudoers_b64 = base64.b64encode(sudoers.encode()).decode("ascii")
    key_block = ""
    if is_real_public_key(public_key):
        key_line = normalize_public_key(public_key)
        key_b64 = base64.b64encode(key_line.encode()).decode("ascii")
        key_block = f"""
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
    return f"""#!/bin/bash
# PiHerder privileged (break-glass) user setup
# Supported: Debian, Raspberry Pi OS, Ubuntu
# NOT for HAOS / Alpine / specialised images.
# Run as root on the HOST. PiHerder does not execute this remotely.
# This grants NOPASSWD sudo — tighten sudoers if you do not want that.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Re-run as root (or: sudo bash $0)"
  exec sudo -E bash "$0" "$@"
fi

USER_NAME={shlex.quote(user)}

if ! id "$USER_NAME" >/dev/null 2>&1; then
  if command -v adduser >/dev/null 2>&1; then
    adduser --disabled-password --gecos "PiHerder privileged" "$USER_NAME"
  else
    useradd -m -s /bin/bash "$USER_NAME"
  fi
  echo "Created user $USER_NAME"
else
  echo "User $USER_NAME already exists"
fi

DROPIN="/etc/sudoers.d/piherder-privileged-${{USER_NAME}}"
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
echo "Done. In PiHerder: SSH access → privileged identity → Test connection."
echo "Jobs still use the fleet user — this account is console + Files (privileged)."
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
    if user in {"root", "daemon", "nobody"}:
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


_BOOTSTRAP_OS = frozenset({"ubuntu", "debian", "raspbian"})
_DOCKER_FPR = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


def build_feature_bootstrap_script(
    os_id: str,
    codename: str,
    target_user: str,
    *,
    install_rsync: bool,
    install_docker_ce: bool,
    remove_docker_conflicts: bool = True,
) -> str:
    """One-shot root script for rsync and/or official Docker CE.

    Does not call get.docker.com and does not write sudoers.
    os_id and codename are checked before they are placed in the script.
    """
    distro = (os_id or "").strip().lower()
    suite = (codename or "").strip().lower()
    user = (target_user or "").strip()
    if distro not in _BOOTSTRAP_OS:
        raise ValueError("unsupported OS for bootstrap")
    if not re.fullmatch(r"^[a-z0-9-]+$", suite or ""):
        raise ValueError("unsupported codename for bootstrap")
    if not _USER_RE.fullmatch(user or ""):
        raise ValueError("SSH username is not safe to place in a bootstrap script")
    if not install_rsync and not install_docker_ce:
        raise ValueError("nothing to install")

    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        f"OS_ID={shlex.quote(distro)}",
        f"CODENAME={shlex.quote(suite)}",
        f"TARGET_USER={shlex.quote(user)}",
        "",
    ]
    if install_rsync:
        lines.append(
            "DEBIAN_FRONTEND=noninteractive apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y rsync"
        )
        lines.append("")
    if install_docker_ce:
        # curl is not guaranteed on a minimal image. Install it before the suite probe.
        lines.append(
            "DEBIAN_FRONTEND=noninteractive apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg"
        )
        lines.extend(
            [
                'if ! curl -fsSLI "https://download.docker.com/linux/${OS_ID}/dists/${CODENAME}/Release" >/dev/null; then',
                '  echo "Docker has not published a suite for ${CODENAME} on download.docker.com" >&2',
                "  exit 1",
                "fi",
            ]
        )
        if remove_docker_conflicts:
            # docker-compose-v2 is Ubuntu-only. apt-get remove of a name that is
            # not installed exits 100, so only remove packages dpkg already has.
            lines.extend(
                [
                    "_conflict_pkgs=",
                    "for _pkg in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do",
                    "  _st=$(dpkg-query -W -f='${db:Status-Abbrev}' \"$_pkg\" 2>/dev/null || true)",
                    '  case "$_st" in',
                    '    ii*) _conflict_pkgs="${_conflict_pkgs:+${_conflict_pkgs} }$_pkg" ;;',
                    "  esac",
                    "done",
                    'if [ -n "${_conflict_pkgs:-}" ]; then',
                    "  DEBIAN_FRONTEND=noninteractive apt-get remove -y $_conflict_pkgs",
                    "fi",
                ]
            )
        lines.extend(
            [
                "install -m 0755 -d /etc/apt/keyrings",
                'curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" -o /etc/apt/keyrings/docker.asc.tmp',
                "chmod a+r /etc/apt/keyrings/docker.asc.tmp",
                "GNUPGHOME=$(mktemp -d)",
                "export GNUPGHOME",
                "FPR=$(gpg --batch --show-keys --with-colons /etc/apt/keyrings/docker.asc.tmp | awk -F: '$1==\"fpr\" {print $10; exit}') || {",
                '  rm -rf "$GNUPGHOME"',
                "  unset GNUPGHOME",
                '  echo "Docker GPG fingerprint mismatch" >&2',
                "  rm -f /etc/apt/keyrings/docker.asc.tmp",
                "  exit 1",
                "}",
                'rm -rf "$GNUPGHOME"',
                "unset GNUPGHOME",
                f'if [ "$FPR" != "{_DOCKER_FPR}" ]; then',
                '  rm -rf "${GNUPGHOME:-}"',
                "  unset GNUPGHOME",
                '  echo "Docker GPG fingerprint mismatch" >&2',
                "  rm -f /etc/apt/keyrings/docker.asc.tmp",
                "  exit 1",
                "fi",
                "mv /etc/apt/keyrings/docker.asc.tmp /etc/apt/keyrings/docker.asc",
                "chmod a+r /etc/apt/keyrings/docker.asc",
                "cat > /etc/apt/sources.list.d/docker.sources <<EOF",
                "Types: deb",
                "URIs: https://download.docker.com/linux/${OS_ID}",
                "Suites: ${CODENAME}",
                "Components: stable",
                "Architectures: $(dpkg --print-architecture)",
                "Signed-By: /etc/apt/keyrings/docker.asc",
                "EOF",
                "DEBIAN_FRONTEND=noninteractive apt-get update && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
                'groupadd -f docker && usermod -aG docker "$TARGET_USER"',
                "systemctl enable --now docker || true",
                "",
            ]
        )
    lines.append('echo "PiHerder bootstrap finished"')
    return "\n".join(lines) + "\n"


def build_host_bootstrap_script(
    server,
    os_id: str,
    codename: str,
    target_user: Optional[str] = None,
    *,
    deps: Optional[dict] = None,
) -> str:
    """Script for one server. Feature flags on the row decide rsync and Docker CE.

    install_rsync is true only when backups are on and the preflight says rsync
    is missing. install_docker is true only when the Docker feature is on and
    the preflight says Docker CE is missing. A caller cannot pass those flags.
    """
    from . import host_deps as host_deps_svc

    data = deps if deps is not None else (host_deps_svc.parse_host_deps(server) or {})
    boot = data.get("bootstrap") or {}
    is_haos = "haos" in (getattr(server, "os_type", None) or "").lower()
    install_rsync = (
        bool(getattr(server, "backup_enabled", False))
        and bool(boot.get("install_rsync"))
        and not is_haos
    )
    install_docker = (
        bool(getattr(server, "container_patch_enabled", False))
        and bool(boot.get("install_docker_ce"))
        and not is_haos
    )
    user = target_user if target_user is not None else (getattr(server, "ssh_username", None) or "")
    return build_feature_bootstrap_script(
        os_id,
        codename,
        user,
        install_rsync=install_rsync,
        install_docker_ce=install_docker,
        remove_docker_conflicts=bool(install_docker),
    )


def preview_feature_bootstrap_script(server: Server) -> str:
    """Script for the copy box. Empty when this host has nothing to install."""
    from . import host_deps as host_deps_svc

    data = host_deps_svc.parse_host_deps(server) or {}
    os_meta = data.get("os") or {}
    if "haos" in (getattr(server, "os_type", None) or "").lower():
        return ""
    if not os_meta.get("supported_os"):
        return ""
    try:
        return build_host_bootstrap_script(
            server,
            str(os_meta.get("os_id") or ""),
            str(os_meta.get("codename") or ""),
            deps=data,
        )
    except ValueError:
        return ""


def _redact_secret(text: str, secret: Optional[str]) -> str:
    if not secret or not text:
        return text or ""
    return text.replace(secret, "")


def _exec_elevated(client, script: str, sudo_password: Optional[str], timeout: int = 300):
    """Run script as root. A one-time password is written to sudo stdin only."""
    status, out, _err = ssh_service.run_command(client, "id -u", timeout=15)
    is_root = status == 0 and (out or "").strip() == "0"
    secret = (sudo_password or "").strip() or None
    if is_root:
        cmd = "bash -s"
        secret = None
    elif secret:
        cmd = "sudo -H -S -p '' bash -s"
    else:
        cmd = "sudo -H -n bash -s"
        secret = None
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    if secret:
        stdin.write(secret + "\n")
    payload = script if script.endswith("\n") else script + "\n"
    stdin.write(payload)
    try:
        stdin.flush()
    except Exception:
        pass
    try:
        stdin.channel.shutdown_write()
    except Exception:
        pass
    out_s = stdout.read()
    err_s = stderr.read()
    if isinstance(out_s, bytes):
        out_s = out_s.decode(errors="replace")
    if isinstance(err_s, bytes):
        err_s = err_s.decode(errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, _redact_secret(out_s or "", sudo_password), _redact_secret(err_s or "", sudo_password)


def _verify_docker_fresh(server: Server) -> tuple[bool, str]:
    """New SSH session so the docker group from usermod is visible."""
    client = ssh_service.get_ssh_client(server)
    try:
        info_rc, _info_out, info_err = ssh_service.run_command(client, "docker info", timeout=20)
        comp_rc, comp_out, comp_err = ssh_service.run_command(
            client, "docker compose version", timeout=20
        )
    finally:
        try:
            client.close()
        except Exception:
            pass
    if info_rc != 0 or comp_rc != 0:
        return False, (info_err or comp_err or "docker check failed")[:240]
    return True, (comp_out or "docker compose ok").strip()[:160]


def bootstrap_host_tools(
    server: Server,
    session,
    *,
    confirm_docker_bootstrap: bool = False,
    sudo_password: Optional[str] = None,
) -> OnboardingResult:
    """Install missing rsync and/or Docker CE for features already enabled on the server.

    install_rsync and install_docker_ce come from the server flags and the host check.
    Callers cannot turn Docker on by passing a form field.
    """
    from .demo import demo_mode
    from . import host_deps as host_deps_svc

    if demo_mode():
        return OnboardingResult(ok=False, message="Bootstrap is disabled in demo mode")
    try:
        deps = host_deps_svc.check_and_persist(session, server)
    except Exception as exc:
        return OnboardingResult(ok=False, message=f"Host check failed: {exc}"[:200])

    os_meta = (deps or {}).get("os") or {}
    boot = (deps or {}).get("bootstrap") or {}
    is_haos = "haos" in (getattr(server, "os_type", None) or "").lower()
    install_rsync = (
        bool(getattr(server, "backup_enabled", False))
        and bool(boot.get("install_rsync"))
        and not is_haos
    )
    install_docker = (
        bool(getattr(server, "container_patch_enabled", False))
        and bool(boot.get("install_docker_ce"))
        and not is_haos
    )
    os_id = str(os_meta.get("os_id") or "")
    codename = str(os_meta.get("codename") or "")
    if not install_rsync and not install_docker:
        return OnboardingResult(
            ok=False,
            message=(
                "Nothing to bootstrap. Enable Backups or Docker, then re-check. "
                "Only missing tools for those features are installed."
            ),
            details={"os_id": os_id, "codename": codename, "features": []},
        )
    if install_docker and not confirm_docker_bootstrap:
        extra = ""
        conflicts = list(boot.get("conflicting_packages") or [])
        running = int(boot.get("running_containers") or 0)
        if conflicts or running:
            extra = (
                " Distro Docker packages will be replaced with official Docker CE. "
                "apt-get remove is used. /var/lib/docker is left in place."
            )
        return OnboardingResult(
            ok=False,
            message=(
                f"Confirm Docker bootstrap before adding {server.ssh_username} to the docker group. "
                "That group grants root-equivalent container access."
                + extra
            ),
            details={
                "need_confirm": True,
                "os_id": os_id,
                "codename": codename,
                "features": [],
            },
        )
    if not os_meta.get("supported_os"):
        return OnboardingResult(
            ok=False,
            message="This OS is not supported for bootstrap. Expected Ubuntu, Debian, or Raspbian.",
            details={"os_id": os_id, "codename": codename, "features": []},
        )
    try:
        script = build_host_bootstrap_script(server, os_id, codename, deps=deps)
    except ValueError as exc:
        return OnboardingResult(ok=False, message=str(exc)[:200])

    client = None
    try:
        client = ssh_service.get_ssh_client(server)
        rc, out, err = _exec_elevated(client, script, sudo_password)
    except Exception as exc:
        return OnboardingResult(ok=False, message=f"Bootstrap SSH failed: {exc}"[:200])
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    if rc != 0:
        detail = _redact_secret((err or out or "").strip(), sudo_password)[:240]
        return OnboardingResult(
            ok=False,
            message=f"Bootstrap failed (exit {rc}). {detail}".strip(),
            details={"os_id": os_id, "codename": codename, "features": []},
        )

    features: list[str] = []
    if install_rsync:
        features.append("rsync")
    if install_docker:
        features.append("docker_ce")
        verified, verify_msg = _verify_docker_fresh(server)
    else:
        verified, verify_msg = True, ""
    try:
        host_deps_svc.check_and_persist(session, server)
    except Exception:
        pass
    if install_docker and not verified:
        return OnboardingResult(
            ok=False,
            message=(
                "Bootstrap ran but a fresh SSH session could not use docker without sudo. "
                + _redact_secret(verify_msg, sudo_password)
            )[:300],
            details={"os_id": os_id, "codename": codename, "features": features},
        )
    return OnboardingResult(
        ok=True,
        message="Bootstrap finished",
        details={"os_id": os_id, "codename": codename, "features": features},
    )


def run_host_bootstrap(
    server: Server,
    session,
    *,
    confirm_docker_bootstrap: bool = False,
    sudo_password: Optional[str] = None,
) -> OnboardingResult:
    """Install missing tools for features already enabled on this server.

    Same path as bootstrap_host_tools. Docker is not installed when
    container_patch_enabled is false, and rsync is not installed when
    backup_enabled is false.
    """
    return bootstrap_host_tools(
        server,
        session,
        confirm_docker_bootstrap=confirm_docker_bootstrap,
        sudo_password=sudo_password,
    )
