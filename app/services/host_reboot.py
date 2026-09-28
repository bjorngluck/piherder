"""Remote host reboot commands (kernel / OS pending).

systemd logind refuses a plain ``reboot`` while anyone is logged in (our own
SSH session, a GUI seat, another tty). The operator already confirmed
**Reboot now**, so we pass ``--ignore-inhibitors`` (``-i``). That still does a
clean unit shutdown — not ``systemctl reboot --force``.
"""
from __future__ import annotations

# Deferred + backgrounded so the SSH exec returns and PiHerder can finish
# HTTP + audit, including when this host *is* the herder.
_INNER = (
    "sudo -n /usr/bin/systemctl reboot --ignore-inhibitors",
    "sudo -n /bin/systemctl reboot --ignore-inhibitors",
    "sudo -n /usr/sbin/reboot --ignore-inhibitors",
    "sudo -n /sbin/reboot --ignore-inhibitors",
    "sudo -n /usr/bin/systemctl reboot -i",
    "sudo -n /usr/sbin/reboot -i",
)


def deferred_reboot_commands() -> tuple[str, ...]:
    """Shell one-liners to schedule a reboot; first success wins."""
    return tuple(
        f"nohup sh -c 'sleep 1; {inner}' >/dev/null 2>&1 &" for inner in _INNER
    )


def send_deferred_reboot(client) -> tuple[bool, str]:
    """Run deferred reboot commands on an open SSH client.

    A dropped channel after the command is accepted is success. The caller
    does not wait for the host to come back.
    """
    import time as _time

    last_err = ""
    for cmd in deferred_reboot_commands():
        try:
            _stdin, stdout, stderr = client.exec_command(cmd, timeout=8)
            deadline = _time.monotonic() + 1.5
            while _time.monotonic() < deadline:
                if stdout.channel.exit_status_ready():
                    break
                _time.sleep(0.1)
            if stdout.channel.exit_status_ready():
                code = stdout.channel.recv_exit_status()
                err = (stderr.read() or b"").decode(errors="replace")[:200]
                out = (stdout.read() or b"").decode(errors="replace")[:200]
                if code == 0:
                    return True, "Reboot scheduled (host will restart shortly)"
                last_err = (err or out or f"exit {code}").strip()
                continue
            return True, "Reboot command sent"
        except Exception as exc:
            msg = str(exc).lower()
            if any(x in msg for x in ("eof", "reset", "closed", "timeout", "timed out")):
                return True, "Reboot sent (connection closed)"
            last_err = str(exc)[:200]
    if last_err:
        return False, f"Reboot command failed: {last_err}"
    return False, "Reboot command failed"
