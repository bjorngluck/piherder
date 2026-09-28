"""v1.7 coverage — host-file search, compose writes, cancel, cert probe, backup fallback.

SSH and openssl are stubs. Database rows live in a temp SQLite engine.
"""
from __future__ import annotations

import io
import stat
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from tests.test_coverage_v17_q1 import _engine

_FP = "ab" * 32


def _server():
    return SimpleNamespace(
        id=7,
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        ssh_port=22,
        container_patch_enabled=False,
        docker_base_dir=None,
    )


class _Buf:
    def __init__(self, data=b""):
        self._data = data

    def write(self, data):
        return len(data)

    def read(self, n=-1):
        data, self._data = self._data, b""
        return data if n < 0 else data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Fs:
    """Small jail under /home/pi, including a symlink root and a few odd names."""

    def __init__(self):
        self.fail_list = False
        self.as_text = False
        self.fail_open = set()

    def normalize(self, path):
        path = str(path)
        if path.rstrip("/") == "/home/pi":
            return "/home/pi/real"
        if path.endswith("/out"):
            return "/etc/passwd"
        if path.endswith("/badlink"):
            raise OSError("dangling")
        return path

    def lstat(self, path):
        path = str(path)
        if path.rstrip("/") in ("/home/pi", "/home/pi/real"):
            mode = stat.S_IFLNK if path.rstrip("/") == "/home/pi" else stat.S_IFDIR
            return SimpleNamespace(st_mode=mode | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000)
        if path.endswith("/sub"):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000)
        if path.endswith("/notes.txt"):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=11, st_mtime=1, st_uid="nope", st_gid="nope")
        if path.endswith("/readme.md"):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=5, st_mtime=1, st_uid=1, st_gid=1)
        if path.endswith("/.env"):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=8, st_mtime=1, st_uid=1, st_gid=1)
        if path.endswith("/out") or path.endswith("/badlink"):
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_size=0, st_mtime=1, st_uid=1, st_gid=1)
        if path.endswith("/file"):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=1, st_mtime=1, st_uid=1, st_gid=1)
        raise FileNotFoundError(path)

    def listdir_attr(self, path):
        if self.fail_list:
            raise OSError("list")
        return [
            SimpleNamespace(filename="notes.txt", st_mode=stat.S_IFREG | 0o644, st_size=11, st_mtime=1, st_uid="x", st_gid="y"),
            SimpleNamespace(filename="out", st_mode=stat.S_IFLNK | 0o777, st_size=0, st_mtime=1, st_uid=1, st_gid=1),
            SimpleNamespace(filename="badlink", st_mode=stat.S_IFLNK | 0o777, st_size=0, st_mtime=1, st_uid=1, st_gid=1),
            SimpleNamespace(filename="../etc", st_mode=stat.S_IFREG | 0o644, st_size=1, st_mtime=1, st_uid=1, st_gid=1),
            SimpleNamespace(filename=".", st_mode=stat.S_IFDIR, st_size=0, st_mtime=1, st_uid=1, st_gid=1),
            SimpleNamespace(filename="", st_mode=0, st_size=0, st_mtime=1, st_uid=1, st_gid=1),
            SimpleNamespace(filename="sub", st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1, st_gid=1),
        ]

    def listdir(self, path):
        return ["notes.txt", "readme.md", ".env", "out", "badlink", "missing", "sub"]

    def open(self, path, mode="rb"):
        path = str(path)
        if path in self.fail_open or path.endswith("/.env"):
            raise OSError("open")
        if path.endswith("/notes.txt"):
            if self.as_text:
                return _Buf("hello notes\nnope\n")
            return _Buf(b"hello notes\nnope\n")
        return _Buf(b"readme")

    def close(self):
        return None


