"""Host reboot: ignore systemd inhibitors, do not --force."""
from __future__ import annotations

from app.services.host_reboot import deferred_reboot_commands
from app.services.ssh_onboarding import build_sudoers_content


def test_deferred_reboot_ignores_inhibitors_not_force():
    cmds = deferred_reboot_commands()
    assert cmds
    joined = "\n".join(cmds)
    assert "systemctl reboot --ignore-inhibitors" in joined
    assert "sleep 1" in joined
    assert "nohup" in joined
    assert "--force" not in joined
    assert "reboot -f" not in joined
    assert cmds[0].endswith("&")


def test_os_patch_sudoers_allows_systemctl_reboot():
    text = build_sudoers_content("ph", backup=False, docker=False, os_patch=True)
    assert "PIHERDER_REBOOT" in text
    assert "/usr/bin/systemctl" in text
    assert "ignore-inhibitors" in text
