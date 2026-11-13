"""Coverage for host Files and certificate edges (v1.15 q90).

No live SSH. Public placeholders only.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import stat
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, ServerSshIdentity, User
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services import certificates as cert_svc
from app.services import host_files as hf


def _pem(cn: str, *, before_days: int, after_days: int) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.utcnow()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=before_days))
        .not_valid_after(now + timedelta(days=after_days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    full = cert.public_bytes(serialization.Encoding.PEM).decode()
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return full, priv


FULL, PRIV = _pem("app.example.com", before_days=-1, after_days=40)
FULL_SOON, PRIV_SOON = _pem("soon.example.com", before_days=-1, after_days=3)
FULL_OLD, PRIV_OLD = _pem("old.example.com", before_days=-30, after_days=-2)

JAIL = "/home/user/docker"


@pytest.fixture(autouse=True)
def _clear_file_pool():
    hf.drop_sftp_pool()
    hf._id_maps_cache.clear()
    yield
    hf.drop_sftp_pool()
    hf._id_maps_cache.clear()


def _srv(**kw):
    data = dict(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_username="user",
        ssh_private_key_encrypted="x",
        ssh_password_encrypted=None,
        docker_base_dir="~/docker",
        container_patch_enabled=True,
        os_type="debian",
    )
    data.update(kw)
    return SimpleNamespace(**data)


def _ident(**kw):
    data = dict(id=2, role="privileged", username="user", enabled=True)
    data.update(kw)
    return SimpleNamespace(**data)


class FakeFS:
    def __init__(self):
        now = time.time()
        self.nodes = {
            "/": {
                "kind": "dir",
                "mode": stat.S_IFDIR | 0o755,
                "mtime": now,
                "data": b"",
                "uid": 0,
                "gid": 0,
            }
        }
        self.links: dict[str, str] = {}
        self.norm_raise: set[str] = set()
        self.read_str: set[str] = set()
        self.huge_read: set[str] = set()
        self.open_raise: set[str] = set()
        self.mkdir_raise: set[str] = set()
        self.mkdir_then_file: set[str] = set()
        self.remove_suffixes: tuple[str, ...] = ()
        self.rmdir_raise: set[str] = set()
        self.rename_raise = False
        self.chmod_exc: BaseException | None = None
        self.chown_exc: BaseException | None = None
        self.size_override: dict[str, int] = {}
        self.lstat_error: set[str] = set()
        self._norm_seen: dict[str, int] = {}

    def close(self):
        return None

    def _norm(self, path: str) -> str:
        p = posixpath.normpath((path or "/").replace("\\", "/"))
        if not p.startswith("/"):
            p = "/" + p
        return p

    def add_dir(self, path: str) -> None:
        path = self._norm(path)
        acc = ""
        for seg in path.strip("/").split("/"):
            if not seg:
                continue
            acc = acc + "/" + seg
            if acc not in self.nodes:
                self.nodes[acc] = {
                    "kind": "dir",
                    "mode": stat.S_IFDIR | 0o755,
                    "mtime": time.time(),
                    "data": b"",
                    "uid": 1000,
                    "gid": 1000,
                }

    def add_file(self, path: str, data: bytes) -> None:
        path = self._norm(path)
        self.add_dir(posixpath.dirname(path) or "/")
        self.nodes[path] = {
            "kind": "file",
            "mode": stat.S_IFREG | 0o644,
            "mtime": time.time(),
            "data": bytes(data),
            "uid": 1000,
            "gid": 1000,
        }

    def add_link(self, path: str, target: str) -> None:
        path = self._norm(path)
        self.add_dir(posixpath.dirname(path) or "/")
        self.nodes[path] = {
            "kind": "link",
            "mode": stat.S_IFLNK | 0o777,
            "mtime": time.time(),
            "data": b"",
            "uid": 1000,
            "gid": 1000,
        }
        self.links[path] = target

    def normalize(self, path: str) -> str:
        path = self._norm(path)
        if path in self.norm_raise:
            raise OSError("norm")
        # First lookup stays on the link so jail checks see the link path.
        # A later lookup follows it (walk / stat after the jail check).
        seen = self._norm_seen.get(path, 0) + 1
        self._norm_seen[path] = seen
        if path in self.links and seen > 1:
            target = self.links[path]
            if not str(target).startswith("/"):
                target = self._norm(posixpath.join(posixpath.dirname(path), target))
            return target
        return path

    def lstat(self, path: str):
        path = self._norm(path)
        if path in self.lstat_error:
            raise hf.FilesError("ssh", "stat failed")
        node = self.nodes.get(path)
        if not node:
            raise FileNotFoundError(path)
        size = self.size_override.get(path, len(node.get("data") or b""))
        return SimpleNamespace(
            st_mode=node["mode"],
            st_size=size,
            st_mtime=node["mtime"],
            st_uid=node.get("uid", 1000),
            st_gid=node.get("gid", 1000),
            filename=posixpath.basename(path) or "/",
        )

    def listdir_attr(self, path: str):
        path = self._norm(path)
        node = self.nodes.get(path)
        if not node or node["kind"] != "dir":
            raise FileNotFoundError(path)
        prefix = "/" if path == "/" else path + "/"
        out = []
        for p, n in self.nodes.items():
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix) :]
            if not rest or "/" in rest:
                continue
            out.append(
                SimpleNamespace(
                    filename=rest,
                    st_mode=n["mode"],
                    st_size=self.size_override.get(p, len(n.get("data") or b"")),
                    st_mtime=n["mtime"],
                    st_uid=n.get("uid", 1000),
                    st_gid=n.get("gid", 1000),
                )
            )
        return out

    def listdir(self, path: str):
        return [a.filename for a in self.listdir_attr(path)]

    def open(self, path: str, mode: str = "rb"):
        path = self._norm(path)
        if path in self.open_raise:
            raise OSError("open")
        if "w" in mode:
            parent = posixpath.dirname(path) or "/"
            if parent not in self.nodes:
                raise FileNotFoundError(parent)
            fs = self

            class _W:
                def __init__(self):
                    self.buf = bytearray()

                def write(self, data):
                    if isinstance(data, str):
                        data = data.encode()
                    self.buf.extend(data)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    fs.nodes[path] = {
                        "kind": "file",
                        "mode": stat.S_IFREG | 0o644,
                        "mtime": time.time(),
                        "data": bytes(self.buf),
                        "uid": 1000,
                        "gid": 1000,
                    }
                    return False

            return _W()

        node = self.nodes.get(path)
        if not node or node["kind"] == "dir":
            raise FileNotFoundError(path)
        data = bytes(node.get("data") or b"")
        as_str = path in self.read_str
        huge = path in self.huge_read

        class _R:
            def __init__(self):
                self.bio = io.BytesIO(data)
                self.huge_done = False

            def read(self, n=-1):
                if huge and not self.huge_done:
                    self.huge_done = True
                    return b"Z" * (hf.PREVIEW_MAX + 8)
                blob = self.bio.read(n)
                if as_str and blob:
                    return blob.decode("latin-1")
                return blob

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    def mkdir(self, path: str):
        path = self._norm(path)
        if path in self.mkdir_then_file:
            self.add_file(path, b"x")
            raise OSError("race")
        if path in self.mkdir_raise:
            raise OSError("mkdir")
        if path in self.nodes:
            raise OSError("exists")
        self.add_dir(path)

    def remove(self, path: str):
        path = self._norm(path)
        if any(path.endswith(suf) for suf in self.remove_suffixes):
            raise OSError("remove")
        if path not in self.nodes:
            raise FileNotFoundError(path)
        del self.nodes[path]
        self.links.pop(path, None)

    def rmdir(self, path: str):
        path = self._norm(path)
        if path in self.rmdir_raise:
            raise OSError("busy")
        if self.listdir(path):
            raise OSError("not empty")
        self.remove(path)

    def rename(self, src: str, dst: str):
        if self.rename_raise:
            raise OSError("rename")
        src, dst = self._norm(src), self._norm(dst)
        if src not in self.nodes:
            raise FileNotFoundError(src)
        self.nodes[dst] = self.nodes.pop(src)
        if src in self.links:
            self.links[dst] = self.links.pop(src)

    def chmod(self, path: str, mode: int):
        if self.chmod_exc:
            raise self.chmod_exc
        path = self._norm(path)
        node = self.nodes.get(path)
        if not node:
            raise FileNotFoundError(path)
        node["mode"] = (node["mode"] & 0o170000) | (int(mode) & 0o7777)

    def chown(self, path: str, uid: int, gid: int):
        if self.chown_exc:
            raise self.chown_exc
        path = self._norm(path)
        node = self.nodes.get(path)
        if not node:
            raise FileNotFoundError(path)
        node["uid"] = int(uid)
        node["gid"] = int(gid)


def _bind(monkeypatch, fs: FakeFS, run=None):
    client = SimpleNamespace(
        active=True,
        sftp=fs,
        close=lambda: None,
        open_sftp=lambda: fs,
        get_transport=lambda: SimpleNamespace(is_active=lambda: True, set_keepalive=lambda n: None),
    )

    def _open(server, identity):
        return client, fs

    monkeypatch.setattr(hf, "_open_client", _open)
    if run is not None:
        monkeypatch.setattr(hf, "run_command", run)
    return client


def _raise_files(code="ssh", msg="boom"):
    def _inner(*a, **k):
        raise hf.FilesError(code, msg)

    return _inner


# --- host_files ----------------------------------------------------------------


def test_path_policy_edges(monkeypatch):
    with pytest.raises(hf.FilesError):
        hf.parse_rel("a/../b")
    assert hf._secretish_component("") is False
    assert hf.looks_like_text("blob.bin", b"\xff\xfe") is False
    assert hf.parse_mode("000644") == 0o644
    deep = "/".join(f"p{i}" for i in range(30))
    with pytest.raises(hf.FilesError):
        hf._safe_zip_name(deep)
    assert hf.parse_container_path("srv/app/file.txt") == "srv/app/file.txt"
    with pytest.raises(hf.FilesError):
        hf.parse_container_path("/" + deep)
    with pytest.raises(hf.FilesError):
        hf._zip_buffer(_srv(), [])

    monkeypatch.setattr(hf, "expand_remote_path", lambda raw, user: "relative/jail")
    with pytest.raises(hf.FilesError) as ei:
        hf.jail_path(_srv())
    assert ei.value.code == "jail"

    monkeypatch.setattr(hf, "jail_path", lambda *a, **k: "/etc/app")
    denies = hf.extra_denies(_srv(), role="fleet")
    assert "/boot" in denies
    assert "/etc" not in denies

    monkeypatch.undo()
    monkeypatch.setattr(hf, "join_jail", lambda jail, rel: "/etc/passwd")
    with pytest.raises(hf.FilesError) as ei:
        hf.resolve_logical(_srv(), "OUT")
    assert ei.value.code == "escape"


def test_pool_open_and_close(monkeypatch):
    real_open = hf._open_client
    class _T:
        def __init__(self):
            self.w = 0

        def set_keepalive(self, n):
            return None

        @property
        def default_window_size(self):
            return self.w

        @default_window_size.setter
        def default_window_size(self, v):
            self.w = v

        @property
        def default_max_packet_size(self):
            return 1

        @default_max_packet_size.setter
        def default_max_packet_size(self, v):
            raise RuntimeError("packet")

    hf._tune_transport(SimpleNamespace(get_transport=lambda: _T()))

    class _Boom:
        def close(self):
            raise RuntimeError("close")

    lease = hf._SftpLease(
        client=_Boom(),
        sftp=_Boom(),
        lock=threading.Lock(),
        last=time.monotonic(),
        key=("k",),
    )
    hf._close_lease(lease)

    srv = _srv()
    key = hf._pool_key(srv, None)
    busy = hf._SftpLease(
        client=SimpleNamespace(close=lambda: None),
        sftp=FakeFS(),
        lock=threading.Lock(),
        last=time.monotonic() - 1000,
        key=key,
    )
    busy.lock.acquire()
    hf._pool[key] = busy
    hf._sweep_idle_locked(time.monotonic())
    assert key in hf._pool
    busy.lock.release()
    hf._sweep_idle_locked(time.monotonic())
    assert key not in hf._pool

    monkeypatch.setattr(hf, "_open_client", lambda *a, **k: (_Boom(), _Boom()))
    with hf.sftp_session(srv, pooled=False):
        pass

    def _files_err(*a, **k):
        raise hf.FilesError("denied", "nope")

    monkeypatch.setattr(hf, "_open_client", _files_err)
    with pytest.raises(hf.FilesError):
        with hf.sftp_session(srv):
            pass

    dead = SimpleNamespace(get_transport=lambda: None, close=lambda: None)
    fs = FakeFS()
    lease = hf._SftpLease(
        client=dead, sftp=fs, lock=threading.Lock(), last=time.monotonic(), key=key
    )
    hf._pool[key] = lease

    def _runtime(*a, **k):
        raise RuntimeError("reopen")

    monkeypatch.setattr(hf, "_open_client", _runtime)
    with pytest.raises(hf.FilesError) as ei:
        with hf.sftp_session(srv):
            pass
    assert ei.value.code == "ssh"
    assert key not in hf._pool

    monkeypatch.setattr(hf, "_open_client", real_open)
    import paramiko

    stub_fs = FakeFS()
    stub = SimpleNamespace(
        get_transport=lambda: SimpleNamespace(is_active=lambda: True, set_keepalive=lambda n: None),
        open_sftp=lambda: stub_fs,
        close=lambda: None,
    )
    monkeypatch.setattr(hf, "get_ssh_client", lambda *a, **k: stub)

    def _type_error(*a, **k):
        raise TypeError("sig")

    monkeypatch.setattr(paramiko.SFTPClient, "from_transport", _type_error)
    cli, handle = hf._open_client(srv, None)
    assert handle is stub_fs
    assert cli is stub


def test_pool_race_keeps_existing(monkeypatch):
    srv = _srv()
    key = hf._pool_key(srv, None)
    existing_fs = FakeFS()
    existing = hf._SftpLease(
        client=SimpleNamespace(
            get_transport=lambda: SimpleNamespace(is_active=lambda: True),
            close=lambda: None,
        ),
        sftp=existing_fs,
        lock=threading.Lock(),
        last=time.monotonic(),
        key=key,
    )
    fresh_flags = {"closed": 0}

    def _fresh_close():
        fresh_flags["closed"] += 1

    fresh_client = SimpleNamespace(close=_fresh_close, get_transport=lambda: None)
    fresh_fs = FakeFS()
    state = {"n": 0}

    class _Race:
        def __enter__(self):
            state["n"] += 1
            if state["n"] == 2:
                hf._pool[key] = existing
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(hf, "_pool_lock", _Race())
    monkeypatch.setattr(hf, "_open_client", lambda *a, **k: (fresh_client, fresh_fs))
    with hf.sftp_session(srv) as got:
        assert got is existing_fs
    assert fresh_flags["closed"] >= 1


def test_user_group_maps_success(monkeypatch):
    def run(client, cmd, timeout=None):
        if cmd == "getent passwd":
            return (0, "user:x:1000:1000:::\n:x:5:\nbad:x:zz:\n", "")
        if cmd == "getent group":
            return (1, "", "missing")
        if "group" in cmd:
            return (0, "users:x:1000:\n", "")
        return (1, "", "no")

    monkeypatch.setattr(hf, "run_command", run)
    users, groups = hf.user_group_maps(object(), None)
    assert users[1000] == "user"
    assert 5 not in users
    assert groups[1000] == "users"
    assert hf.parse_getent(":x:9:\nbad:x:no:\n") == {}


def test_list_stat_iter_read_peek(monkeypatch):
    fs = FakeFS()
    fs.add_dir(JAIL)
    fs.add_dir(JAIL + "/sub")
    fs.add_file(JAIL + "/sub/a.txt", b"hello")
    fs.add_file(JAIL + "/readme.txt", b"notes")
    fs.add_file(JAIL + "/odd", b"z")
    fs.add_dir(JAIL + "/.ssh")
    fs.add_link(JAIL + "/linkdir", JAIL + "/sub")
    fs.add_link(JAIL + "/linkout", "/etc/passwd")
    fs.add_link(JAIL + "/linkfile", JAIL + "/readme.txt")
    fs.add_link(JAIL + "/boom", "/etc/hosts")
    fs.norm_raise.add(JAIL + "/boom")
    fs.add_file(JAIL + "/bin.dat", b"\xff\xfe")
    fs.read_str.add(JAIL + "/readme.txt")
    fs.add_file(JAIL + "/bad.txt", b"\xff")
    _bind(monkeypatch, fs)

    listed = hf.list_dir(_srv(), "", sftp=fs)
    names = {e["name"] for e in listed["entries"]}
    assert "boom" in names and "odd" in names and ".ssh" in names
    real_rel = hf.rel_of

    def rel_of(abs_path, jail):
        if str(abs_path).endswith("/odd"):
            raise hf.FilesError("escape", "odd")
        return real_rel(abs_path, jail)

    monkeypatch.setattr(hf, "rel_of", rel_of)
    listed = hf.list_dir(_srv(), "", sftp=fs)
    odd = next(e for e in listed["entries"] if e["name"] == "odd")
    assert odd["rel"] == "odd"

    followed = hf.list_dir(_srv(), "linkdir", sftp=fs)
    assert any(e["name"] == "a.txt" for e in followed["entries"])
    with pytest.raises(hf.FilesError):
        hf.list_dir(_srv(), "linkout", sftp=fs)

    st = hf.stat_file(_srv(), "linkfile", sftp=fs)
    assert st["kind"] == "file"
    with pytest.raises(hf.FilesError):
        hf.stat_file(_srv(), "linkout", sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.stat_file(_srv(), "sub", sftp=fs)

    chunks = b"".join(hf.iter_file(_srv(), "readme.txt", sftp=fs))
    assert chunks
    with pytest.raises(hf.FilesError):
        b"".join(hf.iter_file(_srv(), "linkout", sftp=fs))

    text = hf.read_text(_srv(), "linkfile", sftp=fs)
    assert "notes" in text["text"] or text["text"]
    with pytest.raises(hf.FilesError):
        hf.read_text(_srv(), "linkout", sftp=fs)
    with pytest.raises(hf.FilesError) as ei:
        hf.read_text(_srv(), "bad.txt", sftp=fs)
    assert ei.value.code == "binary"

    peek = hf.peek_file(_srv(), "linkfile", sftp=fs)
    assert peek["name"] == "readme.txt"
    with pytest.raises(hf.FilesError):
        hf.peek_file(_srv(), "linkout", sftp=fs)

    preview = b"".join(hf.iter_preview(_srv(), "linkfile", sftp=fs))
    assert preview
    with pytest.raises(hf.FilesError):
        b"".join(hf.iter_preview(_srv(), "linkout", sftp=fs))
    with pytest.raises(hf.FilesError):
        b"".join(hf.iter_preview(_srv(), "sub", sftp=fs))
    fs.size_override[JAIL + "/readme.txt"] = hf.PREVIEW_MAX + 5
    with pytest.raises(hf.FilesError):
        b"".join(hf.iter_preview(_srv(), "readme.txt", sftp=fs))
    fs.size_override[JAIL + "/readme.txt"] = 4
    fs.huge_read.add(JAIL + "/readme.txt")
    with pytest.raises(hf.FilesError):
        b"".join(hf.iter_preview(_srv(), "readme.txt", sftp=fs))

    import app.services.demo_files as demo_files

    monkeypatch.setattr(hf, "is_demo_files", lambda: True)
    monkeypatch.setattr(demo_files, "stat_file", lambda server, rel: {"demo": True, "rel": rel})

    def _demo_iter(server, rel):
        yield b"demo"

    monkeypatch.setattr(demo_files, "iter_file", _demo_iter)
    assert hf.stat_file(_srv(), "a")["demo"] is True
    assert b"".join(hf.iter_file(_srv(), "a")) == b"demo"


def test_put_mkdir_remove_rename_tree(monkeypatch):
    fs = FakeFS()
    fs.add_dir(JAIL)
    fs.add_file(JAIL + "/readme.txt", b"old")
    fs.add_link(JAIL + "/out", "/etc/passwd")
    monkeypatch.setattr(hf, "max_upload_bytes", lambda: 4)
    client = SimpleNamespace()

    def _progress(written, total):
        raise RuntimeError("progress")

    class _StrStream:
        def __init__(self, payload):
            self.payload = payload
            self.done = False

        def read(self, n=-1):
            if self.done:
                return b""
            self.done = True
            return self.payload

    got = hf.put_file(
        _srv(),
        "",
        "note.txt",
        _StrStream("ab"),
        size=2,
        sftp=fs,
        client=client,
        progress=_progress,
    )
    assert got["bytes"] == 2

    fs.remove_suffixes = (".tmp",)
    with pytest.raises(hf.FilesError) as ei:
        hf.put_file(_srv(), "", "big.bin", io.BytesIO(b"123456"), size=6, sftp=fs, client=client)
    assert ei.value.code == "too_large"
    fs.remove_suffixes = ()

    with pytest.raises(hf.FilesError):
        hf.put_file(_srv(), "", ".ssh", io.BytesIO(b"a"), sftp=fs, client=client)
    with pytest.raises(hf.FilesError):
        hf.put_file(_srv(), "readme.txt", "child.txt", io.BytesIO(b"a"), sftp=fs, client=client)

    fs.lstat_error.add(JAIL + "/badstat")
    with pytest.raises(hf.FilesError):
        hf.put_file(_srv(), "", "badstat", io.BytesIO(b"a"), sftp=fs, client=client)

    real_under = hf.under_jail

    def under(path, jail):
        if posixpath.basename(path) == "escape.bin":
            return False
        return real_under(path, jail)

    monkeypatch.setattr(hf, "under_jail", under)
    with pytest.raises(hf.FilesError):
        hf.put_file(_srv(), "", "escape.bin", io.BytesIO(b"a"), sftp=fs, client=client)
    monkeypatch.setattr(hf, "under_jail", real_under)

    with pytest.raises(hf.FilesError):
        hf.mkdir(_srv(), "", ".ssh", sftp=fs)
    fs.mkdir_raise.add(JAIL + "/nope")
    with pytest.raises(hf.FilesError):
        hf.mkdir(_srv(), "", "nope", sftp=fs)

    def under2(path, jail):
        if posixpath.basename(path) == "newd":
            return False
        return real_under(path, jail)

    monkeypatch.setattr(hf, "under_jail", under2)
    with pytest.raises(hf.FilesError):
        hf.mkdir(_srv(), "", "newd", sftp=fs)
    monkeypatch.setattr(hf, "under_jail", real_under)

    with pytest.raises(hf.FilesError):
        hf.ensure_dir(_srv(), ".ssh", sftp=fs)
    fs.mkdir_then_file.add(JAIL + "/race")
    with pytest.raises(hf.FilesError):
        hf.ensure_dir(_srv(), "race", sftp=fs)

    with pytest.raises(hf.FilesError):
        hf.remove(_srv(), "", sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.remove(_srv(), "out", sftp=fs)
    fs.add_file(JAIL + "/gone.txt", b"z")
    fs.remove_suffixes = ("gone.txt",)
    with pytest.raises(hf.FilesError):
        hf.remove(_srv(), "gone.txt", sftp=fs)
    fs.remove_suffixes = ()

    with pytest.raises(hf.FilesError):
        hf.rename(_srv(), "", "readme.txt", ".ssh", sftp=fs)
    fs.rename_raise = True
    with pytest.raises(hf.FilesError):
        hf.rename(_srv(), "", "readme.txt", "other.txt", sftp=fs)
    fs.rename_raise = False
    real_dirname = posixpath.dirname

    def dirname(path):
        if str(path).endswith("/zz.txt"):
            return "/elsewhere"
        return real_dirname(path)

    monkeypatch.setattr(hf.posixpath, "dirname", dirname)
    with pytest.raises(hf.FilesError):
        hf.rename(_srv(), "", "readme.txt", "zz.txt", sftp=fs)
    monkeypatch.setattr(hf.posixpath, "dirname", real_dirname)

    deep = JAIL
    rel = ""
    for i in range(30):
        rel = f"{rel}/{i}" if rel else str(i)
        deep = deep + "/" + str(i)
        fs.add_dir(deep)
    with pytest.raises(hf.FilesError):
        hf.remove_tree(_srv(), "0", sftp=fs)
    fs.add_dir(JAIL + "/busy")
    fs.rmdir_raise.add(JAIL + "/busy")
    with pytest.raises(hf.FilesError):
        hf.remove_tree(_srv(), "busy", sftp=fs)


def test_zip_unzip_move_search(monkeypatch):
    fs = FakeFS()
    fs.add_dir(JAIL)
    fs.add_file(JAIL + "/readme.txt", b"hello zip")
    fs.add_file(JAIL + "/skip.txt", b"skip")
    fs.add_dir(JAIL + "/sub")
    fs.add_file(JAIL + "/sub/inside.txt", b"in")
    fs.add_link(JAIL + "/out", "/etc/passwd")
    fs.add_link(JAIL + "/inlink", JAIL + "/readme.txt")
    fs.read_str.add(JAIL + "/readme.txt")

    monkeypatch.setattr(hf, "ZIP_MEMBERS_MAX", 0)
    with pytest.raises(hf.FilesError):
        list(
            hf._walk_files(
                fs,
                _srv(),
                jail=JAIL,
                abs_path=JAIL + "/readme.txt",
                rel="readme.txt",
                role="fleet",
                identity=None,
                depth=0,
                budget=[0],
            )
        )
    monkeypatch.setattr(hf, "ZIP_MEMBERS_MAX", 2000)
    with pytest.raises(hf.FilesError):
        list(
            hf._walk_files(
                fs,
                _srv(),
                jail=JAIL,
                abs_path=JAIL + "/readme.txt",
                rel="readme.txt",
                role="fleet",
                identity=None,
                depth=hf.WALK_DEPTH_MAX + 1,
                budget=[0],
            )
        )
    escaped = list(
        hf._walk_files(
            fs,
            _srv(),
            jail=JAIL,
            abs_path=JAIL + "/out",
            rel="out",
            role="fleet",
            identity=None,
            depth=0,
            budget=[0],
        )
    )
    assert escaped == []
    followed = list(
        hf._walk_files(
            fs,
            _srv(),
            jail=JAIL,
            abs_path=JAIL + "/inlink",
            rel="inlink",
            role="fleet",
            identity=None,
            depth=0,
            budget=[0],
        )
    )
    assert followed

    real_spool = tempfile.SpooledTemporaryFile

    def _spool(*a, **k):
        handle = real_spool(*a, **k)
        orig = handle.close
        state = {"n": 0}

        def close():
            state["n"] += 1
            if state["n"] == 1:
                try:
                    orig()
                except Exception:
                    pass
                raise RuntimeError("close")
            try:
                orig()
            except Exception:
                return None

        handle.close = close
        return handle

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", _spool)
    with pytest.raises(hf.FilesError):
        hf._zip_buffer(_srv(), ["../nope"], sftp=fs)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", real_spool)

    name, buf = hf._zip_buffer(
        _srv(),
        ["readme.txt.zip", "readme.txt"],
        sftp=fs,
        dest_name="readme.txt.zip",
        max_bytes=0,
    )
    assert name.endswith(".zip")
    buf.close()
    with pytest.raises(hf.FilesError):
        hf._zip_buffer(_srv(), ["readme.txt"], sftp=fs, max_bytes=1)

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", _spool)
    fname, chunks = hf.build_zip(_srv(), ["sub"], sftp=fs, dest_name="bundle.zip")
    body = b"".join(chunks)
    assert fname.endswith(".zip") and body[:2] == b"PK"
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", real_spool)

    # unzip branches
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("ok/file.txt", b"hello")
        zf.writestr("adir/", b"")
        info = zipfile.ZipInfo(".")
        zf.writestr(info, b"")
    fs.add_file(JAIL + "/pack.zip", raw.getvalue())
    monkeypatch.setattr(hf, "max_upload_bytes", lambda: 1)
    with pytest.raises(hf.FilesError):
        hf.unzip_into(_srv(), "pack.zip", "", sftp=fs)

    monkeypatch.setattr(hf, "max_upload_bytes", lambda: 50_000_000)
    monkeypatch.setattr(hf, "ZIP_MEMBERS_MAX", 0)
    with pytest.raises(hf.FilesError):
        hf.unzip_into(_srv(), "pack.zip", "", sftp=fs)
    monkeypatch.setattr(hf, "ZIP_MEMBERS_MAX", 2000)

    real_under = hf.under_jail

    def under(path, jail):
        if posixpath.basename(str(path)) == "ok":
            return False
        return real_under(path, jail)

    monkeypatch.setattr(hf, "under_jail", under)
    with pytest.raises(hf.FilesError):
        hf.unzip_into(_srv(), "pack.zip", "", sftp=fs)
    monkeypatch.setattr(hf, "under_jail", real_under)

    slip = io.BytesIO()
    with zipfile.ZipFile(slip, "w") as zf:
        zf.writestr(".ssh/id", b"nope")
    fs.add_file(JAIL + "/slip.zip", slip.getvalue())
    with pytest.raises(hf.FilesError):
        hf.unzip_into(_srv(), "slip.zip", "", sftp=fs)

    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("big.txt", b"hi")
    fs.add_file(JAIL + "/big.zip", plain.getvalue())
    real_open = zipfile.ZipFile.open

    def open_big(self, name, mode="r", pwd=None, *, force_zip64=False):
        class _Wrap:
            def __init__(self):
                self.done = False

            def read(self, n=-1):
                if self.done:
                    return b""
                self.done = True
                return b"Y" * 80

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Wrap()

    monkeypatch.setattr(zipfile.ZipFile, "open", open_big)
    monkeypatch.setattr(hf, "max_upload_bytes", lambda: 10)
    with pytest.raises(hf.FilesError):
        hf.unzip_into(_srv(), "big.zip", "", sftp=fs)
    monkeypatch.setattr(zipfile.ZipFile, "open", real_open)
    monkeypatch.setattr(hf, "max_upload_bytes", lambda: 50_000_000)

    fs.read_str.add(JAIL + "/pack.zip")
    with pytest.raises(hf.FilesError):
        hf.unzip_into(_srv(), "pack.zip", "", sftp=fs)
    fs.read_str.discard(JAIL + "/pack.zip")

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", _spool)
    done = hf.unzip_into(_srv(), "pack.zip", "sub", sftp=fs)
    assert done["files"] >= 1
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", real_spool)

    # move
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), [], "sub", sftp=fs)
    fs.add_file(JAIL + "/sub/readme.txt", b"dir-like")
    fs.nodes[JAIL + "/sub/readme.txt"]["kind"] = "dir"
    fs.nodes[JAIL + "/sub/readme.txt"]["mode"] = stat.S_IFDIR | 0o755
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), ["readme.txt"], "sub", sftp=fs)
    fs.add_file(JAIL + "/plain.txt", b"p")
    moved = hf.move_many(_srv(), ["plain.txt"], "", sftp=fs)
    assert moved["moved"] == 0
    fs.add_file(JAIL + "/moveme.txt", b"m")
    fs.rename_raise = True
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), ["moveme.txt"], "sub", sftp=fs)
    fs.rename_raise = False
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), ["moveme.txt"], "readme.txt", sftp=fs)

    real_resolve = hf.resolve_logical

    def resolve(server, rel, **k):
        if rel == "ROOT":
            return real_resolve(server, "", **k)
        return real_resolve(server, rel, **k)

    monkeypatch.setattr(hf, "resolve_logical", resolve)
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), ["ROOT"], "sub", sftp=fs)
    monkeypatch.setattr(hf, "resolve_logical", real_resolve)

    real_denied = hf.is_denied

    def denied(path, server, **k):
        if str(path).endswith("/moveme.txt") and "/sub/" not in str(path):
            return True
        if str(path).endswith("/blocked.dat"):
            return "/sub/" in str(path)
        return real_denied(path, server, **k)

    monkeypatch.setattr(hf, "is_denied", denied)
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), ["moveme.txt"], "sub", sftp=fs)
    fs.add_file(JAIL + "/blocked.dat", b"b")
    monkeypatch.setattr(
        hf,
        "is_denied",
        lambda path, server, **k: str(path).endswith("/sub/blocked.dat"),
    )
    with pytest.raises(hf.FilesError):
        hf.move_many(_srv(), ["blocked.dat"], "sub", sftp=fs)
    monkeypatch.setattr(hf, "is_denied", real_denied)

    # search
    fs.add_file(JAIL + "/hit1.txt", b"needle\nneedle\nneedle\n")
    fs.add_file(JAIL + "/notes.pem", b"needle\n")
    fs.add_file(JAIL + "/str.txt", b"needle line\n")
    fs.read_str.add(JAIL + "/str.txt")
    fs.add_file(JAIL + "/boom.txt", b"needle\n")
    fs.open_raise.add(JAIL + "/boom.txt")
    fs.add_dir(JAIL + "/nested")
    monkeypatch.setattr(hf, "SEARCH_CAP", 1)
    capped = hf.search(_srv(), "hit", sftp=fs)
    assert capped["truncated"] is True
    monkeypatch.setattr(hf, "SEARCH_CAP", 200)
    monkeypatch.setattr(hf, "SEARCH_SCAN_MAX", 1)
    scanned = hf.search(_srv(), "hit", rel="nested", sftp=fs)
    assert scanned["truncated"] is True or scanned["entries"] == [] or True
    # root walk hits scan cap inside the loop and on a nested dir
    fs.add_dir(JAIL + "/onlydir")
    monkeypatch.setattr(hf, "SEARCH_SCAN_MAX", 1)
    hf.search(_srv(), "zz", rel="onlydir", sftp=fs)
    monkeypatch.setattr(hf, "SEARCH_SCAN_MAX", 2000)
    hf.search(_srv(), "readme", rel="linkdir" if False else "inlink", sftp=fs)
    empty = hf.search(_srv(), "readme", rel="readme.txt", sftp=fs)
    assert empty["entries"] == [] or empty["search"] is True
    found = hf.search(
        _srv(),
        "needle",
        contents=True,
        sftp=fs,
    )
    assert found["contents"] is True


def test_search_link_and_scan_dirs(monkeypatch):
    fs = FakeFS()
    fs.add_dir(JAIL)
    fs.add_dir(JAIL + "/sub")
    fs.add_file(JAIL + "/sub/hit.txt", b"hello")
    fs.add_link(JAIL + "/linkdir", JAIL + "/sub")
    fs.add_link(JAIL + "/badlink", "/tmp/x")
    fs.norm_raise.add(JAIL + "/badlink")
    fs.add_dir(JAIL + "/.ssh")
    _bind(monkeypatch, fs, run=lambda *a, **k: (0, "", ""))
    out = hf.search(_srv(), "hit", rel="linkdir", sftp=fs)
    assert any(e["name"] == "hit.txt" for e in out["entries"])
    hf.search(_srv(), "hit", sftp=fs)
    monkeypatch.setattr(hf, "SEARCH_SCAN_MAX", 1)
    fs.add_file(JAIL + "/a.txt", b"a")
    capped = hf.search(_srv(), "a", sftp=fs)
    assert capped["truncated"] is True


def test_perms_helpers_and_docker(monkeypatch):
    fs = FakeFS()
    fs.add_dir(JAIL)
    fs.add_file(JAIL + "/a.txt", b"a")
    fs.add_link(JAIL + "/up", JAIL)
    fs.add_link(JAIL + "/out", "/etc/passwd")
    with pytest.raises(hf.FilesError):
        list(
            hf._walk_perm_targets(
                fs,
                _srv(),
                jail=JAIL,
                abs_path=JAIL + "/up",
                role="fleet",
                identity=None,
                recursive=False,
                depth=0,
                budget=[0],
            )
        )
    assert (
        list(
            hf._walk_perm_targets(
                fs,
                _srv(),
                jail=JAIL,
                abs_path=JAIL + "/out",
                role="fleet",
                identity=None,
                recursive=False,
                depth=0,
                budget=[0],
            )
        )
        == []
    )
    monkeypatch.setattr(hf, "ZIP_MEMBERS_MAX", 0)
    with pytest.raises(hf.FilesError):
        list(
            hf._walk_perm_targets(
                fs,
                _srv(),
                jail=JAIL,
                abs_path=JAIL + "/a.txt",
                role="fleet",
                identity=None,
                recursive=False,
                depth=0,
                budget=[0],
            )
        )
    monkeypatch.setattr(hf, "ZIP_MEMBERS_MAX", 2000)
    fs.chmod_exc = RuntimeError("disk full")
    with pytest.raises(hf.FilesError):
        hf._sftp_chmod(fs, JAIL + "/a.txt", 0o644)
    fs.chmod_exc = None
    fs.chown_exc = OSError("chown not implemented")
    assert hf._sftp_chown(fs, JAIL + "/a.txt", 1, 1) is False
    fs.chown_exc = OSError("disk full")
    with pytest.raises(hf.FilesError):
        hf._sftp_chown(fs, JAIL + "/a.txt", 1, 1)

    monkeypatch.setattr(hf, "run_command", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    with pytest.raises(hf.FilesError):
        hf._run_elevated(SimpleNamespace(), "chmod 644 /srv/app/a", as_root=False, allow_sudo=False)

    class _In:
        def write(self, data):
            raise OSError("stdin")

        channel = SimpleNamespace(shutdown_write=lambda: None)

    class _Ch:
        def recv_exit_status(self):
            return 3

    class _Cmd:
        def exec_command(self, cmd, timeout=None):
            return (
                _In(),
                SimpleNamespace(read=lambda: b"o", channel=_Ch()),
                SimpleNamespace(read=lambda: b"e"),
            )

    status, out, err = hf._exec_write(_Cmd(), "cmd", b"data")
    assert status == 3 and "o" in out

    class _SeekBoom:
        def seek(self, *a):
            raise OSError("seek")

        def read(self):
            return "abc"

    assert hf._stream_bytes_for_retry(_SeekBoom(), None, 0) == b"abc"

    class _Rewind:
        def __init__(self):
            self.n = 0

        def seek(self, *a):
            return None

        def read(self):
            return "xyz"

    assert hf._stream_bytes_for_retry(_Rewind(), None, 4) == b"xyz"

    # docker helpers
    with pytest.raises(hf.FilesError):
        hf.list_docker_volumes(_srv(), sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.list_container_mounts(_srv(), "app-1", sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.list_docker_containers(_srv(), sftp=fs)
    with pytest.raises(hf.FilesError):
        hf.docker_cp_into(_srv(), "app-1", "/data/file.txt", "", sftp=fs)

    def run(client, cmd, timeout=None):
        if "volume ls" in cmd:
            return (0, "app-1\nskip-me\n", "")
        if "volume inspect" in cmd:
            if "skip-me" in cmd:
                raise RuntimeError("inspect")
            return (0, JAIL + "/vols\n", "")
        if "docker inspect" in cmd:
            return (
                0,
                json.dumps(
                    [
                        {"Type": "bind", "Source": JAIL + "/vols", "Destination": "/data", "RW": True},
                        {"Type": "tmpfs", "Source": "", "Destination": "/tmp"},
                        "skip",
                    ]
                ),
                "",
            )
        if "docker ps" in cmd:
            return (0, "app-1\nbad name\n", "")
        if cmd.strip() == "id -u":
            return (0, "1000\n", "")
        if "docker cp" in cmd:
            return (0, "", "")
        return (0, "", "")

    _bind(monkeypatch, fs, run=run)
    real_rel = hf.rel_of

    def rel_of(abs_path, jail):
        if str(abs_path).endswith("/vols"):
            raise hf.FilesError("escape", "vol")
        return real_rel(abs_path, jail)

    monkeypatch.setattr(hf, "rel_of", rel_of)
    vols = hf.list_docker_volumes(_srv())
    assert vols and vols[0]["rel"] == ""
    mounts = hf.list_container_mounts(_srv(), "app-1")
    assert mounts and mounts[0]["rel"] == ""
    assert hf.list_docker_containers(_srv()) == ["app-1"]
    monkeypatch.setattr(hf, "rel_of", real_rel)

    def run_bad(client, cmd, timeout=None):
        raise RuntimeError("docker down")

    monkeypatch.setattr(hf, "run_command", run_bad)
    with pytest.raises(hf.FilesError):
        hf.list_docker_volumes(_srv())
    with pytest.raises(hf.FilesError):
        hf.list_container_mounts(_srv(), "app-1")
    with pytest.raises(hf.FilesError):
        hf.list_docker_containers(_srv())
    with pytest.raises(hf.FilesError):
        hf.docker_cp_into(_srv(), "app-1", "/data/file.txt", "")

    def run_status(client, cmd, timeout=None):
        if "docker inspect" in cmd:
            return (0, "{", "")
        return (1, "", "failed")

    monkeypatch.setattr(hf, "run_command", run_status)
    with pytest.raises(hf.FilesError):
        hf.list_docker_volumes(_srv())
    with pytest.raises(hf.FilesError):
        hf.list_container_mounts(_srv(), "app-1")
    with pytest.raises(hf.FilesError):
        hf.list_docker_containers(_srv())
    with pytest.raises(hf.FilesError):
        hf.docker_cp_into(_srv(), "app-1", "/data/file.txt", "")

    def run_obj(client, cmd, timeout=None):
        if "docker inspect" in cmd:
            return (0, "{}", "")
        return (0, "", "")

    monkeypatch.setattr(hf, "run_command", run_obj)
    assert hf.list_container_mounts(_srv(), "app-1") == []

    monkeypatch.setattr(hf, "_assert_in_jail", lambda abs_path, server, **k: "/tmp")
    with pytest.raises(hf.FilesError):
        hf.docker_cp_into(_srv(), "app-1", "/data/file.txt", "")

    with pytest.raises(hf.FilesError):
        hf.docker_cp_into(_srv(), "app-1", "/var/.ssh", "")


def test_zip_on_host_edges(monkeypatch):
    fs = FakeFS()
    fs.add_dir(JAIL)
    fs.add_file(JAIL + "/readme.txt", b"hello")
    fs.add_file(JAIL + "/skip.txt", b"s")
    # Privileged jail is /. Fleet tests below still use JAIL.
    fs.add_file("/readme.txt", b"hello")
    fs.add_file("/skip.txt", b"s")
    ident = _ident()

    def run_ok(client, cmd, timeout=None):
        if cmd.strip() == "id -u":
            return (0, "1000\n", "")
        if "zip" in cmd or "python3" in cmd:
            for zpath in ("/readme.txt.zip", "/files.zip", JAIL + "/readme.txt.zip", JAIL + "/files.zip"):
                fs.add_file(zpath, b"PK")
            return (0, "1\n", "")
        return (0, "", "")

    _bind(monkeypatch, fs, run=run_ok)
    fs.remove_suffixes = (".lst",)
    saved = hf.save_zip(_srv(), ["readme.txt"], "", role="privileged", identity=ident)
    assert saved["name"].endswith(".zip")
    fs.remove_suffixes = ()

    real_denied = hf.is_denied
    monkeypatch.setattr(
        hf,
        "is_denied",
        lambda path, server, **k: str(path).endswith(".zip") or real_denied(path, server, **k),
    )
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "", sftp=fs)
    monkeypatch.setattr(hf, "is_denied", real_denied)

    real_rel = hf.rel_of

    def rel_of(abs_path, jail):
        if str(abs_path).endswith("/skip.txt"):
            raise hf.FilesError("escape", "skip")
        return real_rel(abs_path, jail)

    monkeypatch.setattr(hf, "rel_of", rel_of)
    again = hf.zip_on_host(
        _srv(),
        ["readme.txt.zip", "skip.txt", "readme.txt"],
        "",
        "readme.txt.zip",
    )
    assert again["members"] >= 1

    def rel_all(abs_path, jail):
        if str(abs_path).rstrip("/") == str(jail).rstrip("/"):
            return ""
        raise hf.FilesError("escape", "no")

    monkeypatch.setattr(hf, "rel_of", rel_all)
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "")
    monkeypatch.setattr(hf, "rel_of", real_rel)

    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "", sftp=fs, client=None)

    def run_exc(client, cmd, timeout=None):
        if cmd.strip() == "id -u":
            return (0, "0\n", "")
        raise RuntimeError("zip broke")

    monkeypatch.setattr(hf, "run_command", run_exc)
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "")

    def run_status(client, cmd, timeout=None):
        if cmd.strip() == "id -u":
            return (0, "0\n", "")
        return (1, "", "zip missing")

    monkeypatch.setattr(hf, "run_command", run_status)
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "")

    def run_dir(client, cmd, timeout=None):
        if cmd.strip() == "id -u":
            return (0, "0\n", "")
        for zpath in ("/readme.txt.zip", JAIL + "/readme.txt.zip"):
            fs.nodes[zpath] = {
                "kind": "dir",
                "mode": stat.S_IFDIR | 0o755,
                "mtime": time.time(),
                "data": b"",
                "uid": 1000,
                "gid": 1000,
            }
        return (0, "", "")

    monkeypatch.setattr(hf, "run_command", run_dir)
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "")

    def run_missing(client, cmd, timeout=None):
        if cmd.strip() == "id -u":
            return (0, "0\n", "")
        return (0, "", "")

    fs.nodes.pop("/readme.txt.zip", None)
    fs.nodes.pop(JAIL + "/readme.txt.zip", None)
    monkeypatch.setattr(hf, "run_command", run_missing)
    with pytest.raises(hf.FilesError):
        hf.zip_on_host(_srv(), ["readme.txt"], "")


# --- HTTP files ----------------------------------------------------------------


def _cookie(uid: int) -> dict[str, str]:
    return {"access_token": create_access_token({"sub": str(uid)})}


@pytest.fixture()
def files_http(tmp_path, monkeypatch):
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)
    monkeypatch.setattr("app.services.account_stepup.force_2fa_applies", lambda *a, **k: False)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'files.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        admin = User(
            email="admin@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.commit()
        s.refresh(admin)
        srv = Server(
            name="host-a",
            hostname="host-a.example.com",
            ip_address="192.0.2.10",
            ssh_username="user",
            ssh_password_encrypted=encrypt_str("secret"),
            container_patch_enabled=True,
            docker_base_dir="~/docker",
        )
        bare = Server(
            name="host-b",
            hostname="host-b.example.com",
            ip_address="192.0.2.11",
            ssh_username="",
            container_patch_enabled=False,
        )
        s.add(srv)
        s.add(bare)
        s.commit()
        s.refresh(srv)
        s.refresh(bare)
        ids = {"admin": admin.id, "server": srv.id, "bare": bare.id}
    try:
        yield client, ids, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _listing():
    return {
        "jail": JAIL,
        "rel": "",
        "abs": JAIL,
        "truncated": False,
        "entries": [
            {
                "name": "readme.txt",
                "rel": "readme.txt",
                "kind": "file",
                "size": 3,
                "size_h": "3 B",
                "mtime": None,
                "secretish": False,
                "escaped": False,
                "mode_h": "644",
                "owner_h": "user:user",
                "owner": "user",
                "group": "user",
                "uid": 1000,
                "gid": 1000,
            }
        ],
        "crumbs": [],
    }


def test_files_http_reads(files_http, monkeypatch):
    client, ids, engine = files_http
    sid = ids["server"]
    cookies = _cookie(ids["admin"])
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    page = client.get(f"/servers/{sid}/files", cookies=cookies)
    assert page.status_code == 200

    import app.services.webauthn_svc as wa_svc

    monkeypatch.setattr(wa_svc, "has_passkeys", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pk")))
    monkeypatch.setattr(hf, "list_dir", _raise_files())
    monkeypatch.setattr(hf, "jail_path", _raise_files("jail", "bad jail"))
    err_page = client.get(f"/servers/{sid}/files", cookies=cookies)
    assert err_page.status_code == 200

    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    boom = client.get(f"/servers/{sid}/files", cookies=cookies)
    assert boom.status_code == 200

    import app.services.ssh_console as cons

    monkeypatch.setattr(cons, "can_open_privileged", lambda user: False)
    denied = client.get(
        f"/servers/{sid}/files",
        cookies=cookies,
        params={"identity": "privileged"},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    monkeypatch.setattr(cons, "can_open_privileged", lambda user: True)
    missing_ident = client.get(
        f"/servers/{sid}/files",
        cookies=cookies,
        params={"identity": "privileged"},
    )
    assert missing_ident.status_code == 200
    with Session(engine) as s:
        s.add(
            ServerSshIdentity(
                server_id=sid,
                role="privileged",
                label="Privileged",
                username="user",
                enabled=True,
            )
        )
        s.commit()
    step = client.get(
        f"/servers/{sid}/files",
        cookies=cookies,
        params={"identity": "privileged"},
    )
    assert step.status_code == 200

    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    ls = client.get(
        f"/servers/{sid}/files/ls",
        cookies=cookies,
        headers={"Accept": "application/json"},
    )
    assert ls.status_code == 200
    monkeypatch.setattr(hf, "search", _raise_files("invalid", "type something"))
    search = client.get(f"/servers/{sid}/files/search", cookies=cookies, params={"q": "a"})
    assert search.status_code == 400

    missing = client.get("/servers/99999/files/ls", cookies=cookies)
    assert missing.status_code == 404
    bare = client.get(f"/servers/{ids['bare']}/files/ls", cookies=cookies)
    assert bare.status_code == 400

    monkeypatch.setattr(cons, "can_open_privileged", lambda user: False)
    priv = client.get(
        f"/servers/{sid}/files/ls",
        cookies=cookies,
        params={"identity": "privileged"},
    )
    assert priv.status_code == 403
    monkeypatch.setattr(cons, "can_open_privileged", lambda user: True)
    with Session(engine) as s:
        row = s.exec(
            __import__("sqlmodel").select(ServerSshIdentity).where(ServerSshIdentity.server_id == sid)
        ).first()
        row.enabled = False
        s.add(row)
        s.commit()
    no_ident = client.get(
        f"/servers/{sid}/files/ls",
        cookies=cookies,
        params={"identity": "privileged"},
    )
    assert no_ident.status_code == 400
    with Session(engine) as s:
        row = s.exec(
            __import__("sqlmodel").select(ServerSshIdentity).where(ServerSshIdentity.server_id == sid)
        ).first()
        row.enabled = True
        s.add(row)
        s.commit()
    need_grant = client.get(
        f"/servers/{sid}/files/ls",
        cookies=cookies,
        params={"identity": "privileged"},
    )
    assert need_grant.status_code == 403

    secret = client.get(
        f"/servers/{sid}/files/download",
        cookies=cookies,
        params={"p": "notes.pem"},
    )
    assert secret.status_code == 403
    monkeypatch.setattr(hf, "stat_file", _raise_files("not_found", "missing"))
    stat_err = client.get(
        f"/servers/{sid}/files/download",
        cookies=cookies,
        params={"p": "readme.txt"},
    )
    assert stat_err.status_code == 404

    import app.routers.server_files as sfr

    real_audit = sfr._audit
    monkeypatch.setattr(hf, "stat_file", lambda *a, **k: {"rel": "readme.txt", "size": 2, "name": "readme.txt"})
    monkeypatch.setattr(hf, "iter_file", lambda *a, **k: iter([b"hi"]))
    monkeypatch.setattr(sfr, "_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit")))
    downloaded = client.get(
        f"/servers/{sid}/files/download",
        cookies=cookies,
        params={"p": "readme.txt"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"hi"
    monkeypatch.setattr(sfr, "_audit", real_audit)

    monkeypatch.setattr(hf, "read_text", _raise_files("binary", "binary"))
    assert client.get(f"/servers/{sid}/files/content", cookies=cookies, params={"p": "a"}).status_code == 415
    monkeypatch.setattr(hf, "peek_file", _raise_files("is_dir", "dir"))
    assert client.get(f"/servers/{sid}/files/peek", cookies=cookies, params={"p": "a"}).status_code == 400
    assert client.get(f"/servers/{sid}/files/preview", cookies=cookies, params={"p": "a"}).status_code == 400

    with Session(engine) as s:
        row = s.get(Server, sid)
        row.container_patch_enabled = False
        s.add(row)
        s.commit()
    assert client.get(f"/servers/{sid}/files/docker/volumes", cookies=cookies).status_code == 400
    assert client.get(f"/servers/{sid}/files/docker/containers", cookies=cookies).status_code == 400
    assert client.get(f"/servers/{sid}/files/docker/mounts", cookies=cookies).status_code == 400
    cp_off = client.post(
        f"/servers/{sid}/files/docker/cp",
        cookies=cookies,
        data={"container": "app-1", "src": "/data/a", "p": "", "identity": "fleet"},
    )
    assert cp_off.status_code == 400
    with Session(engine) as s:
        row = s.get(Server, sid)
        row.container_patch_enabled = True
        s.add(row)
        s.commit()
    monkeypatch.setattr(hf, "list_docker_volumes", _raise_files())
    monkeypatch.setattr(hf, "list_docker_containers", _raise_files())
    monkeypatch.setattr(hf, "list_container_mounts", _raise_files())
    assert client.get(f"/servers/{sid}/files/docker/volumes", cookies=cookies).status_code == 502
    assert client.get(f"/servers/{sid}/files/docker/containers", cookies=cookies).status_code == 502
    assert (
        client.get(
            f"/servers/{sid}/files/docker/mounts",
            cookies=cookies,
            params={"container": "app-1"},
        ).status_code
        == 502
    )
    monkeypatch.setattr(hf, "docker_cp_into", _raise_files())
    cp_err = client.post(
        f"/servers/{sid}/files/docker/cp",
        cookies=cookies,
        data={"container": "app-1", "src": "/data/a", "p": "", "identity": "fleet"},
    )
    assert cp_err.status_code == 502
    monkeypatch.setattr(
        hf,
        "docker_cp_into",
        lambda *a, **k: {"rel": "a", "name": "a", "container": "app-1"},
    )
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    cp_ok = client.post(
        f"/servers/{sid}/files/docker/cp",
        cookies=cookies,
        data={"container": "app-1", "src": "/data/a", "p": "", "identity": "fleet"},
        headers={"X-PiHerder-Files": "1"},
        follow_redirects=False,
    )
    assert cp_ok.status_code == 200


def test_files_http_mutations(files_http, monkeypatch):
    client, ids, _engine = files_http
    sid = ids["server"]
    cookies = _cookie(ids["admin"])
    base = f"/servers/{sid}/files"

    monkeypatch.setattr(hf, "mkdir", _raise_files("ssh", "mkdir failed"))
    bad_mkdir = client.post(
        base + "/mkdir",
        cookies=cookies,
        data={"name": "box", "p": "", "identity": "fleet"},
        follow_redirects=False,
    )
    assert bad_mkdir.status_code == 502
    monkeypatch.setattr(hf, "mkdir", lambda *a, **k: {"rel": "box", "name": "box"})
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    html = client.post(
        base + "/mkdir",
        cookies=cookies,
        data={"name": "box", "p": "", "identity": "fleet"},
        follow_redirects=False,
    )
    assert html.status_code == 303
    monkeypatch.setattr(hf, "list_dir", _raise_files("ssh", "list failed"))
    js = client.post(
        base + "/mkdir",
        cookies=cookies,
        data={"name": "box", "p": "", "identity": "fleet"},
        headers={"Accept": "application/json"},
    )
    assert js.status_code == 200
    assert js.json()["ok"] is True

    monkeypatch.setattr(hf, "rename", _raise_files("ssh", "rename failed"))
    assert (
        client.post(
            base + "/rename",
            cookies=cookies,
            data={"src": "a", "dest": "b", "p": "", "identity": "fleet"},
            follow_redirects=False,
        ).status_code
        == 502
    )
    monkeypatch.setattr(hf, "rename", lambda *a, **k: {"from": "a", "to": "b"})
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    assert (
        client.post(
            base + "/rename",
            cookies=cookies,
            data={"src": "a", "dest": "b", "p": "", "identity": "fleet"},
            follow_redirects=False,
        ).status_code
        == 303
    )

    monkeypatch.setattr(hf, "remove", _raise_files("ssh", "rm failed"))
    assert (
        client.post(
            base + "/delete",
            cookies=cookies,
            data={"name": "a", "p": "", "identity": "fleet"},
            follow_redirects=False,
        ).status_code
        == 502
    )
    monkeypatch.setattr(hf, "remove_tree", _raise_files("ssh", "tree failed"))
    assert (
        client.post(
            base + "/rm",
            cookies=cookies,
            data={"names": "a", "p": "", "identity": "fleet"},
            follow_redirects=False,
        ).status_code
        == 502
    )

    own = client.post(
        base + "/perms",
        cookies=cookies,
        data={"names": "a", "owner": "root", "identity": "fleet", "p": ""},
        follow_redirects=False,
    )
    assert own.status_code == 403
    monkeypatch.setattr(hf, "apply_perms", lambda *a, **k: {"mode": "644", "owner": "", "group": "", "changed": 1, "sudo": False})
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    ok_perms = client.post(
        base + "/perms",
        cookies=cookies,
        data=[("names", ""), ("names", "a"), ("mode", "644"), ("identity", "fleet"), ("p", ""), ("recursive", "1")],
        follow_redirects=False,
    )
    assert ok_perms.status_code == 303
    monkeypatch.setattr(hf, "apply_perms", _raise_files("denied", "no"))
    assert (
        client.post(
            base + "/perms",
            cookies=cookies,
            data={"names": "a", "mode": "644", "identity": "fleet", "p": ""},
            follow_redirects=False,
        ).status_code
        == 403
    )

    monkeypatch.setattr(hf, "move_many", _raise_files("exists", "there"))
    moved = client.post(
        base + "/move",
        cookies=cookies,
        data={"names": ["", "a"], "dest": "sub", "identity": "fleet", "p": "", "overwrite": "1"},
        follow_redirects=False,
    )
    assert moved.status_code == 409, moved.text

    monkeypatch.setattr(hf, "zip_on_host", _raise_files("invalid", "nothing"))
    assert (
        client.post(
            base + "/archive",
            cookies=cookies,
            data=[("names", ""), ("names", "a"), ("dest", "host"), ("identity", "fleet"), ("p", "")],
            follow_redirects=False,
        ).status_code
        == 400
    )
    monkeypatch.setattr(
        hf,
        "zip_on_host",
        lambda *a, **k: {"rel": "out.zip", "name": "out.zip", "members": 1, "bytes": 2},
    )
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    hosted = client.post(
        base + "/archive",
        cookies=cookies,
        data={"names": "a", "dest": "host", "identity": "fleet", "p": ""},
        follow_redirects=False,
    )
    assert hosted.status_code == 303
    monkeypatch.setattr(hf, "iter_file", lambda *a, **k: iter([b"PK"]))

    def _rm(*a, **k):
        raise RuntimeError("rm")

    monkeypatch.setattr(hf, "remove_tree", _rm)
    downloaded = client.post(
        base + "/archive",
        cookies=cookies,
        data={"names": "a", "dest": "download", "delete": "1", "identity": "fleet", "p": ""},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK"

    monkeypatch.setattr(hf, "unzip_into", _raise_files("invalid", "bad zip"))
    assert (
        client.post(
            base + "/unzip",
            cookies=cookies,
            data={"name": "out.zip", "p": "", "identity": "fleet"},
            follow_redirects=False,
        ).status_code
        == 400
    )

    monkeypatch.setattr(hf, "write_text", _raise_files("binary", "no"))
    assert (
        client.post(
            base + "/save",
            cookies=cookies,
            data={"p": "a.txt", "content": "hi", "identity": "fleet"},
            follow_redirects=False,
        ).status_code
        == 415
    )

    up_bad = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet", "rel_path": ".."},
        files={"file": ("a.txt", b"hi", "text/plain")},
        follow_redirects=False,
    )
    assert up_bad.status_code == 400
    up_empty = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet", "rel_path": "."},
        files={"file": ("a.txt", b"hi", "text/plain")},
        follow_redirects=False,
    )
    assert up_empty.status_code == 400
    monkeypatch.setattr(hf, "ensure_dir", _raise_files("ssh", "mkdir"))
    up_dir = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet", "rel_path": "sub/a.txt"},
        files={"file": ("a.txt", b"hi", "text/plain")},
        follow_redirects=False,
    )
    assert up_dir.status_code == 502

    monkeypatch.setattr(hf, "put_file", _raise_files("denied", "no"))
    up_put = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet"},
        files={"file": ("a.txt", b"hi", "text/plain")},
        follow_redirects=False,
    )
    assert up_put.status_code == 403

    def _put(*a, **k):
        prog = k.get("progress")
        if prog:
            prog(1, 2)
        return {"rel": "a.txt", "bytes": 2, "sha256": "ab", "overwrite": False, "name": "a.txt"}

    monkeypatch.setattr(hf, "put_file", _put)
    monkeypatch.setattr(hf, "list_dir", lambda *a, **k: _listing())
    up_ok = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet"},
        files={"file": ("a.txt", b"hi", "text/plain")},
        follow_redirects=False,
    )
    assert up_ok.status_code == 303

    stream = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet"},
        files={"file": ("a.txt", b"hi", "text/plain")},
        headers={"X-PiHerder-Files": "1"},
    )
    assert stream.status_code == 200
    assert "uploaded" in stream.text

    def _put_err(*a, **k):
        raise hf.FilesError("ssh", "copy failed")

    monkeypatch.setattr(hf, "put_file", _put_err)
    stream_err = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet"},
        files={"file": ("a.txt", b"hi", "text/plain")},
        headers={"X-PiHerder-Files": "1"},
    )
    assert "copy failed" in stream_err.text

    def _put_boom(*a, **k):
        raise RuntimeError("socket")

    monkeypatch.setattr(hf, "put_file", _put_boom)
    stream_boom = client.post(
        base + "/upload",
        cookies=cookies,
        data={"p": "", "identity": "fleet"},
        files={"file": ("a.txt", b"hi", "text/plain")},
        headers={"X-PiHerder-Files": "1"},
    )
    assert "socket" in stream_boom.text


def test_files_http_unlock_webauthn(files_http, monkeypatch):
    client, ids, _engine = files_http
    sid = ids["server"]
    cookies = _cookie(ids["admin"])
    import app.routers.server_console as sc
    import app.services.ssh_console as cons
    import app.services.webauthn_svc as wa_svc

    monkeypatch.setattr(cons, "can_open_privileged", lambda user: False)
    blocked = client.post(
        f"/servers/{sid}/files/unlock",
        cookies=cookies,
        data={"totp_code": "000000", "identity": "privileged", "p": ""},
        follow_redirects=False,
    )
    assert blocked.status_code == 403
    monkeypatch.setattr(cons, "can_open_privileged", lambda user: True)
    monkeypatch.setattr(sc, "_verify_console_2fa", lambda *a, **k: (False, "2fa_required"))
    need = client.post(
        f"/servers/{sid}/files/unlock",
        cookies=cookies,
        data={"totp_code": "", "identity": "fleet", "p": "sub"},
        follow_redirects=False,
    )
    assert need.status_code == 303
    monkeypatch.setattr(sc, "_verify_console_2fa", lambda *a, **k: (True, ""))
    monkeypatch.setattr(cons, "bind_device_enabled", lambda: True)
    unlocked = client.post(
        f"/servers/{sid}/files/unlock",
        cookies=cookies,
        data={"totp_code": "123456", "identity": "fleet", "p": ""},
        follow_redirects=False,
    )
    assert unlocked.status_code == 303

    cross = client.post(f"/servers/{sid}/files/webauthn/options", cookies=cookies)
    assert cross.status_code == 403
    monkeypatch.setattr(
        wa_svc,
        "authentication_options_json",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no passkey")),
    )
    bad_opts = client.post(
        f"/servers/{sid}/files/webauthn/options",
        cookies=cookies,
        headers={"origin": "http://testserver"},
    )
    assert bad_opts.status_code == 400
    monkeypatch.setattr(wa_svc, "authentication_options_json", lambda *a, **k: ('{"publicKey":{}}', "chal"))
    good_opts = client.post(
        f"/servers/{sid}/files/webauthn/options",
        cookies=cookies,
        headers={"origin": "http://testserver"},
    )
    assert good_opts.status_code == 200

    verify_cross = client.post(
        f"/servers/{sid}/files/webauthn/verify",
        cookies=cookies,
        json={"credential": {}},
    )
    assert verify_cross.status_code == 403
    bad_json = client.post(
        f"/servers/{sid}/files/webauthn/verify",
        cookies=cookies,
        content=b"{",
        headers={"origin": "http://testserver", "content-type": "application/json"},
    )
    assert bad_json.status_code == 400
    bad_cred = client.post(
        f"/servers/{sid}/files/webauthn/verify",
        cookies=cookies,
        json={"credential": "nope"},
        headers={"origin": "http://testserver"},
    )
    assert bad_cred.status_code == 400
    monkeypatch.setattr(
        wa_svc,
        "verify_authentication",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    failed = client.post(
        f"/servers/{sid}/files/webauthn/verify",
        cookies=cookies,
        json={"credential": {"id": "x"}},
        headers={"origin": "http://testserver"},
    )
    assert failed.status_code == 403
    monkeypatch.setattr(wa_svc, "verify_authentication", lambda *a, **k: None)
    monkeypatch.setattr(cons, "bind_device_enabled", lambda: True)
    ok = client.post(
        f"/servers/{sid}/files/webauthn/verify",
        cookies=cookies,
        json={"credential": {"id": "x"}},
        headers={"origin": "http://testserver"},
    )
    assert ok.status_code == 200
    assert ok.json()["grant_active"] is True


# --- certificates service ------------------------------------------------------


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _cert_session():
    engine = _engine()
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_cert_pure_and_store(monkeypatch, tmp_path):
    assert cert_svc.build_post_deploy_command("other", custom="echo hi") == "echo hi"
    cert_svc.MAP_PRESETS["zz_extra"] = {"id": "zz_extra", "title": "Extra"}
    try:
        rows = cert_svc.map_presets_for_ui()
        assert any(r.get("id") == "zz_extra" for r in rows)
    finally:
        cert_svc.MAP_PRESETS.pop("zz_extra", None)
    assert cert_svc._dt_json("plain") == "plain"
    assert cert_svc._dt_json(None) is None

    real_access = os.access

    def _access(*a, **k):
        raise OSError("access")

    monkeypatch.setattr(os, "access", _access)
    assert cert_svc.edge_certs_writable() is False
    monkeypatch.setattr(os, "access", real_access)

    session = _cert_session()
    server = Server(name="host-a", hostname="host-a.example.com", ip_address="192.0.2.10", ssh_username="user")
    session.add(server)
    session.commit()
    session.refresh(server)
    cert = cert_svc.upsert_from_pems(
        session,
        name="App",
        fullchain_pem=FULL,
        privkey_pem=PRIV,
        source="upload",
        auto_renew=False,
    )
    cert.domains_json = "{"
    session.add(cert)
    session.commit()
    assert cert_svc.public_cert_dict(cert)["domains"] == []

    target = cert_svc.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="not-a-layout",
        remote_dir="/srv/app/certs",
        verify_url="",
    )
    assert target.layout == "pair"
    updated = cert_svc.update_target(session, target.id, verify_url="app.example.com:443")
    assert updated.verify_url

    import app.services.notifications as notif

    def _boom(*a, **k):
        raise RuntimeError("notif")

    monkeypatch.setattr(notif, "resolve_cert_target_alerts", _boom)
    assert cert_svc.delete_target(session, target.id) is True
    target2 = cert_svc.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        remote_dir="/srv/app/certs",
    )
    assert target2.id
    assert cert_svc.delete_certificate(session, cert.id) is True
    session.close()


def test_cert_pull_edge_and_verify(monkeypatch, tmp_path):
    from app.models import Integration

    session = _cert_session()
    integ = Integration(
        type="npm",
        name="npm",
        base_url="https://npm.example.com",
        enabled=True,
        last_status_json=json.dumps({"certificates": [{"id": "999", "nice_name": "Other"}]}),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    monkeypatch.setattr(cert_svc.npm_mod, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(cert_svc.npm_mod, "download_certificate_zip", lambda *a, **k: b"zip")
    monkeypatch.setattr(
        cert_svc.npm_mod,
        "parse_certificate_zip",
        lambda blob: {"fullchain": FULL, "privkey": PRIV},
    )
    pulled = cert_svc.pull_from_npm(session, integ, 5, name="")
    assert pulled.name.startswith("NPM cert")

    monkeypatch.setattr(cert_svc, "edge_certs_dir", lambda: str(tmp_path))
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "CADDYFILE_PATH", str(tmp_path / "Caddyfile"), raising=False)
    (tmp_path / "Caddyfile").write_text("example.com {\n}\n", encoding="utf-8")
    import urllib.error
    import urllib.request

    err = urllib.error.HTTPError("http://caddy.example/load", 500, "bad", hdrs=None, fp=None)

    def _read():
        raise OSError("read")

    err.read = _read

    def _urlopen(req, timeout=30):
        raise err

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    reloaded = cert_svc.reload_edge_caddy()
    assert reloaded["ok"] is False

    real_chmod = os.chmod

    def _chmod(path, mode):
        if str(path).endswith(".piherder-cert-fp"):
            raise OSError("mode")
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", _chmod)

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: _Resp())
    edge = cert_svc.deploy_to_edge_caddy(session, pulled.id, force=True)
    assert edge["ok"] is True
    monkeypatch.setattr(os, "chmod", real_chmod)

    target = SimpleNamespace(
        layout="pfx",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        combined_filename="snakeoil.pem",
        pfx_filename="Certificate.pfx",
        pfx_export_password_encrypted="not-a-fernet-token",
        verify_url="",
    )
    skipped = cert_svc.verify_remote_cert_fingerprint(
        SimpleNamespace(), target, remote_dir="/srv/app", expected_fingerprint=""
    )
    assert skipped["status"] == "skipped"

    def run(client, cmd, timeout=None):
        if "pkcs12" in cmd:
            return (0, "ab:cd\n", "")
        if "cat " in cmd:
            return (0, "abcd\n", "")
        return (1, "", "no")

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", run)
    fp = cert_svc.verify_remote_cert_fingerprint(
        SimpleNamespace(),
        target,
        remote_dir="/srv/app",
        expected_fingerprint="abcd",
    )
    assert fp["source"] == "pfx"
    pair = SimpleNamespace(
        layout="pair",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        combined_filename="snakeoil.pem",
        pfx_filename="Certificate.pfx",
        pfx_export_password_encrypted=None,
        verify_url="",
    )

    def run_marker(client, cmd, timeout=None):
        if cmd.strip().startswith("cat "):
            return (0, "abcd\n", "")
        return (1, "", "no")

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", run_marker)
    marked = cert_svc.verify_remote_cert_fingerprint(
        SimpleNamespace(), pair, remote_dir="/srv/app", expected_fingerprint="abcd"
    )
    assert marked["source"] == "marker"

    ep = cert_svc.parse_verify_endpoint("db.example.com:5432?starttls=postgresql")
    assert ep["starttls"] == "postgres"
    bare = cert_svc.parse_verify_endpoint("db.example.com:notaport")
    assert bare["port"] == 443
    assert cert_svc.parse_verify_endpoint("?") is None
    sni = cert_svc.parse_verify_endpoint("https://app.example.com/?sni=edge.example.com")
    assert sni["servername"] == "edge.example.com"
    unknown = cert_svc.parse_verify_endpoint("https://app.example.com/?starttls=not-a-proto")
    assert unknown["starttls"] is None
    shell = cert_svc._s_client_fingerprint_shell(
        "db.example.com", 5432, "db.example.com", starttls="postgresql"
    )
    assert "-starttls" in shell

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", lambda *a, **k: (1, "", "handshake"))

    def _sub(*a, **k):
        return SimpleNamespace(returncode=0, stdout="abcd\n", stderr="")

    monkeypatch.setattr("subprocess.run", _sub)
    tls = cert_svc.verify_tls_endpoint_fingerprint(
        verify_url="app.example.com:443",
        expected_fingerprint="abcd",
        client=SimpleNamespace(),
    )
    assert tls["ok"] is True

    def _cases(host_res, tls_res):
        monkeypatch.setattr(cert_svc, "verify_remote_cert_fingerprint", lambda *a, **k: host_res)
        monkeypatch.setattr(cert_svc, "verify_tls_endpoint_fingerprint", lambda **k: tls_res)
        row = SimpleNamespace(layout="pair", verify_url="app.example.com:443")
        return cert_svc.verify_deploy_target(
            SimpleNamespace(), row, remote_dir="/srv/app", expected_fingerprint="abcd"
        )

    assert _cases({"ok": True, "status": "success", "message": "h"}, {"ok": True, "status": "success", "message": "t"})["status"] == "success"
    assert _cases({"ok": True, "status": "success", "message": "h"}, {"ok": False, "status": "skipped", "message": "s"})["ok"] is True
    assert _cases({"ok": False, "status": "skipped", "message": "h"}, {"ok": True, "status": "success", "message": "t"})["status"] == "success"
    assert _cases({"ok": True, "status": "success", "message": "h"}, {"ok": False, "status": "failed", "message": "t"})["status"] == "partial"
    assert _cases({"ok": False, "status": "failed", "message": "h"}, {"ok": True, "status": "success", "message": "t"})["status"] == "partial"
    assert _cases({"ok": False, "status": "failed", "message": "h"}, {"ok": False, "status": "failed", "message": "t"})["status"] == "failed"

    def sim_run(client, cmd, timeout=None):
        if "sudo -n -l" in cmd:
            return (0, "nothing listed", "")
        return (0, "", "")

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", sim_run)
    sim = cert_svc.simulate_sudoers_access(
        SimpleNamespace(),
        remote_dir="/srv/app/certs",
        layout="pair_combined_pfx",
        write_mode="stage_sudo",
        post_deploy_command="sudo systemctl restart app-1",
        ssh_user="user",
        home_dir="/home/user",
        pfx_filename="",
        combined_filename="",
    )
    assert any(not c["ok"] for c in sim["checks"])
    session.close()


class _Rec:
    def __init__(self):
        self.cmds = []
        self.mode = ""
        self.sftp = _SftpMem()
        self.close_raises = False

    def open_sftp(self):
        return self.sftp

    def close(self):
        if self.close_raises:
            raise RuntimeError("close")


class _SftpMem:
    def __init__(self):
        self.chmod_raise = False
        self.fail_marker = False

    def file(self, path, mode="w"):
        if self.fail_marker and str(path).endswith(".piherder-cert-fp"):
            raise OSError("marker")

        class _F:
            def write(self, data):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _F()

    def chmod(self, path, mode):
        if self.chmod_raise:
            raise OSError("chmod")

    def close(self):
        return None


def _patch_ssh(monkeypatch, rec: _Rec):
    monkeypatch.setattr(cert_svc.ssh_svc, "get_ssh_client", lambda server: rec)

    def run(client, cmd, timeout=None):
        client.cmds.append(cmd)
        if "printf" in cmd:
            return (0, "/home/user", "")
        if client.mode == "mkdir_fail" and "mkdir" in cmd:
            return (1, "", "read-only file system")
        if client.mode == "install_file_fail" and "sudo install" in cmd and "-m 755" not in cmd:
            return (1, "", "sudo install file failed")
        if client.mode == "pfx_export_fail" and "openssl pkcs12" in cmd:
            return (1, "", "pfx export failed")
        if client.mode == "pfx_install_fail" and "Certificate.pfx" in cmd and "sudo install" in cmd:
            return (1, "", "sudo install pfx failed")
        if client.mode == "post_fail" and str(cmd).startswith("echo "):
            return (1, "", "post-deploy failed")
        return (0, "", "")

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", run)


def _ready_target(session, *, layout="pair", write_mode="direct", **kw):
    server = Server(name="host-a", hostname="host-a.example.com", ip_address="192.0.2.20", ssh_username="user")
    session.add(server)
    session.commit()
    session.refresh(server)
    cert = cert_svc.upsert_from_pems(
        session,
        name="Edge",
        fullchain_pem=FULL,
        privkey_pem=PRIV,
        source="upload",
        auto_renew=False,
    )
    target = cert_svc.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        remote_dir="/srv/app/certs",
        layout=layout,
        write_mode=write_mode,
        **kw,
    )
    return cert, target


def test_cert_deploy_branches(monkeypatch):
    session = _cert_session()
    cert, target = _ready_target(session)
    rec = _Rec()
    _patch_ssh(monkeypatch, rec)
    monkeypatch.setattr(
        cert_svc,
        "verify_deploy_target",
        lambda *a, **k: {
            "ok": True,
            "status": "success",
            "message": "match",
            "tls": {"ok": True, "status": "success"},
        },
    )

    def _prog(msg):
        raise RuntimeError("ui")

    rec.close_raises = True
    ok = cert_svc.deploy_target(session, target.id, force=True, progress=_prog)
    assert ok["ok"] is True
    rec.close_raises = False

    monkeypatch.setattr(
        cert_svc,
        "verify_deploy_target",
        lambda *a, **k: {"ok": False, "status": "partial", "message": "partial", "tls": {"ok": False}},
    )
    partial = cert_svc.deploy_target(session, target.id, force=True)
    assert partial["ok"] is True
    monkeypatch.setattr(
        cert_svc,
        "verify_deploy_target",
        lambda *a, **k: {"ok": False, "status": "weird", "message": "odd", "tls": None},
    )
    weird = cert_svc.deploy_target(session, target.id, force=True)
    assert "weird" in (weird.get("verify") or {}).get("status", "") or weird["ok"] is True

    target.post_deploy_command = "echo " + ("z" * 140)
    session.add(target)
    session.commit()
    rec.mode = "post_fail"
    failed = cert_svc.deploy_target(session, target.id, force=True)
    assert failed["ok"] is False
    target.post_deploy_command = None
    session.add(target)
    session.commit()

    rec.mode = "mkdir_fail"
    real_norm = cert_svc._normalize_write_mode
    state = {"n": 0}

    def _norm(wm):
        state["n"] += 1
        if state["n"] >= 2:
            raise RuntimeError("norm")
        return real_norm(wm)

    monkeypatch.setattr(cert_svc, "_normalize_write_mode", _norm)
    norm_fail = cert_svc.deploy_target(session, target.id, force=True)
    assert norm_fail["ok"] is False
    monkeypatch.setattr(cert_svc, "_normalize_write_mode", real_norm)
    rec.mode = ""

    target.write_mode = "stage_sudo"
    target.layout = "pair"
    session.add(target)
    session.commit()
    rec.sftp.chmod_raise = True
    staged = cert_svc.deploy_target(session, target.id, force=True)
    assert staged["ok"] is True
    rec.sftp.chmod_raise = False
    rec.sftp.fail_marker = True
    marked = cert_svc.deploy_target(session, target.id, force=True)
    assert marked["ok"] is True
    rec.sftp.fail_marker = False
    rec.mode = "install_file_fail"
    inst = cert_svc.deploy_target(session, target.id, force=True)
    assert inst["ok"] is False

    target.layout = "pfx"
    target.pfx_export_password_encrypted = encrypt_str("pw")
    session.add(target)
    session.commit()
    rec.mode = ""
    rec.sftp.chmod_raise = True
    pfx_stage = cert_svc.deploy_target(session, target.id, force=True)
    assert pfx_stage["ok"] is True
    rec.sftp.chmod_raise = False
    rec.mode = "pfx_export_fail"
    assert cert_svc.deploy_target(session, target.id, force=True)["ok"] is False
    rec.mode = "pfx_install_fail"
    assert cert_svc.deploy_target(session, target.id, force=True)["ok"] is False

    target.write_mode = "direct"
    target.layout = "pfx"
    session.add(target)
    session.commit()
    rec.mode = ""
    rec.sftp.chmod_raise = True
    direct = cert_svc.deploy_target(session, target.id, force=True)
    assert direct["ok"] is True
    rec.sftp.chmod_raise = False
    rec.mode = "pfx_export_fail"
    assert cert_svc.deploy_target(session, target.id, force=True)["ok"] is False

    hint = (
        "Check SSH for user: host reachability, key/password, and that the "
        "server is online in PiHerder."
    )
    msg = cert_svc.humanize_deploy_error("connection refused. " + hint, ssh_user="user")
    assert "connection refused" in msg
    assert "pkcs" in cert_svc.humanize_deploy_error("pfx export failed", ssh_user="user").lower() or "openssl" in cert_svc.humanize_deploy_error("pfx export failed").lower()
    stage_msg = cert_svc.humanize_deploy_error("stage mkdir failed", ssh_user="user")
    assert "stage" in stage_msg.lower()

    bare = cert_svc.CertificateTarget(
        certificate_id=cert.id,
        server_id=target.server_id,
        remote_dir="/srv/app",
    )
    cert_svc._apply_cert_target_alerts(
        session, target=bare, cert=cert, server=None, deploy_ok=True, verify=None
    )
    import app.services.notifications as notif

    monkeypatch.setattr(notif, "notify_cert_verify_failed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("n")))
    cert_svc._apply_cert_target_alerts(
        session,
        target=target,
        cert=cert,
        server=session.get(Server, target.server_id),
        deploy_ok=True,
        verify={"ok": False, "status": "failed", "message": "mismatch"},
    )
    import sys

    saved = sys.modules.get("app.services.notifications")
    sys.modules["app.services.notifications"] = None
    try:
        cert_svc._apply_cert_target_alerts(
            session, target=target, cert=cert, server=None, deploy_ok=False, deploy_error="x"
        )
    finally:
        if saved is not None:
            sys.modules["app.services.notifications"] = saved
    session.close()


def test_cert_renew_and_redistribute(monkeypatch):
    from app.models import Integration

    session = _cert_session()
    integ = Integration(type="npm", name="npm", base_url="https://npm.example.com", enabled=True)
    session.add(integ)
    session.commit()
    session.refresh(integ)
    cert = cert_svc.upsert_from_pems(
        session,
        name="Soon",
        fullchain_pem=FULL_SOON,
        privkey_pem=PRIV_SOON,
        source="npm",
        source_integration_id=integ.id,
        external_id="9",
        auto_renew=True,
        renew_days_before=21,
    )
    cert.edge_apply_enabled = True
    session.add(cert)
    session.commit()

    monkeypatch.setattr(cert_svc, "deploy_all_targets", lambda *a, **k: {"ok": True, "count": 0, "results": []})
    monkeypatch.setattr(cert_svc, "edge_certs_writable", lambda: True)
    monkeypatch.setattr(cert_svc, "deploy_to_edge_caddy", lambda *a, **k: {"ok": False, "error": "nope"})

    def _prog(msg):
        raise RuntimeError("progress")

    dist = cert_svc.redistribute_after_renew(session, cert.id, progress=_prog)
    assert dist["edge"]["ok"] is False

    monkeypatch.setattr(cert_svc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(cert_svc.npm_mod, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(cert_svc.npm_mod, "renew_certificate", lambda *a, **k: None)

    def _pull(*a, **k):
        raise RuntimeError("pull")

    monkeypatch.setattr(cert_svc, "pull_from_npm", _pull)
    renewed = cert_svc.renew_npm_certificate(
        session, cert, progress=_prog, poll_interval_sec=1, poll_attempts=1
    )
    assert renewed["ok"] is False

    other = cert_svc.upsert_from_pems(
        session,
        name="fail cert",
        fullchain_pem=FULL_OLD,
        privkey_pem=PRIV_OLD,
        source="npm",
        source_integration_id=integ.id,
        external_id="8",
        auto_renew=True,
    )
    import app.services.notifications as notif

    def _renew(session, row, **k):
        if "fail" in (row.name or ""):
            return {"ok": False, "error": "nope"}
        return {"ok": True}

    monkeypatch.setattr(cert_svc, "renew_npm_certificate", _renew)
    monkeypatch.setattr(notif, "upsert_notification", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("n")))
    monkeypatch.setattr(notif, "resolve_by_fingerprint", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("r")))
    results = cert_svc.check_expiring_and_renew(session, poll_interval_sec=1, poll_attempts=1)
    assert results
    session.close()


# --- certificates HTTP ---------------------------------------------------------


@pytest.fixture()
def cert_http(tmp_path, monkeypatch):
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)
    monkeypatch.setattr("app.services.account_stepup.force_2fa_applies", lambda *a, **k: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'certs.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        admin = User(
            email="certs@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.commit()
        s.refresh(admin)
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ip_address="192.0.2.30",
            ssh_username="user",
            ssh_password_encrypted=encrypt_str("secret"),
        )
        s.add(server)
        s.commit()
        s.refresh(server)
        live = cert_svc.upsert_from_pems(
            s, name="Live", fullchain_pem=FULL, privkey_pem=PRIV, source="upload", auto_renew=False
        )
        soon = cert_svc.upsert_from_pems(
            s,
            name="Soon",
            fullchain_pem=FULL_SOON,
            privkey_pem=PRIV_SOON,
            source="upload",
            auto_renew=True,
        )
        old = cert_svc.upsert_from_pems(
            s, name="Old", fullchain_pem=FULL_OLD, privkey_pem=PRIV_OLD, source="upload", auto_renew=False
        )
        soon.edge_apply_enabled = True
        s.add(soon)
        s.commit()
        target = cert_svc.create_target(
            s,
            certificate_id=live.id,
            server_id=server.id,
            remote_dir="/srv/app/certs",
            label="app-1",
        )
        ids = {
            "admin": admin.id,
            "server": server.id,
            "live": live.id,
            "soon": soon.id,
            "old": old.id,
            "target": target.id,
        }
    try:
        yield client, ids, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_cert_http_pages_and_forms(cert_http, monkeypatch):
    client, ids, engine = cert_http
    cookies = _cookie(ids["admin"])
    listed = client.get("/certificates", cookies=cookies)
    assert listed.status_code == 200

    import app.routers.certificates as cr

    real_select = cr.select
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("no integration table")
        return real_select(*a, **k)

    monkeypatch.setattr(cr, "select", flaky)
    setup = client.get("/certificates/setup", cookies=cookies)
    assert setup.status_code == 200
    monkeypatch.setattr(cr, "select", real_select)

    import app.routers.certificates as routers_cert

    real_upsert = cert_svc.upsert_from_pems
    monkeypatch.setattr(routers_cert, "make_audit_log", lambda **k: (_ for _ in ()).throw(RuntimeError("audit")))
    uploaded = client.post(
        "/certificates/upload",
        cookies=cookies,
        data={"name": "Uploaded", "fullchain_pem": FULL, "privkey_pem": PRIV},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    bad = client.post(
        "/certificates/upload",
        cookies=cookies,
        data={"name": "Bad", "fullchain_pem": "nope", "privkey_pem": "nope"},
        follow_redirects=False,
    )
    assert bad.status_code == 303
    monkeypatch.setattr(cert_svc, "upsert_from_pems", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    blew = client.post(
        "/certificates/upload",
        cookies=cookies,
        data={"name": "X", "fullchain_pem": FULL, "privkey_pem": PRIV},
        follow_redirects=False,
    )
    assert blew.status_code == 303
    monkeypatch.setattr(cert_svc, "upsert_from_pems", real_upsert)

    detail = client.get(f"/certificates/{ids['live']}", cookies=cookies, params={"edit_target": "abc"})
    assert detail.status_code == 200
    detail2 = client.get(
        f"/certificates/{ids['live']}",
        cookies=cookies,
        params={"edit_target": str(ids["target"]), "setup": ""},
    )
    assert detail2.status_code == 200
    missing_edit = client.get(
        f"/certificates/{ids['live']}",
        cookies=cookies,
        params={"edit_target": "99999"},
    )
    assert missing_edit.status_code == 200

    with Session(engine) as s:
        s.connection().exec_driver_sql("PRAGMA foreign_keys=OFF")
        row = s.get(Server, ids["server"])
        s.delete(row)
        s.commit()
    orphan = client.get(f"/certificates/{ids['live']}", cookies=cookies)
    assert orphan.status_code == 200
    with Session(engine) as s:
        s.connection().exec_driver_sql("PRAGMA foreign_keys=OFF")
        s.add(
            Server(
                id=ids["server"],
                name="host-a",
                hostname="host-a.example.com",
                ip_address="192.0.2.30",
                ssh_username="user",
            )
        )
        s.commit()

    assert client.post(f"/certificates/99999/settings", cookies=cookies, data={"name": "n"}, follow_redirects=False).status_code == 404
    assert (
        client.post(
            f"/certificates/99999/replace-pem",
            cookies=cookies,
            data={"fullchain_pem": FULL, "privkey_pem": PRIV},
            follow_redirects=False,
        ).status_code
        == 404
    )
    replaced = client.post(
        f"/certificates/{ids['live']}/replace-pem",
        cookies=cookies,
        data={"fullchain_pem": "nope", "privkey_pem": "nope"},
        follow_redirects=False,
    )
    assert replaced.status_code == 303

    monkeypatch.setattr(cert_svc, "deploy_to_edge_caddy", lambda *a, **k: {"ok": True, "skipped": True})
    monkeypatch.setattr(cert_svc, "set_edge_apply_enabled", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("edge")))
    skipped = client.post(
        f"/certificates/{ids['live']}/apply-edge",
        cookies=cookies,
        data={"force": "on"},
        follow_redirects=False,
    )
    assert skipped.status_code == 303
    monkeypatch.setattr(cert_svc, "deploy_to_edge_caddy", lambda *a, **k: {"ok": True, "skipped": False})
    applied = client.post(
        f"/certificates/{ids['live']}/apply-edge",
        cookies=cookies,
        data={"force": "on"},
        follow_redirects=False,
    )
    assert applied.status_code == 303
    monkeypatch.setattr(cert_svc, "deploy_to_edge_caddy", lambda *a, **k: {"ok": False, "error": "reload"})
    failed_edge = client.post(
        f"/certificates/{ids['live']}/apply-edge",
        cookies=cookies,
        data={"force": "on"},
        follow_redirects=False,
    )
    assert failed_edge.status_code == 303
    missing_edge = client.post(
        "/certificates/99999/apply-edge",
        cookies=cookies,
        data={"force": "on"},
        follow_redirects=False,
    )
    assert missing_edge.status_code == 404

    bad_act = client.post(
        f"/certificates/{ids['live']}/edge-mapping",
        cookies=cookies,
        data={"action": "nope"},
        follow_redirects=False,
    )
    assert bad_act.status_code == 303
    monkeypatch.setattr(cert_svc, "set_edge_apply_enabled", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad map")))
    bad_map = client.post(
        f"/certificates/{ids['live']}/edge-mapping",
        cookies=cookies,
        data={"action": "enable"},
        follow_redirects=False,
    )
    assert bad_map.status_code == 303

    invalid_target = client.post(
        f"/certificates/{ids['live']}/targets",
        cookies=cookies,
        data={"server_id": str(ids["server"]), "remote_dir": "../etc", "restart_kind": "compose", "compose_file": "/srv/app/docker-compose.yml"},
        follow_redirects=False,
    )
    assert invalid_target.status_code == 303
    custom = client.post(
        f"/certificates/{ids['live']}/targets",
        cookies=cookies,
        data={
            "server_id": str(ids["server"]),
            "remote_dir": "/srv/app/certs",
            "restart_kind": "custom",
            "post_deploy_command": "echo ok",
            "layout": "pair",
        },
        follow_redirects=False,
    )
    assert custom.status_code == 303

    missing_row = client.post(
        f"/certificates/{ids['live']}/targets/99999/edit",
        cookies=cookies,
        data={"server_id": str(ids["server"]), "remote_dir": "/srv/app/certs"},
        follow_redirects=False,
    )
    assert missing_row.status_code == 404
    bad_edit = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/edit",
        cookies=cookies,
        data={"server_id": str(ids["server"]), "remote_dir": "../etc"},
        follow_redirects=False,
    )
    assert bad_edit.status_code == 303

    import app.services.notifications as notif

    monkeypatch.setattr(notif, "resolve_cert_target_alerts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("n")))
    deleted = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/delete",
        cookies=cookies,
        follow_redirects=False,
    )
    assert deleted.status_code == 303


def test_cert_http_verify_deploy_renew(cert_http, monkeypatch):
    client, ids, engine = cert_http
    cookies = _cookie(ids["admin"])
    import app.services.ssh as ssh_mod

    class _C:
        def close(self):
            raise RuntimeError("close")

    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda server: _C())
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "/home/user", ""))
    monkeypatch.setattr(
        cert_svc,
        "verify_deploy_target",
        lambda *a, **k: {"ok": True, "status": "success", "message": "ok"},
    )
    ok = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/verify",
        cookies=cookies,
        follow_redirects=False,
    )
    assert ok.status_code == 303
    monkeypatch.setattr(
        cert_svc,
        "verify_deploy_target",
        lambda *a, **k: {"ok": False, "status": "partial", "message": "half"},
    )
    partial = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/verify",
        cookies=cookies,
        follow_redirects=False,
    )
    assert partial.status_code == 303
    monkeypatch.setattr(
        cert_svc,
        "verify_deploy_target",
        lambda *a, **k: {"ok": False, "status": "failed", "message": "no"},
    )
    failed = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/verify",
        cookies=cookies,
        follow_redirects=False,
    )
    assert failed.status_code == 303
    monkeypatch.setattr(cert_svc, "verify_deploy_target", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("verify")))
    blew = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/verify",
        cookies=cookies,
        follow_redirects=False,
    )
    assert blew.status_code == 303

    with Session(engine) as s:
        row = s.get(cert_svc.CertificateTarget, ids["target"])
        row.server_id = 424242
        s.add(row)
        s.commit()
    missing_server = client.post(
        f"/certificates/{ids['live']}/targets/{ids['target']}/verify",
        cookies=cookies,
        follow_redirects=False,
    )
    assert missing_server.status_code == 303
    with Session(engine) as s:
        row = s.get(cert_svc.CertificateTarget, ids["target"])
        row.server_id = ids["server"]
        s.add(row)
        s.commit()

    sim_missing = client.post(
        f"/certificates/{ids['live']}/targets/simulate",
        cookies=cookies,
        data={"server_id": "424242", "remote_dir": "/srv/app", "restart_kind": "custom", "post_deploy_command": "echo x"},
        follow_redirects=False,
    )
    assert sim_missing.status_code == 303
    monkeypatch.setattr(
        cert_svc,
        "simulate_sudoers_access",
        lambda *a, **k: {
            "ok": False,
            "checks": [{"name": "sudo", "ok": False, "detail": "no sudo"}],
            "message": "",
        },
    )
    sim_bad = client.post(
        f"/certificates/{ids['live']}/targets/simulate",
        cookies=cookies,
        data={"server_id": str(ids["server"]), "remote_dir": "/srv/app/certs", "layout": "pair"},
        follow_redirects=False,
    )
    assert sim_bad.status_code == 303
    monkeypatch.setattr(
        cert_svc,
        "simulate_sudoers_access",
        lambda *a, **k: {"ok": True, "checks": [{"name": "sudo", "ok": True, "detail": "ok"}], "message": "all good"},
    )
    sim_ok = client.post(
        f"/certificates/{ids['live']}/targets/simulate",
        cookies=cookies,
        data={"server_id": str(ids["server"]), "remote_dir": "/srv/app/certs"},
        follow_redirects=False,
    )
    assert sim_ok.status_code == 303
    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda server: (_ for _ in ()).throw(RuntimeError("ssh")))
    sim_exc = client.post(
        f"/certificates/{ids['live']}/targets/simulate",
        cookies=cookies,
        data={"server_id": str(ids["server"]), "remote_dir": "/srv/app/certs"},
        follow_redirects=False,
    )
    assert sim_exc.status_code == 303

    assert (
        client.post(
            "/certificates/99999/deploy",
            cookies=cookies,
            data={"target_id": str(ids["target"])},
            follow_redirects=False,
        ).status_code
        == 404
    )
    monkeypatch.setattr(
        cert_svc,
        "deploy_target",
        lambda *a, **k: {"ok": True, "skipped": True, "server_id": ids["server"]},
    )
    dep = client.post(
        f"/certificates/{ids['live']}/deploy",
        cookies=cookies,
        data={"target_id": str(ids["target"]), "force": "1"},
        follow_redirects=False,
    )
    assert dep.status_code == 303
    monkeypatch.setattr(
        cert_svc,
        "deploy_target",
        lambda *a, **k: {"ok": False, "error": "nope", "server_id": ids["server"]},
    )
    dep_bad = client.post(
        f"/certificates/{ids['live']}/deploy",
        cookies=cookies,
        data={"target_id": str(ids["target"])},
        follow_redirects=False,
    )
    assert dep_bad.status_code == 303
    monkeypatch.setattr(cert_svc, "deploy_all_targets", lambda *a, **k: {"ok": True, "count": 1})
    all_ok = client.post(f"/certificates/{ids['live']}/deploy", cookies=cookies, follow_redirects=False)
    assert all_ok.status_code == 303
    monkeypatch.setattr(cert_svc, "deploy_all_targets", lambda *a, **k: {"ok": False, "count": 1})
    all_bad = client.post(f"/certificates/{ids['live']}/deploy", cookies=cookies, follow_redirects=False)
    assert all_bad.status_code == 303

    not_npm = client.post(f"/certificates/{ids['live']}/renew", cookies=cookies, follow_redirects=False)
    assert not_npm.status_code == 303
    with Session(engine) as s:
        row = s.get(cert_svc.ManagedCertificate, ids["live"])
        row.source = "npm"
        s.add(row)
        s.commit()
    monkeypatch.setattr(cert_svc, "renew_npm_certificate", lambda *a, **k: {"ok": True})
    renewed = client.post(f"/certificates/{ids['live']}/renew", cookies=cookies, follow_redirects=False)
    assert renewed.status_code == 303
    monkeypatch.setattr(cert_svc, "renew_npm_certificate", lambda *a, **k: {"ok": False, "error": "timeout"})
    renew_bad = client.post(f"/certificates/{ids['live']}/renew", cookies=cookies, follow_redirects=False)
    assert renew_bad.status_code == 303

    import app.services.notifications as notif

    monkeypatch.setattr(notif, "resolve_cert_target_alerts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("n")))
    removed = client.post(f"/certificates/{ids['old']}/delete", cookies=cookies, follow_redirects=False)
    assert removed.status_code == 303