def test_list_and_search(monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    server = _server()
    fs = _Fs()
    listing = hf.list_dir(server, "", sftp=fs)
    names = {e["name"] for e in listing["entries"]}
    assert "notes.txt" in names and "out" in names
    assert any(e.get("escaped") for e in listing["entries"])

    fs.fail_list = True
    with pytest.raises(hf.FilesError):
        hf.list_dir(server, "", sftp=fs)
    fs.fail_list = False

    file_fs = _Fs()

    def _file_lstat(path):
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=1, st_mtime=1, st_uid=1, st_gid=1)

    file_fs.lstat = _file_lstat
    file_fs.normalize = lambda path: path
    with pytest.raises(hf.FilesError):
        hf.list_dir(server, "file", sftp=file_fs)

    found = hf.search(server, "notes", sftp=fs, contents=True)
    assert any("notes" in (e.get("name") or "") for e in found["entries"])

    monkeypatch.setattr(hf, "SEARCH_CAP", 1)
    capped = hf.search(server, "e", sftp=fs)
    assert capped["truncated"] is True or capped["entries"]

    monkeypatch.setattr(hf, "SEARCH_SCAN_MAX", 0)
    scanned = hf.search(server, "notes", sftp=fs)
    assert scanned["truncated"] is True

    monkeypatch.setattr(hf, "WALK_DEPTH_MAX", -1)
    deep = hf.search(server, "notes", sftp=fs)
    assert deep["truncated"] is True

    class _Cli:
        def exec_command(self, *_a, **_k):
            raise OSError("no exec")

    users, groups = hf.user_group_maps(_Cli(), cache_key=("t", 1))
    assert users == {} and groups == {}


def test_compose_writes_and_redeploy(monkeypatch):
    from app.services import docker_management as dm

    class _Sftp:
        def __init__(self):
            self.fail_write = False
            self.fail_close = False

        def remove(self, path):
            if str(path).endswith((".yml", "Dockerfile")) and ".tmp" not in str(path):
                raise OSError("missing")

        def open(self, path, mode="wb"):
            if self.fail_write:
                raise OSError("open")
            return _Buf()

        def rename(self, src, dest):
            return None

        def stat(self, path):
            if str(path).endswith("compose.yaml"):
                return SimpleNamespace()
            raise FileNotFoundError(path)

        def close(self):
            if self.fail_close:
                raise OSError("close")

    class _Client:
        def __init__(self, sftp):
            self._sftp = sftp
            self.fail_close = False

        def open_sftp(self):
            return self._sftp

        def close(self):
            if self.fail_close:
                raise OSError("close")

    sftp = _Sftp()
    client = _Client(sftp)
    monkeypatch.setattr(dm, "get_ssh_client", lambda *_a, **_k: client)
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (0, "", ""))
    ok, err = dm.write_dockerfile(_server(), "/home/pi/docker/web/Dockerfile", "FROM scratch\n")
    assert ok and err == ""
    sftp.fail_write = True
    sftp.fail_close = True
    client.fail_close = True
    ok, err = dm.write_dockerfile(_server(), "/home/pi/docker/web/Dockerfile", b"FROM scratch\n")
    assert ok is False and "OSError" in err

    sftp.fail_write = False
    sftp.fail_close = False
    client.fail_close = False
    ok, err = dm.write_compose_file(_server(), "/home/pi/docker/web", "services: {}\n")
    assert ok and err == ""
    sftp.fail_write = True
    ok, err = dm.write_compose_file(_server(), "/home/pi/docker/web", b"services: {}\n")
    assert ok is False

    empty = dm.redeploy_project(_server(), "  ")
    assert empty["success"] is False
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (1, "image pulled\n", ""))
    soft = dm.redeploy_project(_server(), "/home/pi/docker/web", compose_files=["../x.yml", "notes.txt", "a.yml"])
    assert "pull" in soft


