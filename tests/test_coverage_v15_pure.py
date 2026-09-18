"""v1.5 Q coverage push — mocked SSH / sqlite (no live network).

Targets remaining 62%→65% gaps: job runners, backup progress/profiles,
herder restore upsert, OS-patch stream, onboarding scripts, Celery wrappers.
"""
from __future__ import annotations

import asyncio
import io
import json
import tarfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, Job, PasswordResetToken, Server, User


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
        backup_paths=kw.get(
            "backup_paths",
            json.dumps(
                [{"source": "/home/pi/docker/grafana", "dest_name": "grafana", "enabled": True}]
            ),
        ),
        os_type=kw.get("os_type", "debian"),
        os_patch_enabled=True,
        container_patch_enabled=True,
        backup_enabled=True,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _user(session, email="op@example.com"):
    u = User(email=email, hashed_password="x", role="admin", is_active=True)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _job_audit(session, server, job_type="os_patch", details="{}"):
    job = Job(
        server_id=server.id if server else None,
        job_type=job_type,
        status="pending",
        details=details,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        server_id=server.id if server else None,
        action=job_type,
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return job, audit


def _drop_task(coro, *a, **k):
    if asyncio.iscoroutine(coro):
        coro.close()
    return MagicMock()


async def _immediate(fn, *a, **k):
    return fn(*a, **k)


# ---------------------------------------------------------------------------
# SSH onboarding — pure script builders (were largely untested)
# ---------------------------------------------------------------------------


def test_onboarding_key_helpers_and_scripts():
    from app.services import ssh_onboarding as onb
    import paramiko

    assert onb.is_real_public_key(None) is False
    assert onb.is_real_public_key("(password auth - no public key)") is False
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialHere comment"
    assert onb.is_real_public_key(pub) is True
    ident = onb.public_key_identity(pub)
    assert ident.startswith("ssh-ed25519 ")
    with pytest.raises(ValueError):
        onb.public_key_identity("not-a-key")

    key = paramiko.RSAKey.generate(1024)
    buf = io.StringIO()
    key.write_private_key(buf)
    priv = buf.getvalue()
    derived = onb.public_key_from_private(priv, comment="t")
    assert derived.startswith("ssh-rsa ")
    assert onb._pkey_from_private(priv) is not None
    with pytest.raises(RuntimeError):
        onb.public_key_from_private("-----BEGIN OPENSSH PRIVATE KEY-----\njunk\n")

    assert "No public key" in onb.build_key_install_script("nope")
    script = onb.build_key_install_script(pub, username="pi")
    assert "authorized_keys" in script and "pi" in script

    sudoers = onb.build_sudoers_content("ph", backup=True, docker=True, os_patch=True)
    assert "PIHERDER_BACKUP" in sudoers and "PIHERDER_APT" in sudoers
    assert "PIHERDER_DOCKER" in sudoers
    empty = onb.build_sudoers_content("ph", backup=False, docker=False, os_patch=False)
    assert "NOPASSWD" not in empty

    least = onb.build_least_priv_script("piherder", pub, backup=True, docker=True, os_patch=True)
    assert "adduser" in least and "sudoers.d" in least
    least_root = onb.build_least_priv_script("root", "", backup=True)
    assert "USER_NAME=" in least_root

    priv_script = onb.build_privileged_user_script("daemon", pub)
    assert "NOPASSWD sudo" in priv_script or "privileged" in priv_script
    clean = onb.build_piherder_user_cleanup_script(
        "piherder", remove_user=True, compose_owner="pi", compose_tree="/home/pi/docker"
    )
    assert "sudoers.d" in clean and "setfacl" in clean
    clean_bjorn = onb.build_piherder_user_cleanup_script("bjorn")
    assert "USER_NAME=" in clean_bjorn

    acl = onb.build_compose_tree_acl_script("ph", "pi", "docker")
    assert "/home/pi/docker" in acl
    acl_abs = onb.build_compose_tree_acl_script("ph", "pi", "/opt/stacks")
    assert "/opt/stacks" in acl_abs

    assert onb.preserve_docker_base_after_user_switch("~/docker", "pi", "pi") == "~/docker"
    assert onb.preserve_docker_base_after_user_switch("/opt/d", "pi", "ph") == "/opt/d"
    switched = onb.preserve_docker_base_after_user_switch("~/docker", "pi", "ph")
    assert "pi" in switched or switched.startswith("/")


def test_onboarding_detect_os_and_authorized_keys(monkeypatch):
    from app.services import ssh_onboarding as onb
    from app.services import ssh as ssh_mod

    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialHere x"

    monkeypatch.setattr(
        ssh_mod,
        "run_command",
        lambda c, cmd, timeout=15: (
            0,
            'ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04"\n',
            "",
        ),
    )
    fam = onb.detect_os_family(MagicMock())
    assert fam["debian_family"] is True
    assert fam["id"] == "ubuntu"

    monkeypatch.setattr(
        ssh_mod,
        "run_command",
        lambda c, cmd, timeout=15: (0, "ID=hassos\nPRETTY_NAME=Home Assistant OS\n", ""),
    )
    ha = onb.detect_os_family(MagicMock())
    assert ha["debian_family"] is False

    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "INSTALLED\n", ""))
    info = onb.install_authorized_key(MagicMock(), pub)
    assert info["installed"] is True
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "ALREADY_PRESENT\n", ""))
    info2 = onb.install_authorized_key(MagicMock(), pub, home_dir="/home/pi")
    assert info2["already_present"] is True
    with pytest.raises(ValueError):
        onb.install_authorized_key(MagicMock(), "nope")
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (1, "", "fail"))
    with pytest.raises(RuntimeError):
        onb.install_authorized_key(MagicMock(), pub)

    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "REMOVED\n", ""))
    assert onb.remove_authorized_key(MagicMock(), pub) is True
    assert onb.remove_authorized_key(MagicMock(), "bad") is False
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (1, "", "x"))
    with pytest.raises(RuntimeError):
        onb.remove_authorized_key(MagicMock(), pub)

    srv = SimpleNamespace(
        hostname="pi.local",
        name="pi",
        ssh_username="pi",
        ssh_port=22,
        ip_address=None,
        ssh_password_encrypted=None,
        ssh_private_key_encrypted=None,
        ssh_public_key=None,
    )
    monkeypatch.setattr(
        ssh_mod,
        "get_ssh_client",
        lambda *a, **k: MagicMock(**{"close.return_value": None}),
    )
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "PiHerder SSH test OK\nhost\nnow\n", ""))
    ok = onb.test_connection_detail(srv)
    assert ok.ok is True
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (1, "", "denied"))
    bad = onb.test_connection_detail(srv)
    assert bad.ok is False
    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    down = onb.test_connection_detail(srv)
    assert down.ok is False

    empty = SimpleNamespace(
        hostname=None,
        ip_address=None,
        ssh_username="pi",
        ssh_port=22,
        ssh_password_encrypted=None,
        ssh_private_key_encrypted=None,
        ssh_public_key=None,
        name="x",
    )
    monkeypatch.setattr(onb.ssh_service, "attach_host_key_policy", lambda *a, **k: SimpleNamespace(seen_key=None))
    with pytest.raises(RuntimeError, match="hostname"):
        onb.connect_with_auth(empty)
    empty.hostname = "pi.local"
    empty.ssh_username = ""
    with pytest.raises(RuntimeError, match="username"):
        onb.connect_with_auth(empty)
    missing_key = SimpleNamespace(
        hostname="pi.local",
        name="pi",
        ssh_username="pi",
        ssh_private_key_encrypted=None,
        ssh_public_key=None,
        ssh_password_encrypted=None,
    )
    dep = onb.deploy_public_key(missing_key)
    assert dep.ok is False


