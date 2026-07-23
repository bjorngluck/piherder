"""OS patching service — apt sequence with live log streaming + clean audit payloads."""
from __future__ import annotations

import time
from datetime import datetime

from ..models import Server
from ..services.ssh import get_ssh_client, run_command

_os_patch_progress: dict = {}

# Full buffer kept in-memory for debugging; UI only gets a short tail
_MAX_LOG_LINES = 300
_PROGRESS_UI_LINES = 40  # focus the live modal on recent output
_HEARTBEAT_SECS = 12.0

_ALLOWED_OS_PATCH_STEPS = frozenset({"update", "upgrade", "full-upgrade", "autoremove"})

# Prefix env assignments (shell syntax) — must come *before* any real binary.
# Keep it simple: no stdbuf/bash -lc wrappers (those caused rc=127 on targets).
_APT_ENV = (
    "DEBIAN_FRONTEND=noninteractive "
    "NEEDRESTART_MODE=a "
    "APT_LISTCHANGES_FRONTEND=none "
)
_APT_OPTS = (
    '-o Dpkg::Options::="--force-confold" '
    '-o Dpkg::Options::="--force-confdef" '
    "-o APT::Color=0 "
    "-o Dpkg::Progress-Fancy=0 "
)


def get_os_patch_progress(hostname: str) -> dict:
    p = _os_patch_progress.get(hostname)
    if not p:
        return {
            "current": None,
            "log_lines": [],
            "done": False,
            "finished_ok": None,
            "total_lines": 0,
            "tail": True,
        }
    all_lines = list(p.get("log_lines") or [])
    total = len(all_lines)
    tail = all_lines[-_PROGRESS_UI_LINES:]
    # Hint when older lines are omitted so the modal stays scannable
    if total > _PROGRESS_UI_LINES:
        tail = [f"… ({total - _PROGRESS_UI_LINES} earlier lines omitted)"] + tail
    return {
        "current": p.get("current"),
        "log_lines": tail,
        "done": bool(p.get("done")),
        "finished_ok": p.get("finished_ok"),
        "total_lines": total,
        "tail": True,
    }


def _ensure_progress(hostname: str) -> dict:
    if hostname not in _os_patch_progress:
        _os_patch_progress[hostname] = {
            "current": None,
            "log_lines": [],
            "done": False,
            "finished_ok": None,
            "last_activity": time.time(),
        }
    return _os_patch_progress[hostname]


def _append_os_log(hostname: str, text: str, *, replace_progress: bool = False):
    """Append log text. Carriage-return chunks update the last progress line in place."""
    p = _ensure_progress(hostname)
    lines = p["log_lines"]
    # Normalize CRLF
    text = text.replace("\r\n", "\n")

    # Progress-style updates (apt status / bars): keep one live trailing line
    if replace_progress or ("\r" in text and "\n" not in text):
        chunk = text.replace("\r", " ").strip()
        if not chunk:
            return
        if lines and lines[-1].startswith("… "):
            lines[-1] = f"… {chunk[:200]}"
        else:
            lines.append(f"… {chunk[:200]}")
        p["last_activity"] = time.time()
        if len(lines) > _MAX_LOG_LINES:
            p["log_lines"] = lines[-_MAX_LOG_LINES:]
        return

    for part in text.split("\n"):
        # leftover \r progress within a multi-line chunk
        if "\r" in part:
            part = part.split("\r")[-1]
        ln = part.strip()
        if not ln:
            continue
        # Drop stale in-place progress line when a real line arrives
        if lines and lines[-1].startswith("… "):
            lines.pop()
        lines.append(ln)
        p["last_activity"] = time.time()

    if len(lines) > _MAX_LOG_LINES:
        p["log_lines"] = lines[-_MAX_LOG_LINES:]


def _init_progress(hostname: str):
    _os_patch_progress[hostname] = {
        "current": None,
        "log_lines": [],
        "done": False,
        "finished_ok": None,
        "last_activity": time.time(),
    }


def init_os_patch_progress(hostname: str, initial_msg: str = "starting"):
    """Public helper for pre-initializing so live UI can attach before the long work starts."""
    _os_patch_progress[hostname] = {
        "current": "starting",
        "log_lines": [f"[os] {initial_msg}"],
        "done": False,
        "finished_ok": None,
        "last_activity": time.time(),
    }


