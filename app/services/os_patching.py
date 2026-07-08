"""OS patching service — replicates the apt sequence from the spec."""
from ..services.ssh import get_ssh_client, run_command
from ..models import Server
import time

_os_patch_progress: dict = {}

def get_os_patch_progress(hostname: str) -> dict:
    return _os_patch_progress.get(hostname, {"current": None, "log_lines": []})

def _append_os_log(hostname: str, text: str):
    if hostname not in _os_patch_progress:
        _os_patch_progress[hostname] = {"current": None, "log_lines": []}
    p = _os_patch_progress[hostname]
    # Split aggressively on \n and \r so live output appears fast
    for sep in ['\n', '\r']:
        if sep in text:
            parts = text.split(sep)
            text = parts[-1]
            for part in parts[:-1]:
                ln = part.strip()
                if ln:
                    p["log_lines"].append(ln)
    if text and text.strip():
        p["log_lines"].append(text.strip())
    if len(p["log_lines"]) > 100:
        p["log_lines"] = p["log_lines"][-100:]

def _init_progress(hostname: str):
    _os_patch_progress[hostname] = {"current": None, "log_lines": []}

def init_os_patch_progress(hostname: str, initial_msg: str = "starting"):
    """Public helper for pre-initializing so live UI can attach before the long work starts."""
    _os_patch_progress[hostname] = {"current": "starting", "log_lines": [f"[os] {initial_msg}"]}


def run_os_patch(server: Server, selected_steps: list[str] = None) -> dict:
    """Run selected patch steps over SSH. Defaults to update, upgrade, autoremove.
    Uses non-interactive mode to keep local configs and restart services by default.
    Streams output live into _os_patch_progress for SSE/poll.
    """
    if selected_steps is None:
        selected_steps = ["update", "upgrade", "autoremove"]

    client = get_ssh_client(server)
    step_cmds = {
        "update": "sudo apt update",
        "upgrade": 'DEBIAN_FRONTEND=noninteractive sudo apt upgrade -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef" -y',
        "full-upgrade": 'DEBIAN_FRONTEND=noninteractive sudo apt full-upgrade -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef" -y',
        "autoremove": "sudo apt autoremove -y",
    }
    steps = [(name, cmd) for name, cmd in step_cmds.items() if name in selected_steps]

    results = []
    # Do not fully reset if pre-initialized by job scheduler for instant UI feedback.
    if server.hostname not in _os_patch_progress:
        _init_progress(server.hostname)
    p = _os_patch_progress[server.hostname]
    p["log_lines"] = p.get("log_lines", [])[-5:] or []  # keep a couple seed lines if any

    for name, cmd in steps:
        _os_patch_progress[server.hostname]["current"] = name
        _append_os_log(server.hostname, f"[{name}] $ {cmd}")
        try:
            # Live streaming via channel (not full buffer read)
            stdin, stdout, stderr = client.exec_command(cmd, timeout=900, get_pty=True)
            channel = stdout.channel
            buf = ""
            last_flush = time.time()
            while True:
                got = False
                if channel.recv_ready():
                    data = channel.recv(8192).decode(errors="replace")
                    if data:
                        buf += data
                        got = True
                        _append_os_log(server.hostname, data)  # append raw chunk immediately for streaming
                        # split for cleanliness
                        for sep in ('\n', '\r'):
                            while sep in buf:
                                line, buf = buf.split(sep, 1)
                                if line.strip():
                                    _append_os_log(server.hostname, line)
                if channel.recv_stderr_ready():
                    edata = channel.recv_stderr(8192).decode(errors="replace")
                    if edata:
                        _append_os_log(server.hostname, edata)
                        got = True
                if channel.exit_status_ready() and not (channel.recv_ready() or channel.recv_stderr_ready()):
                    try:
                        rem = channel.recv(8192).decode(errors="replace")
                        if rem:
                            _append_os_log(server.hostname, rem)
                    except Exception:
                        pass
                    break
                now = time.time()
                if buf and (now - last_flush > 0.4):
                    _append_os_log(server.hostname, buf)
                    buf = ""
                    last_flush = now
                if not got:
                    time.sleep(0.06)
            if buf and buf.strip():
                _append_os_log(server.hostname, buf)
            status = channel.recv_exit_status()
            results.append({"step": name, "rc": status})
            _append_os_log(server.hostname, f"[{name}] exit={status}")
        except Exception as e:
            results.append({"step": name, "error": str(e)})
            _append_os_log(server.hostname, f"[{name}] ERROR: {e}")

    # Check reboot-required
    try:
        status, out, _ = run_command(client, "test -f /var/run/reboot-required && echo REBOOT || echo no-reboot", timeout=10)
        needs_reboot = "REBOOT" in out
        _append_os_log(server.hostname, f"[reboot-check] {'REBOOT REQUIRED' if needs_reboot else 'no reboot needed'}")
    except Exception:
        needs_reboot = False

    client.close()
    # leave final snapshot a moment for UI polls; pop shortly after in caller if desired
    # do not pop immediately so last SSE/poll can see it
    return {
        "server": server.hostname,
        "results": results,
        "needs_reboot": needs_reboot,
        "timestamp": "now"
    }


def check_os_updates(server: Server) -> dict:
    """Check-only: apt update + list upgradable packages + reboot-required.
    Does NOT run upgrade/full-upgrade/autoremove.
    """
    os_type = (server.os_type or "debian").lower()
    if os_type not in ("debian", "ubuntu", "raspbian", "raspberrypi", "linux", ""):
        # Best-effort: still try apt on unknown; skip only explicit non-apt labels
        if os_type in ("alpine", "fedora", "rhel", "centos", "arch", "haos"):
            return {
                "server": server.hostname,
                "supported": False,
                "updates_count": None,
                "reboot_pending": False,
                "packages_sample": [],
                "error": f"OS type '{server.os_type}' not supported for apt check",
            }

    client = get_ssh_client(server)
    error = None
    packages_sample: list[str] = []
    updates_count = 0
    reboot_pending = False

    try:
        # Refresh package lists (non-interactive)
        status, out, err = run_command(
            client,
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>&1 || sudo apt update 2>&1",
            timeout=180,
        )
        if status != 0:
            error = (out or err or "apt update failed")[:400]

        # List upgradable (ignore "Listing..." header)
        _, list_out, _ = run_command(
            client,
            "apt list --upgradable 2>/dev/null | grep -v '^Listing' | grep -v '^$' || true",
            timeout=60,
        )
        lines = [ln.strip() for ln in (list_out or "").splitlines() if ln.strip()]
        # Filter noise
        lines = [ln for ln in lines if "/" in ln or "upgradable" in ln.lower()]
        updates_count = len(lines)
        packages_sample = lines[:15]

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
        "updates_count": updates_count,
        "reboot_pending": reboot_pending,
        "packages_sample": packages_sample,
        "error": error,
    }
