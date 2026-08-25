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
    def __init__(self, filename, mode, size=0, mtime=1.0, uid=1000, gid=1000):
        self.filename = filename
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = mtime
        self.st_uid = uid
        self.st_gid = gid


class MemSFTP:
    """Minimal SFTP surface for Files unit tests."""

    def __init__(self):
        now = time.time()
        self.nodes: dict[str, dict] = {
            "/": {
                "kind": "dir",
                "mode": stat.S_IFDIR | 0o755,
                "mtime": now,
                "data": b"",
                "uid": 0,
                "gid": 0,
            },
        }
        self.chmod_fail: set[str] = set()
        self.chown_fail: set[str] = set()
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
            st_uid=int(n.get("uid", 1000)),
            st_gid=int(n.get("gid", 1000)),
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
                        uid=int(n.get("uid", 1000)),
                        gid=int(n.get("gid", 1000)),
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
                "uid": 1000,
                "gid": 1000,
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

    def chmod(self, path: str, mode: int):
        path = self._norm(path)
        if path in self.chmod_fail:
            raise PermissionError("denied")
        n = self.nodes.get(path)
        if not n:
            raise FileNotFoundError(path)
        ftype = n["mode"] & 0o170000
        n["mode"] = ftype | (int(mode) & 0o7777)

    def chown(self, path: str, uid: int, gid: int):
        path = self._norm(path)
        if path in self.chown_fail:
            raise PermissionError("denied")
        n = self.nodes.get(path)
        if not n:
            raise FileNotFoundError(path)
        n["uid"] = int(uid)
        n["gid"] = int(gid)


class FakeSSH:
    def __init__(self, uid="0", status=0):
        self.cmds: list[str] = []
        self.uid = uid
        self.status = status

    def exec_command(self, cmd, timeout=None):
        self.cmds.append(cmd)
        out = self.uid + "\n" if cmd.strip() == "id -u" else ""
        stdin = SimpleNamespace()
        stdout = SimpleNamespace(
            read=lambda: out.encode(),
            channel=SimpleNamespace(recv_exit_status=lambda: self.status),
        )
        stderr = SimpleNamespace(read=lambda: b"")
        return stdin, stdout, stderr


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


def test_clamp_files_max_allows_12gib():
    twelve = 12 * 1024 * 1024 * 1024
    assert hf.clamp_files_max_bytes(twelve) == twelve
    assert hf.clamp_files_max_bytes(64 * 1024 * 1024 * 1024) == hf.MAX_UPLOAD_CEILING
    assert hf.clamp_files_max_bytes(100) == 100
    assert hf.files_max_gib(twelve) == 12.0


def test_max_upload_uses_settings_when_env_unlocked(monkeypatch):
    twelve = 12 * 1024 * 1024 * 1024
    monkeypatch.setattr(hf, "files_max_env_locked", lambda: False)

    def _raw():
        return {"files_max_bytes": twelve}

    monkeypatch.setattr("app.services.app_settings._load_raw_from_db", _raw)
    assert hf.max_upload_bytes() == twelve


def test_upload_over_cap_rejected(monkeypatch):
    monkeypatch.setattr(hf, "MAX_UPLOAD_DEFAULT", 8)
    monkeypatch.setattr(hf, "files_max_env_locked", lambda: False)
    monkeypatch.setattr(hf, "max_upload_bytes", lambda: 8)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES_MAX_BYTES", 8, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
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


def test_put_file_perm_denied_is_clear():
    class DenySFTP(MemSFTP):
        def open(self, path, mode="r"):
            if "w" in str(mode):
                raise PermissionError(13, "Permission denied")
            return super().open(path, mode)

    s = _server()
    fs = DenySFTP()
    fs.add_dir("/home/piherder/docker")
    with pytest.raises(hf.FilesError) as e:
        hf.put_file(s, "", "root.conf", io.BytesIO(b"x"), sftp=fs)
    assert e.value.code == "denied"
    assert "PermissionError" not in e.value.message
    assert "Privileged" in e.value.message or "own" in e.value.message.lower()


