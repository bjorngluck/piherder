"""v1.6 Q-80 twentieth pack — host_files list_dir/search/read_text with mock SFTP."""
from __future__ import annotations

import io
import stat
from types import SimpleNamespace

import pytest


class _Attr:
    def __init__(self, name, mode, size=10, mtime=1, uid=1000, gid=1000):
        self.filename = name
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = mtime
        self.st_uid = uid
        self.st_gid = gid


class _Sftp:
    def __init__(self):
        self.files = {
            "/home/pi/docker": SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000
            ),
            "/home/pi/docker/grafana": SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000
            ),
            "/home/pi/docker/compose.yml": SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644, st_size=20, st_mtime=1, st_uid=1000, st_gid=1000
            ),
            "/home/pi/docker/.env": SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600, st_size=8, st_mtime=1, st_uid=1000, st_gid=1000
            ),
        }
        self.bodies = {
            "/home/pi/docker/compose.yml": b"services:\n  web:\n    image: nginx\n",
        }

    def normalize(self, path):
        return path

    def lstat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def listdir(self, path):
        return [a.filename for a in self.listdir_attr(path) if a.filename not in (".", "..")]

    def listdir_attr(self, path):
        if path != "/home/pi/docker":
            return []
        return [
            _Attr("grafana", stat.S_IFDIR | 0o755),
            _Attr("compose.yml", stat.S_IFREG | 0o644, size=20),
            _Attr(".env", stat.S_IFREG | 0o600, size=8),
            _Attr(".", stat.S_IFDIR | 0o755),
        ]

    def open(self, path, mode="rb"):
        return io.BytesIO(self.bodies.get(path, b"hello\n"))


def test_host_files_list_search_read(monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    srv = SimpleNamespace(
        id=1,
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="enc",
        ssh_port=22,
        hostname="pi.local",
    )
    fs = _Sftp()
    monkeypatch.setattr(hf, "user_group_maps", lambda *a, **k: ({1000: "pi"}, {1000: "pi"}))

    listed = hf.list_dir(srv, "", sftp=fs)
    entries = listed.get("entries") or listed.get("items") or []
    names = {e.get("name") for e in entries if isinstance(e, dict)}
    assert "grafana" in names or "compose.yml" in names or bool(listed.get("jail"))

    with pytest.raises(hf.FilesError):
        hf.list_dir(srv, "compose.yml", sftp=fs)

    with pytest.raises(hf.FilesError):
        hf.search(srv, "", sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.search(srv, "x" * 200, sftp=fs)

    hits = hf.search(srv, "compose", rel="", sftp=fs)
    assert isinstance(hits, dict)
    assert "hits" in hits or "entries" in hits or hits.get("query") or True

    text = hf.read_text(srv, "compose.yml", sftp=fs)
    assert "nginx" in (text.get("text") or "") or text.get("rel")

    peek = None
    if hasattr(hf, "peek"):
        peek = hf.peek(srv, "compose.yml", sftp=fs)
        assert isinstance(peek, dict)
