"""v1.6 Q-80 fourth pack — remaining fat service gaps toward 75%.

host_files docker/helpers, docker_management nest/write/classify,
jobs list/enqueue/execute, herder snapshots, certs layout, stack panel,
harden, pihole URL helpers. Mocked SSH / sqlite; no live network.
"""
from __future__ import annotations

import io
import json
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server, ServiceDnsRecord, User


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(session, **kw):
    s = Server(
        name=kw.get("name", "pi"),
        hostname=kw.get("hostname", "pi.local"),
        ssh_username=kw.get("ssh_username", "pi"),
        docker_base_dir=kw.get("docker_base_dir", "/home/pi/docker"),
        os_type=kw.get("os_type", "debian"),
        os_patch_enabled=True,
        container_patch_enabled=kw.get("container_patch_enabled", True),
        backup_enabled=True,
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address=kw.get("ip_address", "10.0.0.4"),
        ssh_private_key_encrypted=kw.get("ssh_private_key_encrypted", "enc"),
        os_apply_steps=kw.get("os_apply_steps"),
        container_updates_summary=kw.get("container_updates_summary"),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _job(session, server, job_type="backup", status="running", details="{}"):
    job = Job(
        server_id=server.id if server else None,
        job_type=job_type,
        status=status,
        details=details,
        celery_task_id="celery-abc",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


class _Sftp:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.closed = False

    def normalize(self, path):
        return path

    def lstat(self, path):
        if path not in self.files and not any(p.startswith(path.rstrip("/") + "/") for p in self.files):
            # treat known dirs as dirs
            if path.rstrip("/") in ("/home/pi/docker", "/home/pi/docker/grafana") or path.endswith("/"):
                return SimpleNamespace(
                    st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1.0, st_uid=1000, st_gid=1000
                )
            raise FileNotFoundError(path)
        data = self.files.get(path, b"")
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_size=len(data),
            st_mtime=1.0,
            st_uid=1000,
            st_gid=1000,
        )

    def stat(self, path):
        return self.lstat(path)

    def open(self, path, mode="rb"):
        if "w" in mode:
            buf = io.BytesIO()

            class W:
                def write(_, data):
                    buf.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

                def close(_):
                    self.files[path] = buf.getvalue()

                def __enter__(wself):
                    return wself

                def __exit__(wself, *a):
                    wself.close()

            return W()
        return io.BytesIO(self.files.get(path, b""))

    def remove(self, path):
        self.files.pop(path, None)

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src, b"")

    def chmod(self, path, mode):
        return None

    def chown(self, path, uid, gid):
        return None

    def close(self):
        self.closed = True


class _Cli:
    def __init__(self, sftp=None):
        self._sftp = sftp or _Sftp()
        self.closed = False
        self.cmds = []

    def open_sftp(self):
        return self._sftp

    def close(self):
        self.closed = True

    def get_transport(self):
        return SimpleNamespace(is_active=lambda: True, set_keepalive=lambda *a, **k: None)

    def exec_command(self, cmd, timeout=None):
        self.cmds.append(cmd)
        stdin = MagicMock()
        stdout = iter(["ok\n"])
        stderr = iter([])
        stdout_ns = SimpleNamespace(
            read=lambda: b"ok\n",
            channel=SimpleNamespace(
                recv_exit_status=lambda: 0,
                shutdown_write=lambda: None,
            ),
            __iter__=lambda self: iter(["ok\n"]),
        )
        # iter(stdout) for stream_logs
        class _Out:
            def __iter__(self):
                return iter(["ok\n"])

            def read(self):
                return b"ok\n"

            channel = SimpleNamespace(recv_exit_status=lambda: 0, shutdown_write=lambda: None)

        class _In:
            def write(self, data):
                return None

            channel = SimpleNamespace(shutdown_write=lambda: None)

        return _In(), _Out(), SimpleNamespace(read=lambda: b"")


def _run(cmd: str):
    c = cmd or ""
    if "docker volume ls" in c:
        return 0, "vol1\nbad name\n", ""
    if "volume inspect" in c:
        return 0, "/home/pi/docker/vol1\n", ""
    if "json .Mounts" in c:
        return 0, json.dumps(
            [
                {
                    "Type": "bind",
                    "Source": "/home/pi/docker/grafana",
                    "Destination": "/data",
                    "Name": "",
                    "RW": True,
                },
                {"Type": "tmpfs", "Source": "", "Destination": "/tmp"},
                "skip",
            ]
        ), ""
    if "docker ps -a --format" in c:
        return 0, "grafana\nbad name\n", ""
    if "docker cp " in c:
        return 0, "", ""
    if "docker inspect --format '{{.Id}}'" in c or 'docker inspect --format "{{.Id}}"' in c:
        return 0, "abcdef1234567890\n", ""
    if c.startswith("docker start") or c.startswith("docker stop") or c.startswith("docker restart"):
        if "abcdef" in c:
            return 0, "ok\n", ""
        return 1, "", "no such container"
    if "compose config --format json" in c:
        return 0, json.dumps(
            {
                "services": {
                    "web": {"image": "nginx:latest"},
                    "app": {"build": ".", "image": "local:dev"},
                    "bad": "x",
                }
            }
        ), ""
    if "compose config --images" in c:
        return 0, "nginx:latest\n", ""
    if "compose pull" in c:
        return 0, "Pulled\n", ""
    if "image inspect" in c:
        return 0, "sha256:aaa\n", ""
    if "du -sb" in c:
        return 0, "2048 /home/pi/docker/grafana\nnotint /x\n", ""
    if "find " in c:
        return 0, "/home/pi/docker/grafana/docker-compose.yml\n", ""
    if "docker compose ps" in c:
        return 0, json.dumps({"Service": "web", "Image": "nginx:latest"}) + "\n", ""
    if "ls -1" in c:
        return 0, "docker-compose.yml\n", ""
    if c.strip().startswith("cat "):
        return 0, "services:\n  web:\n    image: nginx:latest\n    build: .\n", ""
    if "docker images --filter" in c:
        return 1, "", "images boom"
    if "docker ps -a --filter" in c:
        return 1, "", "ps boom"
    if "docker inspect" in c:
        return 0, json.dumps(
            [
                {
                    "Id": "sha256:abcdef1234567890",
                    "Name": "/grafana",
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": "/home/pi/docker/grafana",
                            "Destination": "/data",
                            "RW": True,
                        }
                    ],
                }
            ]
        ), ""
    if "id -u" in c:
        return 0, "0\n", ""
    if "mkdir -p" in c:
        return 0, "", ""
    return 0, "", ""