def test_put_file_privileged_retries_sudo_tee(monkeypatch):
    class DenySFTP(MemSFTP):
        def open(self, path, mode="r"):
            if "w" in str(mode):
                raise PermissionError(13, "Permission denied")
            return super().open(path, mode)

    s = _server()
    fs = DenySFTP()
    fs.add_dir("/home/piherder/docker")
    ssh = SimpleNamespace()
    wrote = {}

    def exec_command(cmd, timeout=60):
        wrote["cmd"] = cmd
        stdin = io.BytesIO()
        stdin.channel = SimpleNamespace(shutdown_write=lambda: None)
        orig_write = stdin.write

        def _w(data):
            wrote["data"] = data
            return orig_write(data)

        stdin.write = _w
        stdout = io.BytesIO(b"")
        stdout.channel = SimpleNamespace(recv_exit_status=lambda: 0)
        stderr = io.BytesIO(b"")
        return stdin, stdout, stderr

    ssh.exec_command = exec_command
    out = hf.put_file(
        s,
        "",
        "root.conf",
        io.BytesIO(b"hello"),
        role=hf.ROLE_PRIVILEGED,
        sftp=fs,
        client=ssh,
    )
    assert "sudo -n tee" in (wrote.get("cmd") or "")
    assert wrote.get("data") == b"hello"
    assert out["bytes"] == 5
    assert out["sha256"]


def test_put_file_progress_callback():
    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    seen = []
    data = b"x" * (1024 * 64)
    hf.put_file(s, "", "blob.bin", io.BytesIO(data), size=len(data), sftp=fs, progress=lambda n, t: seen.append((n, t)))
    assert seen
    assert seen[-1][0] == len(data)


def test_sftp_pool_reuses_session(monkeypatch):
    hf.drop_sftp_pool()
    opens = {"n": 0}

    class FakeClient:
        def __init__(self):
            opens["n"] += 1
            self._sftp = MemSFTP()
            self._sftp.add_dir("/home/piherder/docker")
            self._alive = True

        def open_sftp(self):
            return self._sftp

        def get_transport(self):
            t = SimpleNamespace()
            t.is_active = lambda: self._alive
            t.set_keepalive = lambda *a, **k: None
            return t

        def close(self):
            self._alive = False

    monkeypatch.setattr(hf, "get_ssh_client", lambda server, identity=None: FakeClient())
    s = _server(id=42)
    hf.list_dir(s, "")
    hf.list_dir(s, "")
    assert opens["n"] == 1
    hf.drop_sftp_pool()


def test_haos_supported_and_files_supported():
    s = _server(os_type="haos", container_patch_enabled=False, ssh_username="root")
    assert hf.files_supported(s) is True
    assert hf.jail_path(s) == "/root"


def test_looks_like_text():
    assert hf.looks_like_text("config.yml", b"image: nginx\n")
    assert hf.looks_like_text("notes.txt", b"hello")
    assert not hf.looks_like_text("blob.bin", b"\x00\x01\x02")
    assert not hf.looks_like_text("x.dat", b"\xff\xfe\x00\x01")


def test_read_write_text():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"ok: 1\n")
    data = hf.read_text(s, "frigate/config.yml", sftp=fs)
    assert data["text"] == "ok: 1\n"
    assert data["name"] == "config.yml"
    hf.write_text(s, "frigate/config.yml", "ok: 2\n", sftp=fs)
    data2 = hf.read_text(s, "frigate/config.yml", sftp=fs)
    assert data2["text"] == "ok: 2\n"


def test_read_text_rejects_binary_and_large():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.bin", b"\x00\x01\x02")
    with pytest.raises(hf.FilesError) as e:
        hf.read_text(s, "a.bin", sftp=fs)
    assert e.value.code == "binary"
    fs.add_file("/home/piherder/docker/big.yml", b"x" * (hf.EDIT_MAX + 1))
    with pytest.raises(hf.FilesError) as e2:
        hf.read_text(s, "big.yml", sftp=fs)
    assert e2.value.code == "too_large"


def test_safe_zip_name_refuses_slip():
    with pytest.raises(hf.FilesError) as e:
        hf._safe_zip_name("../etc/passwd")
    assert e.value.code == "escape"
    with pytest.raises(hf.FilesError):
        hf._safe_zip_name("/tmp/evil")
    with pytest.raises(hf.FilesError):
        hf._safe_zip_name("foo/../../etc")
    assert hf._safe_zip_name("nested/hello.txt") == "nested/hello.txt"
    assert hf._safe_zip_name("./a/b") == "a/b"


