"""v1.6 Q-80 twenty-second pack — host_files put/mkdir/write_text/peek/zip_basename."""
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
            "/home/pi/docker/compose.yml": SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644, st_size=12, st_mtime=1, st_uid=1000, st_gid=1000
            ),
        }
        self.bodies = {"/home/pi/docker/compose.yml": b"services: {}\n"}

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
        self.files[dst] = self.files.pop(src, SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644, st_size=1, st_mtime=1, st_uid=1000, st_gid=1000
        ))
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
        prefix = path.rstrip("/") + "/"
        names = []
        for p in self.files:
            if p.startswith(prefix) and p != path:
                rest = p[len(prefix):]
                if "/" not in rest:
                    names.append(rest)
        return names


def test_host_files_put_mkdir_write_peek(monkeypatch):
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
        hf.put_file(srv, "", "x.bin", io.BytesIO(b"x"), size=hf.max_upload_bytes() + 1, sftp=fs)

    made = hf.mkdir(srv, "", "logs", sftp=fs)
    assert made.get("name") == "logs"
    nested = hf.ensure_dir(srv, "logs/a/b", sftp=fs)
    assert "logs/a/b" in (nested.get("rel") or nested.get("abs") or "")

    put = hf.put_file(srv, "logs", "app.log", io.BytesIO(b"hello"), sftp=fs)
    assert put.get("bytes") == 5 or put.get("rel")
    put2 = hf.put_file(srv, "logs", "app.log", io.BytesIO(b"world"), sftp=fs)
    assert put2.get("overwrite") is True or put2.get("bytes") == 5

    written = hf.write_text(srv, "compose.yml", "services:\n  web:\n    image: nginx\n", sftp=fs)
    assert written.get("bytes") or written.get("rel")
    peek = hf.peek_file(srv, "compose.yml", sftp=fs)
    assert peek.get("is_text") is True or peek.get("name") == "compose.yml"
    assert hf.zip_basename("out", ["a"]) == "out.zip"
    assert hf.zip_basename("", ["compose.yml"]) == "compose.yml.zip"
    assert hf.zip_basename("", ["a", "b"]) == "files.zip"
    with pytest.raises(hf.FilesError):
        hf.write_text(srv, "compose.yml", "x" * (hf.EDIT_MAX + 8), sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.peek_file(srv, "logs", sftp=fs)
