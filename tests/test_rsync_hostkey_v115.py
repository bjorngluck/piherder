"""rsync ssh uses the pinned host key."""
from __future__ import annotations

import shlex
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest

from app.services.ssh import rsync_e_command


class _Server(paramiko.ServerInterface):
    def get_allowed_auths(self, username):
        return "none"

    def check_auth_none(self, username):
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


def _serve(sock, host_key):
    try:
        conn, _addr = sock.accept()
    except OSError:
        return
    transport = paramiko.Transport(conn)
    transport.add_server_key(host_key)
    try:
        transport.start_server(server=_Server())
    except Exception:
        pass
    finally:
        try:
            transport.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def test_rsync_ssh_quotes_the_key_path_and_writes_the_pin(tmp_path):
    key_path = tmp_path / "id with space"
    key_path.write_text("not-a-real-key\n", encoding="utf-8")
    server = SimpleNamespace(
        hostname="host-a.example.com",
        ssh_port=2222,
        ssh_hostkey_type="ssh-ed25519",
        ssh_hostkey_b64="AAAA",
    )
    cmd = rsync_e_command(server, str(key_path))
    assert shlex.quote(str(key_path)) in cmd
    assert "StrictHostKeyChecking=yes" in cmd
    assert "accept-new" not in cmd
    known = Path(str(key_path) + ".known").read_text(encoding="utf-8")
    assert "host-a.example.com ssh-ed25519 AAAA" in known
    assert "[host-a.example.com]:2222 ssh-ed25519 AAAA" in known


def test_rsync_refuses_a_changed_host_key(tmp_path):
    ssh = subprocess.run(["ssh", "-V"], capture_output=True, text=True)
    if ssh.returncode not in (0, 1) or "OpenSSH" not in (ssh.stderr or "") + (ssh.stdout or ""):
        pytest.skip("OpenSSH client is not available")
    pinned = paramiko.RSAKey.generate(2048)
    presented = paramiko.RSAKey.generate(2048)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    sock.settimeout(8)
    port = sock.getsockname()[1]
    thread = threading.Thread(target=_serve, args=(sock, presented), daemon=True)
    thread.start()
    server = SimpleNamespace(
        hostname="127.0.0.1",
        ssh_port=port,
        ssh_hostkey_type=pinned.get_name(),
        ssh_hostkey_b64=pinned.get_base64(),
    )
    key_path = tmp_path / "id_client"
    key_path.write_text("unused\n", encoding="utf-8")
    cmd = rsync_e_command(server, str(key_path))
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        proc = subprocess.run(
            [
                "rsync",
                "-a",
                "-e",
                cmd,
                f"user@127.0.0.1:{tmp_path}/",
                str(dest) + "/",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
    finally:
        sock.close()
        thread.join(timeout=2)
    err = (proc.stderr or "") + (proc.stdout or "")
    assert proc.returncode != 0
    assert "Host key verification failed" in err or "IDENTIFICATION HAS CHANGED" in err