def mark_os_patch_done(hostname: str, finished_ok: bool | None = None):
    """Keep final logs for the UI; clear after a short grace (caller may pop later)."""
    p = _ensure_progress(hostname)
    p["current"] = None
    p["done"] = True
    p["finished_ok"] = finished_ok
    p["last_activity"] = time.time()


_AUDIT_LOG_TAIL = 50  # lines persisted on AuditLog for post-hoc review


def get_os_patch_log_tail(hostname: str, n: int = _AUDIT_LOG_TAIL) -> list[str]:
    """Return recent in-memory apt log lines (for audit payload / debugging)."""
    p = _os_patch_progress.get(hostname) or {}
    lines = list(p.get("log_lines") or [])
    if not lines:
        return []
    return lines[-max(1, int(n)) :]


def attach_audit_fields(
    res: dict | None,
    hostname: str,
    post_check: dict | None = None,
) -> dict:
    """Enrich OS patch result for AuditLog.output_snippet (summary + log tail + post-check)."""
    out: dict = dict(res) if isinstance(res, dict) else {}
    if hostname and not out.get("server"):
        out["server"] = hostname

    tail = get_os_patch_log_tail(hostname)
    if tail:
        out["log_tail"] = tail

    if post_check and isinstance(post_check, dict) and not post_check.get("error"):
        pc = {
            "actionable_count": post_check.get(
                "actionable_count", post_check.get("updates_count")
            ),
            "phased_count": int(post_check.get("phased_count") or 0),
            "reboot_pending": bool(post_check.get("reboot_pending")),
            "updates_count": post_check.get("updates_count"),
        }
        out["post_check"] = pc
        bits: list[str] = []
        ac = pc.get("actionable_count")
        if ac is not None:
            bits.append(f"{ac} ready after")
        if pc.get("phased_count"):
            bits.append(f"{pc['phased_count']} phased")
        if pc.get("reboot_pending"):
            bits.append("reboot pending")
        if bits:
            base = (out.get("summary") or "").strip()
            extra = " · ".join(bits)
            out["summary"] = f"{base} · {extra}" if base else extra

    if not (out.get("summary") or "").strip():
        out["summary"] = summarize_os_patch_result(out)

    return out


def clear_os_patch_progress(hostname: str):
    _os_patch_progress.pop(hostname, None)


def normalize_os_patch_steps(selected_steps: list[str] | None) -> list[str]:
    """Filter to known steps; upgrade and full-upgrade are mutually exclusive (prefer upgrade).

    Canonical order: update → upgrade|full-upgrade → autoremove.
    """
    if selected_steps is None:
        return ["update", "upgrade", "autoremove"]

    chosen = [s for s in selected_steps if s in _ALLOWED_OS_PATCH_STEPS]
    if "upgrade" in chosen and "full-upgrade" in chosen:
        chosen = [s for s in chosen if s != "full-upgrade"]

    order = ("update", "upgrade", "full-upgrade", "autoremove")
    return [s for s in order if s in chosen]


def summarize_os_patch_result(res: dict) -> str:
    """Short human summary for audit list / webhooks."""
    if not res:
        return "OS patch"
    if res.get("error"):
        return f"Failed: {str(res['error'])[:120]}"
    parts = []
    for r in res.get("results") or []:
        step = r.get("step") or "?"
        if r.get("error"):
            parts.append(f"{step} ✗")
        elif int(r.get("rc", 1)) != 0:
            parts.append(f"{step} rc={r.get('rc')}")
        else:
            parts.append(f"{step} ✓")
    summary = " · ".join(parts) if parts else "no steps"
    if res.get("needs_reboot"):
        summary += " · reboot needed"
    return summary


def os_patch_succeeded(res: dict) -> bool:
    if not res or res.get("error"):
        return False
    results = res.get("results") or []
    if not results:
        return False
    for r in results:
        if r.get("error"):
            return False
        if int(r.get("rc", 1)) != 0:
            return False
    return True