def test_cancel_job_edges(tmp_path, monkeypatch):
    jobs = sys.modules["app.services.jobs"]
    from app.models import Job, Server

    engine = _engine(tmp_path / "cancel.db")
    monkeypatch.setattr(jobs, "engine", engine)
    monkeypatch.setattr(jobs.backup, "stop_backup", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(jobs.os_patching, "_append_os_log", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("log")))
    monkeypatch.setattr(
        jobs.container_patching,
        "append_container_log",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("clog")),
    )
    monkeypatch.setattr(jobs, "make_audit_log", lambda **_k: (_ for _ in ()).throw(RuntimeError("audit")))

    with Session(engine) as session:
        server = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
        session.add(server)
        session.commit()
        session.refresh(server)
        with pytest.raises(jobs.JobNotCancellable):
            jobs.cancel_job(session, None)
        done = Job(server_id=server.id, job_type="os_patch", status="success")
        session.add(done)
        session.commit()
        with pytest.raises(jobs.JobNotCancellable):
            jobs.cancel_job(session, done)
        for kind, details in (
            ("backup", "{}"),
            ("os_patch", "["),
            ("container_patch", "{}"),
        ):
            row = Job(
                server_id=server.id,
                job_type=kind,
                status="running",
                details=details,
                celery_task_id="task-1",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            out = jobs.cancel_job(session, row, user_id=1, message="   ")
            assert out.status == "cancelled"


def test_pem_and_tls_probe(monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from app.services import certificates as certs

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "app.lab")])
    now = datetime.utcnow()
    leaf = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(7)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("app.lab"), x509.DNSName("www.lab")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    pem = leaf.public_bytes(serialization.Encoding.PEM).decode()
    meta = certs.parse_pem_metadata(pem)
    assert "app.lab" in meta["domains"]

    class _Boom:
        def get_attributes_for_oid(self, *_a):
            raise RuntimeError("cn")

        def rfc4514_string(self):
            raise RuntimeError("issuer")

    class _Ext:
        def get_extension_for_class(self, *_a):
            raise RuntimeError("san")

    class _Cert:
        subject = _Boom()
        issuer = _Boom()
        not_valid_before_utc = now
        not_valid_after_utc = now
        extensions = _Ext()

        def __init__(self):
            self._serial_hits = 0

        @property
        def serial_number(self):
            self._serial_hits += 1
            if self._serial_hits == 1:
                raise RuntimeError("serial")
            return 7

        def public_bytes(self, _enc):
            return b"der"

    monkeypatch.setattr(certs.x509, "load_pem_x509_certificate", lambda *_a, **_k: _Cert())
    rough = certs.parse_pem_metadata(pem)
    assert rough["fingerprint_sha256"]

    skipped = certs.verify_tls_endpoint_fingerprint(verify_url="https://app.lab", expected_fingerprint="")
    assert skipped["status"] == "skipped"
    missing = certs.verify_tls_endpoint_fingerprint(verify_url="https://", expected_fingerprint=_FP)
    assert missing["status"] == "skipped"

    import app.services.ssh as ssh_mod

    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, _FP + "\n", ""))
    matched = certs.verify_tls_endpoint_fingerprint(
        verify_url="postgres://db.local:5432",
        expected_fingerprint=_FP,
        client=SimpleNamespace(),
    )
    assert matched["ok"] is True

    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "cd" * 32 + "\n", ""))
    mismatch = certs.verify_tls_endpoint_fingerprint(
        verify_url="db.local:5432",
        expected_fingerprint=_FP,
        client=SimpleNamespace(),
    )
    assert mismatch["ok"] is False and mismatch["status"] == "failed"

    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (_ for _ in ()).throw(OSError("ssh")))

    def _local(*_a, **_k):
        raise TimeoutError("openssl")

    monkeypatch.setattr("subprocess.run", _local)
    failed = certs.verify_tls_endpoint_fingerprint(
        verify_url="10.0.0.5:443?sni=app.lab",
        expected_fingerprint=_FP,
        client=SimpleNamespace(),
    )
    assert failed["ok"] is False and failed["attempts"]