def test_unzip_and_zip_roundtrip():
    import zipfile

    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nested/hello.txt", b"hi")
        zf.writestr("sidecar.yml", b"x: 1\n")
    fs.add_file("/home/piherder/docker/pack.zip", buf.getvalue())
    out = hf.unzip_into(s, "pack.zip", "", sftp=fs)
    assert out["files"] == 2
    assert fs.nodes["/home/piherder/docker/nested/hello.txt"]["data"] == b"hi"
    assert fs.nodes["/home/piherder/docker/sidecar.yml"]["data"] == b"x: 1\n"
    fname, chunks = hf.build_zip(s, ["nested", "sidecar.yml"], sftp=fs)
    assert fname.endswith(".zip")
    raw = b"".join(chunks)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
        assert any(n.endswith("hello.txt") for n in names)
        assert "sidecar.yml" in names
        assert zf.read([n for n in names if n.endswith("hello.txt")][0]) == b"hi"


def test_unzip_refuses_zip_slip():
    import zipfile

    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../etc/passwd", b"nope")
    fs.add_file("/home/piherder/docker/bad.zip", buf.getvalue())
    with pytest.raises(hf.FilesError) as e:
        hf.unzip_into(s, "bad.zip", "", sftp=fs)
    assert e.value.code == "escape"
    assert "/etc/passwd" not in fs.nodes
    assert "/home/piherder/etc/passwd" not in fs.nodes


def test_remove_tree_deletes_folder():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/logs/a.log", b"x")
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"y")
    out = hf.remove_tree(s, "frigate", sftp=fs)
    assert out["files"] >= 2
    assert "/home/piherder/docker/frigate" not in fs.nodes
    assert "/home/piherder/docker/frigate/config.yml" not in fs.nodes
    with pytest.raises(hf.FilesError) as e:
        hf.remove_tree(s, "", sftp=fs)
    assert e.value.code == "denied"


def test_list_includes_mode_and_owner():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    listing = hf.list_dir(s, "", sftp=fs)
    e = [x for x in listing["entries"] if x["name"] == "a.yml"][0]
    assert e["mode_h"] == "644"
    assert e["owner_h"] == "1000:1000"


def test_parse_mode_and_names():
    assert hf.parse_mode("644") == 0o644
    assert hf.parse_mode("0755") == 0o755
    with pytest.raises(hf.FilesError):
        hf.parse_mode("999")
    with pytest.raises(hf.FilesError):
        hf.parse_mode("rwx")
    assert hf.parse_id_name("www-data", kind="owner") == "www-data"
    assert hf.parse_id_name("1000", kind="owner") == "1000"
    with pytest.raises(hf.FilesError):
        hf.parse_id_name("root;rm", kind="owner")
    with pytest.raises(hf.FilesError):
        hf.parse_id_name("../x", kind="group")


def test_chmod_sftp_owned_file():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    out = hf.apply_perms(s, ["a.yml"], mode="600", sftp=fs)
    assert out["changed"] == 1
    assert stat.S_IMODE(fs.nodes["/home/piherder/docker/a.yml"]["mode"]) == 0o600


def test_chown_requires_privileged():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    with pytest.raises(hf.FilesError) as e:
        hf.apply_perms(s, ["a.yml"], owner="root", sftp=fs)
    assert e.value.code == "privileged_forbidden"


def test_chmod_denied_on_fleet_falls_through_to_hint():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    fs.chmod_fail.add("/home/piherder/docker/a.yml")
    with pytest.raises(hf.FilesError) as e:
        hf.apply_perms(s, ["a.yml"], mode="600", sftp=fs)
    assert e.value.code == "denied"
    assert "privileged" in e.value.message.lower()


def test_privileged_chmod_uses_sudo_when_sftp_denied():
    s = _server()
    fs = MemSFTP()
    rel = "home/piherder/docker/a.yml"
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    fs.chmod_fail.add("/home/piherder/docker/a.yml")
    ssh = FakeSSH(uid="1000")
    ident = SimpleNamespace(username="deploy")
    out = hf.apply_perms(
        s,
        [rel],
        mode="640",
        role="privileged",
        identity=ident,
        sftp=fs,
        client=ssh,
    )
    assert out["sudo"] is True
    joined = " ".join(ssh.cmds)
    assert "sudo -n chmod 640 --" in joined
    assert "/home/piherder/docker/a.yml" in joined