def _stream_ssh_command(client, hostname: str, step_name: str, cmd: str, timeout: int = 900) -> int:
    """Run remote cmd with live log streaming; return exit status.

    Runs the full shell command string as-is (env prefix + sudo apt-get …).
    Matches the simple pattern that previously worked on Pi hosts.
    """
    # get_pty for live progress; command must already include env + sudo apt-get
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    channel = stdout.channel
    buf = ""
    last_heartbeat = time.time()
    started = time.time()

    while True:
        got = False
        now = time.time()
        if channel.recv_ready():
            data = channel.recv(8192).decode(errors="replace")
            if data:
                got = True
                last_heartbeat = now
                if "\n" not in data and "\r" in data:
                    _append_os_log(hostname, data, replace_progress=True)
                else:
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        if "\r" in line:
                            line = line.split("\r")[-1]
                        if line.strip():
                            _append_os_log(hostname, line)
                    if buf and "\r" in buf and "\n" not in buf:
                        _append_os_log(hostname, buf, replace_progress=True)
                        buf = ""
        if channel.recv_stderr_ready():
            edata = channel.recv_stderr(8192).decode(errors="replace")
            if edata:
                got = True
                last_heartbeat = now
                _append_os_log(hostname, edata)

        if channel.exit_status_ready() and not (channel.recv_ready() or channel.recv_stderr_ready()):
            try:
                rem = channel.recv(8192).decode(errors="replace")
                if rem:
                    _append_os_log(hostname, rem)
            except Exception:
                pass
            break

        if now - last_heartbeat >= _HEARTBEAT_SECS:
            elapsed = int(now - started)
            _append_os_log(
                hostname,
                f"[{step_name}] still running… {elapsed}s elapsed (waiting for apt/dpkg output)",
            )
            last_heartbeat = now

        if not got:
            time.sleep(0.08)
        if now - started > timeout:
            _append_os_log(hostname, f"[{step_name}] ERROR: timed out after {timeout}s")
            try:
                channel.close()
            except Exception:
                pass
            return 124

    if buf and buf.strip():
        _append_os_log(hostname, buf)

    rc = channel.recv_exit_status()
    if rc == 127:
        _append_os_log(
            hostname,
            f"[{step_name}] hint: exit 127 usually means command-not-found "
            f"(path/shell). Command was: {cmd[:180]}",
        )
    return rc


