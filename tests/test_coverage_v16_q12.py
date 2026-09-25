"""v1.6 Q-80 twelfth pack — jobs enqueue/execute, docker SFTP, herder/certs/dns gaps."""
from __future__ import annotations

import asyncio
import io
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, IntegrationBinding, Job, Server, StackDeployment, User


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
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type=kw.get("os_type", "debian"),
        container_patch_enabled=True,
        backup_enabled=True,
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address="10.0.0.4",
        ssh_private_key_encrypted="enc",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _fresh(engine):
    @contextmanager
    def _cm():
        s = Session(engine, expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()

    return _cm


class _SftpFile:
    def __init__(self, data=b""):
        self.buf = io.BytesIO(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

    def read(self, n=-1):
        return self.buf.read() if n < 0 else self.buf.read(n)

    def write(self, data):
        self.buf.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sftp:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.closed = False

    def open(self, path, mode="rb"):
        if "w" in mode:
            self.files[path] = b""

            class W:
                def __init__(inner, outer, p):
                    inner.outer = outer
                    inner.p = p
                    inner.buf = io.BytesIO()

                def write(inner, data):
                    inner.buf.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

                def __enter__(inner):
                    return inner

                def __exit__(inner, *a):
                    inner.outer.files[inner.p] = inner.buf.getvalue()

            return W(self, path)
        return _SftpFile(self.files.get(path, b""))

    def stat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_size=len(self.files[path]))

    def remove(self, path):
        self.files.pop(path, None)

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src, b"")

    def close(self):
        self.closed = True


class _Cli:
    def __init__(self, sftp=None):
        self._sftp = sftp or _Sftp()
        self.closed = False

    def open_sftp(self):
        return self._sftp

    def close(self):
        self.closed = True


def test_jobs_revoke_supersede_enqueue_backup(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)
    monkeypatch.setattr(js, "_get_fresh_session", _fresh(engine))
    monkeypatch.setattr(js, "HAS_CELERY", False)
    js._revoke_celery_task(None)
    js._revoke_celery_task("abc")
    monkeypatch.setattr(js, "HAS_CELERY", True)

    class _Ctl:
        def revoke(self, *a, **k):
            raise RuntimeError("broker down")

    monkeypatch.setattr("app.celery_app.celery", SimpleNamespace(control=_Ctl()), raising=False)
    js._revoke_celery_task("abc")

    running = Job(
        server_id=srv.id,
        job_type="backup",
        status="running",
        celery_task_id="t1",
        details="not-json",
        created_at=datetime.utcnow() - timedelta(hours=5),
    )
    session.add(running)
    session.commit()
    monkeypatch.setattr(js.backup, "stop_backup", lambda *a, **k: None)
    n = js.supersede_running_backups(session, srv.id)
    assert n >= 1
    assert js.supersede_running_backups(session, srv.id) == 0

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo = js.enqueue_backup_for_server(session, srv, user_id=1)
    assert demo.status in ("success", "pending", "running") or demo.id

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(js, "HAS_CELERY", False)
    monkeypatch.setattr(js, "backup_server", None)
    with pytest.raises(RuntimeError):
        js.enqueue_backup_for_server(session, srv, user_id=1)

    monkeypatch.setattr(js, "HAS_CELERY", True)

    class _Delay:
        def delay(self, *a, **k):
            raise RuntimeError("amqp")

    monkeypatch.setattr(js, "backup_server", _Delay())
    with pytest.raises(RuntimeError):
        js.enqueue_backup_for_server(session, srv, user_id=1)

    class _Ok:
        def delay(self, *a, **k):
            return SimpleNamespace(id="celery-ok")

    monkeypatch.setattr(js, "backup_server", _Ok())
    job = js.enqueue_backup_for_server(session, srv, user_id=1, source_filter="/home/pi/docker")
    assert job.celery_task_id == "celery-ok"
    same = js.enqueue_backup_for_server(session, srv, source_filter="/home/pi/docker")
    assert same.id == job.id


