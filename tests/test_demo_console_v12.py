"""D5 — demo simulated console (no live SSH)."""
from __future__ import annotations

from app.services.demo_console import DemoShellChannel, open_demo_shell
from app.services import ssh_console as cons
from app.services import demo as demo_svc


def test_demo_shell_help_and_blocked(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    ch = DemoShellChannel(host_label="lab-core.demo", username="demo")
    # Drain banner
    while ch.recv_ready():
        ch.recv(8192)
    ch.send(b"help\r")
    out = b""
    for _ in range(30):
        if ch.recv_ready():
            out += ch.recv(8192)
        if b"simulated" in out or b"Demo commands" in out:
            break
    assert b"Demo commands" in out or b"help" in out.lower() or b"whoami" in out
    ch.send(b"sudo su\r")
    out2 = b""
    for _ in range(20):
        if ch.recv_ready():
            out2 += ch.recv(8192)
        if b"blocked" in out2:
            break
    assert b"blocked" in out2
    ch.send(b"exit\r")
    assert ch.exit_status_ready() or ch.closed


def test_open_demo_shell_tuple(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    client, ch = open_demo_shell(host_label="x", username="demo")
    assert ch.recv_ready()
    client.close()
    assert ch.closed


def test_production_console_still_needs_flag(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", False)
    assert cons.console_enabled() is False
    assert cons.is_demo_console() is False


def _drain(ch: DemoShellChannel) -> bytes:
    out = b""
    while ch.recv_ready():
        out += ch.recv(8192)
    return out


def test_demo_tab_completes_docker_path(monkeypatch):
    """Mobile soft Tab: `cd do` + Tab → `cd docker/` (was ignored as control)."""
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    ch = DemoShellChannel(host_label="lab", username="demo")
    _drain(ch)
    ch.send(b"cd do")
    _drain(ch)
    ch.send(b"\t")
    out = _drain(ch)
    assert b"cker/" in out
    # Line buffer holds full completion
    assert ch._line == b"cd docker/"


def test_demo_double_tab_lists_matches(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    ch = DemoShellChannel(host_label="lab", username="demo")
    _drain(ch)
    ch.send(b"cd ")
    _drain(ch)
    ch.send(b"\t")  # first Tab: ambiguous
    _drain(ch)
    ch.send(b"\t")  # second Tab: list
    out = _drain(ch)
    assert b"docker/" in out
    assert b"backups/" in out


def test_demo_tab_completes_command(monkeypatch):
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    ch = DemoShellChannel(host_label="lab", username="demo")
    _drain(ch)
    ch.send(b"who")
    _drain(ch)
    ch.send(b"\t")
    out = _drain(ch)
    assert b"ami" in out
    assert ch._line.startswith(b"whoami")


def test_demo_tab_fixes_mobile_cd_dot_glitch(monkeypatch):
    """Mobile keyboards turn `cd do` into `cd.do` / `cd .do` when Tab is hit."""
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    for raw in (b"cd.do\t", b"cd .do\t"):
        ch = DemoShellChannel(host_label="lab", username="demo")
        _drain(ch)
        ch.send(raw)
        _drain(ch)
        assert ch._line == b"cd docker/", (raw, bytes(ch._line))


def test_demo_tab_via_str_like_websocket_text(monkeypatch):
    """WS text frames deliver str; Tab must still complete paths."""
    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    ch = DemoShellChannel(host_label="lab", username="demo")
    _drain(ch)
    for ch_ in "cd do":
        ch.send(ch_)
    ch.send("\t")  # str Tab like Starlette text frame
    _drain(ch)
    assert ch._line == b"cd docker/"