def run_os_patch(server: Server, selected_steps: list[str] = None) -> dict:
    """Run selected patch steps over SSH.

    - **HAOS**: ``ha supervisor|core|os update`` (see ``haos.run_haos_update``)
    - **Debian/Ubuntu**: apt update → upgrade|full-upgrade → autoremove
    """
    from . import haos as haos_svc

    selected_steps = normalize_os_patch_steps(selected_steps)
    hostname = server.hostname

    if hostname not in _os_patch_progress:
        _init_progress(hostname)
    p = _os_patch_progress[hostname]
    p["done"] = False
    p["finished_ok"] = None
    seed = (p.get("log_lines") or [])[-5:]
    p["log_lines"] = seed
    p["last_activity"] = time.time()

    # HAOS path (marked or will be detected inside apply)
    if haos_svc.is_haos_server(server):
        _append_os_log(hostname, "[os] HAOS host — using ha CLI (not apt)")
        res = haos_svc.run_haos_update(
            server,
            selected_steps=selected_steps,
            hostname=hostname,
            stream_log=_append_os_log,
            stream_cmd=_stream_ssh_command,
        )
        res["summary"] = res.get("summary") or summarize_os_patch_result(res)
        ok = os_patch_succeeded(res)
        p = _ensure_progress(hostname)
        p["current"] = "rechecking"
        p["finished_ok"] = ok
        p["done"] = False
        _append_os_log(hostname, f"[os] HA update steps finished: {res['summary']}")
        _append_os_log(hostname, "[os] rechecking update counts…")
        return res

    client = get_ssh_client(server)
    # Opportunistic HAOS detect when still marked debian/linux
    try:
        identity = haos_svc.probe_haos_identity(client)
        if identity.get("is_haos"):
            try:
                client.close()
            except Exception:
                pass
            _append_os_log(
                hostname,
                "[os] Detected HAOS via SSH — switching to ha CLI updates",
            )
            res = haos_svc.run_haos_update(
                server,
                selected_steps=selected_steps,
                hostname=hostname,
                stream_log=_append_os_log,
                stream_cmd=_stream_ssh_command,
            )
            res["summary"] = res.get("summary") or summarize_os_patch_result(res)
            res["auto_mark_haos"] = True
            res["detected_os_type"] = "haos"
            ok = os_patch_succeeded(res)
            p = _ensure_progress(hostname)
            p["current"] = "rechecking"
            p["finished_ok"] = ok
            p["done"] = False
            _append_os_log(hostname, f"[os] HA update steps finished: {res['summary']}")
            _append_os_log(hostname, "[os] rechecking update counts…")
            return res
    except Exception as e:
        _append_os_log(hostname, f"[os] HAOS probe skipped: {e}")

    # Full paths; plain `apt` (not apt-get) — matches sudoers + earlier successful jobs
    step_cmds = {
        "update": f"{_APT_ENV}sudo /usr/bin/apt update",
        "upgrade": f"{_APT_ENV}sudo /usr/bin/apt upgrade {_APT_OPTS}-y",
        "full-upgrade": f"{_APT_ENV}sudo /usr/bin/apt full-upgrade {_APT_OPTS}-y",
        "autoremove": f"{_APT_ENV}sudo /usr/bin/apt autoremove -y",
    }
    steps = [(name, cmd) for name, cmd in step_cmds.items() if name in selected_steps]

    results = []
    for name, cmd in steps:
        p["current"] = name
        _append_os_log(hostname, f"[{name}] $ {cmd.strip()}")
        try:
            status = _stream_ssh_command(client, hostname, name, cmd, timeout=900)
            results.append({"step": name, "rc": status})
            _append_os_log(hostname, f"[{name}] exit={status}")
        except Exception as e:
            results.append({"step": name, "error": str(e)})
            _append_os_log(hostname, f"[{name}] ERROR: {e}")

    # Check reboot-required
    needs_reboot = False
    try:
        status, out, _ = run_command(
            client,
            "test -f /var/run/reboot-required && echo REBOOT || echo no-reboot",
            timeout=10,
        )
        needs_reboot = "REBOOT" in (out or "")
        _append_os_log(
            hostname,
            f"[reboot-check] {'REBOOT REQUIRED' if needs_reboot else 'no reboot needed'}",
        )
    except Exception as e:
        _append_os_log(hostname, f"[reboot-check] ERROR: {e}")

    try:
        client.close()
    except Exception:
        pass

    # Detect Ubuntu phased deferral in live logs (upgrade ran but installed nothing)
    log_blob = "\n".join((_os_patch_progress.get(hostname) or {}).get("log_lines") or [])
    phased_deferred = "due to phasing" in log_blob.lower() or "not upgrading yet due to phasing" in log_blob.lower()

    res = {
        "server": hostname,
        "backend": "apt",
        "steps": list(selected_steps),
        "results": results,
        "needs_reboot": needs_reboot,
        "phased_deferred": phased_deferred,
        "summary": "",
        "finished_at": datetime.utcnow().isoformat() + "Z",
    }
    res["summary"] = summarize_os_patch_result(res)
    if phased_deferred and "phased" not in res["summary"].lower():
        res["summary"] += " · some packages deferred (Ubuntu phasing)"
    ok = os_patch_succeeded(res)
    # Do NOT mark done yet — job runner still rechecks update counts.
    p = _ensure_progress(hostname)
    p["current"] = "rechecking"
    p["finished_ok"] = ok
    p["done"] = False
    _append_os_log(hostname, f"[os] apt steps finished: {res['summary']}")
    if phased_deferred:
        _append_os_log(
            hostname,
            "[os] note: Ubuntu phased updates are listed as upgradable but not installed "
            "until the rollout reaches this host — not a PiHerder failure.",
        )
    _append_os_log(hostname, "[os] rechecking update counts…")
    return res