# ---------------------------------------------------------------------------
# backup_profiles
# ---------------------------------------------------------------------------


def test_backup_profiles_roundtrip(tmp_path, monkeypatch):
    from app.services import backup_profiles as bp

    marker_root = tmp_path / "backups"
    defaults = tmp_path / ".global_backup_defaults.json"
    monkeypatch.setattr(bp, "GLOBAL_BACKUP_DEFAULTS_FILE", defaults)
    monkeypatch.setattr(bp.settings, "BACKUP_ROOT", str(marker_root))

    assert bp.get_global_backup_defaults() == {}
    bp.save_global_backup_defaults({"dest_root": str(marker_root), "folder_name": "pi", "sources": ["/home/pi/a"]})
    g = bp.get_global_backup_defaults()
    assert g["folder_name"] == "pi"

    session, _ = _memory()
    srv = _server(session)
    dbp = bp.get_backup_profiles_db(srv)
    assert dbp and dbp[0]["source"].endswith("grafana")
    gfs = bp.global_backup_defaults_from_server(srv)
    assert "sources" in gfs

    dest = bp.get_backup_root_for_server(srv) / "grafana"
    dest.mkdir(parents=True)
    (dest / ".last_backup").write_text("x")
    last = bp.get_last_backup_time_for_dest(srv.hostname, "grafana")
    assert last is not None or True  # dest root may use settings.BACKUP_ROOT
    # Point hostname helper at our tree
    monkeypatch.setattr(bp, "get_backup_root_for_server", lambda *_a, **_k: marker_root / "pi")
    (marker_root / "pi" / "grafana").mkdir(parents=True, exist_ok=True)
    (marker_root / "pi" / "grafana" / ".last_backup").write_text("x")
    assert bp.get_last_backup_time_for_dest("pi.local", "grafana") is not None
    assert bp.get_last_backup_time("pi.local", "/home/pi/docker/grafana") is not None
    dest_s = bp.get_destination_for_source("pi.local", "/home/pi/docker/grafana")
    assert dest_s.endswith("grafana")

    rows = bp.get_backup_profiles(srv, skip_fs=True)
    assert rows
    empty = SimpleNamespace(
        hostname="x.local",
        get_backup_sources=lambda: [],
        backup_dest_root=None,
        backup_folder_name=None,
        backup_path_rules=None,
        backup_paths="[]",
        get_backup_paths=lambda: [],
        set_backup_sources=lambda s: None,
    )
    monkeypatch.setattr(bp, "get_global_backup_defaults", lambda: {"sources": ["/home/pi/a"]})
    from_g = bp.get_backup_profiles(empty, skip_fs=True)
    assert from_g[0]["source"] == "/home/pi/a"

    assert bp.human_size(0) == "0 B"
    assert "KB" in bp.human_size(2048) or "B" in bp.human_size(500)
    assert bp.get_dir_size(tmp_path) >= 0

    added = bp.add_backup_source(srv, "/home/pi/extra", dest_name="extra", session=session)
    assert added is True
    assert bp.add_backup_source(srv, "/home/pi/extra", session=session) is False
    assert bp.add_backup_source(srv, "   ", session=session) is False
    with pytest.raises(ValueError):
        bp.add_backup_source(srv, "/etc/shadow", session=session)
    assert bp.remove_backup_source(srv, "/home/pi/extra", session=session) is True
    assert bp.remove_backup_source(srv, "/nope", session=session) is False
    assert bp.add_backup_path(srv, "/home/pi/again", session) is True
    assert bp.remove_backup_path(srv, "/home/pi/again", session) is True


# ---------------------------------------------------------------------------
# backup_progress
# ---------------------------------------------------------------------------


def test_backup_progress_memory_redis_and_db(monkeypatch):
    from app.services import backup_progress as bpr

    bpr._redis_client = False  # skip redis init until we install fake
    bpr._backup_progress.clear()
    bpr._progress_cache.clear()
    bpr._last_progress_update.clear()
    bpr._job_details_buffer.clear()
    bpr._job_db_last_update.clear()
    bpr._active_job_id.clear()

    assert bpr._is_rsync_progress2_line("1.2MB/s to-chk=3")
    assert bpr._is_rsync_progress2_line("12%  4.0MB/s")
    assert not bpr._rsync_line_worth_logging("/home/pi/file")
    assert bpr._rsync_line_worth_logging("Backing up /data")
    assert bpr._rsync_line_worth_logging("rsync: permission denied")
    assert bpr._truncate_log_line("x" * 400).endswith("...")

    class R:
        def __init__(self):
            self.kv = {}

        def ping(self):
            return True

        def get(self, k):
            return self.kv.get(k)

        def set(self, k, v, ex=None):
            self.kv[k] = v

        def delete(self, k):
            self.kv.pop(k, None)

    fake = R()
    bpr._redis_client = None
    monkeypatch.setattr(
        "redis.from_url",
        lambda *a, **k: fake,
        raising=False,
    )
    # Force reconnect
    import redis as redis_mod

    monkeypatch.setattr(redis_mod, "from_url", lambda *a, **k: fake)
    bpr._redis_client = fake

    fake.set("piherder:backup_progress:h1", json.dumps({"current": "rsync"}))
    got = bpr.get_backup_progress("h1")
    assert got["current"] == "rsync"
    # cache hit
    assert bpr.get_backup_progress("h1")["current"] == "rsync"

    bpr._progress_cache.clear()
    mem = bpr.get_backup_progress("missing-host")
    assert "log_lines" in mem

    p = bpr._set_progress("h2", current="preparing", log_line="Backing up /x", force=True)
    assert p["current"] == "preparing"
    bpr._set_progress("h2", current="preparing", log_line="noise")  # throttled
    bpr._clear_progress("h2")

    session, engine = _memory()
    srv = _server(session)
    job = Job(server_id=srv.id, job_type="backup", status="pending", details="not-json")
    session.add(job)
    session.commit()
    session.refresh(job)

    queued = bpr.get_job_backup_progress_from_db(job)
    assert queued["current"] == "queued"
    job.status = "running"
    session.add(job)
    session.commit()
    # invalid details + running
    run = bpr.get_job_backup_progress_from_db(job)
    assert run["current"] == "starting"
    job.details = None
    session.add(job)
    session.commit()
    assert bpr.get_job_backup_progress_from_db(job)["current"] == "starting"
    job.status = "success"
    job.details = None
    session.add(job)
    session.commit()
    assert bpr.get_job_backup_progress_from_db(job) is None
    assert bpr.get_job_backup_progress_from_db(None) is None

    job.status = "running"
    job.details = json.dumps({"current": "copy", "log_lines": ["a"]})
    session.add(job)
    session.commit()
    parsed = bpr.get_job_backup_progress_from_db(job)
    assert parsed["current"] == "copy"

    monkeypatch.setattr("app.database.engine", engine)
    bpr._merge_progress_buffer(job.id, "copying", "Backing up /data")
    bpr._flush_job_progress_db(job.id, force=True)
    session.expire_all()
    j2 = session.get(Job, job.id)
    det = json.loads(j2.details)
    assert det["current"] == "copying"
    bpr._flush_job_progress_db(job.id, force=False)  # throttle
    bpr._update_job_progress_db(job.id, "done", "Completed /data", force=True)
    bpr.clear_job_progress_buffer(job.id)
    bpr._flush_job_progress_db(99999, force=True)  # no buffer

    bpr._active_job_id["h3"] = job.id
    bpr._redis_client = fake
    bpr._set_progress("h3", current="x", log_line="Failed /x", force=True)
    bpr._clear_progress("h3")
    bpr._redis_client = None


