"""v1.6 Mux-1 — host tmux/screen probe, session names, kill vs park."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import ssh_console as sc


def test_mux_session_name_isolates_privileged():
    fleet = sc.mux_session_name(user_id=3, server_id=12, tab=1, identity_role="fleet")
    priv = sc.mux_session_name(user_id=3, server_id=12, tab=1, identity_role="privileged")
    assert fleet == "ph-u3-s12-n1-f"
    assert priv == "ph-u3-s12-n1-p"
    assert fleet != priv
    assert sc.mux_session_name(user_id=1, server_id=1, tab=99) == "ph-u1-s1-n99-f"
    assert sc.mux_session_name(user_id=1, server_id=1, tab=200) == "ph-u1-s1-n99-f"


def test_mux_allowed_off_demo_haos(monkeypatch):
    host = SimpleNamespace(console_mux_enabled=True, os_type="debian")
    monkeypatch.setattr(sc, "is_demo_console", lambda: False)
    assert sc.mux_allowed_for_server(host) is True
    host.console_mux_enabled = False
    assert sc.mux_allowed_for_server(host) is False
    host.console_mux_enabled = True
    host.os_type = "haos"
    assert sc.mux_allowed_for_server(host) is False
    monkeypatch.setattr(sc, "is_demo_console", lambda: True)
    host.os_type = "debian"
    assert sc.mux_allowed_for_server(host) is False


def test_probe_mux_prefers_tmux_then_screen(monkeypatch):
    cli = object()

    def _run(client, cmd, timeout=8):
        if "tmux" in cmd:
            return 0, "/usr/bin/tmux\n", ""
        return 0, "", ""

    monkeypatch.setattr("app.services.ssh.run_command", _run)
    assert sc.probe_mux_backend(cli) == "tmux"

    def _run2(client, cmd, timeout=8):
        if "tmux" in cmd:
            return 0, "", ""
        if "screen" in cmd:
            return 0, "/usr/bin/screen\n", ""
        return 1, "", ""

    monkeypatch.setattr("app.services.ssh.run_command", _run2)
    assert sc.probe_mux_backend(cli) == "screen"

    monkeypatch.setattr(
        "app.services.ssh.run_command", lambda *a, **k: (0, "", "")
    )
    assert sc.probe_mux_backend(cli) is None
    assert sc.probe_mux_backend(None) is None


def test_mux_exec_argv_and_kill(monkeypatch):
    assert "new-session -A" in sc.mux_exec_argv("tmux", "ph-u1-s2-n0-f")
    assert "screen -S" in sc.mux_exec_argv("screen", "ph-u1-s2-n0-f")
    with pytest.raises(ValueError):
        sc.mux_exec_argv("nope", "x")
    called = []
    monkeypatch.setattr(
        "app.services.ssh.run_command",
        lambda client, cmd, timeout=8: called.append(cmd) or (0, "", ""),
    )
    sc.kill_mux_session(object(), "tmux", "ph-u1-s2-n0-f")
    assert called and "kill-session" in called[0]
    called.clear()
    sc.kill_mux_session(object(), "screen", "ph-u1-s2-n0-f")
    assert called and "quit" in called[0]
    sc.kill_mux_session(None, "tmux", "x")
    sc.kill_mux_session(object(), None, "x")


def test_open_session_plain_when_mux_off(monkeypatch):
    monkeypatch.setattr(sc, "is_demo_console", lambda: False)
    chan = MagicMock()
    cli = MagicMock()
    cli.invoke_shell.return_value = chan
    monkeypatch.setattr(
        "app.services.ssh.get_ssh_client", lambda server: cli
    )
    host = SimpleNamespace(console_mux_enabled=False, os_type="debian")
    c, ch = sc.open_session_channel(host)
    assert c is cli
    cli.invoke_shell.assert_called_once()
    assert getattr(c, "_ph_mux_note", "") == "pty"


def test_open_session_mux_tmux(monkeypatch):
    monkeypatch.setattr(sc, "is_demo_console", lambda: False)
    monkeypatch.setattr(sc, "probe_mux_backend", lambda client: "tmux")
    chan = MagicMock()
    transport = MagicMock()
    transport.open_session.return_value = chan
    cli = MagicMock()
    cli.get_transport.return_value = transport
    monkeypatch.setattr("app.services.ssh.get_ssh_client", lambda server: cli)
    host = SimpleNamespace(console_mux_enabled=True, os_type="debian")
    c, ch = sc.open_session_channel(
        host, mux_enabled=True, session_name="ph-u1-s2-n0-f"
    )
    assert ch is chan
    chan.get_pty.assert_called()
    chan.exec_command.assert_called()
    assert "tmux" in chan.exec_command.call_args[0][0]
    assert getattr(c, "_ph_mux_backend") == "tmux"
    assert getattr(c, "_ph_mux_name") == "ph-u1-s2-n0-f"


def test_open_session_mux_missing_binary_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "is_demo_console", lambda: False)
    monkeypatch.setattr(sc, "probe_mux_backend", lambda client: None)
    chan = MagicMock()
    cli = MagicMock()
    cli.invoke_shell.return_value = chan
    monkeypatch.setattr("app.services.ssh.get_ssh_client", lambda server: cli)
    host = SimpleNamespace(console_mux_enabled=True, os_type="debian")
    c, ch = sc.open_session_channel(
        host, mux_enabled=True, session_name="ph-u1-s2-n0-f"
    )
    cli.invoke_shell.assert_called_once()
    assert getattr(c, "_ph_mux_note") == "no_binary"


def test_discard_parked_kills_mux_idle_destroy_does_not(monkeypatch):
    sc.reset_runtime_state_for_tests()
    killed = []
    monkeypatch.setattr(
        sc, "kill_mux_session", lambda client, backend, name: killed.append((backend, name))
    )
    cli = MagicMock()
    chan = MagicMock()
    held = sc.HeldConsole(
        resume_id="rid-kill",
        user_id=1,
        server_id=2,
        session_version=1,
        ticket_payload={},
        device_id="d",
        client=cli,
        channel=chan,
        started_mono=0.0,
        last_activity_mono=0.0,
        held_at_mono=0.0,
        server_hostname="pi",
        mux_backend="tmux",
        mux_name="ph-u1-s2-n0-f",
    )
    sc.park_console(held)
    assert sc.discard_parked_for_user("rid-kill", user_id=1, server_id=2) is True
    assert killed == [("tmux", "ph-u1-s2-n0-f")]

    killed.clear()
    held2 = sc.HeldConsole(
        resume_id="rid-idle",
        user_id=1,
        server_id=2,
        session_version=1,
        ticket_payload={},
        device_id="d",
        client=cli,
        channel=chan,
        started_mono=0.0,
        last_activity_mono=0.0,
        held_at_mono=0.0,
        server_hostname="pi",
        mux_backend="tmux",
        mux_name="ph-u1-s2-n0-f",
    )
    sc.park_console(held2)
    assert sc.destroy_held("rid-idle", reason="idle") is True
    assert killed == []


def test_mint_ticket_includes_tab(monkeypatch):
    monkeypatch.setattr(sc, "console_enabled", lambda: True)
    tok = sc.mint_ticket(user_id=1, server_id=2, session_version=0, tab=3)
    from app.security.auth import decode_token_payload

    payload = decode_token_payload(tok)
    assert payload.get("n") == 3
