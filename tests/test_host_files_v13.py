"""Stream F — jailed host Files (path policy, verbs, size, symlink)."""
from __future__ import annotations

import io
import posixpath
import stat
import time
from types import SimpleNamespace

import pytest

from app.services import host_files as hf


def _server(**kw):
    defaults = dict(
        name="lab",
        ssh_username="piherder",
        ssh_private_key_encrypted="x",
        ssh_password_encrypted=None,
        docker_base_dir="~/docker",
        container_patch_enabled=True,
        os_type="debian",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class _Attr:
    def __init__(self, filename, mode, size=0, mtime=1.0):
        self.filename = filename
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = mtime


class MemSFTP:
    """Minimal SFTP surface for Files unit tests."""

    def __init__(self):
        now = time.time()
        self.nodes: dict[str, dict] = {
            "/": {"kind": "dir", "mode": stat.S_IFDIR | 0o755, "mtime": now, "data": b""},
        }
        self.links: dict[str, str] = {}

    def _norm(self, path: str) -> str:
        p = posixpath.normpath(path.replace("\\", "/"))
        return p if p.startswith("/") else "/" + p

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
                }

    def add_file(self, path: str, data: bytes) -> None:
        path = self._norm(path)
        self.add_dir(posixpath.dirname(path) or "/")
        self.nodes[path] = {
            "kind": "file",
            "mode": stat.S_IFREG | 0o644,
            "mtime": time.time(),
            "data": bytes(data),
        }

    def add_link(self, path: str, target: str) -> None:
        path = self._norm(path)
        self.add_dir(posixpath.dirname(path) or "/")
        self.nodes[path] = {
            "kind": "link",
            "mode": stat.S_IFLNK | 0o777,
            "mtime": time.time(),
            "data": b"",
        }
        self.links[path] = target

    def normalize(self, path: str) -> str:
        path = self._norm(path)
        if path in self.links:
            t = self.links[path]
            return t if t.startswith("/") else self._norm(posixpath.join(posixpath.dirname(path), t))
        return path

    def lstat(self, path: str):
        path = self._norm(path)
        n = self.nodes.get(path)
        if not n:
            raise FileNotFoundError(path)
        return SimpleNamespace(
            st_mode=n["mode"],
            st_size=len(n.get("data") or b""),
            st_mtime=n["mtime"],
            filename=posixpath.basename(path),
        )

    def listdir_attr(self, path: str):
        path = self._norm(path)
        if path not in self.nodes or self.nodes[path]["kind"] != "dir":
            raise FileNotFoundError(path)
        prefix = "/" if path == "/" else path + "/"
        out = []
        for p, n in self.nodes.items():
            if p == path:
                continue
            if p.startswith(prefix) and p[len(prefix) :].find("/") < 0:
                out.append(
                    _Attr(
                        posixpath.basename(p),
                        n["mode"],
                        len(n.get("data") or b""),
                        n["mtime"],
                    )
                )
        return out

    def listdir(self, path: str):
        return [a.filename for a in self.listdir_attr(path)]

    def open(self, path: str, mode: str = "rb"):
        path = self._norm(path)
        if "w" in mode:
            parent = posixpath.dirname(path) or "/"
            if parent not in self.nodes:
                raise FileNotFoundError(parent)
            node = {
                "kind": "file",
                "mode": stat.S_IFREG | 0o644,
                "mtime": time.time(),
                "data": bytearray(),
            }
            self.nodes[path] = node

            class W:
                def write(_, data):
                    node["data"].extend(data if isinstance(data, (bytes, bytearray)) else data.encode())

                def close(_):
                    node["data"] = bytes(node["data"])

                def __enter__(wself):
                    return wself

                def __exit__(wself, *a):
                    wself.close()

            return W()
        n = self.nodes.get(path)
        if not n or n["kind"] == "dir":
            raise FileNotFoundError(path)
        return io.BytesIO(bytes(n.get("data") or b""))

    def mkdir(self, path: str):
        path = self._norm(path)
        if path in self.nodes:
            raise OSError("exists")
        self.add_dir(path)

    def remove(self, path: str):
        path = self._norm(path)
        if path not in self.nodes:
            raise FileNotFoundError(path)
        del self.nodes[path]
        self.links.pop(path, None)

    def rmdir(self, path: str):
        if self.listdir(path):
            raise OSError("not empty")
        self.remove(path)

    def rename(self, src: str, dst: str):
        src, dst = self._norm(src), self._norm(dst)
        if src not in self.nodes:
            raise FileNotFoundError(src)
        self.nodes[dst] = self.nodes.pop(src)
        if src in self.links:
            self.links[dst] = self.links.pop(src)


def test_jail_docker_host_fleet():
    s = _server()
    assert hf.jail_path(s) == "/home/piherder/docker"


def test_jail_backup_only_is_home():
    s = _server(container_patch_enabled=False)
    assert hf.jail_path(s) == "/home/piherder"


def test_jail_haos_root_home():
    s = _server(
        os_type="haos",
        container_patch_enabled=False,
        ssh_username="root",
        docker_base_dir="~/docker",
    )
    assert hf.jail_path(s) == "/root"


def test_jail_privileged_is_root():
    s = _server()
    assert hf.jail_path(s, role="privileged") == "/"


