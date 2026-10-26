"""Remote host dependency probes for enabled PiHerder features.

After SSH works, check tools needed for backups / Docker / OS patch so operators
see failures before the first job. No remote package install — report + hints only.
"""
from __future__ import annotations

import json
import logging
import re
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
    "docker_ce": (
        "Official Docker CE is not installed. SSH access can bootstrap docker-ce "
        "from download.docker.com after you confirm. This does not use get.docker.com."
    ),
    "docker_compose": (
        "The Compose v2 plugin is missing. Bootstrap installs docker-compose-plugin "
        "(`docker compose version`)."
    ),
    "docker_conflicts": (
        "Distro Docker packages are installed. Bootstrap removes them with apt-get remove "
        "(not purge) and leaves /var/lib/docker in place, then installs Docker CE."
    ),
    "docker_group": (
        "The SSH user is not in the docker group. Bootstrap runs usermod -aG docker. "
        "That group can start containers as root."
    ),
}

_OS_IDS = frozenset({"ubuntu", "debian", "raspbian"})
_ARCHS = frozenset({"amd64", "arm64", "armhf"})
_TOKEN = re.compile(r"^[a-z0-9-]+$")
DOCKER_REPO_URLS = {
    "ubuntu": "https://download.docker.com/linux/ubuntu",
    "debian": "https://download.docker.com/linux/debian",
    "raspbian": "https://download.docker.com/linux/raspbian",
}
_CONFLICT_PACKAGES = (
    "docker.io",
    "docker-compose",
    "docker-compose-v2",
    "docker-doc",
    "podman-docker",
    "containerd",
    "runc",
)


def parse_os_release(text: str) -> dict[str, str]:
    """KEY=value lines from /etc/os-release. Quotes are stripped."""
    data: dict[str, str] = {}
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data


def classify_os(release_text: str, arch_text: str) -> dict[str, Any]:
    """Map a probed OS to an official Docker apt repo, or mark it unsupported."""
    parsed = parse_os_release(release_text)
    raw_id = (parsed.get("ID") or "").strip().lower()
    raw_code = (parsed.get("VERSION_CODENAME") or "").strip().lower()
    pretty = (parsed.get("PRETTY_NAME") or "").replace("\n", " ").strip()[:160]
    os_id = raw_id if _TOKEN.fullmatch(raw_id or "") else ""
    codename = raw_code if _TOKEN.fullmatch(raw_code or "") else ""
    arch_line = ""
    if arch_text:
        arch_line = arch_text.strip().splitlines()[0].strip().lower()
    arch = arch_line if arch_line in _ARCHS else ""
    supported = os_id in _OS_IDS and bool(codename)
    return {
        "os_id": os_id,
        "codename": codename,
        "pretty_name": pretty,
        "arch": arch,
        "docker_repo_url": DOCKER_REPO_URLS.get(os_id, "") if supported else "",
        "supported_os": supported,
    }


def parse_installed_conflicts(text: str) -> list[str]:
    """Package names whose dpkg status starts with ii."""
    found: list[str] = []
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        status = parts[1].strip()
        if name and status.startswith("ii"):
            found.append(name)
    return found


def _empty_os() -> dict[str, Any]:
    return classify_os("", "")


