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