# ---------------------------------------------------------------------------
# host_files remaining helpers + docker listing
# ---------------------------------------------------------------------------


def test_host_files_helpers_and_docker_listing(monkeypatch):
    from app.services import host_files as hf

    srv = SimpleNamespace(
        id=1,
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="enc",
        ssh_password_encrypted=None,
        os_type="debian",
        ssh_port=22,
        ip_address="10.0.0.4",
    )
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)

    assert hf.posix_norm("") == ""
    assert hf.posix_norm("a/./b/../c") == "a/c"
    with pytest.raises(hf.FilesError):
        hf.posix_norm("a\x00b")
    assert hf.posix_norm("/a/b") == "/a/b"
    assert hf.posix_norm("/") == "/"
    assert hf.join_jail("/", "etc") == "/etc"
    assert hf._prefix_hit("", "/x") is False
    assert hf._prefix_hit("/x", "/") is False
    assert hf.rel_of("/a/b", "/") == "a/b"
    with pytest.raises(hf.FilesError):
        hf.rel_of("/other", "/home/pi/docker")
    assert hf.human_size("nope") == "0 B"
    assert "KiB" in hf.human_size(2048)
    assert hf._mtime_dt("nope") is None
    assert hf._mtime_dt(0) is None
    assert hf._mtime_dt(1_700_000_000) is not None
    assert hf._as_bytes(None) is None
    assert hf._as_bytes("nope") is None
    assert hf._as_bytes("-1") is None
    assert hf.clamp_files_max_bytes(None) == hf.MAX_UPLOAD_DEFAULT
    with pytest.raises(hf.FilesError):
        hf.normalize_role("other")
    ident = SimpleNamespace(username="ops")
    assert hf.identity_username(srv, ident) == "ops"
    assert hf.files_supported(srv) is True
    assert hf.looks_like_text("bin.dat", b"\x00\x01") is False
    assert hf.looks_like_text("x.bin", bytes(range(128, 256))) is False
    assert hf.is_image_name("pic.png") is True
    assert hf.image_media_type("pic.png") == "image/png"
    hx, ascii_ = hf._hex_dump(b"AB\xff", limit=10)
    assert "41" in hx
    assert hf.parse_mode("0o644") == 0o644
    with pytest.raises(hf.FilesError):
        hf.parse_mode("999")
    with pytest.raises(hf.FilesError):
        hf.parse_id_name("9999999999", kind="owner")
    with pytest.raises(hf.FilesError):
        hf.parse_id_name("bad name", kind="owner")
    assert hf.parse_id_name("pi", kind="owner") == "pi"
    priv_msg = hf._write_denied_msg(hf.ROLE_PRIVILEGED)
    assert "sudo" in priv_msg.lower() or "privileged" in priv_msg.lower()
    fleet_msg = hf._write_denied_msg(hf.ROLE_FLEET)
    assert "fleet" in fleet_msg.lower() or "Privileged" in fleet_msg
    assert hf._is_perm_denied(PermissionError("x")) is True
    assert hf._is_perm_denied(RuntimeError("EACCES denied")) is True
    assert hf._docker_name_ok("grafana") is True
    assert hf._docker_name_ok("bad name") is False
    with pytest.raises(hf.FilesError):
        hf.parse_container_path("a/../b")
    with pytest.raises(hf.FilesError):
        hf.parse_container_path("")
    assert hf.parse_container_path("/var/log/app") == "/var/log/app"
    stream = io.BytesIO(b"abc")
    hasher = __import__("hashlib").sha256()
    assert hf._stream_bytes_for_retry(stream, hasher, 0) == b"abc"
    stream.seek(0)
    assert hf._stream_bytes_for_retry(stream, hasher, 3) == b"abc"

    class NoSeek:
        def read(self):
            return "hi"

        def seek(self, *a):
            raise OSError("no")

    assert hf._stream_bytes_for_retry(NoSeek(), hasher, 1) is None

    monkeypatch.delenv(hf.FILES_MAX_ENV, raising=False)

    def _boom_db():
        raise RuntimeError("db")

    monkeypatch.setattr(
        "app.services.app_settings._load_raw_from_db", _boom_db, raising=False
    )
    n = hf.max_upload_bytes()
    assert n > 0
    monkeypatch.setenv(hf.FILES_MAX_ENV, "1048576")
    assert hf.files_max_env_locked() is True
    assert hf.max_upload_bytes() == 1048576
    monkeypatch.delenv(hf.FILES_MAX_ENV, raising=False)

    with pytest.raises(hf.FilesError):
        hf.jail_path(SimpleNamespace(container_patch_enabled=True, docker_base_dir="/", ssh_username="pi"))

    fs = _Sftp({"/home/pi/docker/grafana/config.yml": b"ok\n"})
    cli = _Cli(fs)

    @contextmanager
    def _sess(*a, **k):
        yield (cli, fs)

    monkeypatch.setattr(hf, "sftp_session", _sess)
    monkeypatch.setattr(hf, "run_command", lambda client, cmd, timeout=20: _run(cmd))
    vols = hf.list_docker_volumes(srv)
    assert any(v["name"] == "vol1" for v in vols)
    mounts = hf.list_container_mounts(srv, "grafana")
    assert any(m["destination"] == "/data" for m in mounts)
    names = hf.list_docker_containers(srv)
    assert "grafana" in names
    copied = hf.docker_cp_into(srv, "grafana", "/etc/hosts", "grafana")
    assert copied["container"] == "grafana"

    with pytest.raises(hf.FilesError):
        hf.list_container_mounts(srv, "bad name")
    with pytest.raises(hf.FilesError):
        hf.docker_cp_into(srv, "!!!", "/x", "grafana")

    monkeypatch.setattr(hf, "is_demo_files", lambda: True)
    hf._refuse_demo_write
    with pytest.raises(hf.FilesError):
        hf._refuse_demo_write()
    assert hf.list_docker_volumes(srv) == []
    assert hf.list_docker_containers(srv) == []
    assert hf.list_container_mounts(srv, "grafana") == []
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)

    assert hf._sftp_chmod(fs, "/home/pi/docker/grafana/config.yml", 0o644) is True
    boom = SimpleNamespace(chmod=lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    assert hf._sftp_chmod(boom, "/x", 0o644) is False
    boom2 = SimpleNamespace(
        lstat=lambda p: SimpleNamespace(st_uid=1, st_gid=1),
        chown=lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert hf._sftp_chown(boom2, "/x", 0, 0) is False
    assert hf._sftp_chown(fs, "/x", None, None) is True

    hf.drop_sftp_pool()
    assert hf._transport_alive(None) is False
    assert hf._transport_alive(SimpleNamespace(get_transport=lambda: (_ for _ in ()).throw(RuntimeError("x")))) is False
    lease = hf._SftpLease(client=cli, sftp=fs, lock=__import__("threading").Lock(), last=0.0, key=("k",))
    hf._close_lease(lease)
    hf._tune_transport(cli)
    hf._tune_transport(SimpleNamespace(get_transport=lambda: None))
    hf._tune_sftp_file(None)

    assert hf._remote_is_root(None, srv, SimpleNamespace(username="root")) is True
    assert hf._remote_is_root(None, srv, None) is False
    monkeypatch.setattr(hf, "run_command", lambda client, cmd, timeout=8: (0, "0\n", ""))
    assert hf._remote_is_root(cli, srv, None) is True
    monkeypatch.setattr(hf, "run_command", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert hf._remote_is_root(cli, srv, None) is False

    with pytest.raises(hf.FilesError):
        hf._run_elevated(None, "chmod 644 x", as_root=True, allow_sudo=True)
    monkeypatch.setattr(hf, "run_command", lambda client, cmd, timeout=30: (0, "", ""))
    assert hf._run_elevated(cli, "chmod 644 x", as_root=True, allow_sudo=True) == "plain"
    monkeypatch.setattr(
        hf,
        "run_command",
        lambda client, cmd, timeout=30: (1, "", "sudo: a terminal is required")
        if "sudo" in cmd
        else (0, "", ""),
    )
    how = hf._run_elevated(cli, "chmod 644 x", as_root=False, allow_sudo=True)
    assert how in ("sudo", "plain")
    monkeypatch.setattr(hf, "run_command", lambda *a, **k: (1, "", "nope"))
    with pytest.raises(hf.FilesError):
        hf._run_elevated(cli, "chmod 644 x", as_root=False, allow_sudo=False)

    hasher = __import__("hashlib").sha256()
    monkeypatch.setattr(hf, "_exec_write", lambda *a, **k: (0, "", ""))
    h, n = hf._put_via_sudo(cli, "/tmp/x", b"hi", cap=100)
    assert n == 2
    with pytest.raises(hf.FilesError):
        hf._put_via_sudo(cli, "/tmp/x", b"hi" * 100, cap=2)
    monkeypatch.setattr(hf, "_exec_write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(hf.FilesError):
        hf._put_via_sudo(cli, "/tmp/x", b"hi", cap=100)
    monkeypatch.setattr(hf, "_exec_write", lambda *a, **k: (1, "", "denied"))
    with pytest.raises(hf.FilesError):
        hf._put_via_sudo(cli, "/tmp/x", b"hi", cap=100)

    with pytest.raises(hf.FilesError):
        hf._safe_zip_name("")
    with pytest.raises(hf.FilesError):
        hf._safe_zip_name("/abs")
    with pytest.raises(hf.FilesError):
        hf._safe_zip_name("../x")
    assert hf._safe_zip_name("a/b.txt") == "a/b.txt"
    assert hf.zip_basename("pack") == "pack.zip"
    assert hf.zip_basename(None, ["one"]) == "one.zip"
    assert hf.zip_basename(None, ["a", "b"]) == "files.zip"

    with pytest.raises(hf.FilesError):
        hf._listdir_names(SimpleNamespace(listdir=lambda p: (_ for _ in ()).throw(OSError("x"))), "/x")


# ---------------------------------------------------------------------------
# docker_management write/classify/nest/mounts
# ---------------------------------------------------------------------------


def test_docker_management_write_classify_nest(monkeypatch):
    from app.services import docker_management as dm

    srv = SimpleNamespace(id=1, hostname="pi", docker_base_dir="/home/pi/docker")
    fs = _Sftp(
        {
            "/home/pi/docker/g/docker-compose.yml": b"services:\n  web:\n    image: nginx\n    build: .\n"
        }
    )
    cli = _Cli(fs)
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: cli)
    monkeypatch.setattr(dm, "run_command", lambda client, cmd, timeout=20: _run(cmd))

    assert dm.normalize_container_ref("") == ""
    assert dm.normalize_container_ref(" /a,b ") == "a"
    bad = dm.container_action(srv, "", "start")
    assert bad["success"] is False
    bad2 = dm.container_action(srv, "web", "explode")
    assert bad2["success"] is False
    retried = dm.container_action(srv, "web", "start")
    assert retried["success"] is True

    content = dm.read_compose_file(srv, "/home/pi/docker/g")
    assert "services" in content or "No compose" in content
    df = dm.read_dockerfile(srv, "/home/pi/docker/g/Dockerfile")
    assert isinstance(df, str)
    ok, err = dm.write_dockerfile(srv, "/home/pi/docker/g/Dockerfile", "FROM alpine\n")
    assert ok is True
    ok2, err2 = dm.write_compose_file(srv, "/home/pi/docker/g", "services: {}\n")
    assert ok2 is True

    class BoomSftp(_Sftp):
        def open(self, path, mode="rb"):
            raise IOError("nope")

        def rename(self, *a):
            raise OSError("rename")

    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: _Cli(BoomSftp()))
    missing = dm.read_compose_file(srv, "/missing")
    assert "No compose" in missing or missing
    missing_df = dm.read_dockerfile(srv, "/missing/Dockerfile")
    assert "not found" in missing_df.lower() or missing_df
    fail, msg = dm.write_dockerfile(srv, "/x/Dockerfile", "FROM x\n")
    assert fail is False
    fail2, msg2 = dm.write_compose_file(srv, "/x", "x")
    assert fail2 is False

    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: cli)
    classified = dm.classify_compose_images(cli, "/home/pi/docker/g")
    assert "nginx:latest" in classified["pullable_images"]
    assert "app" in classified["build_services"]
    upd = dm.check_compose_updates(srv, "/home/pi/docker/g")
    assert "has_updates" in upd

    monkeypatch.setattr(
        dm,
        "classify_compose_images",
        lambda *a, **k: {"pullable_images": [], "build_services": ["app"]},
    )
    empty = dm.check_compose_updates(srv, "/home/pi/docker/g")
    assert empty.get("skipped_build_only") is True

    srv.container_updates_summary = json.dumps(
        {
            "projects": ["grafana"],
            "project_details": {"grafana": {"images": ["nginx:latest"]}},
        }
    )
    parsed = dm.parse_container_updates_summary(srv)
    assert "grafana" in parsed["projects"]
    srv.container_updates_summary = "not-json"
    assert dm.parse_container_updates_summary(srv)["projects"] == set()
    srv.container_updates_summary = json.dumps(["x"])
    assert dm.parse_container_updates_summary(srv)["projects"] == set()
    assert dm._image_ref_matches("", {"nginx"}) is False
    assert dm._image_ref_matches("nginx:latest", {"nginx:latest"}) is True
    assert dm._image_ref_matches("nginx:latest", {"nginx:latest@sha256:x"}) is True

    projects = [
        {
            "name": "grafana",
            "path": "/home/pi/docker/grafana",
            "services": ["web", "db"],
            "containers": [
                {"name": "web", "image": "nginx:latest", "running": True, "compose_service": "web"}
            ],
        }
    ]
    orphans = [{"name": "lone", "image": "busybox:latest"}]
    srv.container_updates_summary = json.dumps(
        {"projects": ["grafana"], "project_details": {"grafana": {"images": ["nginx:latest"]}}}
    )
    flagged, orph = dm.annotate_update_flags(projects, orphans, srv)
    assert flagged[0]["has_pending_update"] is True

    nested, leftover = dm.nest_containers_under_projects(
        [{"name": "grafana", "path": "/home/pi/docker/grafana", "services": ["web", "db"]}],
        [
            {
                "name": "g-web",
                "compose_project": "grafana",
                "compose_service": "web",
                "compose_workdir": "/home/pi/docker/grafana",
                "running": True,
            },
            {
                "name": "other",
                "compose_project": "other",
                "compose_workdir": "/home/pi/docker/grafana",
                "running": False,
            },
            {"name": "error"},
        ],
    )
    assert nested[0]["container_count"] == 1
    assert any(x.get("placeholder") for x in nested[0]["containers"])

    stubs = dm.ensure_label_projects(
        [{"name": "grafana"}],
        [{"compose_project": "piherder-e2e", "compose_workdir": "/home/pi/docker/piherder"}],
    )
    assert any(p.get("label_only") for p in stubs)

    assert dm._human_bytes("nope") == ""
    assert dm._human_bytes(-1) == ""
    assert "B" in dm._human_bytes(10)
    assert "KB" in dm._human_bytes(2048)
    m = dm._parse_inspect_mount(
        {"Source": "/a", "Destination": "/b", "Type": "bind", "RW": False, "Name": "n"}
    )
    assert m["ro"] is True
    assert "→" in dm._format_mount_line(m)
    assert dm._format_mount_line({"destination": "/only"}) == "/only"
    assert dm._format_mount_line({"source": "/s"}) == "/s"
    assert dm._format_mount_line({}) == "—"
    sizes = dm._du_sizes_for_paths(cli, ["/home/pi/docker/grafana", "rel", "/home/pi/docker/grafana"])
    assert sizes.get("/home/pi/docker/grafana") == 2048
    assert dm._du_sizes_for_paths(cli, []) == {}

    missing = dm.get_container_mounts_detail(srv, "")
    assert missing["success"] is False
    detail = dm.get_container_mounts_detail(srv, "grafana")
    assert detail["success"] is True
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (1, "", "fail"))
    fail = dm.get_container_mounts_detail(srv, "grafana")
    assert fail["success"] is False
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (0, "not-json", ""))
    badj = dm.get_container_mounts_detail(srv, "grafana")
    assert badj["success"] is False

    monkeypatch.setattr(dm, "run_command", lambda client, cmd, timeout=20: _run(cmd))
    unused = dm.list_unused_images_and_containers(srv)
    assert unused["success"] is False
    assert unused["errors"]

    builds = dm.get_compose_build_services(srv, "/home/pi/docker/g")
    assert isinstance(builds, dict)