def test_jail_refuses_os_trees():
    s = _server(docker_base_dir="/etc/piherder")
    with pytest.raises(hf.FilesError) as e:
        hf.jail_path(s)
    assert e.value.code == "jail"


def test_parse_rel_rejects_dotdot_and_absolute():
    with pytest.raises(hf.FilesError) as e:
        hf.parse_rel("../etc")
    assert e.value.code == "escape"
    with pytest.raises(hf.FilesError):
        hf.parse_rel("/etc/passwd")
    with pytest.raises(hf.FilesError):
        hf.parse_rel("a\\b")
    assert hf.parse_rel("") == []
    assert hf.parse_rel("frigate/config.yml") == ["frigate", "config.yml"]


def test_resolve_logical_stays_in_jail():
    s = _server()
    jail, abs_p = hf.resolve_logical(s, "frigate/config.yml")
    assert jail == "/home/piherder/docker"
    assert abs_p == "/home/piherder/docker/frigate/config.yml"


def test_fleet_denies_ssh_dir():
    s = _server(container_patch_enabled=False)
    with pytest.raises(hf.FilesError) as e:
        hf.resolve_logical(s, ".ssh/id_rsa")
    assert e.value.code == "denied"


def test_privileged_allows_etc_but_not_proc():
    s = _server()
    jail, p = hf.resolve_logical(s, "etc/passwd", role="privileged")
    assert jail == "/"
    assert p == "/etc/passwd"
    with pytest.raises(hf.FilesError) as e:
        hf.resolve_logical(s, "proc/1/environ", role="privileged")
    assert e.value.code == "denied"


def test_secretish_names():
    assert hf.is_secretish(".env")
    assert hf.is_secretish(".env.prod")
    assert hf.is_secretish("tls.pem")
    assert hf.is_secretish("id_rsa")
    assert not hf.is_secretish("config.yml")


def test_sanitize_strips_path():
    assert hf.sanitize_basename("dir/evil.txt") == "evil.txt"
    with pytest.raises(hf.FilesError):
        hf.sanitize_basename("..")
    with pytest.raises(hf.FilesError):
        hf.sanitize_basename("")


def test_files_enabled_off_and_demo(monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", False, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    assert hf.files_enabled() is False
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    assert hf.files_enabled() is True
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", True, raising=False)
    assert hf.files_enabled() is False


def test_list_get_put_mkdir_rename_delete():
    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker/frigate")
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"ok")
    listing = hf.list_dir(s, "frigate", sftp=fs)
    names = [e["name"] for e in listing["entries"]]
    assert "config.yml" in names
    chunks = b"".join(hf.iter_file(s, "frigate/config.yml", sftp=fs))
    assert chunks == b"ok"
    hf.mkdir(s, "frigate", "logs", sftp=fs)
    out = hf.put_file(s, "frigate/logs", "app.log", io.BytesIO(b"hello"), sftp=fs)
    assert out["bytes"] == 5
    assert out["overwrite"] is False
    out2 = hf.put_file(s, "frigate/logs", "app.log", io.BytesIO(b"world"), sftp=fs)
    assert out2["overwrite"] is True
    hf.rename(s, "frigate/logs", "app.log", "app.txt", sftp=fs)
    hf.remove(s, "frigate/logs/app.txt", sftp=fs)
    listing2 = hf.list_dir(s, "frigate/logs", sftp=fs)
    assert listing2["entries"] == []
    hf.remove(s, "frigate/logs", sftp=fs)


def test_delete_nonempty_dir_refused():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"x")
    with pytest.raises(hf.FilesError) as e:
        hf.remove(s, "frigate", sftp=fs)
    assert e.value.code == "not_empty"


def test_symlink_escape_refused():
    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    fs.add_file("/etc/shadow", b"nope")
    fs.add_link("/home/piherder/docker/escape", "/etc/shadow")
    listing = hf.list_dir(s, "", sftp=fs)
    esc = [e for e in listing["entries"] if e["name"] == "escape"][0]
    assert esc["escaped"] is True
    with pytest.raises(hf.FilesError) as e:
        list(hf.iter_file(s, "escape", sftp=fs))
    assert e.value.code == "escape"


def test_upload_over_cap_rejected(monkeypatch):
    monkeypatch.setattr(hf, "MAX_UPLOAD_DEFAULT", 8)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES_MAX_BYTES", 8, raising=False)
    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    with pytest.raises(hf.FilesError) as e:
        hf.put_file(s, "", "big.bin", io.BytesIO(b"0123456789"), size=10, sftp=fs)
    assert e.value.code == "too_large"
    with pytest.raises(hf.FilesError) as e2:
        hf.put_file(s, "", "big.bin", io.BytesIO(b"0123456789"), sftp=fs)
    assert e2.value.code == "too_large"
    # tmp must not remain
    assert "/home/piherder/docker/big.bin.tmp" not in fs.nodes
    assert "/home/piherder/docker/big.bin" not in fs.nodes


def test_haos_supported_and_files_supported():
    s = _server(os_type="haos", container_patch_enabled=False, ssh_username="root")
    assert hf.files_supported(s) is True
    assert hf.jail_path(s) == "/root"
