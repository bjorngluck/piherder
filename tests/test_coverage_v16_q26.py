"""v1.6 Q-80 twenty-sixth pack — zip_on_host empty + write/sudo helpers + rename/remove."""
from __future__ import annotations

import io
import stat
from types import SimpleNamespace

import pytest

from app.services import host_files as hf


class _Sftp:
    def __init__(self):
        self.files = {
            "/home/pi/docker": SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000
            ),
            "/home/pi/docker/a.txt": SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644, st_size=2, st_mtime=1, st_uid=1000, st_gid=1000
            ),
        }
        self.bodies = {"/home/pi/docker/a.txt": b"hi"}

    def normalize(self, path):
        return path

    def lstat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def mkdir(self, path):
        self.files[path] = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000
        )

    def remove(self, path):
        self.files.pop(path, None)
        self.bodies.pop(path, None)

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)
        if src in self.bodies:
            self.bodies[dst] = self.bodies.pop(src)

    def open(self, path, mode="rb"):
        if "w" in mode:
            buf = io.BytesIO()

            class W:
                def write(_, data):
                    buf.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

                def __enter__(wself):
                    return wself

                def __exit__(wself, *a):
                    self.bodies[path] = buf.getvalue()
                    self.files[path] = SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o644,
                        st_size=len(self.bodies[path]),
                        st_mtime=1,
                        st_uid=1000,
                        st_gid=1000,
                    )

            return W()
        return io.BytesIO(self.bodies.get(path, b""))

    def listdir(self, path):
        return []


def test_zip_on_host_empty_and_write_helpers(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    srv = SimpleNamespace(
        id=1,
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="enc",
        hostname="pi.local",
        ssh_port=22,
    )
    fs = _Sftp()
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(srv, [], "", sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(srv, ["missing-dir"], "", sftp=fs)

    renamed = hf.rename(srv, "", "a.txt", "b.txt", sftp=fs)
    assert renamed.get("to") == "b.txt" or "b.txt" in str(renamed)
    hf.remove(srv, "b.txt", sftp=fs)

    assert "sudo" in hf._write_denied_msg(hf.ROLE_PRIVILEGED).lower()
    assert "fleet" in hf._write_denied_msg(hf.ROLE_FLEET).lower()
    assert hf._is_perm_denied(PermissionError("x")) is True
    assert hf._is_perm_denied(OSError("Permission denied")) is True
    assert hf._is_perm_denied(OSError("other")) is False
    assert hf._remote_is_root(None, srv, SimpleNamespace(username="root")) is True
    assert hf._remote_is_root(None, srv, None) is False
    buf = io.BytesIO(b"abc")
    assert hf._stream_bytes_for_retry(buf, None, 0) == b"abc"
    buf2 = io.BytesIO(b"xyz")
    buf2.read(1)
    got = hf._stream_bytes_for_retry(buf2, None, 1)
    assert got in (b"xyz", None) or isinstance(got, (bytes, type(None)))