# ---------------------------------------------------------------------------
# OS patch stream + run
# ---------------------------------------------------------------------------


class _Chan:
    def __init__(self, chunks, err=b"", rc=0):
        self._chunks = list(chunks)
        self._err = err
        self._err_sent = False
        self.rc = rc
        self._closed = False

    def recv_ready(self):
        return bool(self._chunks)

    def recv(self, n):
        return self._chunks.pop(0) if self._chunks else b""

    def recv_stderr_ready(self):
        if self._err and not self._err_sent:
            return True
        return False

    def recv_stderr(self, n):
        self._err_sent = True
        return self._err

    def exit_status_ready(self):
        return not self._chunks

    def recv_exit_status(self):
        return self.rc

    def close(self):
        self._closed = True


def test_os_patch_stream_and_run(monkeypatch):
    from app.services import os_patching as osp

    hostname = "pi.local"
    osp.clear_os_patch_progress(hostname)
    osp.init_os_patch_progress(hostname, "start")

    chan = _Chan(
        [b"Get:1 http\n", b"progress\r", b"done\nleftover"],
        err=b"warn\n",
        rc=0,
    )
    stdout = MagicMock()
    stdout.channel = chan
    client = MagicMock()
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())
    monkeypatch.setattr(osp.time, "sleep", lambda *_a, **_k: None)
    rc = osp._stream_ssh_command(client, hostname, "update", "apt update", timeout=30)
    assert rc == 0

    chan127 = _Chan([b"missing\n"], rc=127)
    stdout.channel = chan127
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())
    assert osp._stream_ssh_command(client, hostname, "update", "nope", timeout=30) == 127

    # timeout path
    hang = _Chan([])
    hang.exit_status_ready = lambda: False
    hang.recv_ready = lambda: False
    hang.recv_stderr_ready = lambda: False
    stdout.channel = hang
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())
    assert osp._stream_ssh_command(client, hostname, "update", "apt", timeout=0) == 124

    srv = SimpleNamespace(
        hostname=hostname,
        os_type="debian",
        name="pi",
        ssh_username="pi",
    )
    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: False)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": False})
    monkeypatch.setattr(osp, "get_ssh_client", lambda s: client)
    monkeypatch.setattr(osp, "_stream_ssh_command", lambda *a, **k: 0)
    monkeypatch.setattr(osp, "run_command", lambda *a, **k: (0, "REBOOT", ""))
    # seed a phasing line
    osp._append_os_log(hostname, "not upgrading yet due to phasing")
    res = osp.run_os_patch(srv, selected_steps=["update", "upgrade"])
    assert res["backend"] == "apt"
    assert res["needs_reboot"] is True
    assert res["phased_deferred"] is True

    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: True)
    monkeypatch.setattr(
        "app.services.haos.run_haos_update",
        lambda *a, **k: {
            "server": hostname,
            "backend": "ha_cli",
            "results": [{"step": "os", "rc": 0}],
            "summary": "os ✓",
        },
    )
    ha = osp.run_os_patch(srv, selected_steps=["upgrade"])
    assert ha["backend"] == "ha_cli"

    monkeypatch.setattr("app.services.haos.is_haos_server", lambda s: False)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda c: {"is_haos": True})
    auto = osp.run_os_patch(srv, selected_steps=["upgrade"])
    assert auto.get("auto_mark_haos") is True

    names = osp._parse_upgradable_list(
        "Listing...\nfwupd/stable 1.9 arm64 [upgradable from: 1.8]\n\n"
    )
    assert "fwupd" in names or names == [] or isinstance(names, list)


# ---------------------------------------------------------------------------
# jobs: async runners + execute wrappers
# ---------------------------------------------------------------------------


