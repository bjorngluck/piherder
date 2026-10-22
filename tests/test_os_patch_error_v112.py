"""OS patch jobs keep apt, debconf, and sudo error lines."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import os_patching as osp


class _Chan:
    def __init__(self, chunks, rc=100):
        self._chunks = list(chunks)
        self.rc = rc

    def recv_ready(self):
        return bool(self._chunks)

    def recv(self, n):
        return self._chunks.pop(0) if self._chunks else b""

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, n):
        return b""

    def exit_status_ready(self):
        return not self._chunks

    def recv_exit_status(self):
        return self.rc

    def close(self):
        return None


def _client(chunks, rc=100):
    stdout = MagicMock()
    stdout.channel = _Chan(chunks, rc=rc)
    client = MagicMock()
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())
    return client


def test_carriage_return_keeps_debconf_sudo_and_apt_errors(monkeypatch):
    host = "host-a.local"
    osp.clear_os_patch_progress(host)
    monkeypatch.setattr(osp.time, "sleep", lambda *_a, **_k: None)
    client = _client(
        [
            (
                b"debconf: unable to initialize frontend: Dialog\r"
                b"Reading package lists...\n"
            ),
            (
                b"sudo: sorry, you are not allowed to set the following "
                b"environment variables: DEBIAN_FRONTEND\n"
            ),
            b"E: Could not get lock /var/lib/dpkg/lock-frontend\r99%\n",
        ]
    )
    rc = osp._stream_ssh_command(client, host, "upgrade", "apt upgrade", timeout=30)
    assert rc == 100
    lines = osp.get_os_patch_log_tail(host, n=20)
    assert any(line.startswith("debconf: unable to initialize frontend") for line in lines)
    assert any("DEBIAN_FRONTEND" in line and line.startswith("sudo:") for line in lines)
    assert any(line.startswith("E: Could not get lock") for line in lines)
    assert osp.apt_failure_reason(lines).startswith("E: Could not get lock")
    osp.clear_os_patch_progress(host)


def test_debconf_line_survives_the_next_real_line():
    host = "host-a.local"
    osp.clear_os_patch_progress(host)
    osp._append_os_log(
        host,
        "\rdebconf: unable to initialize frontend: Dialog",
        replace_progress=True,
    )
    osp._append_os_log(host, "\rReading package lists...", replace_progress=True)
    osp._append_os_log(host, "[upgrade] exit=1")
    lines = osp.get_os_patch_log_tail(host, n=20)
    assert any(line.startswith("debconf: unable to initialize frontend") for line in lines)
    assert not any("Reading package lists" in line for line in lines)
    reason = osp.apt_failure_reason(lines)
    assert reason.startswith("debconf: unable to initialize frontend")
    osp.clear_os_patch_progress(host)


def test_patch_summary_keeps_the_debconf_line_when_apt_prints_no_e(monkeypatch):
    host = "host-a.local"
    osp.clear_os_patch_progress(host)
    monkeypatch.setattr(osp.time, "sleep", lambda *_a, **_k: None)
    client = _client(
        [
            b"debconf: unable to initialize frontend: Dialog\r50%\n",
            b"sudo: sorry, you are not allowed to set the following environment variables: DEBIAN_FRONTEND\n",
        ],
        rc=100,
    )
    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: False)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": False})
    monkeypatch.setattr(osp, "get_ssh_client", lambda s: client)
    monkeypatch.setattr(osp, "run_command", lambda *a, **k: (0, "no-reboot", ""))
    server = SimpleNamespace(
        hostname=host,
        os_type="debian",
        name="host-a",
        ssh_username="pi",
    )
    res = osp.run_os_patch(server, selected_steps=["upgrade"])
    assert res["results"][0]["rc"] == 100
    reason = res["results"][0]["reason"]
    assert "debconf: unable to initialize frontend" in reason
    assert "DEBIAN_FRONTEND" in reason
    assert "debconf:" in res["summary"]
    assert not osp.os_patch_succeeded(res)
    osp.clear_os_patch_progress(host)