def _empty_bootstrap() -> dict[str, Any]:
    return {
        "install_rsync": False,
        "install_docker_ce": False,
        "remove_docker_conflicts": False,
        "conflicting_packages": [],
        "running_containers": 0,
        "needs_docker_group": False,
        "bootstrap_available": False,
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
    os_meta = _empty_os()
    conflicts: list[str] = []
    running_containers = 0
    needs_docker_group = False
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
            return _deps_result(
                checked_at,
                "fail",
                checks,
                backup_on,
                docker_on,
                os_on,
                os_meta,
                _empty_bootstrap(),
            )
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
        return _deps_result(
            checked_at,
            "fail",
            checks,
            backup_on,
            docker_on,
            os_on,
            os_meta,
            _empty_bootstrap(),
        )

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
        rel_ok, rel_out = _cmd_ok(client, "cat /etc/os-release", timeout=10)
        arch_ok, arch_out = _cmd_ok(client, "dpkg --print-architecture", timeout=8)
        os_meta = classify_os(rel_out if rel_ok else "", arch_out if arch_ok else "")
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
                conflicts, running_containers, needs_docker_group = _probe_docker_bootstrap(
                    client, checks, is_root=is_root
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
    rsync_missing = any(c.get("id") == "rsync" and c.get("status") == "fail" for c in checks)
    ce_missing = any(c.get("id") == "docker_ce" and c.get("status") == "fail" for c in checks)
    compose_missing = any(
        c.get("id") == "docker_compose" and c.get("status") == "fail" for c in checks
    )
    install_rsync = bool(backup_on) and rsync_missing and not is_haos and bool(os_meta.get("supported_os"))
    install_docker = (
        bool(docker_on)
        and not is_haos
        and bool(os_meta.get("supported_os"))
        and (ce_missing or compose_missing or needs_docker_group)
    )
    bootstrap = {
        "install_rsync": install_rsync,
        "install_docker_ce": install_docker,
        "remove_docker_conflicts": bool(conflicts),
        "conflicting_packages": conflicts,
        "running_containers": running_containers,
        "needs_docker_group": needs_docker_group,
        "bootstrap_available": bool(os_meta.get("supported_os"))
        and not is_haos
        and (install_rsync or install_docker),
    }
    return _deps_result(
        checked_at,
        overall,
        checks,
        backup_on,
        docker_on,
        os_on,
        os_meta,
        bootstrap,
    )


def _deps_result(
    checked_at: str,
    overall: str,
    checks: list[dict[str, Any]],
    backup_on: bool,
    docker_on: bool,
    os_on: bool,
    os_meta: dict[str, Any],
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    return {
        "checked_at": checked_at,
        "overall": overall,
        "checks": checks,
        "features": {
            "backup": backup_on,
            "docker": docker_on,
            "os_patch": os_on,
        },
        "os": os_meta,
        "bootstrap": bootstrap,
    }


def _probe_docker_bootstrap(
    client, checks: list[dict[str, Any]], *, is_root: bool
) -> tuple[list[str], int, bool]:
    """Conflicting packages, Docker CE, Compose v2, group, and running containers."""
    pkg_list = " ".join(_CONFLICT_PACKAGES)
    _ok, conflict_out = _cmd_ok(
        client,
        "dpkg-query -W -f='${Package}\\t${db:Status-Abbrev}\\n' "
        f"{pkg_list} 2>/dev/null",
        timeout=15,
    )
    conflicts = parse_installed_conflicts(conflict_out)
    if conflicts:
        checks.append(
            _check(
                "docker_conflicts",
                "Distro Docker packages",
                "warn",
                required=False,
                message="installed: " + ", ".join(conflicts),
                hint=HINTS["docker_conflicts"],
            )
        )
    ce_ok, ce_out = _cmd_ok(
        client,
        "dpkg-query -W -f='${db:Status-Abbrev}' docker-ce",
        timeout=12,
    )
    if ce_ok and (ce_out or "").strip().startswith("ii"):
        checks.append(
            _check(
                "docker_ce",
                "Docker CE",
                "ok",
                required=True,
                message="docker-ce installed",
            )
        )
    else:
        checks.append(
            _check(
                "docker_ce",
                "Docker CE",
                "fail",
                required=True,
                message="docker-ce is not installed",
                hint=HINTS["docker_ce"],
            )
        )
    compose_ok, compose_out = _cmd_ok(client, "docker compose version", timeout=15)
    if compose_ok and (compose_out or "").strip():
        checks.append(
            _check(
                "docker_compose",
                "Docker Compose plugin",
                "ok",
                required=True,
                message=(compose_out or "").strip().splitlines()[0][:120],
            )
        )
    else:
        checks.append(
            _check(
                "docker_compose",
                "Docker Compose plugin",
                "fail",
                required=True,
                message="docker compose version failed",
                hint=HINTS["docker_compose"],
            )
        )
    group_ok, group_out = _cmd_ok(client, "id -nG", timeout=8)
    groups = set((group_out or "").split()) if group_ok else set()
    needs_group = (not is_root) and ("docker" not in groups)
    if is_root or not needs_group:
        checks.append(
            _check(
                "docker_group",
                "docker group",
                "ok",
                required=False,
                message="root" if is_root else "SSH user is in the docker group",
            )
        )
    else:
        checks.append(
            _check(
                "docker_group",
                "docker group",
                "warn",
                required=False,
                message="SSH user is not in the docker group",
                hint=HINTS["docker_group"],
            )
        )
    info_ok, _info = _cmd_ok(client, "docker info", timeout=20)
    if not info_ok:
        checks.append(
            _check(
                "docker_info",
                "docker info without sudo",
                "fail",
                required=True,
                message="docker info failed without sudo",
                hint=HINTS["docker_group"],
            )
        )
    else:
        checks.append(
            _check(
                "docker_info",
                "docker info without sudo",
                "ok",
                required=True,
                message="docker info ok",
            )
        )
    ps_ok, ps_out = _cmd_ok(client, "docker ps -q", timeout=15)
    running = (
        len([ln for ln in (ps_out or "").splitlines() if ln.strip()]) if ps_ok else 0
    )
    return conflicts, running, needs_group


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