def _parse_upgradable_list(list_out: str) -> list[str]:
    """Package names from `apt list --upgradable` (includes phased)."""
    names: list[str] = []
    for ln in (list_out or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("Listing"):
            continue
        # e.g. fwupd/resolute-updates 1.9.x arm64 [upgradable from: ...]
        if "/" in ln:
            names.append(ln.split("/", 1)[0].strip())
        elif "upgradable" in ln.lower():
            names.append(ln.split()[0])
    # de-dupe preserve order
    seen = set()
    out = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _parse_sim_upgrade_inst(sim_out: str) -> list[str]:
    """Package names that a normal upgrade would install (`apt-get -s upgrade`)."""
    names: list[str] = []
    for ln in (sim_out or "").splitlines():
        ln = ln.strip()
        # Inst pkg [old] (new ...) or Inst pkg (new ...)
        if ln.startswith("Inst "):
            parts = ln.split()
            if len(parts) >= 2:
                names.append(parts[1])
    seen = set()
    out = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def check_os_updates(server: Server) -> dict:
    """Check-only OS updates.

    - **HAOS** (``os_type=haos`` or SSH fingerprint): ``ha core|os|supervisor info``
    - **Debian family**: apt update + upgradable list + simulated upgrade + reboot flag

    Ubuntu *phased* updates appear in `apt list --upgradable` but are deferred by a
    normal `apt upgrade` / `apt full-upgrade` ("Not upgrading yet due to phasing").
    Those must not drive alerts as if they were actionable.

    - updates_count / actionable_count: packages (or HA components) installable now
    - phased_count: listed upgradable minus actionable (apt only)
    - total_upgradable: apt list count or HA component count
    """
    from . import haos as haos_svc

    os_type = (server.os_type or "debian").lower()

    # Known non-apt OSes (except HAOS, which has its own path)
    if os_type in ("alpine", "fedora", "rhel", "centos", "arch"):
        return {
            "server": server.hostname,
            "supported": False,
            "updates_count": None,
            "actionable_count": None,
            "phased_count": None,
            "total_upgradable": None,
            "reboot_pending": False,
            "packages_sample": [],
            "phased_sample": [],
            "error": f"OS type '{server.os_type}' not supported for OS update check",
        }

    client = get_ssh_client(server)
    error = None
    packages_sample: list[str] = []
    phased_sample: list[str] = []
    updates_count = 0
    actionable_count = 0
    phased_count = 0
    total_upgradable = 0
    reboot_pending = False

    try:
        # Prefer HA CLI path when already marked or fingerprint says HAOS
        use_haos = haos_svc.is_haos_server(server)
        identity = None
        if not use_haos:
            try:
                identity = haos_svc.probe_haos_identity(client)
                use_haos = bool(identity.get("is_haos"))
            except Exception:
                identity = None

        if use_haos:
            # Reuse open client (we still own close in finally)
            return haos_svc.check_haos_updates(server, client=client)

        if os_type not in ("debian", "ubuntu", "raspbian", "raspberrypi", "linux", "", "haos"):
            error = f"OS type '{server.os_type}' not supported for apt check"
            return {
                "server": server.hostname,
                "supported": False,
                "updates_count": None,
                "actionable_count": None,
                "phased_count": None,
                "total_upgradable": None,
                "reboot_pending": False,
                "packages_sample": [],
                "phased_sample": [],
                "error": error,
            }

        status, out, err = run_command(
            client,
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>&1 || sudo apt update 2>&1",
            timeout=180,
        )
        if status != 0:
            error = (out or err or "apt update failed")[:400]

        _, list_out, _ = run_command(
            client,
            "apt list --upgradable 2>/dev/null | grep -v '^Listing' | grep -v '^$' || true",
            timeout=60,
        )
        all_upgradable = _parse_upgradable_list(list_out or "")
        total_upgradable = len(all_upgradable)

        # Simulate a normal upgrade (respects phasing — does not force phased-in).
        # -s is read-only; prefer apt-get for stable machine-readable Inst lines.
        _, sim_out, _ = run_command(
            client,
            "sudo DEBIAN_FRONTEND=noninteractive apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null "
            "|| DEBIAN_FRONTEND=noninteractive apt-get -s upgrade 2>/dev/null || true",
            timeout=120,
        )
        actionable = _parse_sim_upgrade_inst(sim_out or "")
        actionable_set = set(actionable)
        phased = [p for p in all_upgradable if p not in actionable_set]

        # If simulation produced nothing but list is non-empty, still treat list-only
        # as total; actionable may be 0 due to phasing (correct) or sim failure.
        sim_empty = not (sim_out or "").strip()
        if sim_empty and total_upgradable > 0:
            actionable = list(all_upgradable)
            phased = []
            if not error:
                error = "upgrade simulation empty; counted all listed packages as actionable"

        actionable_count = len(actionable)
        phased_count = len(phased)
        updates_count = actionable_count
        packages_sample = actionable[:15]
        phased_sample = phased[:15]

        st, ro, _ = run_command(
            client,
            "test -f /var/run/reboot-required && echo REBOOT || echo no-reboot",
            timeout=10,
        )
        reboot_pending = "REBOOT" in (ro or "")
    except Exception as e:
        error = str(e)[:400]
    finally:
        try:
            client.close()
        except Exception:
            pass

    return {
        "server": server.hostname,
        "supported": True,
        "backend": "apt",
        "updates_count": updates_count,
        "actionable_count": actionable_count,
        "phased_count": phased_count,
        "total_upgradable": total_upgradable,
        "reboot_pending": reboot_pending,
        "packages_sample": packages_sample,
        "phased_sample": phased_sample,
        "error": error,
    }
