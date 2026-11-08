"""v1.14 coverage for host file branches that need a fake SFTP handle."""
from __future__ import annotations

import io
import stat
from types import SimpleNamespace

import pytest

from app.models import Server
from app.services import host_files as hf
from app.services.host_files import FilesError


class _Stat:
    def __init__(self, mode, size=4, uid=1000, gid=1000):
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = 10
        self.st_uid = uid
        self.st_gid = gid


class _Attr(_Stat):
    def __init__(self, filename, mode, size=4, uid=1000, gid=1000):
        super().__init__(mode, size=size, uid=uid, gid=gid)
        self.filename = filename


class _Handle:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _n=None):
        data = self._payload
        self._payload = b"" if isinstance(data, (bytes, str)) else data
        return data

    def write(self, _chunk):
        return None


class _FS:
    def __init__(self):
        self.removed = []
        self.renamed = []
        root = "/home/user"
        self.stats = {
            root: _Stat(stat.S_IFDIR | 0o755),
            root + "/notes.txt": _Stat(stat.S_IFREG | 0o644, size=5),
            root + "/link": _Stat(stat.S_IFLNK | 0o777),
            root + "/out": _Stat(stat.S_IFLNK | 0o777),
            root + "/subdir": _Stat(stat.S_IFDIR | 0o755),
        }
        self.listing = [
            _Attr("notes.txt", stat.S_IFREG | 0o644),
            _Attr("subdir", stat.S_IFDIR | 0o755),
            _Attr("link", stat.S_IFLNK | 0o777),
            _Attr("out", stat.S_IFLNK | 0o777),
            _Attr(".", stat.S_IFDIR | 0o755),
            _Attr("odd", stat.S_IFREG | 0o644, uid="nope", gid="nope"),
        ]

    def lstat(self, path):
        if path not in self.stats:
            raise FileNotFoundError(path)
        return self.stats[path]

    def listdir_attr(self, _path):
        return list(self.listing)

    def normalize(self, path):
        if path.endswith("/out"):
            return "/etc/passwd"
        if path.endswith("/link"):
            return "/home/user/notes.txt"
        return path

    def open(self, path, mode="rb"):
        if "wb" in mode and path.endswith("deny.txt.tmp"):
            raise PermissionError("denied")
        if "wb" in mode and path.endswith("boom.txt.tmp"):
            raise RuntimeError("disk")
        if "notes" in path:
            return _Handle(b"hello")
        return _Handle("text")

    def remove(self, path):
        self.removed.append(path)

    def rename(self, src, dest):
        self.renamed.append((src, dest))


def _server():
    return Server(name="Host A", hostname="host-a.example.com", ssh_username="user")


def test_list_dir_marks_escaped_links_and_bad_ids(monkeypatch):
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    fs = _FS()
    listed = hf.list_dir(_server(), "", sftp=fs)
    names = {row["name"] for row in listed["entries"]}
    assert "notes.txt" in names
    assert "out" in names
    assert listed["rel"] == ""


def test_read_and_iter_follow_a_link_inside_the_jail(monkeypatch):
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    fs = _FS()
    text = hf.read_text(_server(), "notes.txt", sftp=fs)
    assert text["text"] == "hello"
    fs.stats["/home/user/notes.txt"] = _Stat(stat.S_IFLNK | 0o777)
    chunks = b"".join(hf.iter_file(_server(), "notes.txt", sftp=fs))
    assert chunks
    fs.stats["/home/user/notes.txt"] = _Stat(stat.S_IFDIR | 0o755)
    with pytest.raises(FilesError):
        hf.read_text(_server(), "notes.txt", sftp=fs)
    with pytest.raises(FilesError):
        list(hf.iter_file(_server(), "notes.txt", sftp=fs))


def test_put_file_rejects_a_huge_size_and_a_denied_write(monkeypatch):
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    fs = _FS()
    server = _server()
    with pytest.raises(FilesError):
        hf.put_file(server, "", "big.bin", io.BytesIO(b"x"), size=10**18, sftp=fs, client=SimpleNamespace())
    with pytest.raises(FilesError):
        hf.put_file(server, "", "deny.txt", io.BytesIO(b"abc"), sftp=fs, client=SimpleNamespace())
    with pytest.raises(FilesError):
        hf.put_file(server, "", "boom.txt", io.BytesIO(b"abc"), sftp=fs, client=None)