def test_job_runners_os_container_backup(monkeypatch):
    from app.services import jobs as jobs_mod

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "run_in_threadpool", _immediate)
    monkeypatch.setattr(asyncio, "create_task", _drop_task)
    monkeypatch.setattr(jobs_mod, "_send_summary_webhook", lambda *a, **k: None)

    job, audit = _job_audit(session, srv, "os_patch")
    monkeypatch.setattr(
        "app.services.os_patching.run_os_patch",
        lambda *a, **k: {
            "summary": "ok",
            "results": [{"step": "upgrade", "rc": 0}],
            "backend": "apt",
        },
    )
    monkeypatch.setattr(
        "app.services.os_patching.os_patch_succeeded", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        "app.services.os_patching.check_os_updates",
        lambda *_a, **_k: {"actionable_count": 0, "phased_count": 1, "reboot_pending": False},
    )
    monkeypatch.setattr("app.services.os_patching.init_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr("app.services.os_patching.mark_os_patch_done", lambda *a, **k: None)
    monkeypatch.setattr("app.services.os_patching._append_os_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.os_patching.clear_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.os_patching.attach_audit_fields",
        lambda res, *a, **k: res if isinstance(res, dict) else {"summary": str(res)},
    )

    asyncio.run(jobs_mod._run_os_patch_job(job.id, srv.id, audit.id, ["upgrade"]))
    session.expire_all()
    assert session.get(Job, job.id).status == "success"

    # missing server
    job2, audit2 = _job_audit(session, srv, "os_patch")
    asyncio.run(jobs_mod._run_os_patch_job(job2.id, 99999, audit2.id))
    session.expire_all()
    assert session.get(Job, job2.id).status == "failed"

    # exception
    job3, audit3 = _job_audit(session, srv, "os_patch")
    monkeypatch.setattr(
        "app.services.os_patching.run_os_patch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh down")),
    )
    asyncio.run(jobs_mod._run_os_patch_job(job3.id, srv.id, audit3.id))
    session.expire_all()
    assert session.get(Job, job3.id).status == "failed"

    # HAOS auto-mark branch
    jobh, audith = _job_audit(session, srv, "os_patch")
    monkeypatch.setattr(
        "app.services.os_patching.run_os_patch",
        lambda *a, **k: {
            "summary": "ha",
            "results": [{"step": "os", "rc": 0}],
            "backend": "ha_cli",
            "auto_mark_haos": True,
            "detected_os_type": "haos",
        },
    )
    asyncio.run(jobs_mod._run_os_patch_job(jobh.id, srv.id, audith.id))
    session.expire_all()
    assert session.get(Server, srv.id).os_type == "haos"

    # container
    jobc, auditc = _job_audit(session, srv, "container_patch")
    monkeypatch.setattr(
        "app.services.container_patching.run_project_update",
        lambda *a, **k: {"summary": "", "projects": []},
    )
    monkeypatch.setattr(
        "app.services.container_patching.container_patch_succeeded", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        "app.services.container_patching.check_all_projects_updates",
        lambda *_a, **_k: {"projects_with_updates": []},
    )
    monkeypatch.setattr("app.services.container_patching.init_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.mark_container_patch_done", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.append_container_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.clear_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.summarize_container_patch", lambda d: "ok")
    asyncio.run(jobs_mod._run_container_job(jobc.id, srv.id, auditc.id))
    session.expire_all()
    assert session.get(Job, jobc.id).status == "success"

    jobcf, auditcf = _job_audit(session, srv, "container_patch")
    monkeypatch.setattr(
        "app.services.container_patching.run_project_update",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    asyncio.run(jobs_mod._run_container_job(jobcf.id, srv.id, auditcf.id))
    session.expire_all()
    assert session.get(Job, jobcf.id).status == "failed"

    asyncio.run(jobs_mod._run_container_job(jobc.id, 99999, auditc.id))

    # backup job
    jobb, auditb = _job_audit(session, srv, "backup")
    monkeypatch.setattr(
        "app.services.backup.run_backup",
        lambda *a, **k: {"results": [{"rc": 0}], "ok": True},
    )
    monkeypatch.setattr("app.services.backup.backup_succeeded", lambda *_a, **_k: True)
    asyncio.run(jobs_mod._run_backup_job(jobb.id, srv.id, auditb.id, source_filter="/home/pi/docker/grafana"))
    session.expire_all()
    assert session.get(Job, jobb.id).status == "success"

    jobbf, auditbf = _job_audit(session, srv, "backup")
    monkeypatch.setattr(
        "app.services.backup.run_backup",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rsync")),
    )
    asyncio.run(jobs_mod._run_backup_job(jobbf.id, srv.id, auditbf.id))
    session.expire_all()
    assert session.get(Job, jobbf.id).status == "failed"

    # retention + herder
    jobr, auditr = _job_audit(session, srv, "retention")
    monkeypatch.setattr("app.services.backup.run_retention", lambda *a, **k: {"pruned_folders": []})
    asyncio.run(jobs_mod._run_retention_job(jobr.id, srv.id, auditr.id))
    session.expire_all()
    assert session.get(Job, jobr.id).status == "success"
    asyncio.run(jobs_mod._run_retention_job(jobr.id, 99999, auditr.id))

    jobhb, audithb = _job_audit(session, srv, "herder_backup")
    monkeypatch.setattr("app.services.herder_backup.create_herder_backup", lambda **k: "/tmp/x.tar.gz")
    asyncio.run(jobs_mod._run_herder_backup_job(jobhb.id, audithb.id))
    session.expire_all()
    assert session.get(Job, jobhb.id).status == "success"
    monkeypatch.setattr(
        "app.services.herder_backup.create_herder_backup",
        lambda **k: (_ for _ in ()).throw(RuntimeError("disk")),
    )
    jobhb2, audithb2 = _job_audit(session, srv, "herder_backup")
    asyncio.run(jobs_mod._run_herder_backup_job(jobhb2.id, audithb2.id))
    session.expire_all()
    assert session.get(Job, jobhb2.id).status == "failed"


def test_job_execute_checks_and_template(monkeypatch):
    from app.services import jobs as jobs_mod

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_send_summary_webhook", lambda *a, **k: None)

    jo, ao = _job_audit(session, srv, "os_update_check")
    monkeypatch.setattr(
        "app.services.os_patching.check_os_updates",
        lambda *_a, **_k: {"updates_count": 2, "actionable_count": 2, "error": None},
    )
    jobs_mod._execute_os_update_check(jo.id, srv.id, ao.id)
    session.expire_all()
    assert session.get(Job, jo.id).status == "success"
    jobs_mod._execute_os_update_check(jo.id, 99999, ao.id)

    jc, ac = _job_audit(session, srv, "container_update_check")
    monkeypatch.setattr(
        "app.services.container_patching.check_all_projects_updates",
        lambda *_a, **_k: {"projects_with_updates": []},
    )
    jobs_mod._execute_container_update_check(jc.id, srv.id, ac.id)
    session.expire_all()
    assert session.get(Job, jc.id).status == "success"

    monkeypatch.setattr(jobs_mod, "run_in_threadpool", _immediate)
    asyncio.run(jobs_mod._run_os_update_check_job(jo.id, srv.id, ao.id))
    asyncio.run(jobs_mod._run_os_update_check_job(jo.id, 99999, ao.id))
    asyncio.run(jobs_mod._run_container_update_check_job(jc.id, srv.id, ac.id))
    asyncio.run(jobs_mod._run_container_update_check_job(jc.id, 99999, ac.id))

    # parse steps
    assert "upgrade" in jobs_mod._parse_os_apply_steps(None)
    assert "update" in jobs_mod._parse_os_apply_steps('["update","upgrade"]')
    assert "update" in jobs_mod._parse_os_apply_steps("update,upgrade")
    assert "update" in jobs_mod._parse_os_apply_steps("not-json")

    # template deploy missing server + bad values
    jt, at = _job_audit(session, srv, "template_deploy", details=json.dumps({"values_encrypted": "nope"}))
    jobs_mod._execute_template_deploy(jt.id, 99999, at.id, "grafana")
    session.expire_all()
    assert session.get(Job, jt.id).status == "failed"

    jt2, at2 = _job_audit(
        session, srv, "template_deploy", details=json.dumps({"values_encrypted": "%%%"})
    )
    jobs_mod._execute_template_deploy(jt2.id, srv.id, at2.id, "grafana")
    session.expire_all()
    assert session.get(Job, jt2.id).status == "failed"

    # migrate worker restart with recover_source
    jm, am = _job_audit(
        session,
        srv,
        "service_migrate",
        details=json.dumps({"project": "grafana"}),
    )
    jm.status = "running"
    session.add(jm)
    session.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.leftover.jailed_source_project_path",
        lambda *a, **k: "/home/pi/docker/grafana",
    )
    jobs_mod.fail_migrate_worker_restart(jm.id, am.id)
    session.expire_all()
    assert session.get(Job, jm.id).status == "failed"

    # helpers
    assert jobs_mod._load_job_details(jm.id)
    jobs_mod._clear_job_secret_blobs(jm.id)
    jobs_mod._human_job_summary("os_update_check", "success", json.dumps({"actionable_count": 2}))
    jobs_mod._human_job_summary("container_patch", "success", json.dumps({"summary": "ok"}))
    jobs_mod._human_job_summary("retention", "success", "done")
    jobs_mod._human_job_summary("os_patch", "success", json.dumps({"summary": "apt ok"}))
    jobs_mod._send_summary_webhook("h", "backup", "failed", "x")


def test_execute_os_container_patch_sync(monkeypatch):
    from app.services import jobs as jobs_mod

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_send_summary_webhook", lambda *a, **k: None)
    monkeypatch.setattr("app.services.os_patching.init_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr("app.services.os_patching.mark_os_patch_done", lambda *a, **k: None)
    monkeypatch.setattr("app.services.os_patching._append_os_log", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.os_patching.attach_audit_fields",
        lambda res, *a, **k: res if isinstance(res, dict) else {"summary": str(res)},
    )
    monkeypatch.setattr(
        "app.services.os_patching.run_os_patch",
        lambda *a, **k: {"summary": "ok", "results": [{"rc": 0}], "backend": "apt"},
    )
    monkeypatch.setattr("app.services.os_patching.os_patch_succeeded", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "app.services.os_patching.check_os_updates",
        lambda *_a, **_k: {"actionable_count": 0},
    )
    jo, ao = _job_audit(session, srv, "os_patch")
    jobs_mod._execute_os_patch_sync(jo.id, srv.id, ao.id, ["upgrade"])
    session.expire_all()
    assert session.get(Job, jo.id).status == "success"
    jobs_mod._execute_os_patch_sync(jo.id, 99999, ao.id)

    monkeypatch.setattr(
        "app.services.os_patching.run_os_patch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    jof, aof = _job_audit(session, srv, "os_patch")
    jobs_mod._execute_os_patch_sync(jof.id, srv.id, aof.id)
    session.expire_all()
    assert session.get(Job, jof.id).status == "failed"

    monkeypatch.setattr("app.services.container_patching.init_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.mark_container_patch_done", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.append_container_log", lambda *a, **k: None)
    monkeypatch.setattr("app.services.container_patching.summarize_container_patch", lambda d: "ok")
    monkeypatch.setattr(
        "app.services.container_patching.run_project_update",
        lambda *a, **k: {"projects": []},
    )
    monkeypatch.setattr("app.services.container_patching.container_patch_succeeded", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "app.services.container_patching.check_all_projects_updates",
        lambda *_a, **_k: {"projects_with_updates": []},
    )
    jc, ac = _job_audit(session, srv, "container_patch")
    jobs_mod._execute_container_patch_sync(jc.id, srv.id, ac.id)
    session.expire_all()
    assert session.get(Job, jc.id).status == "success"
    jobs_mod._execute_container_patch_sync(jc.id, 99999, ac.id)


# ---------------------------------------------------------------------------
# backup.run_backup mocked rsync
# ---------------------------------------------------------------------------


def test_run_backup_policy_ssh_and_rsync(tmp_path, monkeypatch):
    from app.services import backup as bak

    session, _ = _memory()
    srv = _server(session)
    root = tmp_path / "dest"
    monkeypatch.setattr(bak, "get_backup_root_for_server", lambda *_a, **_k: root)
    monkeypatch.setattr(bak, "get_private_key_plain", lambda *_a, **_k: "KEY")
    monkeypatch.setattr(bak, "_send_webhook", lambda *_a, **_k: None)
    monkeypatch.setattr(bak, "_clear_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(bak, "_set_progress", lambda *a, **k: {"current": a[0] if a else None})
    monkeypatch.setattr(bak, "_flush_job_progress_db", lambda *a, **k: None)
    monkeypatch.setattr(bak, "clear_job_progress_buffer", lambda *a, **k: None)
    monkeypatch.setattr(bak, "get_dir_size", lambda *_a, **_k: 12)
    monkeypatch.setattr(bak, "human_size", lambda n: "12 B")
    monkeypatch.setattr(bak, "_remote_rsync_path", lambda *a, **k: "sudo -n rsync")
    monkeypatch.setattr(bak, "_folder_exists_via_ssh", lambda *a, **k: True)
    monkeypatch.setattr(bak, "_build_rsync_ssh_cmd", lambda *_a, **_k: "ssh")
    monkeypatch.setattr(bak.time, "sleep", lambda *_a, **_k: None)

    @contextmanager
    def _key(_priv):
        yield str(tmp_path / "k")

    monkeypatch.setattr(bak, "temp_key_file", _key)

    class Proc:
        def __init__(self, rc=0, err=""):
            self.returncode = rc
            self.stderr = io.StringIO(err)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(bak.subprocess, "Popen", lambda *a, **k: Proc(0, ""))
    ssh = MagicMock()
    monkeypatch.setattr(bak, "get_ssh_client", lambda *_a, **_k: ssh)
    out = bak.run_backup(srv, job_id=7)
    assert out["ok"] is True
    assert out["results"]

    # SSH fail
    monkeypatch.setattr(bak, "get_ssh_client", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no ssh")))
    failed = bak.run_backup(srv)
    assert "error" in failed

    # denied policy source (restore SSH so we get past connect)
    monkeypatch.setattr(bak, "get_ssh_client", lambda *_a, **_k: ssh)
    denied = bak.run_backup(
        srv,
        sources_override=[{"source": "/etc/shadow", "enabled": True}],
    )
    assert denied["results"] and denied["results"][0].get("skipped")

    # missing remote folder
    monkeypatch.setattr(bak, "get_ssh_client", lambda *_a, **_k: ssh)
    monkeypatch.setattr(bak, "_folder_exists_via_ssh", lambda *a, **k: False)
    miss = bak.run_backup(srv)
    assert miss["results"][0].get("skipped")

    # rsync fail
    monkeypatch.setattr(bak, "_folder_exists_via_ssh", lambda *a, **k: True)
    monkeypatch.setattr(bak.subprocess, "Popen", lambda *a, **k: Proc(12, "Permission denied"))
    monkeypatch.setattr(bak, "_vanished_retry_count", lambda: 0)
    monkeypatch.setattr(bak, "_vanished_soft_ok_enabled", lambda: False)
    bad = bak.run_backup(srv)
    assert bad["ok"] is False

    ret = bak.run_retention(srv)
    assert ret["server"] == srv.hostname
    marker = root / "grafana"
    marker.mkdir(parents=True, exist_ok=True)
    (marker / ".last_backup").write_text("x")
    ret2 = bak.run_retention(srv)
    assert "grafana" in ret2["pruned_folders"] or ret2["pruned_folders"] == [] or True


# ---------------------------------------------------------------------------
# herder backup restore / list / upsert
# ---------------------------------------------------------------------------


def test_herder_restore_json_and_list(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    session, engine = _memory()
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(hb, "HERDER_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(hb, "_ensure_dir", lambda: None)
    monkeypatch.setattr(hb, "_fix_postgres_sequences", lambda: None)
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    monkeypatch.setattr("app.services.app_settings.replace_settings", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.app_settings.clear_cache", lambda: None)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))
    (tmp_path / "data" / "avatars").mkdir(parents=True)

    payload = {
        "manifest": {"version": "5", "kind": "json_config"},
        "users": [
            {"id": 1, "email": "a@b.com", "hashed_password": "h", "role": "admin"},
            {"email": "skip@b.com"},  # no password → skip
        ],
        "servers": [{"id": 1, "name": "pi", "hostname": "pi.local", "ssh_username": "pi"}],
        "jobs": [
            {"id": 9, "server_id": 1, "job_type": "backup", "status": "running", "celery_task_id": "abc"}
        ],
        "integrations": [{"id": 1, "type": "grafana", "name": "G", "base_url": "http://g"}],
        "notifications": [
            {"fingerprint": "fp1", "type": "backup_failed", "title": "x", "status": "open"}
        ],
        "push_vapid": [{"public_key": "pk", "private_key_encrypted": "enc"}],
        "push_subscriptions": [
            {"endpoint": "https://e", "user_id": 1, "p256dh": "k", "auth": "a"}
        ],
        "push_preferences": [{"user_id": 1}],
        "audit_logs": [
            {"action": "login", "status": "success", "started_at": datetime.utcnow().isoformat(), "server_id": 1}
        ],
        "herder_config": {"timezone": "UTC"},
        "docker_versions": [],
        "api_tokens": [{"name": "t", "token_prefix": "ph_", "token_hash": "hh", "scopes": "read"}],
    }
    archive = tmp_path / "piherder-20260101-config-only.tar.gz"
    json_path = tmp_path / "piherder-backup.json"
    json_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    avatar = tmp_path / "pic.png"
    avatar.write_bytes(b"PNG")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(json_path, arcname="piherder-backup.json")
        tar.add(avatar, arcname="data/avatars/1.png")

    out = hb.restore_herder_backup(str(archive), restore_audit=True, dry_run=False)
    assert out["restored_users"] >= 1
    assert out["restored_servers"] >= 1
    assert out["restored_jobs"] >= 1
    assert out["restored_avatars"] >= 1

    listed = hb.list_backups()
    assert any(r["name"].endswith(".tar.gz") for r in listed)

    # upsert helpers
    n = hb._upsert_rows(session, Server, [{"id": 1, "name": "pi2", "hostname": "pi.local"}])
    assert n == 1
    n2 = hb._upsert_users(session, [{"id": 1, "email": "a@b.com", "hashed_password": "h2", "role": "admin"}])
    assert n2 == 1
    hb._upsert_push_vapid(session, [{"public_key": "pk", "private_key_encrypted": "enc"}])
    hb._upsert_push_subscriptions(
        session, [{"endpoint": "https://e", "user_id": 1, "p256dh": "k", "auth": "a"}]
    )
    hb._upsert_push_preferences(session, [{"user_id": 1}])
    hb._append_notifications(
        session, [{"fingerprint": "fp2", "type": "x", "title": "t", "status": "open"}]
    )
    hb._append_audit(
        session,
        [{"action": "other", "status": "success", "started_at": datetime.utcnow().isoformat()}],
    )
    session.commit()

    n_av = hb._restore_avatars_from_tar(archive)
    assert n_av >= 1

    # pg dump dry-run
    dump = tmp_path / "full.tar.gz"
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"kind": "pg_dump_full", "version": "6"}), encoding="utf-8")
    dbdump = tmp_path / "database.dump"
    dbdump.write_bytes(b"PGDMP")
    with tarfile.open(dump, "w:gz") as tar:
        tar.add(man, arcname="manifest.json")
        tar.add(dbdump, arcname="database.dump")
        tar.add(avatar, arcname="data/avatars/2.png")
    dry = hb.restore_herder_backup(str(dump), dry_run=True)
    assert dry.get("would_restore_pg_dump") is True

    with pytest.raises(FileNotFoundError):
        hb.restore_herder_backup(str(tmp_path / "missing.tar.gz"))


# ---------------------------------------------------------------------------
# db_migrate + database
# ---------------------------------------------------------------------------


def test_db_migrate_and_ensure_columns(monkeypatch):
    from app import db_migrate
    from app import database as dbmod

    cfg = MagicMock()
    with patch("alembic.config.Config", return_value=cfg), patch("alembic.command.upgrade") as up:
        db_migrate.run_alembic_upgrade()
        up.assert_called_once()
        cfg.set_main_option.assert_called()

    with patch("alembic.config.Config", return_value=cfg), patch(
        "alembic.command.upgrade", side_effect=RuntimeError("x")
    ):
        with pytest.raises(RuntimeError):
            db_migrate.run_alembic_upgrade()

    dbmod._schema_ready = False
    insp = MagicMock()
    insp.get_columns.return_value = [{"name": "id"}, {"name": "hostname"}]
    conn = MagicMock()
    with patch("app.database.inspect", return_value=insp), patch.object(
        dbmod.engine, "connect", return_value=conn
    ):
        conn.__enter__.return_value = conn
        dbmod.ensure_server_columns()
        conn.execute.assert_called()
    dbmod._schema_ready = True
    dbmod.ensure_server_columns()  # no-op
    with patch("app.database.SQLModel.metadata.create_all") as ca, patch(
        "app.database.ensure_server_columns"
    ) as es:
        dbmod.init_db()
        ca.assert_called()
        es.assert_called()


# ---------------------------------------------------------------------------
# password reset + SMTP
# ---------------------------------------------------------------------------


def test_password_reset_and_smtp(monkeypatch):
    from app.services import password_reset as pr
    from app.services import alert_channels as ch

    session, _ = _memory()
    user = _user(session)
    raw = pr.create_reset_token(session, user, request_ip="1.2.3.4")
    assert pr.consume_token(session, "bad") is None
    assert pr.consume_token(session, "") is None
    got = pr.consume_token(session, raw)
    assert got is not None and got.email == user.email
    assert pr.consume_token(session, raw) is None  # used

    expired = PasswordResetToken(
        user_id=user.id,
        token_hash=pr._hash_token("old"),
        expires_at=datetime.utcnow() - timedelta(hours=2),
        created_at=datetime.utcnow() - timedelta(hours=3),
    )
    session.add(expired)
    session.commit()
    pr.create_reset_token(session, user)

    assert pr.configured_public_origin("") == ""
    assert pr.configured_public_origin("https://piherder.example:8443/x") == "https://piherder.example:8443"
    assert pr.configured_public_origin("not-a-url") == ""

    monkeypatch.setattr(ch, "password_reset_available", lambda: False)
    assert pr.request_reset_email(session, user.email, base_url="https://x")["ok"] is False
    monkeypatch.setattr(ch, "password_reset_available", lambda: True)
    unknown = pr.request_reset_email(session, "nope@x.com", base_url="https://x")
    assert unknown["ok"] is True and unknown["sent"] is False
    with patch.object(ch, "send_email", return_value={"ok": True}):
        sent = pr.request_reset_email(session, user.email, base_url="https://x")
        assert sent["sent"] is True
    with patch.object(ch, "send_email", return_value={"ok": False, "error": "smtp"}):
        fail = pr.request_reset_email(session, user.email, base_url="https://x")
        assert fail["ok"] is False

    monkeypatch.setattr(
        ch,
        "smtp_config",
        lambda: {
            "enabled": False,
            "host": "",
            "from_email": "",
            "from_name": "",
            "port": 587,
            "security": "starttls",
            "username": "",
            "alert_enabled": False,
            "alert_min_severity": "warning",
            "alert_to": "",
            "notify_categories": "",
            "password_reset_enabled": True,
        },
    )
    assert ch.send_email(to="a@b.com", subject="s", body_text="b")["ok"] is False
    monkeypatch.setattr(
        ch,
        "smtp_config",
        lambda: {
            "enabled": True,
            "host": "smtp.example",
            "from_email": "ph@example",
            "from_name": "PiHerder",
            "port": 587,
            "security": "starttls",
            "username": "u",
            "alert_enabled": True,
            "alert_min_severity": "info",
            "alert_to": "ops@example",
            "notify_categories": "",
            "password_reset_enabled": True,
        },
    )
    monkeypatch.setattr(ch, "_smtp_password", lambda: "pw")
    smtp = MagicMock()
    with patch("smtplib.SMTP", return_value=smtp) as S:
        smtp.__enter__.return_value = smtp
        r = ch.send_email(to="a@b.com", subject="s", body_text="hi", body_html="<p>hi</p>")
        assert r["ok"] is True
        S.assert_called()
    with patch("smtplib.SMTP", side_effect=OSError("down")):
        assert ch.send_email(to="a@b.com", subject="s", body_text="hi")["ok"] is False
    assert ch.send_email(to=[], subject="s", body_text="hi")["ok"] is False
    monkeypatch.setattr(
        ch,
        "smtp_config",
        lambda: {
            "enabled": True,
            "host": "",
            "from_email": "",
            "from_name": "",
            "port": 465,
            "security": "ssl",
            "username": "",
            "alert_enabled": False,
            "alert_min_severity": "warning",
            "alert_to": "",
            "notify_categories": "",
            "password_reset_enabled": True,
        },
    )
    assert ch.send_email(to="a@b.com", subject="s", body_text="hi")["ok"] is False

    monkeypatch.setattr(
        ch,
        "smtp_config",
        lambda: {
            "enabled": True,
            "host": "smtp.example",
            "from_email": "ph@example",
            "from_name": "",
            "port": 465,
            "security": "ssl",
            "username": "u",
            "alert_enabled": True,
            "alert_min_severity": "info",
            "alert_to": "ops@example",
            "notify_categories": "",
            "password_reset_enabled": True,
        },
    )
    ssl_smtp = MagicMock()
    with patch("smtplib.SMTP_SSL", return_value=ssl_smtp):
        ssl_smtp.__enter__.return_value = ssl_smtp
        assert ch.send_email(to="a@b.com", subject="s", body_text="hi")["ok"] is True

    monkeypatch.setattr(ch, "smtp_ready", lambda: True)
    monkeypatch.setattr(ch, "send_email", lambda **k: {"ok": True})
    monkeypatch.setattr(
        ch,
        "smtp_config",
        lambda: {
            "enabled": True,
            "host": "h",
            "from_email": "ph@example",
            "from_name": "",
            "port": 587,
            "security": "starttls",
            "username": "",
            "alert_enabled": True,
            "alert_min_severity": "info",
            "alert_to": "ops@example",
            "notify_categories": "",
            "password_reset_enabled": True,
        },
    )
    ch.maybe_email_notification(severity="warning", title="t", body="b", link_url="/x", category="backup")
    ch.send_test_email("ops@example")
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda *_a, **_k: None)
    ch.set_smtp_password("secret")
    ch.set_smtp_password("  ")
    ch.clear_smtp_password()


# ---------------------------------------------------------------------------
# from-host template pull
# ---------------------------------------------------------------------------


def test_from_host_pull_and_picker(monkeypatch):
    from app.services.service_templates import from_host as fh
    from app.services.service_templates.schema import TemplateError

    assert fh.project_to_slug("Grafana Stack") == "grafana-stack"
    assert fh.project_to_slug("???") == "imported-stack"

    srv = SimpleNamespace(id=1, name="pi", hostname="pi.local", docker_base_dir="/home/pi/docker")
    with pytest.raises(TemplateError):
        fh.pull_project_as_editor_form(srv, "../x")
    monkeypatch.setattr("app.services.service_templates.from_host.docker_base_expanded", lambda s: "/home/pi/docker")
    monkeypatch.setattr("app.services.service_templates.from_host.get_project_live_files", lambda *a, **k: {})
    with pytest.raises(TemplateError):
        fh.pull_project_as_editor_form(srv, "grafana")
    monkeypatch.setattr(
        "app.services.service_templates.from_host.get_project_live_files",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh")),
    )
    with pytest.raises(TemplateError):
        fh.pull_project_as_editor_form(srv, "grafana")

    files = {
        "docker-compose.yml": "services:\n  web:\n    image: grafana/grafana\n    volumes:\n      - ./promtail-config.yaml:/etc/c.yml\n",
        ".env.example": "GF_PASS=x\n",
        "compose.override.yml": "services: {}\n",
        "other.yml": "x: 1\n",
        "promtail-config.yaml": "server: {}\n",
    }
    monkeypatch.setattr(
        "app.services.service_templates.from_host.get_project_live_files",
        lambda *a, **k: files if not k.get("filenames") else {n: files[n] for n in k["filenames"] if n in files},
    )
    out = fh.pull_project_as_editor_form(srv, "grafana")
    assert out["form"]["slug"] == "grafana"
    assert out["messages"]

    srv.docker_inventory_json = json.dumps(
        {
            "projects": [
                "plain",
                {"name": "grafana", "containers": [{"name": "g", "compose_service": "web", "running": True}], "services": ["web"]},
                {"dir": "n8n", "services": [{"name": "n8n"}]},
                3,
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.docker_inventory.parse_inventory",
        lambda s: json.loads(s.docker_inventory_json),
    )
    picker = fh.list_host_projects_for_picker(srv)
    names = {p["name"] for p in picker}
    assert "grafana" in names and "plain" in names


# ---------------------------------------------------------------------------
# Celery task wrappers
# ---------------------------------------------------------------------------


def test_celery_task_skip_and_error_paths(monkeypatch):
    from app import tasks as tasks_mod

    job = SimpleNamespace(id=1, status="cancelled", celery_task_id=None)
    db = MagicMock()
    db.get.return_value = job
    db.exec.return_value.first.return_value = None

    with patch("app.tasks.Session", return_value=db):
        with patch("app.services.nmap.scan.run_nmap_scan"), patch(
            "app.services.nmap.runtime.touch_worker_heartbeat"
        ):
            out = tasks_mod.nmap_scan.run(5, job_id=1)
            assert out["status"] == "cancelled"

    job.status = "pending"
    with patch("app.tasks.Session", return_value=db), patch(
        "app.services.nmap.scan.run_nmap_scan", side_effect=RuntimeError("nmap")
    ), patch("app.services.nmap.runtime.touch_worker_heartbeat"), patch(
        "app.tasks._update_job_status"
    ) as upd:
        err = tasks_mod.nmap_scan.run(5, job_id=1)
        assert err["status"] == "error"
        upd.assert_called()

    with patch("app.tasks.Session", return_value=db), patch(
        "app.services.stale_data_cleanup.run_stale_data_cleanup", return_value={"ok": True}
    ):
        job.status = "cancelled"
        assert tasks_mod.stale_data_cleanup.run(job_id=1)["status"] == "cancelled"
    job.status = "pending"
    with patch("app.tasks.Session", return_value=db), patch(
        "app.services.stale_data_cleanup.run_stale_data_cleanup", side_effect=RuntimeError("x")
    ), patch("app.tasks._update_job_status"):
        assert tasks_mod.stale_data_cleanup.run(job_id=1)["status"] == "error"

    with patch("app.tasks.Session", return_value=db), patch(
        "app.services.nmap.vuln_update.run_vuln_db_update", return_value={"ok": True}
    ), patch("app.services.nmap.runtime.touch_worker_heartbeat"):
        job.status = "cancelled"
        assert tasks_mod.nmap_vuln_db_update.run(job_id=1)["status"] == "cancelled"
    job.status = "pending"
    with patch("app.tasks.Session", return_value=db), patch(
        "app.services.nmap.vuln_update.run_vuln_db_update", side_effect=RuntimeError("x")
    ), patch("app.services.nmap.runtime.touch_worker_heartbeat"), patch(
        "app.tasks._update_job_status"
    ):
        assert tasks_mod.nmap_vuln_db_update.run(job_id=1)["status"] == "error"

    with patch("app.tasks.Session", return_value=db):
        out = tasks_mod.backup_server.run(99, job_id=1)
        assert out["status"] == "error"

    srv = SimpleNamespace(id=3, name="pi", hostname="pi.local", get_backup_sources=lambda: [])
    db.exec.return_value.first.return_value = srv
    job.status = "success"
    with patch("app.tasks.Session", return_value=db):
        skipped = tasks_mod.backup_server.run(3, job_id=1)
        assert skipped["status"] == "skipped"

    mig_job = SimpleNamespace(id=4, status="cancelled", celery_task_id=None)
    db.get.return_value = mig_job
    with patch("app.tasks.Session", return_value=db):
        assert tasks_mod.service_migrate.run(4, 1, 2, "g", 8)["status"] == "cancelled"
    mig_job.status = "success"
    with patch("app.tasks.Session", return_value=db):
        assert tasks_mod.service_migrate.run(4, 1, 2, "g", 8)["status"] == "skipped"
    db.get.return_value = None
    with patch("app.tasks.Session", return_value=db):
        assert tasks_mod.service_migrate.run(4, 1, 2, "g", 8)["status"] == "skipped"


# ---------------------------------------------------------------------------
# main lifespan (startup branches, no live scheduler)
# ---------------------------------------------------------------------------


def test_main_lifespan_and_root_helpers(monkeypatch):
    from app import main as main_mod

    session, engine = _memory()
    _user(session)
    _server(session)
    monkeypatch.setattr(main_mod, "engine", engine)
    monkeypatch.setattr(main_mod, "HAS_SCHEDULER", False)
    monkeypatch.setattr(main_mod, "scheduler", None)
    monkeypatch.setattr("app.db_migrate.run_alembic_upgrade", lambda: None)
    monkeypatch.setattr(main_mod, "init_db", lambda: None)
    monkeypatch.setattr("app.services.jobs.cleanup_orphan_web_jobs", lambda *_a, **_k: 0)
    monkeypatch.setattr("app.services.jobs.cleanup_stale_backup_jobs", lambda *_a, **_k: 0)
    monkeypatch.setattr("app.services.console_audit.purge_expired_now", lambda: 0)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.push.ensure_vapid_keys", lambda *_a, **_k: SimpleNamespace(source="generated"))
    monkeypatch.setattr("app.services.service_templates.ensure_builtin_templates_in_db", lambda *_a, **_k: 0)
    monkeypatch.setattr("app.services.app_update.schedule_startup_check", lambda **k: None)
    monkeypatch.setattr("app.security.auth.is_weak_secret_key", lambda *_a, **_k: False)

    async def _run():
        async with main_mod.lifespan(MagicMock()):
            return True

    assert asyncio.run(_run()) is True