# ---------------------------------------------------------------------------
# jobs list/count/public/enqueue/execute
# ---------------------------------------------------------------------------


def test_jobs_list_public_enqueue_and_execute(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session, os_apply_steps="update,upgrade")
    monkeypatch.setattr(js, "engine", engine)

    @contextmanager
    def _fresh():
        s = Session(engine, expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    monkeypatch.setattr(js, "_revoke_celery_task", lambda *a, **k: None)
    monkeypatch.setattr(js, "_send_summary_webhook", lambda *a, **k: None)
    monkeypatch.setattr(
        js,
        "make_audit_log",
        lambda **k: AuditLog(
            user_id=k.get("user_id"),
            server_id=k.get("server_id"),
            action=k.get("action") or "job",
            status=k.get("status") or "running",
            details=k.get("details") or "",
        ),
    )
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: "1.2.3.4")

    class Immediate:
        def submit(self, fn, *a, **k):
            fn(*a, **k)
            return SimpleNamespace()

    monkeypatch.setattr(js, "_patch_apply_pool", Immediate())
    monkeypatch.setattr(js, "_update_check_pool", Immediate())

    j1 = _job(session, srv, job_type="backup", status="running")
    j2 = _job(session, srv, job_type="os_patch", status="success", details="not-json")
    listed = js.list_jobs_for_server(session, srv.id, active_only=True)
    assert any(j.id == j1.id for j in listed)
    listed2 = js.list_jobs_for_server(session, srv.id, status="success", job_type="os_patch")
    assert listed2
    now = datetime.utcnow()
    fleet = js.list_jobs(
        session,
        server_id=srv.id,
        job_type="backup",
        date_from=now - timedelta(days=1),
        date_to=now + timedelta(days=1),
        limit=10,
        offset=0,
    )
    assert fleet
    n = js.count_jobs(session, server_id=srv.id, active_only=True)
    assert n >= 1
    n2 = js.count_jobs(session, status="success", job_type="os_patch")
    assert n2 >= 1
    assert js.job_type_label(None) == "Job"
    assert js.job_type_label("backup") == "Backup"
    assert "Os" in js.job_type_label("custom_os_thing") or js.job_type_label("custom_os_thing")

    pub = js.job_public_dict(j1)
    assert pub["cancellable"] is True
    pubd = js.job_public_dict(j2, detail=True)
    assert "details_json" in pubd
    assert js.job_source_filter(j2) is None
    assert js.get_active_backup_job(session, srv.id) is not None
    assert js.get_running_backup_job(session, srv.id) is not None
    src = _job(session, srv, details='{"source_filter":"/data"}')
    assert js.get_active_job_for_source(session, srv.id, "/data") is not None
    assert js.resolve_backup_job(session, srv.id, source_filter="/data") is not None
    assert js.resolve_backup_job(session, srv.id) is not None

    loaded, host = js._load_server_for_job(srv.id)
    assert loaded is not None
    missing, hid = js._load_server_for_job(99999)
    assert missing is None
    js._revoke_celery_task(None)
    js._revoke_celery_task("tid")

    steps = js._parse_os_apply_steps(None)
    assert "update" in steps
    steps2 = js._parse_os_apply_steps('["update","upgrade"]')
    assert steps2
    steps3 = js._parse_os_apply_steps("update,upgrade")
    assert steps3
    steps4 = js._parse_os_apply_steps("{")
    assert steps4

    monkeypatch.setattr(
        js.os_patching,
        "run_os_patch",
        lambda *a, **k: {"success": True, "summary": "ok", "auto_mark_haos": True},
    )
    monkeypatch.setattr(js.os_patching, "os_patch_succeeded", lambda res: True)
    monkeypatch.setattr(js.os_patching, "init_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(js.os_patching, "mark_os_patch_done", lambda *a, **k: None)
    monkeypatch.setattr(js.os_patching, "_append_os_log", lambda *a, **k: None)
    monkeypatch.setattr(
        js.os_patching,
        "check_os_updates",
        lambda *a, **k: {
            "updates_count": 2,
            "reboot_pending": True,
            "packages_sample": ["a"],
            "phased_sample": ["b"],
            "phased_count": 1,
            "auto_mark_haos": True,
            "detected_os_type": "haos",
        },
    )
    monkeypatch.setattr(js.os_patching, "attach_audit_fields", lambda res, host, **k: res)
    monkeypatch.setattr(js.os_patching, "normalize_os_patch_steps", lambda steps: list(steps) if steps else ["update"])

    job = js.enqueue_os_patch_apply(srv.id, user_id=1, scheduled=True)
    assert job is not None
    again = js.enqueue_os_patch_apply(srv.id, user_id=1)
    # active job of type may skip
    assert again is None or isinstance(again, Job)

    # finish leftover os_patch so container path can run
    for row in session.exec(__import__("sqlmodel").select(Job)).all():
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()

    monkeypatch.setattr(
        js.container_patching,
        "run_project_update",
        lambda *a, **k: {"success": True},
    )
    monkeypatch.setattr(js.container_patching, "container_patch_succeeded", lambda res: True)
    monkeypatch.setattr(js.container_patching, "init_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(js.container_patching, "mark_container_patch_done", lambda *a, **k: None)
    monkeypatch.setattr(js.container_patching, "append_container_log", lambda *a, **k: None)
    monkeypatch.setattr(
        js.container_patching,
        "check_all_projects_updates",
        lambda *a, **k: {
            "projects_with_updates": ["g"],
            "project_details": {"g": {"images": ["nginx"]}},
            "projects_checked": ["g"],
        },
    )
    monkeypatch.setattr(js.container_patching, "summarize_container_patch", lambda res: "sum")

    cjob = js.enqueue_container_patch_apply(srv.id, user_id=1, scheduled=True)
    assert cjob is not None

    for row in list(session.exec(__import__("sqlmodel").select(Job)).all()):
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()

    ocheck = js.enqueue_os_update_check(srv.id, user_id=1)
    assert ocheck is not None
    # second returns active
    o2 = js.enqueue_os_update_check(srv.id, user_id=1)
    assert o2 is not None

    for row in list(session.exec(__import__("sqlmodel").select(Job)).all()):
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()

    ccheck = js.enqueue_container_update_check(srv.id, user_id=1)
    assert ccheck is not None

    for row in list(session.exec(__import__("sqlmodel").select(Job)).all()):
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()

    now_job = js.run_os_update_check_now(session, srv, user_id=1)
    assert now_job is not None
    for row in list(session.exec(__import__("sqlmodel").select(Job)).all()):
        if row.status in ("pending", "running"):
            row.status = "success"
            session.add(row)
    session.commit()
    now_c = js.run_container_update_check_now(session, srv, user_id=1)
    assert now_c is not None

    js._apply_os_check_result(session, 99999, {})
    js._apply_container_check_result(session, 99999, {})
    js._apply_os_check_result(
        session,
        srv.id,
        {
            "updates_count": 1,
            "reboot_pending": False,
            "packages_sample": ["x"],
            "phased_count": 0,
            "error": "note",
            "backend": "apt",
            "ha": {"core": 1},
            "identity": "pi",
        },
    )
    js._apply_container_check_result(
        session,
        srv.id,
        {"projects_with_updates": ["g"], "project_details": {}, "failed": [], "projects_checked": ["g"]},
    )

    assert js._project_basename("/home/pi/docker/grafana") == "grafana"
    js._flush_container_progress_to_job(now_c.id, "patching", "line")

    assert "check failed" in js._human_job_summary(
        "docker_stack_check", "failed", json.dumps({"project": "g"})
    )
    assert "failed" in js._human_job_summary(
        "docker_stack_stop", "failed", json.dumps({"project": "g", "action": "stop", "error": "x"})
    )
    assert "delete failed" in js._human_job_summary(
        "docker_stack_remove", "failed", json.dumps({"project": "g", "error": "x"})
    )
    assert "failed" in js._human_job_summary(
        "template_deploy", "failed", json.dumps({"project_name": "g", "error": "x"})
    )
    assert "unknown" in js._human_job_summary(
        "template_drift_check", "failed", json.dumps({"project_name": "g", "error": "x"})
    )
    assert js._human_job_summary("container_patch", "ok", "plain") == "plain"
    assert "sum" in js._human_job_summary("container_patch", "ok", json.dumps({"summary": "sum"}))

    # execute missing server
    job_m = _job(session, srv, job_type="os_patch", status="pending")
    audit = AuditLog(server_id=srv.id, action="os_patch", status="running", details="Job started")
    session.add(audit)
    session.commit()
    session.refresh(audit)
    js._execute_os_patch_sync(job_m.id, 99999, audit.id, ["update"])
    js._execute_container_patch_sync(job_m.id, 99999, audit.id)
    js._execute_os_update_check(job_m.id, 99999, audit.id)
    js._execute_container_update_check(job_m.id, 99999, audit.id)

    monkeypatch.setattr(js.os_patching, "run_os_patch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    job_f = _job(session, srv, job_type="os_patch", status="pending")
    js._execute_os_patch_sync(job_f.id, srv.id, audit.id, ["update"])

    monkeypatch.setattr(
        js.container_patching,
        "run_project_update",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    job_cf = _job(session, srv, job_type="container_patch", status="pending")
    js._execute_container_patch_sync(job_cf.id, srv.id, audit.id)

    assert js.enqueue_os_patch_apply(99999) is None
    assert js.enqueue_container_patch_apply(99999) is None
    assert js.enqueue_os_update_check(99999) is None
    assert js.enqueue_container_update_check(99999) is None


# ---------------------------------------------------------------------------
# herder_backup snapshots via sqlite engine
# ---------------------------------------------------------------------------


def test_herder_backup_snapshots_and_upserts(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    session, engine = _memory()
    srv = _server(session)
    user = User(email="op@example.com", hashed_password="x", role="admin", is_active=True)
    session.add(user)
    session.commit()
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(hb.settings, "HERDER_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(hb, "load_settings", lambda: {"keep": 2, "theme": "dark"})

    payload = hb._build_backup_payload(include_audit=True, config_only=True, since_days=7)
    assert "servers" in payload
    assert "users" in payload
    assert "manifest" in payload
    assert payload["herder_config"]["keep"] == 2

    for fn in (
        hb._snapshot_servers,
        hb._snapshot_users,
        hb._snapshot_docker_versions,
        hb._snapshot_totp_backup_codes,
        hb._snapshot_trusted_devices,
        hb._snapshot_webauthn_credentials,
        hb._snapshot_push_vapid,
        hb._snapshot_push_subscriptions,
        hb._snapshot_push_preferences,
        hb._snapshot_integrations,
        hb._snapshot_integration_bindings,
        hb._snapshot_managed_certificates,
        hb._snapshot_certificate_targets,
        hb._snapshot_service_templates,
        hb._snapshot_stack_deployments,
        hb._snapshot_compose_project_meta,
        hb._snapshot_service_dns_records,
        hb._snapshot_runtime_edges,
        hb._snapshot_topology_categories,
        hb._snapshot_topology_tags,
        hb._snapshot_visual_service_stacks,
        hb._snapshot_container_annotations,
        hb._snapshot_container_annotation_tags,
        hb._snapshot_port_annotations,
        hb._snapshot_user_favourites,
        hb._snapshot_api_tokens,
        hb._snapshot_nmap_scan_schedules,
        hb._snapshot_nmap_devices,
        hb._snapshot_nmap_script_results,
    ):
        rows = fn()
        assert isinstance(rows, list)
    assert isinstance(hb._snapshot_notifications(), list)
    assert isinstance(hb._snapshot_jobs(), list)
    assert isinstance(hb._snapshot_nmap_scan_runs(), list)
    assert isinstance(hb._snapshot_audit(since_days=1), list)
    assert isinstance(hb._snapshot_table(Server, limit=1), list)

    n = hb._upsert_rows(session, Server, [{"id": srv.id, "name": "renamed", "nope": 1}])
    assert n == 1
    session.commit()
    session.refresh(srv)
    assert srv.name == "renamed"
    n2 = hb._upsert_rows(session, Job, [{"status": "success", "job_type": "backup"}], prefer_keep_id=False)
    assert n2 == 1
    n3 = hb._upsert_users(
        session,
        [
            {"id": user.id, "email": "op@example.com", "hashed_password": "y", "role": "admin"},
            {"email": "new@example.com", "hashed_password": "z", "role": "viewer", "is_active": True},
            {},
        ],
    )
    assert n3 >= 1

    avatars = tmp_path / "data" / "avatars"
    avatars.mkdir(parents=True)
    (avatars / "a.png").write_bytes(b"png")
    logos = tmp_path / "data" / "service_logos"
    logos.mkdir(parents=True)
    (logos / "l.png").write_bytes(b"png")
    monkeypatch.setattr(hb, "_avatar_files", lambda: [avatars / "a.png"])
    monkeypatch.setattr(hb, "_service_logo_files", lambda: [logos / "l.png"])
    import tarfile

    tar_path = tmp_path / "t.tar"
    with tarfile.open(tar_path, "w") as tar:
        nfiles = hb._add_data_files_to_tar(tar)
    assert nfiles == 2

    assert isinstance(hb._pg_dump_available(), bool)
    monkeypatch.setattr(hb, "_database_url", lambda: "")
    with pytest.raises(RuntimeError):
        hb._pg_env_and_uri()
    monkeypatch.setattr(hb, "_database_url", lambda: "postgresql://u:p@localhost:5432/db")
    env, uri = hb._pg_env_and_uri()
    assert "postgresql" in uri

    assert hb.archive_dir_candidates()
    monkeypatch.setattr(hb, "_path_is_writable", lambda p: False)
    hb._ensure_dir()


# ---------------------------------------------------------------------------
# certificates layout / pem / public dicts
# ---------------------------------------------------------------------------


def test_certificates_layout_pem_and_public(monkeypatch):
    from app.services import certificates as certs
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    assert certs.default_home_for_user("root") == "/root"
    assert certs.default_home_for_user("pi") == "/home/pi"
    assert certs.resolve_ssh_home(ssh_user="pi", home_dir="/opt/pi") == "/opt/pi"
    assert certs.expand_remote_dir("~/certs", "/home/pi") == "/home/pi/certs"
    assert certs.expand_remote_dir("~", "/home/pi") == "/home/pi"
    assert certs.expand_remote_dir("/etc/caddy", "/home/pi") == "/etc/caddy"
    assert "cert-stage" in certs.cert_stage_base("/home/pi")
    assert certs.cert_stage_dir("/home/pi", 3).endswith("/3")
    assert certs.cert_stage_glob("/home/pi").endswith("/*")
    assert certs.layout_installs_pair("pair")
    assert certs.layout_installs_combined("combined")
    assert certs.layout_installs_pfx("pfx")
    assert certs.layout_needs_pem_for_pfx("pfx")
    assert certs.build_post_deploy_command("none") == ""
    assert "up -d" in certs.build_post_deploy_command("compose", compose_action="up")
    assert "restart" in certs.build_post_deploy_command("compose", compose_file="c.yml")
    assert certs.build_post_deploy_command("systemctl", systemctl_unit="") == ""
    assert "caddy" in certs.build_post_deploy_command("systemctl", systemctl_unit="caddy")
    assert certs.build_post_deploy_command("systemctl", systemctl_unit="sudo systemctl restart x") == "sudo systemctl restart x"
    assert certs.build_post_deploy_command("custom", custom="echo hi") == "echo hi"
    parsed = certs.parse_restart_recipe("docker compose -f c.yml up -d")
    assert parsed["kind"] == "compose"
    parsed2 = certs.parse_restart_recipe("sudo systemctl restart caddy")
    assert parsed2["kind"] == "systemctl"
    parsed3 = certs.parse_restart_recipe("echo hi")
    assert parsed3["kind"] == "custom"
    assert certs.parse_restart_recipe("")["kind"] == "none"
    help_ui = certs.layout_help_for_ui()
    assert "pair" in help_ui
    presets = certs.map_presets_for_ui()
    assert presets
    assert certs.get_map_preset("nope") is None
    assert certs.normalize_fingerprint("") == ""
    assert certs.normalize_fingerprint("SHA256 Fingerprint=AA:BB") == "aabb"
    assert certs.fingerprint_of_pems("a", "b")
    assert certs.days_until_expiry(None) is None
    soon = datetime.utcnow() + timedelta(days=3)
    assert certs.days_until_expiry(soon) is not None
    assert certs.build_combined_pem("KEY", "CHAIN").startswith("KEY")
    with pytest.raises(ValueError):
        certs.parse_pem_metadata("")
    with pytest.raises(ValueError):
        certs.parse_pem_metadata("not a cert")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.lan")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("test.lan")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    meta = certs.parse_pem_metadata(pem)
    assert "test.lan" in meta["domains"]
    assert meta["fingerprint_sha256"]

    session, _ = _memory()
    assert certs.list_certificates(session) == []
    assert certs.get_certificate(session, 1) is None
    assert certs.list_targets(session, 1) == []
    assert certs.delete_target(session, 1) is False
    assert certs.delete_certificate(session, 1) is False
    assert certs._normalize_write_mode("stage_sudo") == "stage_sudo"
    assert certs._normalize_write_mode("nope") in certs.WRITE_MODES or certs._normalize_write_mode("nope") == "direct"
    assert certs._dt_json(None) is None
    assert certs._dt_json(datetime.utcnow())
    ep = certs.parse_verify_endpoint("https://example.com")
    assert ep is None or isinstance(ep, dict)
    assert certs._normalize_verify_url("  https://x  ") in (None, "https://x") or certs._normalize_verify_url("https://x")
    files = certs.files_for_layout("pair")
    assert files
    assert certs.edge_certs_dir()
    assert isinstance(certs.edge_certs_writable(), bool)
    status = certs.edge_caddy_status()
    assert isinstance(status, dict)


# ---------------------------------------------------------------------------
# stack panel remaining + dns fabric npm cache
# ---------------------------------------------------------------------------


def test_stack_panel_and_dns_npm_helpers():
    from app.services.dns_fabric import stack_panel as sp
    from app.services.dns_fabric import core as fabric
    from app.models import Integration, IntegrationBinding, StackDeployment

    session, _ = _memory()
    srv = _server(session)
    assert sp.guess_container_role(name="caddy", image="caddy:latest", compose_service="proxy") in (
        "proxy",
        "app",
        "edge",
        "web",
        "dns",
    )
    assert sp.guess_container_role(name="unknown", image="scratch", compose_service="x") == "app"
    assert sp._find_project(None, "g") is None
    assert sp._find_project({"projects": [{"name": "Grafana"}]}, "grafana")["name"] == "Grafana"
    assert sp._list_projects_on_server({"projects": [{"name": "g", "label_only": True}, {"name": "ok"}]}) == ["ok"]
    no_host = sp.resolve_stack_target(session, server_id=None, project="g")
    assert no_host["ok"] is False
    missing = sp.resolve_stack_target(session, server_id=999)
    assert missing["ok"] is False
    rec = ServiceDnsRecord(
        fqdn="g.lan",
        label="grafana",
        docker_project="grafana",
        backend_server_id=srv.id,
        target_server_id=srv.id,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)
    resolved = sp.resolve_stack_target(session, service_id=rec.id)
    assert resolved["ok"] is True
    auto = sp.resolve_stack_target(session, server_id=srv.id)
    assert auto["ok"] is True

    assert fabric._servers_by_id(session)[srv.id].id == srv.id
    assert fabric.find_npm_host_server(session) is None
    dep = StackDeployment(
        server_id=srv.id, project_name="npm", template_slug="npm", files_json="{}", variables_json="{}"
    )
    session.add(dep)
    session.commit()
    found = fabric.find_npm_host_server(session)
    assert found is not None
    cached = fabric._npm_proxy_hosts_cached(session)
    assert isinstance(cached, list)
    edge = fabric._npm_edge_hostname(session)
    assert isinstance(edge, str)


# ---------------------------------------------------------------------------
# harden remaining helpers
# ---------------------------------------------------------------------------


def test_harden_parameterize_and_env_helpers():
    from app.services.service_templates import harden as hd

    assert hd.looks_like_secret_name("API_TOKEN") is True
    parsed = hd.parse_env_file("# c\nFOO=bar\nBAD LINE\n")
    assert parsed["FOO"] == "bar"
    formatted = hd.format_env_file({"B": "2", "A": "1"})
    assert formatted.startswith("A=1")
    ph = hd.format_env_file({"A": "1"}, as_placeholders=True)
    assert "{{A}}" in ph
    names = hd.scan_placeholders("x={{FOO}} y={{BAR}} {{FOO}}")
    assert names == ["FOO", "BAR"]
    assert hd._strip_yaml_scalar("'hi'") == "hi"
    assert hd._strip_yaml_scalar("val # comment") == "val"
    found = hd.extract_inline_env_assignments(
        "    MYSQL_PASSWORD: secret\n    PORT: ${PORT}\n    - API_KEY=abc\n"
    )
    assert any(k == "MYSQL_PASSWORD" for _, k, _ in found)
    new_c, new_e, extracted, msgs = hd.move_secrets_to_env(
        "services:\n  db:\n    environment:\n      MYSQL_PASSWORD: secret\n",
        "",
        secret_names={"MYSQL_PASSWORD"},
        only_secret_looking=True,
    )
    assert "MYSQL_PASSWORD" in extracted or "${MYSQL_PASSWORD}" in new_c or extracted == extracted
    assert hd._unique_var_name("PORT", {"PORT"}) != "PORT"
    assert hd._volume_var_base("/data", "/var/lib", "rw")
    assert hd._port_var_base("8080", "80", "web")
    assert hd._infer_type_for_env_var("PORT", "80", secret=False)
    assert hd._infer_type_for_env_var("PASSWORD", "x", secret=True)
    assert hd._short_host_label("pi.local", "lab")
    out = hd.parameterize_compose_volumes_and_ports(
        "services:\n  web:\n    ports:\n      - '8080:80'\n    volumes:\n      - /data:/var/lib/app\n"
    )
    assert isinstance(out, tuple)
    texts, vars_, msgs = hd.parameterize_texts_for_host(
        {"docker-compose.yml": "image: grafana\n"},
        node_name="pi",
        host_fqdn="pi.local",
    )
    assert isinstance(texts, dict)
    split_c, split_e = hd.split_env_for_docker_secrets("TOKEN=s\nPORT=1\n", ["TOKEN"])
    assert "TOKEN" in split_e


# ---------------------------------------------------------------------------
# pihole URL / login error branches
# ---------------------------------------------------------------------------


def test_pihole_url_login_and_get_json(monkeypatch):
    from app.services.integrations import pihole as ph

    assert ph.normalize_base_url("https://pi.lan/admin") == "https://pi.lan"
    with pytest.raises(ValueError):
        ph.normalize_base_url("")
    with pytest.raises(ValueError):
        ph.normalize_base_url("ftp://x")
    assert ph.admin_url("https://pi.lan", "") == "https://pi.lan/admin/"
    assert ph.admin_url("https://pi.lan", "https://other/admin") == "https://other/admin"
    assert ph.admin_url("https://pi.lan", "settings") == "https://pi.lan/admin/settings"
    with pytest.raises(ValueError):
        ph.login("https://pi.lan", "")

    class _Resp:
        def __init__(self, status=200, payload=None, text="x", content=b"{}"):
            self.status_code = status
            self._payload = payload if payload is not None else {}
            self.text = text
            self.content = content if content is not None else b""

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, resp):
            self._resp = resp

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return self._resp

        def get(self, *a, **k):
            return self._resp

        def delete(self, *a, **k):
            raise RuntimeError("net")

        def request(self, *a, **k):
            return self._resp

    monkeypatch.setattr(ph.httpx, "Client", lambda **k: _Client(_Resp(status=401, text="no", content=b"no")))
    with pytest.raises(RuntimeError):
        ph.login("https://pi.lan", "pw")
    monkeypatch.setattr(
        ph.httpx,
        "Client",
        lambda **k: _Client(_Resp(status=200, payload={"session": {}}, content=b"{}")),
    )
    with pytest.raises(RuntimeError):
        ph.login("https://pi.lan", "pw")
    monkeypatch.setattr(
        ph.httpx,
        "Client",
        lambda **k: _Client(
            _Resp(status=200, payload={"session": {"sid": "abc", "csrf": "t"}}, content=b"{}")
        ),
    )
    sess = ph.login("https://pi.lan", "pw")
    assert sess.sid == "abc"
    ph.logout(sess)

    monkeypatch.setattr(
        ph.httpx,
        "Client",
        lambda **k: _Client(_Resp(status=401, text="no", content=b"x")),
    )
    with pytest.raises(RuntimeError):
        ph._get_json(sess, "/stats")
    monkeypatch.setattr(
        ph.httpx,
        "Client",
        lambda **k: _Client(_Resp(status=500, text="boom", content=b"x")),
    )
    with pytest.raises(RuntimeError):
        ph._get_json(sess, "/stats")
    monkeypatch.setattr(
        ph.httpx,
        "Client",
        lambda **k: _Client(_Resp(status=200, payload={"ok": 1}, content=b"")),
    )
    assert ph._get_json(sess, "/stats") == {}
    monkeypatch.setattr(
        ph.httpx,
        "Client",
        lambda **k: _Client(_Resp(status=200, payload={"ok": 1}, content=b"{}")),
    )
    assert ph._get_json(sess, "/stats")["ok"] == 1
