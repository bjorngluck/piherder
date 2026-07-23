"""Remote host dependency probes for enabled PiHerder features.

After SSH works, check tools needed for backups / Docker / OS patch so operators
see failures before the first job. No remote package install — report + hints only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session

from ..models import Server
from .ssh import get_ssh_client, run_command

logger = logging.getLogger(__name__)

HINTS = {
    "rsync": "Install rsync on the host, e.g. `sudo apt-get install -y rsync`.",
    "rsync_haos": (
        "Install the rsync package on HAOS (Settings → System → Repairs / packages, "
        "or via the SSH add-on). PiHerder backups use plain rsync as root."
    ),
    "rsync_sudo": (
        "Passwordless sudo for rsync is missing. Use SSH access → Least-priv user "
        "(backup privileges), or connect as root / HAOS with plain rsync."
    ),
    "docker": "Install Docker and ensure the SSH user can run `docker` (often via the docker group).",
    "docker_haos": (
        "HAOS manages containers via Supervisor — PiHerder Docker compose fleet "
        "management is not used on HAOS hosts. Disable Docker feature or use a Debian/Pi host."
    ),
    "apt": "OS patching needs apt (`apt-get`). Install or enable the OS package manager.",
    "ha_cli": (
        "Enable the Terminal & SSH add-on on Home Assistant OS and ensure `ha` is on PATH "
        "for the SSH user PiHerder uses (often root)."
    ),
    "ssh": "Fix SSH key/password under SSH access, then re-check.",
}


def parse_host_deps(server: Server) -> Optional[dict[str, Any]]:
    raw = getattr(server, "host_deps_json", None)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def overall_from_checks(checks: list[dict[str, Any]]) -> str:
    """ok if no required failures; warn if only optional issues; fail if required fail."""
    has_fail = False
    has_warn = False
    for c in checks:
        st = (c.get("status") or "").lower()
        required = bool(c.get("required"))
        if st == "fail":
            if required:
                has_fail = True
            else:
                has_warn = True
        elif st == "warn":
            has_warn = True
    if has_fail:
        return "fail"
    if has_warn:
        return "warn"
    return "ok"


def _check(
    id_: str,
    label: str,
    status: str,
    *,
    required: bool,
    message: str = "",
    hint: str = "",
) -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "status": status,
        "required": required,
        "message": (message or "")[:300],
        "hint": (hint or "")[:400],
    }


def _cmd_ok(client, cmd: str, timeout: int = 12) -> tuple[bool, str]:
    try:
        status, out, err = run_command(client, cmd, timeout=timeout)
        text = (out or err or "").strip()
        return status == 0, text
    except Exception as e:
        return False, str(e)[:200]


def run_host_deps_check(server: Server) -> dict[str, Any]:
    """SSH into host and probe tools. Does not persist — caller may save."""
    checked_at = datetime.utcnow().isoformat() + "Z"
    checks: list[dict[str, Any]] = []
    backup_on = bool(getattr(server, "backup_enabled", False))
    docker_on = bool(getattr(server, "container_patch_enabled", False))
    os_on = bool(getattr(server, "os_patch_enabled", False))
    username = (server.ssh_username or "").strip()
    is_root = username.lower() == "root"

    client = None
    try:
        client = get_ssh_client(server)
        ok, out = _cmd_ok(client, "echo piherder_ok && uname -s", timeout=15)
        if ok and "piherder_ok" in (out or ""):
            checks.append(
                _check(
                    "ssh",
                    "SSH + shell",
                    "ok",
                    required=True,
                    message=(out.splitlines()[-1] if out else "connected")[:80],
                )
            )
        else:
            checks.append(
                _check(
                    "ssh",
                    "SSH + shell",
                    "fail",
                    required=True,
                    message=out or "shell probe failed",
                    hint=HINTS["ssh"],
                )
            )
            return {
                "checked_at": checked_at,
                "overall": "fail",
                "checks": checks,
                "features": {
                    "backup": backup_on,
                    "docker": docker_on,
                    "os_patch": os_on,
                },
            }
    except Exception as e:
        checks.append(
            _check(
                "ssh",
                "SSH + shell",
                "fail",
                required=True,
                message=str(e)[:200],
                hint=HINTS["ssh"],
            )
        )
        return {
            "checked_at": checked_at,
            "overall": "fail",
            "checks": checks,
            "features": {
                "backup": backup_on,
                "docker": docker_on,
                "os_patch": os_on,
            },
        }

    assert client is not None
    is_haos = "haos" in (getattr(server, "os_type", None) or "").lower()
    if not is_haos:
        # Light fingerprint so first deps check on HAOS can switch hints before os_type is set
        try:
            from . import haos as haos_svc

            identity = haos_svc.probe_haos_identity(client)
            is_haos = bool(identity.get("is_haos"))
        except Exception:
            pass

    try:
        # --- rsync ---
        if backup_on:
            path_probe = (
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin command -v rsync"
            )
            rsync_bin = None
            for cmd in (
                path_probe,
                "command -v rsync",
                "which rsync",
                "test -x /usr/bin/rsync && echo /usr/bin/rsync",
            ):
                ok, out = _cmd_ok(client, cmd, timeout=10)
                if ok and out.strip():
                    rsync_bin = out.strip().splitlines()[0]
                    break
            if not rsync_bin:
                rsync_hint = HINTS["rsync_haos"] if is_haos else HINTS["rsync"]
                checks.append(
                    _check(
                        "rsync",
                        "rsync binary",
                        "fail",
                        required=True,
                        message="rsync not found on PATH",
                        hint=rsync_hint,
                    )
                )
                checks.append(
                    _check(
                        "rsync_path",
                        "rsync execution path",
                        "fail",
                        required=True,
                        message="skipped (no rsync)",
                        hint=rsync_hint,
                    )
                )
            else:
                checks.append(
                    _check(
                        "rsync",
                        "rsync binary",
                        "ok",
                        required=True,
                        message=rsync_bin,
                    )
                )
                # Match backup._remote_rsync_path: root → plain; else prefer sudo -n
                if is_root:
                    ok, out = _cmd_ok(client, f"{rsync_bin} --version", timeout=12)
                    if ok:
                        checks.append(
                            _check(
                                "rsync_path",
                                "rsync execution path",
                                "ok",
                                required=True,
                                message="plain rsync (root)",
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "rsync_path",
                                "rsync execution path",
                                "fail",
                                required=True,
                                message=out or "rsync --version failed",
                                hint=HINTS["rsync"],
                            )
                        )
                else:
                    sudo_ok = False
                    for probe in (
                        "sudo -n /usr/bin/rsync --version",
                        "sudo -n rsync --version",
                    ):
                        ok, _ = _cmd_ok(client, probe, timeout=12)
                        if ok:
                            sudo_ok = True
                            break
                    if sudo_ok:
                        checks.append(
                            _check(
                                "rsync_path",
                                "rsync execution path",
                                "ok",
                                required=True,
                                message="sudo -n rsync",
                            )
                        )
                    else:
                        # Plain rsync as non-root may still work for home paths
                        ok_plain, _ = _cmd_ok(
                            client, f"{rsync_bin} --version", timeout=12
                        )
                        if ok_plain:
                            checks.append(
                                _check(
                                    "rsync_path",
                                    "rsync execution path",
                                    "warn",
                                    required=True,
                                    message=(
                                        "plain rsync only (no passwordless sudo). "
                                        "Backups of system paths may fail."
                                    ),
                                    hint=HINTS["rsync_sudo"],
                                )
                            )
                        else:
                            checks.append(
                                _check(
                                    "rsync_path",
                                    "rsync execution path",
                                    "fail",
                                    required=True,
                                    message="cannot run rsync with or without sudo -n",
                                    hint=HINTS["rsync_sudo"],
                                )
                            )
        else:
            checks.append(
                _check(
                    "rsync",
                    "rsync binary",
                    "skip",
                    required=False,
                    message="backups feature off",
                )
            )
            checks.append(
                _check(
                    "rsync_path",
                    "rsync execution path",
                    "skip",
                    required=False,
                    message="backups feature off",
                )
            )

        # --- docker ---
        if docker_on:
            if is_haos:
                checks.append(
                    _check(
                        "docker",
                        "Docker CLI",
                        "warn",
                        required=False,
                        message="HAOS: compose fleet mgmt not used (Supervisor owns containers)",
                        hint=HINTS["docker_haos"],
                    )
                )
            else:
                ok, out = _cmd_ok(
                    client,
                    "command -v docker && docker version --format '{{.Server.Version}}' 2>/dev/null || docker info >/dev/null 2>&1",
                    timeout=20,
                )
                if ok:
                    ver = (out or "").strip().splitlines()
                    msg = ver[0] if ver else "docker ok"
                    if len(ver) > 1 and not ver[-1].startswith("/"):
                        msg = ver[-1][:80]
                    checks.append(
                        _check(
                            "docker",
                            "Docker CLI",
                            "ok",
                            required=True,
                            message=msg[:120],
                        )
                    )
                else:
                    has_bin, path_out = _cmd_ok(client, "command -v docker", timeout=8)
                    if has_bin:
                        checks.append(
                            _check(
                                "docker",
                                "Docker CLI",
                                "fail",
                                required=True,
                                message="docker present but not usable (permission or daemon?)",
                                hint=HINTS["docker"],
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "docker",
                                "Docker CLI",
                                "fail",
                                required=True,
                                message="docker not found",
                                hint=HINTS["docker"],
                            )
                        )
        else:
            checks.append(
                _check(
                    "docker",
                    "Docker CLI",
                    "skip",
                    required=False,
                    message="container feature off",
                )
            )

        # --- apt (Debian) or ha CLI (HAOS) ---
        if os_on:
            if is_haos:
                ok, out = _cmd_ok(
                    client,
                    "command -v ha 2>/dev/null || which ha 2>/dev/null",
                    timeout=10,
                )
                if ok and (out or "").strip():
                    checks.append(
                        _check(
                            "ha_cli",
                            "HA CLI (ha)",
                            "ok",
                            required=True,
                            message=(out or "").strip().splitlines()[0][:120],
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "ha_cli",
                            "HA CLI (ha)",
                            "fail",
                            required=True,
                            message="ha not found on PATH",
                            hint=HINTS["ha_cli"],
                        )
                    )
                checks.append(
                    _check(
                        "apt",
                        "apt package manager",
                        "skip",
                        required=False,
                        message="HAOS uses ha CLI for updates (not apt)",
                    )
                )
            else:
                ok, out = _cmd_ok(
                    client,
                    "command -v apt-get || command -v apt",
                    timeout=10,
                )
                if ok and out.strip():
                    checks.append(
                        _check(
                            "apt",
                            "apt package manager",
                            "ok",
                            required=True,
                            message=out.strip().splitlines()[0],
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "apt",
                            "apt package manager",
                            "fail",
                            required=True,
                            message="apt-get / apt not found",
                            hint=HINTS["apt"],
                        )
                    )
        else:
            checks.append(
                _check(
                    "apt",
                    "apt package manager",
                    "skip",
                    required=False,
                    message="OS patch feature off",
                )
            )
    finally:
        try:
            client.close()
        except Exception:
            pass

    overall = overall_from_checks(checks)
    # Required warn counts as overall warn (already handled); required fail → fail
    # For rsync_path warn with required=True, overall_from_checks treats warn as has_warn only
    # unless status is fail. Good.

    return {
        "checked_at": checked_at,
        "overall": overall,
        "checks": checks,
        "features": {
            "backup": backup_on,
            "docker": docker_on,
            "os_patch": os_on,
        },
    }


def persist_host_deps(session: Session, server: Server, result: dict[str, Any]) -> Server:
    """Write check snapshot onto server row."""
    server.host_deps_json = json.dumps(result)
    # Store naive UTC for DB consistency with other datetime fields
    try:
        ts = result.get("checked_at") or ""
        if ts.endswith("Z"):
            ts = ts[:-1]
        server.host_deps_checked_at = datetime.fromisoformat(ts) if ts else datetime.utcnow()
    except Exception:
        server.host_deps_checked_at = datetime.utcnow()
    session.add(server)
    return server


def check_and_persist(session: Session, server: Server) -> dict[str, Any]:
    result = run_host_deps_check(server)
    persist_host_deps(session, server, result)
    session.commit()
    session.refresh(server)
    return result