def test_privileged_chown_named_user_uses_sudo():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    ssh = FakeSSH(uid="1000")
    ident = SimpleNamespace(username="deploy")
    out = hf.apply_perms(
        s,
        ["home/piherder/docker/a.yml"],
        owner="www-data",
        group="www-data",
        role="privileged",
        identity=ident,
        sftp=fs,
        client=ssh,
    )
    assert out["changed"] == 1
    assert any("chown" in c and "www-data:www-data" in c for c in ssh.cmds)


def test_chmod_numeric_chown_via_sftp():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a.yml", b"x")
    ident = SimpleNamespace(username="root")
    out = hf.apply_perms(
        s,
        ["home/piherder/docker/a.yml"],
        owner="0",
        group="0",
        role="privileged",
        identity=ident,
        sftp=fs,
    )
    assert out["changed"] == 1
    assert fs.nodes["/home/piherder/docker/a.yml"]["uid"] == 0
    assert fs.nodes["/home/piherder/docker/a.yml"]["gid"] == 0


def test_chmod_refuses_jail_root():
    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    with pytest.raises(hf.FilesError) as e:
        hf.apply_perms(s, ["."], mode="755", sftp=fs)
    assert e.value.code == "denied"


def test_chmod_recursive_folder():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"x")
    out = hf.apply_perms(s, ["frigate"], mode="750", recursive=True, sftp=fs)
    assert out["changed"] >= 2
    assert stat.S_IMODE(fs.nodes["/home/piherder/docker/frigate"]["mode"]) == 0o750
    assert stat.S_IMODE(fs.nodes["/home/piherder/docker/frigate/config.yml"]["mode"]) == 0o750


def test_search_names_under_folder():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"x")
    fs.add_file("/home/piherder/docker/frigate/logs/app.log", b"y")
    fs.add_file("/home/piherder/docker/other.txt", b"z")
    out = hf.search(s, "config", sftp=fs)
    names = [e["name"] for e in out["entries"]]
    assert "config.yml" in names
    assert out["search"] is True
    nested = hf.search(s, "app.log", rel="frigate", sftp=fs)
    assert any(e["name"] == "app.log" for e in nested["entries"])
    with pytest.raises(hf.FilesError):
        hf.search(s, "", sftp=fs)


def test_ensure_dir_and_nested_rel():
    s = _server()
    fs = MemSFTP()
    fs.add_dir("/home/piherder/docker")
    out = hf.ensure_dir(s, "photos/2024", sftp=fs)
    assert "/home/piherder/docker/photos/2024" in fs.nodes
    assert out["created"] == 2
    again = hf.ensure_dir(s, "photos/2024", sftp=fs)
    assert again["created"] == 0
    assert hf.parse_nested_rel("photos/2024/a.jpg") == ["photos", "2024", "a.jpg"]
    with pytest.raises(hf.FilesError):
        hf.parse_nested_rel("../etc/passwd")
    with pytest.raises(hf.FilesError):
        hf.parse_nested_rel("/tmp/evil")


def test_move_across_folders():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"x")
    hf.ensure_dir(s, "archive", sftp=fs)
    out = hf.move_many(s, ["frigate/config.yml"], "archive", sftp=fs)
    assert out["moved"] == 1
    assert "/home/piherder/docker/archive/config.yml" in fs.nodes
    assert "/home/piherder/docker/frigate/config.yml" not in fs.nodes


def test_move_into_self_refused():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/a/b/x.txt", b"x")
    with pytest.raises(hf.FilesError) as e:
        hf.move_many(s, ["a"], "a/b", sftp=fs)
    assert e.value.code == "denied"


def test_move_overwrite_file():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/src/a.txt", b"new")
    fs.add_file("/home/piherder/docker/dst/a.txt", b"old")
    with pytest.raises(hf.FilesError) as e:
        hf.move_many(s, ["src/a.txt"], "dst", sftp=fs)
    assert e.value.code == "exists"
    hf.move_many(s, ["src/a.txt"], "dst", overwrite=True, sftp=fs)
    assert fs.nodes["/home/piherder/docker/dst/a.txt"]["data"] == b"new"