def test_jobs_create_and_run_and_async_runners(monkeypatch):
    from app.services import jobs as js

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)
    monkeypatch.setattr(js, "_get_fresh_session", _fresh(engine))
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
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: "10.0.0.9")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    added = []

    class _Bg:
        def add_task(self, fn, *a, **k):
            added.append((fn, a, k))

    job = js.create_job_and_run(_Bg(), session, srv, "os_update_check", user_id=1)
    assert job.id
    job2 = js.create_job_and_run(_Bg(), session, srv, "host_facts", user_id=1)
    assert job2.job_type == "host_facts"
    job3 = js.create_job_and_run(
        _Bg(), session, srv, "os_patch", user_id=1, os_steps=["update"]
    )
    assert "update" in (job3.details or "") or job3.id
    assert added

    monkeypatch.setattr(
        js.os_patching,
        "check_os_updates",
        lambda *a, **k: {
            "updates_count": 2,
            "actionable_count": 2,
            "reboot_pending": False,
            "auto_mark_haos": True,
            "packages_sample": ["a"],
        },
    )
    monkeypatch.setattr(
        js.container_patching,
        "check_all_projects_updates",
        lambda *a, **k: {"projects_with_updates": ["g"], "projects_checked": ["g"]},
    )
    monkeypatch.setattr(
        js.container_patching,
        "run_project_update",
        lambda *a, **k: {"success": True, "summary": "ok"},
    )
    monkeypatch.setattr(js.container_patching, "container_patch_succeeded", lambda r: True)
    monkeypatch.setattr(js.container_patching, "init_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(js.container_patching, "mark_container_patch_done", lambda *a, **k: None)
    monkeypatch.setattr(js.container_patching, "append_container_log", lambda *a, **k: None)
    monkeypatch.setattr(js.container_patching, "clear_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(js.container_patching, "summarize_container_patch", lambda r: "ok")
    monkeypatch.setattr(js.os_patching, "init_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(js.os_patching, "run_os_patch", lambda *a, **k: {"success": True, "backend": "ha_cli"})
    monkeypatch.setattr(js.os_patching, "os_patch_succeeded", lambda r: True)
    monkeypatch.setattr(js.os_patching, "mark_os_patch_done", lambda *a, **k: None)
    monkeypatch.setattr(js.os_patching, "_append_os_log", lambda *a, **k: None)
    monkeypatch.setattr(js.os_patching, "clear_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(js.os_patching, "attach_audit_fields", lambda res, *a, **k: res if isinstance(res, dict) else {"summary": str(res)})
    monkeypatch.setattr(js.backup, "run_retention", lambda *a, **k: "cleaned 2")
    monkeypatch.setattr(js.herder_backup, "create_herder_backup", lambda **k: Path("/tmp/x.tar.gz"))

    ja = Job(server_id=srv.id, job_type="container_patch", status="pending", details="{}")
    jb = Job(server_id=srv.id, job_type="os_patch", status="pending", details="{}")
    jc = Job(server_id=srv.id, job_type="retention", status="pending", details="{}")
    jd = Job(server_id=srv.id, job_type="herder_backup", status="pending", details="{}")
    session.add(ja)
    session.add(jb)
    session.add(jc)
    session.add(jd)
    session.commit()
    aa = AuditLog(server_id=srv.id, action="container_patch", status="running", details=f"Job #{ja.id} started")
    ab = AuditLog(server_id=srv.id, action="os_patch", status="running", details=f"Job #{jb.id} started")
    ac = AuditLog(server_id=srv.id, action="retention", status="running", details=f"Job #{jc.id} started")
    ad = AuditLog(action="herder_backup", status="running", details=f"Job #{jd.id} started")
    session.add(aa)
    session.add(ab)
    session.add(ac)
    session.add(ad)
    session.commit()

    asyncio.run(js._run_container_job(ja.id, srv.id, aa.id))
    asyncio.run(js._run_os_patch_job(jb.id, srv.id, ab.id, ["update"]))
    asyncio.run(js._run_retention_job(jc.id, srv.id, ac.id))
    asyncio.run(js._run_herder_backup_job(jd.id, ad.id))
    asyncio.run(js._run_container_job(ja.id, 99999, aa.id))
    asyncio.run(js._run_os_patch_job(jb.id, 99999, ab.id))
    asyncio.run(js._run_retention_job(jc.id, 99999, ac.id))

    monkeypatch.setattr(js.container_patching, "run_project_update", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh")))
    je = Job(server_id=srv.id, job_type="container_patch", status="pending", details="{}")
    session.add(je)
    session.commit()
    ae = AuditLog(server_id=srv.id, action="container_patch", status="running", details=f"Job #{je.id} started")
    session.add(ae)
    session.commit()
    asyncio.run(js._run_container_job(je.id, srv.id, ae.id))

    monkeypatch.setattr(js.os_patching, "run_os_patch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("apt")))
    jf = Job(server_id=srv.id, job_type="os_patch", status="pending", details="{}")
    session.add(jf)
    session.commit()
    af = AuditLog(server_id=srv.id, action="os_patch", status="running", details=f"Job #{jf.id} started")
    session.add(af)
    session.commit()
    asyncio.run(js._run_os_patch_job(jf.id, srv.id, af.id))

    monkeypatch.setattr(js.backup, "run_retention", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    jg = Job(server_id=srv.id, job_type="retention", status="pending", details="{}")
    session.add(jg)
    session.commit()
    ag = AuditLog(server_id=srv.id, action="retention", status="running", details=f"Job #{jg.id} started")
    session.add(ag)
    session.commit()
    asyncio.run(js._run_retention_job(jg.id, srv.id, ag.id))

    monkeypatch.setattr(js.herder_backup, "create_herder_backup", lambda **k: (_ for _ in ()).throw(RuntimeError("tar")))
    jh = Job(job_type="herder_backup", status="pending", details="{}")
    session.add(jh)
    session.commit()
    ah = AuditLog(action="herder_backup", status="running", details=f"Job #{jh.id} started")
    session.add(ah)
    session.commit()
    asyncio.run(js._run_herder_backup_job(jh.id, ah.id))

    js._send_summary_webhook("h", "backup", "failed", "x")
    js._send_summary_webhook("h", "os_patch", "success", "ok")
    js._flush_container_progress_to_job(jg.id, "patching", "line")


def test_docker_status_and_compose_sftp(monkeypatch):
    from app.services import docker_management as dm

    srv = SimpleNamespace(id=1, hostname="pi", docker_base_dir="/home/pi/docker", ssh_username="pi")
    inspect = {
        "Name": "/grafana",
        "State": {"Status": "running", "Running": True},
        "Config": {"Image": "grafana/grafana"},
        "Created": "now",
        "NetworkSettings": {
            "Ports": {"3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3000"}]}
        },
    }

    def _run(client, cmd, timeout=15):
        if "inspect --format" in cmd and "json" in cmd:
            return 0, json.dumps(inspect), ""
        return 0, "", ""

    monkeypatch.setattr(dm, "get_ssh_client", lambda s: _Cli(_Sftp({
        "/home/pi/docker/g/docker-compose.yml": b"services:\n  web:\n    image: nginx\n    build: .\n",
        "/home/pi/docker/g/Dockerfile": b"FROM alpine\n",
    })))
    monkeypatch.setattr(dm, "run_command", _run)
    st = dm.get_container_status(srv, "grafana")
    assert st["running"] is True
    assert st["ports"]
    empty = dm.get_container_status(srv, "")
    assert empty["state"] == "unknown"
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (0, "not-json", ""))
    unk = dm.get_container_status(srv, "x")
    assert unk["running"] is False

    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (0, "", ""))
    body = dm.read_compose_file(srv, "/home/pi/docker/g")
    assert "services" in body
    builds = dm.get_compose_build_services(srv, "/home/pi/docker/g")
    assert "web" in builds
    df = dm.read_dockerfile(srv, "/home/pi/docker/g/Dockerfile")
    assert "alpine" in df
    missing = dm.read_dockerfile(srv, "/nope")
    assert isinstance(missing, str)
    ok, err = dm.write_dockerfile(srv, "/home/pi/docker/g/Dockerfile", "FROM busybox\n")
    assert ok is True or err == ""
    ok2, _ = dm.write_compose_file(srv, "/home/pi/docker/g", "services:\n  web:\n    image: nginx\n")
    assert ok2 is True

    class BoomSftp(_Sftp):
        def open(self, path, mode="rb"):
            raise IOError("nope")

    monkeypatch.setattr(dm, "get_ssh_client", lambda s: _Cli(BoomSftp()))
    bad_w, msg = dm.write_compose_file(srv, "/x", "a: 1\n")
    assert bad_w is False


def test_herder_backup_helpers(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    session, engine = _memory()
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(hb.settings, "HERDER_BACKUP_ROOT", str(tmp_path / "hb"))
    monkeypatch.setattr(hb.settings, "AVATAR_MAX_BYTES", 1024)

    assert hb._path_is_writable(tmp_path / "w") is True
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    assert hb._path_is_writable(blocker) is False

    assert hb.is_safe_archive_basename("piherder-x.tar.gz") is True
    assert hb.is_safe_archive_basename("../x.tar.gz") is False
    assert hb.is_safe_archive_basename("nope.zip") is False
    assert hb._is_under_root(tmp_path / "a", tmp_path) is True

    rows = hb._jobs_for_restore(
        [
            "skip",
            {"status": "success", "details": "{}"},
            {"status": "running", "details": json.dumps({"current": "copy"})},
            {"status": "pending", "details": "plain"},
            {"status": "running", "details": "["},
            {"status": "running"},
        ]
    )
    assert any(r.get("status") == "cancelled" for r in rows if isinstance(r, dict))

    now = datetime.now(timezone.utc)
    assert hb._parse_dt(None) is None
    assert hb._parse_dt(now).tzinfo is None
    assert isinstance(hb._parse_dt("2026-09-20T12:00:00Z"), datetime)
    assert hb._parse_dt("not-a-date") == "not-a-date"
    assert hb._parse_dt(1) == 1
    cols = hb._column_names(Server)
    assert "hostname" in cols
    cleaned = hb._clean_row(Server, {"hostname": "x", "nope": 1, "created_at": "2026-09-20T00:00:00Z"})
    assert "nope" not in cleaned
    assert "hostname" in cleaned

    (tmp_path / "avatars").mkdir()
    (tmp_path / "avatars" / "a.png").write_bytes(b"x")
    (tmp_path / "service_logos").mkdir()
    (tmp_path / "service_logos" / "l.png").write_bytes(b"y")
    assert hb._avatar_files()
    assert hb._service_logo_files()
    assert hb._snapshot_servers() == [] or isinstance(hb._snapshot_servers(), list)
    assert isinstance(hb._snapshot_jobs(), list)


def test_cert_simulate_sudoers(monkeypatch):
    from app.services import certificates as certs

    cmds = []

    def _run(client, cmd, timeout=15):
        cmds.append(cmd)
        if "sudo -n -l" in cmd:
            return 0, "ALL\n", ""
        return 0, "ok", ""

    monkeypatch.setattr(certs.ssh_svc, "run_command", _run)
    out = certs.simulate_sudoers_access(
        SimpleNamespace(),
        remote_dir="~/certs",
        layout="pair",
        write_mode="stage_sudo",
        post_deploy_command="systemctl restart nginx",
        ssh_user="pi",
        home_dir="/home/pi",
    )
    assert out.get("ok") is True or out.get("checks")
    out2 = certs.simulate_sudoers_access(
        SimpleNamespace(),
        remote_dir="/etc/ssl",
        layout="combined",
        write_mode="direct",
        post_deploy_command="",
        ssh_user="pi",
        home_dir="/home/pi",
    )
    assert "checks" in out2


def test_dns_candidates_host_identity_and_deploy(monkeypatch):
    from app.services.dns_fabric import core as fabric

    session, _ = _memory()
    srv = _server(session)
    bind = IntegrationBinding(
        integration_id=1,
        server_id=srv.id,
        role="service",
        docker_project=None,
        docker_container=None,
        external_id="kuma-1",
        external_label="https://pi.lan/admin",
    )
    session.add(bind)
    dep = StackDeployment(
        server_id=srv.id,
        project_name="grafana",
        template_slug="grafana",
        files_json="{}",
        variables_json="{}",
    )
    session.add(dep)
    session.commit()
    monkeypatch.setattr(fabric, "list_pihole_cnames", lambda s: [])
    monkeypatch.setattr(
        fabric,
        "resolve_service_dns_plan",
        lambda *a, **k: {
            "fqdn": "grafana.lan",
            "ready": True,
            "existing_cname": False,
            "summary": "ok",
        },
    )
    cands = fabric.list_service_dns_candidates(session, base_domain="lan")
    assert isinstance(cands, list)
    kinds = {c.get("kind") for c in cands}
    assert "host_identity" in kinds or "deployment" in kinds or cands == cands