def test_herder_backup_fallback(tmp_path, monkeypatch):
    import tarfile

    from app.services import herder_backup as hb

    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    primary.mkdir()
    fallback.mkdir()
    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", primary)
    monkeypatch.setattr(hb, "_ensure_dir", lambda: None)
    monkeypatch.setattr(hb, "load_settings", lambda: {"keep": 2})
    monkeypatch.setattr(hb, "_build_backup_payload", lambda **_k: {"manifest": {}, "servers": []})
    monkeypatch.setattr(hb, "_add_data_files_to_tar", lambda _tar: None)
    monkeypatch.setattr(hb, "prune_old_backups", lambda *_a, **_k: None)
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [primary, fallback])
    monkeypatch.setattr(hb, "_path_is_writable", lambda path: path == fallback)

    real_open = tarfile.open
    state = {"n": 0}

    def _open(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise PermissionError("primary")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(hb.tarfile, "open", _open)
    path = hb.create_herder_backup(config_only=True)
    assert path.parent == fallback and path.exists()

    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", primary)
    state["n"] = 0

    def _always(*_a, **_k):
        raise PermissionError("nope")

    monkeypatch.setattr(hb.tarfile, "open", _always)
    with pytest.raises(PermissionError):
        hb.create_herder_backup(config_only=True)

    monkeypatch.setattr(hb.tarfile, "open", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(hb.os, "access", lambda *_a, **_k: True)
    with pytest.raises(OSError):
        hb.create_herder_backup(config_only=True)


def test_binding_scopes_and_commit_race(tmp_path, monkeypatch):
    from app.models import Integration, IntegrationBinding, Server
    from app.services.integrations import registry as reg

    engine = _engine(tmp_path / "bind.db")
    with Session(engine) as session:
        server = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
        graf = Integration(type="grafana", name="Graf", base_url="http://graf")
        other = Integration(type="grafana", name="Other", base_url="http://other")
        session.add(server)
        session.add(graf)
        session.add(other)
        session.commit()
        session.refresh(server)
        session.refresh(graf)
        session.refresh(other)
        with pytest.raises(ValueError):
            reg.set_binding(session, integration_id=graf.id, server_id=server.id, external_id="  ")
        row = reg.set_binding(
            session,
            integration_id=graf.id,
            server_id=server.id,
            external_id="fleet",
            role=reg.ROLE_DASHBOARD,
            external_meta={"kind": reg.GRAFANA_KIND_CONTAINERS},
        )
        assert row.external_id == "fleet"
        stale = IntegrationBinding(
            integration_id=other.id,
            server_id=server.id,
            role=reg.ROLE_SSH,
            external_id="old",
        )
        session.add(stale)
        session.commit()
        session.refresh(stale)
        moved = reg.set_binding(
            session,
            integration_id=graf.id,
            server_id=server.id,
            external_id="ssh-1",
            role=reg.ROLE_SSH,
            binding_id=stale.id,
        )
        assert moved.role == reg.ROLE_SSH

        monkeypatch.setattr(reg, "maybe_discover_logo", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("logo")))
        svc = reg.set_binding(
            session,
            integration_id=graf.id,
            server_id=server.id,
            external_id="web",
            role=reg.ROLE_SERVICE,
            docker_project="web",
            docker_container="web",
            external_label="Web",
            last_state="up",
            last_message="ok",
        )
        assert svc.docker_project == "web"

        real_commit = session.commit
        calls = {"n": 0}

        def _commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("race")
            return real_commit()

        session.commit = _commit
        raced = reg.set_binding(
            session,
            integration_id=graf.id,
            server_id=server.id,
            external_id="web",
            role=reg.ROLE_SERVICE,
            docker_project="web",
            docker_container="web",
            external_label="Web2",
        )
        assert raced.external_label == "Web2"
        session.commit = real_commit

        def _insert_race():
            raise RuntimeError("unique")

        session.commit = _insert_race
        with pytest.raises(ValueError):
            reg.set_binding(
                session,
                integration_id=graf.id,
                server_id=server.id,
                external_id="brand-new",
                role=reg.ROLE_SERVICE,
                docker_project="other",
            )


def test_compose_yaml_tail():
    from app.services.docker_management import validate_compose_content

    samples = [
        "services:\n  web:\n    image: [\n    ports\n      - 80\n",
        "a: [\nb: [\n  - x\n",
        "foo: bar: baz\n  nested: [\n",
    ]
    assert any(validate_compose_content(text)["errors"] for text in samples)