def test_parse_getent_and_owner_names():
    users = hf.parse_getent("pi:x:1000:1000:Pi:/home/pi:/bin/bash\nwww-data:x:33:33::/var/www:/usr/sbin/nologin\n")
    assert users[1000] == "pi"
    assert users[33] == "www-data"
    groups = hf.parse_getent("pi:x:1000:\ndocker:x:995:pi\n")
    uid, gid, owner, group, owner_h = hf._owner_fields(1000, 1000, users, groups)
    assert owner == "pi"
    assert group == "pi"
    assert owner_h == "pi:pi"
    _u, _g, owner2, group2, h2 = hf._owner_fields(33, 995, users, groups)
    assert owner2 == "www-data"
    assert group2 == "docker"
    assert h2 == "www-data:docker"


def test_zip_basename():
    assert hf.zip_basename("backup") == "backup.zip"
    assert hf.zip_basename("backup.ZIP") == "backup.ZIP"
    assert hf.zip_basename(None, ["frigate/config.yml"]) == "config.yml.zip"
    assert hf.zip_basename("", ["a", "b"]) == "files.zip"


def test_zip_on_host_uses_remote_zip(monkeypatch):
    import zipfile

    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"ok")
    ssh = FakeSSH(uid="0")

    def run(client, cmd, timeout=120):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("frigate/config.yml", b"ok")
        fs.add_file("/home/piherder/docker/frigate-bak.zip", buf.getvalue())
        return 0, "1\n", ""

    monkeypatch.setattr(hf, "run_command", run)
    out = hf.zip_on_host(
        s, ["frigate"], "", "frigate-bak", sftp=fs, client=ssh
    )
    assert out["name"] == "frigate-bak.zip"
    assert out["members"] >= 1
    raw = bytes(fs.nodes["/home/piherder/docker/frigate-bak.zip"]["data"])
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert any(n.endswith("config.yml") for n in zf.namelist())


def test_search_contents():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/frigate/config.yml", b"mqtt:\n  host: core.local\n")
    fs.add_file("/home/piherder/docker/.env", b"SECRET=core.local\n")
    names_only = hf.search(s, "config", sftp=fs)
    assert any(e["name"] == "config.yml" for e in names_only["entries"])
    hits = hf.search(s, "core.local", contents=True, sftp=fs)
    snippets = [e.get("snippet") for e in hits["entries"] if e.get("snippet")]
    assert any("core.local" in (t or "") for t in snippets)
    secret_skip = hf.search(s, "SECRET", contents=True, allow_secrets=False, sftp=fs)
    assert not any(e.get("name") == ".env" and e.get("snippet") for e in secret_skip["entries"])
    secret_ok = hf.search(s, "SECRET", contents=True, allow_secrets=True, sftp=fs)
    assert any(e.get("name") == ".env" and e.get("snippet") for e in secret_ok["entries"])


def test_peek_and_image_name():
    s = _server()
    fs = MemSFTP()
    fs.add_file("/home/piherder/docker/blob.bin", b"\x00\x01\xffABC")
    fs.add_file("/home/piherder/docker/pic.png", b"\x89PNG\r\n")
    peek = hf.peek_file(s, "blob.bin", sftp=fs)
    assert peek["is_image"] is False
    assert "00 01 ff" in peek["hex"]
    assert hf.is_image_name("pic.png")
    img = hf.peek_file(s, "pic.png", sftp=fs)
    assert img["is_image"] is True
    chunks = b"".join(hf.iter_preview(s, "pic.png", sftp=fs))
    assert chunks.startswith(b"\x89PNG")


def test_parse_container_path():
    assert hf.parse_container_path("/data/db") == "/data/db"
    with pytest.raises(hf.FilesError):
        hf.parse_container_path("/data/../etc/passwd")
    with pytest.raises(hf.FilesError):
        hf.parse_container_path("a/../../x")
    assert hf._docker_name_ok("frigate")
    assert not hf._docker_name_ok("frigate;rm")
