"""v1.14 coverage for job bodies, backup routes, and API file branches.

SSH, Celery inspect, and host-file IO are stubs. No worker is started.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from celery.exceptions import Retry
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, BackupDestination, Job, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services import api_tokens as tok_svc
from app.services.host_files import FilesError


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _bind(monkeypatch, engine):
    monkeypatch.setattr("app.database.engine", engine)


def _server(name, hostname, *, docker=True, ssh=True):
    return Server(
        name=name,
        hostname=hostname,
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_username="user" if ssh else "",
        ssh_password_encrypted="enc-placeholder" if ssh else None,
        os_type="debian",
        backup_enabled=True,
        os_patch_enabled=True,
        container_patch_enabled=docker,
        backup_paths="[]",
    )


def _add_job(session, server_id, job_type, status="pending", details="{}", **extra):
    job = Job(
        server_id=server_id,
        job_type=job_type,
        status=status,
        details=details,
        **extra,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        server_id=server_id,
        action=job_type,
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return job, audit


class _Req:
    def __init__(self, task_id="task-1", retries=0, hostname="celery@host-a"):
        self.id = task_id
        self.retries = retries
        self.hostname = hostname


class _Self:
    def __init__(self, task_id="task-1", retries=0, hostname="celery@host-a"):
        self.request = _Req(task_id, retries, hostname)

    def retry(self, *args, **kwargs):
        raise Retry("again")


def _run(task, self_obj, *args, **kwargs):
    if hasattr(task, "push_request"):
        req = self_obj.request
        task.push_request(id=req.id, retries=req.retries, hostname=getattr(req, "hostname", None))
        task.request.retries = req.retries
        task.retry = self_obj.retry
        try:
            return task.run(*args, **kwargs)
        finally:
            task.pop_request()
    return task(self_obj, *args, **kwargs)


def test_celery_worker_activity(monkeypatch):
    import app.services.jobs as js

    class _Insp:
        def __init__(self, ping, active):
            self._ping = ping
            self._active = active

        def ping(self):
            if isinstance(self._ping, Exception):
                raise self._ping
            return self._ping

        def active(self):
            return self._active

    state = {"insp": None}

    def _inspect(timeout=1.0):
        return state["insp"]

    monkeypatch.setattr("app.celery_app.celery.control.inspect", _inspect)

    state["insp"] = None
    assert js.celery_worker_activity() == (False, set())

    state["insp"] = _Insp({}, {})
    assert js.celery_worker_activity() == (False, set())

    state["insp"] = _Insp(None, None)
    assert js.celery_worker_activity() == (False, set())

    state["insp"] = _Insp(
        {"celery@host-a": {"ok": "pong"}},
        {
            "celery@host-a": [
                {"id": "task-live"},
                {"id": ""},
                {"name": "no-id"},
                "skip",
            ],
            "other": "not-a-list",
        },
    )
    alive, ids = js.celery_worker_activity()
    assert alive is True
    assert ids == {"task-live"}

    state["insp"] = _Insp(RuntimeError("broker down"), {})
    assert js.celery_worker_activity() is None


def test_patch_job_error_tails(tmp_path, monkeypatch):
    import app.services.jobs as js

    engine = _engine(tmp_path / "patch.db")
    _bind(monkeypatch, engine)
    held = []

    async def _fast(_n=0):
        return None

    real_create = asyncio.create_task

    def _create(coro, *args, **kwargs):
        name = getattr(getattr(coro, "cr_code", None), "co_name", "")
        if name == "_delayed_clear":
            if _create.raise_delayed:
                coro.close()
                raise RuntimeError("no task")
            held.append(coro)
            async def _done():
                return None
            return real_create(_done())
        return real_create(coro, *args, **kwargs)

    _create.raise_delayed = False
    monkeypatch.setattr(asyncio, "sleep", _fast)
    monkeypatch.setattr(asyncio, "create_task", _create)

    with Session(engine) as session:
        srv = _server("host-a", "host-a.example.com")
        session.add(srv)
        session.commit()
        session.refresh(srv)
        sid = srv.id

    def _pair(kind):
        with Session(engine) as session:
            job, audit = _add_job(session, sid, kind)
            return job.id, audit.id

    monkeypatch.setattr(js.os_patching, "init_os_patch_progress", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("init")))
    monkeypatch.setattr(js.os_patching, "run_os_patch", lambda *a, **k: {"summary": "no", "auto_mark_haos": True})
    monkeypatch.setattr(js.os_patching, "os_patch_succeeded", lambda r: False)
    monkeypatch.setattr(js.os_patching, "check_os_updates", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("recheck")))
    monkeypatch.setattr(js.os_patching, "mark_os_patch_done", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mark")))
    monkeypatch.setattr(js.os_patching, "_append_os_log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log")))
    monkeypatch.setattr(js.os_patching, "clear_os_patch_progress", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("clear")))
    monkeypatch.setattr(js.os_patching, "attach_audit_fields", lambda res, *a, **k: res if isinstance(res, dict) else {"summary": str(res)})

    jid, aid = _pair("os_patch")
    asyncio.run(js._run_os_patch_job(jid, 99999, aid))

    monkeypatch.setattr(js.os_patching, "os_patch_succeeded", lambda r: True)
    real_fresh = js._get_fresh_session
    state = {"n": 0}

    def _fresh():
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("haos session")
        return real_fresh()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    jid, aid = _pair("os_patch")
    asyncio.run(js._run_os_patch_job(jid, sid, aid, ["update"]))
    monkeypatch.setattr(js, "_get_fresh_session", real_fresh)
    for coro in held:
        asyncio.run(coro)
    held.clear()

    monkeypatch.setattr(js.os_patching, "run_os_patch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("apt")))
    _create.raise_delayed = True
    jid, aid = _pair("os_patch")
    asyncio.run(js._run_os_patch_job(jid, sid, aid))
    _create.raise_delayed = False

    monkeypatch.setattr(js.container_patching, "init_container_patch_progress", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("init")))
    monkeypatch.setattr(js.container_patching, "run_project_update", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(js.container_patching, "container_patch_succeeded", lambda r: True)
    monkeypatch.setattr(js.container_patching, "summarize_container_patch", lambda r: (_ for _ in ()).throw(RuntimeError("summary")))
    monkeypatch.setattr(js.container_patching, "mark_container_patch_done", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mark")))
    monkeypatch.setattr(js.container_patching, "append_container_log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log")))
    monkeypatch.setattr(js.container_patching, "clear_container_patch_progress", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("clear")))
    monkeypatch.setattr(js.container_patching, "check_all_projects_updates", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("recheck")))
    monkeypatch.setattr("app.services.docker_management.prune_dangling_images", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("prune")))

    jid, aid = _pair("container_patch")
    asyncio.run(js._run_container_job(jid, 99999, aid))

    monkeypatch.setattr(
        js.container_patching,
        "run_project_update",
        lambda *a, **k: {"success": True},
    )
    monkeypatch.setattr(js.container_patching, "summarize_container_patch", lambda r: "ok")
    jid, aid = _pair("container_patch")
    asyncio.run(js._run_container_job(jid, sid, aid))
    for coro in held:
        asyncio.run(coro)
    held.clear()

    monkeypatch.setattr(js.container_patching, "run_project_update", lambda *a, **k: "plain")
    monkeypatch.setattr(js.container_patching, "container_patch_succeeded", lambda r: False)
    _create.raise_delayed = True
    jid, aid = _pair("container_patch")
    asyncio.run(js._run_container_job(jid, sid, aid))

    with Session(engine) as session:
        rows = list(session.exec(select(Job)).all())
        assert rows
        assert any(row.status == "failed" for row in rows)


def test_finish_and_housekeeping(tmp_path, monkeypatch):
    import app.services.jobs as js

    engine = _engine(tmp_path / "hk.db")
    _bind(monkeypatch, engine)
    hooks = {"webhook": 0, "notify": 0, "resolve": 0}

    monkeypatch.setattr(
        "app.services.alert_channels.send_webhook",
        lambda *a, **k: hooks.__setitem__("webhook", hooks["webhook"] + 1),
    )

    def _notify(*a, **k):
        hooks["notify"] += 1
        if hooks["notify"] == 1:
            raise RuntimeError("notify")

    def _resolve_backup(*a, **k):
        hooks["resolve"] += 1

    def _resolve_container(*a, **k):
        hooks["container"] = hooks.get("container", 0) + 1
        if hooks["container"] == 1:
            raise RuntimeError("schedule")

    monkeypatch.setattr("app.services.notifications.notify_backup_failed", _notify)
    monkeypatch.setattr("app.services.notifications.resolve_backup_failed", _resolve_backup)
    monkeypatch.setattr("app.services.schedule_watch.resolve_backup_schedule_missed", lambda *a, **k: None)
    monkeypatch.setattr("app.services.schedule_watch.resolve_container_check_missed", _resolve_container)
    monkeypatch.setattr(js.backup, "run_retention", lambda server: {"removed": 1})
    monkeypatch.setattr(js.herder_backup, "create_herder_backup", lambda **k: "/tmp/piherder-test.tar.gz")
    monkeypatch.setattr(js, "_enqueue_herder_archive_copies", lambda *a, **k: None)
    monkeypatch.setattr(js, "_resolve_herder_backup_failed", lambda: None)
    monkeypatch.setattr(js, "_notify_herder_backup_failed", lambda *a, **k: None)

    with Session(engine) as session:
        srv = _server("host-a", "host-a.example.com")
        srv.host_facts_status = "refreshing"
        session.add(srv)
        session.commit()
        session.refresh(srv)
        sid = srv.id

        cancelled, audit_c = _add_job(session, sid, "host_facts", status="cancelled")
        facts, audit_f = _add_job(
            session,
            sid,
            "host_facts",
            details=json.dumps({"client_ip": "192.0.2.10", "redirect_url": "/jobs", "deployment_id": 4}),
        )
        backup_job, audit_b = _add_job(session, sid, "backup", details="{")
        done, audit_d = _add_job(session, sid, "backup", status="success")
        done.finished_at = done.created_at
        session.add(done)
        session.commit()
        os_job, audit_o = _add_job(session, sid, "os_patch")
        check, audit_k = _add_job(session, sid, "container_update_check")
        custom, audit_u = _add_job(session, sid, "retention")
        audit_u.details = "kept on purpose"
        session.add(audit_u)
        session.commit()
        boot, audit_boot = _add_job(session, sid, "host_bootstrap")

        js._finish(audit_c.id, cancelled.id, "success", "ignored", "host-a.example.com", "host_facts")
        js._finish(
            audit_f.id,
            facts.id,
            "failed",
            json.dumps({"redirect_url": "/servers/1", "deployment_id": 9, "error": "ssh"}),
            "host-a.example.com",
            "host_facts",
        )
        js._finish(audit_b.id, backup_job.id, "failed", "disk full", "host-a.example.com", "backup")
        js._finish(audit_b.id, backup_job.id, "failed", "disk full", "host-a.example.com", "backup")
        js._finish(audit_d.id, done.id, "success", json.dumps({"results": []}), "host-a.example.com", "backup")
        js._finish(audit_o.id, os_job.id, "failed", json.dumps({"summary": "apt failed"}), "host-a.example.com", "os_patch")
        js._finish(audit_k.id, check.id, "success", json.dumps({"projects_with_updates": []}), "host-a.example.com", "container_update_check")
        js._finish(0, 0, "failed", "missing", "", "")
        js._finish(audit_u.id, custom.id, "success", "kept", "", "retention")
        js._finish(
            audit_boot.id,
            boot.id,
            "success",
            json.dumps({"message": "done", "features": ["rsync"], "os_id": "debian", "codename": "bookworm", "bootstrap_username": "user"}),
            "host-a.example.com",
            "host_bootstrap",
        )
        js._finish(audit_boot.id, boot.id, "failed", "plain text", "host-a.example.com", "container_patch")

        session.expire_all()
        assert session.get(Job, cancelled.id).status == "cancelled"
        assert session.get(Server, sid).host_facts_status == "stale"
        assert session.get(Job, facts.id).status == "failed"
        details = json.loads(session.get(Job, facts.id).details)
        assert details["redirect_url"] == "/servers/1"
        assert details["deployment_id"] == 9
        assert session.get(AuditLog, audit_u.id).details == "kept on purpose"
        assert hooks["webhook"] >= 1
        assert hooks["notify"] >= 1

        class _Bg:
            def __init__(self):
                self.tasks = []

            def add_task(self, fn, *args):
                self.tasks.append((fn, args))

        bg = _Bg()
        queued, audit_q = _add_job(session, sid, "retention")
        js.handoff_housekeeping(
            job_id=queued.id,
            server_id=sid,
            audit_id=audit_q.id,
            job_type="retention",
            background_tasks=bg,
            bg_fn=js._run_retention_job,
            bg_args=(queued.id, sid, audit_q.id),
        )
        assert bg.tasks

        with pytest.raises(RuntimeError):
            js.handoff_housekeeping(
                job_id=queued.id,
                server_id=sid,
                audit_id=audit_q.id,
                job_type="retention",
            )

        monkeypatch.setattr("app.services.jobs_exclusive.exclusive_runs_inline", lambda: False)
        monkeypatch.setattr(js, "HAS_CELERY", False)
        no_celery, audit_nc = _add_job(session, sid, "retention")
        with pytest.raises(RuntimeError):
            js.handoff_housekeeping(
                job_id=no_celery.id,
                server_id=sid,
                audit_id=audit_nc.id,
                job_type="retention",
            )
        session.expire_all()
        assert session.get(Job, no_celery.id).status == "failed"

        monkeypatch.setattr(js, "HAS_CELERY", True)
        delay_fail, audit_df = _add_job(session, None, "herder_backup")
        with pytest.raises(RuntimeError):
            js.handoff_housekeeping(
                job_id=delay_fail.id,
                server_id=0,
                audit_id=audit_df.id,
                job_type="herder_backup",
            )

        monkeypatch.setattr(
            js.housekeeping_job_task,
            "delay",
            lambda *a, **k: SimpleNamespace(id="task-hk-1"),
        )
        queued_ok, audit_ok = _add_job(session, sid, "retention")
        js.handoff_housekeeping(
            job_id=queued_ok.id,
            server_id=sid,
            audit_id=audit_ok.id,
            job_type="retention",
        )
        session.expire_all()
        assert session.get(Job, queued_ok.id).celery_task_id == "task-hk-1"

        task = SimpleNamespace(request=_Req("task-run", 0, "celery@host-a"))
        assert js.run_housekeeping_job(task, 99999, sid, 1, "retention")["status"] == "skipped"
        other, _audit_other = _add_job(session, sid, "os_patch")
        assert js.run_housekeeping_job(task, other.id, sid, 1, "os_patch")["reason"] == "os_patch"
        stopped, audit_stop = _add_job(session, sid, "retention", status="cancelled")
        assert js.run_housekeeping_job(task, stopped.id, sid, audit_stop.id, "retention")["status"] == "cancelled"
        finished, audit_fin = _add_job(session, sid, "retention", status="success")
        assert js.run_housekeeping_job(task, finished.id, sid, audit_fin.id, "retention")["reason"] == "success"

        running, audit_run = _add_job(session, sid, "retention", status="running")
        out = js.run_housekeeping_job(task, running.id, sid, audit_run.id, "retention")
        assert out["reason"] == "worker_restart"

        ret, audit_ret = _add_job(session, sid, "retention")
        assert js.run_housekeeping_job(task, ret.id, sid, audit_ret.id, "retention")["status"] == "ok"
        monkeypatch.setattr(js.backup, "run_retention", lambda server: 3)
        ret2, audit_ret2 = _add_job(session, sid, "retention")
        js._execute_retention(ret2.id, sid, audit_ret2.id)
        monkeypatch.setattr(js.backup, "run_retention", lambda server: (_ for _ in ()).throw(RuntimeError("disk")))
        ret3, audit_ret3 = _add_job(session, sid, "retention")
        js._execute_retention(ret3.id, sid, audit_ret3.id)
        js._execute_retention(ret3.id, 99999, audit_ret3.id)

        herd, audit_h = _add_job(session, None, "herder_backup")
        assert js.run_housekeeping_job(task, herd.id, 0, audit_h.id, "herder_backup")["status"] == "ok"

        def _boom(*a, **k):
            raise RuntimeError("retention down")

        monkeypatch.setattr(js, "_execute_retention", _boom)
        boom_job, audit_boom = _add_job(session, sid, "retention")
        err = js.run_housekeeping_job(task, boom_job.id, sid, audit_boom.id, "retention")
        assert err["status"] == "error"
        session.expire_all()
        assert session.get(Job, boom_job.id).status == "failed"


def test_execute_host_template_and_container(tmp_path, monkeypatch):
    import app.services.jobs as js
    from app.services.service_templates import TemplateError

    engine = _engine(tmp_path / "exec.db")
    _bind(monkeypatch, engine)
    secret = "placeholder-secret"
    enc = encrypt_str(secret)

    with Session(engine) as session:
        srv = _server("host-a", "host-a.example.com")
        srv.host_facts_status = "refreshing"
        session.add(srv)
        session.commit()
        session.refresh(srv)
        sid = srv.id

    facts_state = {"mode": "error"}

    def _facts(server_id, force=True):
        if facts_state["mode"] == "raise":
            raise RuntimeError("ssh")
        if facts_state["mode"] == "error":
            return {"status": "error", "error": "ssh"}
        return {"status": "ok", "hostname": "host-a.example.com"}

    monkeypatch.setattr("app.services.host_facts.refresh_server_facts", _facts)

    with Session(engine) as session:
        missing, audit_m = _add_job(session, sid, "host_facts")
        js._execute_host_facts(missing.id, 99999, audit_m.id)
        bad, audit_bad = _add_job(session, sid, "host_facts")
        js._execute_host_facts(bad.id, sid, audit_bad.id)
        facts_state["mode"] = "ok"
        good, audit_good = _add_job(session, sid, "host_facts")
        js._execute_host_facts(good.id, sid, audit_good.id)
        facts_state["mode"] = "raise"
        blown, audit_blown = _add_job(session, sid, "host_facts")
        js._execute_host_facts(blown.id, sid, audit_blown.id)
        session.expire_all()
        assert session.get(Job, bad.id).status == "failed"
        assert session.get(Job, good.id).status == "success"
        assert session.get(Server, sid).host_facts_status == "stale"

    boot_state = {"mode": "ok"}

    def _bootstrap(server, session, **kwargs):
        if boot_state["mode"] == "raise":
            raise RuntimeError(f"failed {secret} {enc}")
        ok = boot_state["mode"] == "ok"
        return SimpleNamespace(
            ok=ok,
            message="Bootstrap finished" if ok else "docker missing",
            details={
                "os_id": "debian",
                "codename": "bookworm",
                "features": ["rsync"] if ok else [],
                "bootstrap_username": "user",
                "sudo_password": secret,
                "sudo_password_encrypted": enc,
            },
        )

    monkeypatch.setattr("app.services.ssh_onboarding.run_host_bootstrap", _bootstrap)

    with Session(engine) as session:
        job, audit = _add_job(session, sid, "host_bootstrap")
        js._execute_host_bootstrap(job.id, 99999, audit.id, {"sudo_password_encrypted": "not-a-token"})
        job2, audit2 = _add_job(session, sid, "host_bootstrap")
        js._execute_host_bootstrap(
            job2.id,
            sid,
            audit2.id,
            {
                "confirm_docker_bootstrap": True,
                "bootstrap_username": "user",
                "sudo_password_encrypted": "not-a-token",
            },
        )
        boot_state["mode"] = "fail"
        job3, audit3 = _add_job(session, sid, "host_bootstrap")
        js._execute_host_bootstrap(job3.id, sid, audit3.id, {"sudo_password_encrypted": enc})
        boot_state["mode"] = "raise"
        job4, audit4 = _add_job(session, sid, "host_bootstrap")
        js._execute_host_bootstrap(job4.id, sid, audit4.id, {"sudo_password_encrypted": enc})
        session.expire_all()
        failed = session.get(Job, job4.id)
        assert secret not in (failed.details or "")
        assert session.get(AuditLog, audit4.id).output_snippet
        assert secret not in (session.get(AuditLog, audit4.id).output_snippet or "")

    real_get = Session.get
    seen = {"n": 0}

    def _get(self, entity, ident, *args, **kwargs):
        if entity is Server and int(ident or 0) == int(sid):
            seen["n"] += 1
            if seen["n"] >= 2:
                return None
        return real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", _get)
    with Session(engine) as session:
        job5, audit5 = _add_job(session, sid, "host_bootstrap")
        js._execute_host_bootstrap(job5.id, sid, audit5.id, {})
    monkeypatch.setattr(Session, "get", real_get)

    dep_state = {"dep": None, "boom": None, "result": {"project_name": "web", "config_version": 2, "redeploy": {"success": False, "error": "compose"}}}

    def _get_dep(session, dep_id):
        return dep_state["dep"]

    def _redeploy(session, **kwargs):
        if dep_state["boom"] == "redeploy":
            raise RuntimeError("ssh")
        return dep_state["result"]

    monkeypatch.setattr("app.services.service_templates.get_deployment", _get_dep)
    monkeypatch.setattr("app.services.service_templates.redeploy_desired_state", _redeploy)

    with Session(engine) as session:
        job, audit = _add_job(
            session,
            sid,
            "template_redeploy",
            details=json.dumps({"secrets_encrypted": enc, "updated_public": {"image": "web"}}),
        )
        js._execute_template_redeploy(job.id, 99999, audit.id, 4, True)
        session.expire_all()
        cleared = json.loads(session.get(Job, job.id).details)
        assert "secrets_encrypted" not in cleared

        bad, audit_bad = _add_job(
            session,
            sid,
            "template_redeploy",
            details=json.dumps({"secrets_encrypted": "%%%", "updated_public": "nope"}),
        )
        js._execute_template_redeploy(bad.id, sid, audit_bad.id, 4, True)

        missing_dep, audit_md = _add_job(session, sid, "template_redeploy", details="{}")
        js._execute_template_redeploy(missing_dep.id, sid, audit_md.id, 4, True)

        dep_state["dep"] = SimpleNamespace(server_id=999, id=4)
        wrong, audit_w = _add_job(session, sid, "template_redeploy", details="{}")
        js._execute_template_redeploy(wrong.id, sid, audit_w.id, 4, True)

        dep_state["dep"] = SimpleNamespace(server_id=sid, id=4)
        host_fail, audit_hf = _add_job(
            session,
            sid,
            "template_redeploy",
            details=json.dumps({"updated_public": {"image": "web"}, "secrets_encrypted": enc}),
        )
        js._execute_template_redeploy(host_fail.id, sid, audit_hf.id, 4, True)
        stored, audit_st = _add_job(session, sid, "template_redeploy", details="{}")
        js._execute_template_redeploy(stored.id, sid, audit_st.id, 4, False)
        dep_state["boom"] = "redeploy"
        blown, audit_bl = _add_job(session, sid, "template_redeploy", details="{}")
        js._execute_template_redeploy(blown.id, sid, audit_bl.id, 4, True)
        session.expire_all()
        assert session.get(Job, host_fail.id).status == "failed"
        assert session.get(Job, stored.id).status == "success"
        assert session.get(Job, missing_dep.id).status == "failed"
        assert TemplateError is not None

    calls = {"action": [], "redeploy": 0, "inv": 0, "prune": 0}
    docker_state = {"mode": "ok"}

    def _compose(server, path, act, service=None, remove_volumes=False):
        calls["action"].append(act)
        if docker_state["mode"] == "raise":
            raise RuntimeError("compose")
        if docker_state["mode"] == "fail":
            return {"success": False, "error": "no such service", "output": "nope"}
        return {"success": True, "output": "started\n"}

    def _redeploy_svc(server, path, service):
        calls["redeploy"] += 1
        return {"success": True, "output": "up\n"}

    def _inv(*a, **k):
        calls["inv"] += 1
        raise RuntimeError("inventory")

    def _prune(*a, **k):
        calls["prune"] += 1
        raise RuntimeError("prune")

    monkeypatch.setattr("app.services.docker_management.compose_action", _compose)
    monkeypatch.setattr("app.services.docker_management.compose_service_redeploy", _redeploy_svc)
    monkeypatch.setattr("app.services.docker_inventory.invalidate_after_mutation", _inv)
    monkeypatch.setattr("app.services.docker_management.prune_dangling_images", _prune)

    with Session(engine) as session:
        empty, audit_empty = _add_job(session, sid, "container_stop")
        js._execute_container_service(empty.id, sid, audit_empty.id, "", "", "stop")
        unknown, audit_u = _add_job(session, sid, "container_stop")
        js._execute_container_service(unknown.id, sid, audit_u.id, "/home/user/web", "web", "nope")
        gone, audit_g = _add_job(session, sid, "container_start")
        js._execute_container_service(gone.id, 99999, audit_g.id, "/home/user/web", "web", "start")
        docker_state["mode"] = "fail"
        failed, audit_f = _add_job(session, sid, "container_restart")
        js._execute_container_service(failed.id, sid, audit_f.id, "/home/user/web", "web", "restart")
        docker_state["mode"] = "ok"
        started, audit_s = _add_job(session, sid, "container_start")
        js._execute_container_service(started.id, sid, audit_s.id, "/home/user/web", "web", "start")
        redeployed, audit_r = _add_job(session, sid, "container_redeploy")
        js._execute_container_service(redeployed.id, sid, audit_r.id, "/home/user/web", "web", "redeploy")
        docker_state["mode"] = "raise"
        blown, audit_b = _add_job(session, sid, "container_stop")
        js._execute_container_service(blown.id, sid, audit_b.id, "/home/user/web", "web", "stop")
        session.expire_all()
        assert session.get(Job, empty.id).status == "failed"
        assert session.get(Job, started.id).status == "success"
        assert calls["redeploy"] == 1
        assert calls["prune"] == 1
        assert "stop" in calls["action"]


def test_migrate_lock_backup_and_replicate(tmp_path, monkeypatch):
    import app.tasks as tasks

    engine = _engine(tmp_path / "tasks.db")
    _bind(monkeypatch, engine)
    lock_state = {"token": "tok", "dual": ("ta", "tb"), "release_fails": False}
    backup_state = {"mode": "ok", "seen": None}
    flags = {"skip_update": False, "audit_n": 0, "audit_fail_on": 0, "rollback_armed": False}

    def _lock(*a, **k):
        drop_id = lock_state.get("drop_job")
        if drop_id:
            lock_state["drop_job"] = None
            with Session(engine) as s:
                row = s.get(Job, drop_id)
                if row is not None:
                    s.delete(row)
                    s.commit()
            return None
        return lock_state["token"]

    def _dual(*a, **k):
        return lock_state["dual"]

    def _release(*a, **k):
        if lock_state["release_fails"]:
            raise RuntimeError("redis")

    def _run_backup(server, sources_override=None, job_id=None):
        backup_state["seen"] = sources_override
        mode = backup_state["mode"]
        if mode == "cancel":
            with Session(engine) as s:
                job = s.get(Job, job_id)
                job.status = "cancelled"
                s.add(job)
                s.commit()
            return {"results": [{"source": "/home/user", "rc": 0}]}
        if mode == "cancel-raise":
            with Session(engine) as s:
                job = s.get(Job, job_id)
                job.status = "cancelled"
                s.add(job)
                s.commit()
            raise RuntimeError("rsync killed")
        if mode == "transient":
            raise ConnectionError("connection reset by peer")
        if mode == "permanent":
            raise ValueError("permission denied")
        if mode == "raw":
            return "raw-output"
        if mode == "fail":
            return {"error": "disk full", "results": []}
        return {"results": [{"source": "/home/user", "rc": 0}]}

    real_update = tasks._update_job_status

    def _update(job_id, status, extra):
        if flags["skip_update"]:
            return None
        return real_update(job_id, status, extra)

    real_audit = tasks.record_backup_audit_from_job

    def _audit(*a, **k):
        flags["audit_n"] += 1
        if flags["audit_n"] == flags["audit_fail_on"]:
            raise RuntimeError("audit")
        return real_audit(*a, **k)

    real_rollback = Session.rollback

    def _rollback(self):
        if flags["rollback_armed"]:
            flags["rollback_armed"] = False
            raise RuntimeError("rollback")
        return real_rollback(self)

    monkeypatch.setattr(tasks, "try_acquire_server_lock", _lock)
    monkeypatch.setattr(tasks, "try_acquire_dual_server_lock", _dual)
    monkeypatch.setattr(tasks, "release_server_lock", _release)
    monkeypatch.setattr(tasks, "release_dual_server_lock", _release)
    monkeypatch.setattr(tasks, "run_backup", _run_backup)
    monkeypatch.setattr(tasks, "_update_job_status", _update)
    monkeypatch.setattr(tasks, "record_backup_audit_from_job", _audit)
    monkeypatch.setattr(Session, "rollback", _rollback)
    monkeypatch.setattr(
        "app.services.backup_replicate.enqueue_after_host_backup",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("copy")),
    )
    monkeypatch.setattr("app.services.notifications.notify_backup_failed", lambda *a, **k: None)
    monkeypatch.setattr("app.services.notifications.resolve_backup_failed", lambda *a, **k: None)
    monkeypatch.setattr("app.services.schedule_watch.resolve_backup_schedule_missed", lambda *a, **k: None)

    real_sources = Server.get_backup_sources

    def _sources(self):
        if _sources.mode == "raise":
            raise RuntimeError("sources")
        if _sources.mode == "list":
            return [{"source": "/home/user/data"}, {"source": "/var/lib"}]
        return real_sources(self)

    _sources.mode = "real"
    monkeypatch.setattr(Server, "get_backup_sources", _sources)

    with Session(engine) as session:
        src = _server("host-a", "host-a.example.com")
        dst = _server("host-b", "host-b.example.com")
        session.add(src)
        session.add(dst)
        session.commit()
        session.refresh(src)
        session.refresh(dst)
        sid, did = src.id, dst.id

        assert _run(tasks.backup_server, _Self(), 99999, job_id=None)["message"] == "Server not found"
        orphan, _audit = _add_job(session, sid, "backup")
        missing = _run(tasks.backup_server, _Self(), 99999, job_id=orphan.id)
        assert missing["status"] == "error"
        idle, _a = _add_job(session, sid, "backup", status="success")
        assert _run(tasks.backup_server, _Self(), sid, job_id=idle.id)["status"] == "skipped"

        lock_state["token"] = None
        pending, _p = _add_job(session, sid, "backup")
        with pytest.raises(Retry):
            _run(tasks.backup_server, _Self(retries=0), sid, job_id=pending.id)
        lock_state["drop_job"] = pending.id
        try:
            assert _run(tasks.backup_server, _Self(), sid, job_id=pending.id)["status"] == "skipped"
        except Retry:
            pass
        waiting, _w = _add_job(session, sid, "backup")
        timed = _run(
            tasks.backup_server,
            _Self(retries=tasks._LOCK_MAX_RETRIES),
            sid,
            job_id=waiting.id,
        )
        assert timed["status"] == "failed"
        lock_state["token"] = "tok"

        _sources.mode = "list"
        filtered, _f = _add_job(session, sid, "backup")
        ok = _run(tasks.backup_server, _Self("task-ok"), sid, job_id=filtered.id, source_filter="/home/user/data")
        assert ok["status"] == "success"
        assert backup_state["seen"] == [{"source": "/home/user/data"}]

        _sources.mode = "raise"
        flags["audit_fail_on"] = flags["audit_n"] + 2
        flags["rollback_armed"] = True
        audited, _au = _add_job(session, sid, "backup")
        still = _run(tasks.backup_server, _Self("task-audit"), sid, job_id=audited.id, source_filter="/home/user")
        assert still["status"] == "success"
        flags["audit_fail_on"] = 0
        _sources.mode = "real"

        flags["skip_update"] = True
        backup_state["mode"] = "ok"
        warned, _ww = _add_job(session, sid, "backup")
        warned_out = _run(tasks.backup_server, _Self("task-warn"), sid, job_id=warned.id)
        assert warned_out["status"] == "success"
        flags["skip_update"] = False

        backup_state["mode"] = "fail"
        failed, _ff = _add_job(session, sid, "backup")
        assert _run(tasks.backup_server, _Self(), sid, job_id=failed.id)["status"] == "failed"
        backup_state["mode"] = "raw"
        raw_job, _rj = _add_job(session, sid, "backup")
        assert _run(tasks.backup_server, _Self(), sid, job_id=raw_job.id)["status"] == "failed"
        backup_state["mode"] = "cancel"
        cancelled, _cj = _add_job(session, sid, "backup")
        assert _run(tasks.backup_server, _Self(), sid, job_id=cancelled.id)["status"] == "cancelled"
        backup_state["mode"] = "cancel-raise"
        cancelled2, _c2 = _add_job(session, sid, "backup")
        assert _run(tasks.backup_server, _Self(), sid, job_id=cancelled2.id)["status"] == "cancelled"

        backup_state["mode"] = "transient"
        transient, _tj = _add_job(session, sid, "backup")
        with pytest.raises(Retry):
            _run(tasks.backup_server, _Self(retries=1), sid, job_id=transient.id)
        backup_state["mode"] = "permanent"
        lock_state["release_fails"] = True
        permanent, _pj = _add_job(session, sid, "backup")
        assert _run(tasks.backup_server, _Self(), sid, job_id=permanent.id) is None
        lock_state["release_fails"] = False
        backup_state["mode"] = "ok"
        assert _run(tasks.backup_server, _Self("task-nolock"), sid)["status"] == "success"

        direct_calls = []

        def _enqueue(*a, **k):
            direct_calls.append((a, k))
            if _enqueue.fail:
                raise RuntimeError("queue")

        _enqueue.fail = False
        monkeypatch.setattr("app.services.backup_replicate.enqueue_direct_followup", _enqueue)

        def _execute(*a, **k):
            return _execute.result

        _execute.result = {"ok": False, "error": "disk full"}
        monkeypatch.setattr("app.services.backup_replicate.execute", _execute)
        monkeypatch.setattr(
            "app.services.backup_replicate.execute_direct",
            lambda *a, **k: {"ok": False, "error": "invalid_grant"},
        )
        monkeypatch.setattr(
            "app.services.backup_replicate.execute_herder_archive",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("invalid_grant token")),
        )
        monkeypatch.setattr(tasks, "_start_direct_followup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("follow")))

        assert _run(tasks.replicate_backup, _Self(), 99999)["status"] == "skipped"
        done, _d = _add_job(session, None, "backup_replicate", status="success", details="{}")
        assert _run(tasks.replicate_backup, _Self(), done.id)["status"] == "skipped"

        dest = BackupDestination(
            name="Drive",
            provider="drive",
            enabled=True,
            credentials_encrypted="enc",
        )
        session.add(dest)
        session.commit()
        session.refresh(dest)

        missing_dest, _md = _add_job(
            session,
            None,
            "backup_replicate",
            details=json.dumps({"destination_id": 99999}),
        )
        assert _run(tasks.replicate_backup, _Self(), missing_dest.id)["status"] == "failed"

        bad_json, _bj = _add_job(session, None, "backup_replicate", details="not-json")
        assert _run(tasks.replicate_backup, _Self(), bad_json.id)["status"] == "failed"

        plain, _pl = _add_job(
            session,
            None,
            "backup_replicate",
            details=json.dumps({
                "destination_id": dest.id,
                "scope": "checked",
                "direct_after": True,
                "user_id": 1,
            }),
        )
        plain_out = _run(tasks.replicate_backup, _Self(), plain.id)
        assert plain_out["status"] == "failed"
        assert "disk" in plain_out["error"]

        auth_job, _aj = _add_job(
            session,
            None,
            "backup_replicate",
            details=json.dumps({"destination_id": dest.id, "direct_host": True, "scope": "checked"}),
        )
        auth_out = _run(tasks.replicate_backup, _Self(), auth_job.id)
        assert auth_out["status"] == "failed"

        archive, _ar = _add_job(
            session,
            None,
            "backup_replicate",
            details=json.dumps({"destination_id": dest.id, "herder_archive": "piherder.tar.gz"}),
        )
        assert _run(tasks.replicate_backup, _Self(), archive.id)["status"] == "failed"

        def _raise_execute(*a, **k):
            raise RuntimeError("disk full")

        monkeypatch.setattr("app.services.backup_replicate.execute", _raise_execute)
        boom, _bm = _add_job(
            session,
            None,
            "backup_replicate",
            details=json.dumps({"destination_id": dest.id}),
        )
        assert _run(tasks.replicate_backup, _Self(), boom.id)["status"] == "failed"

        tasks._queue_direct_after_folder_failure(session, {})
        tasks._queue_direct_after_folder_failure(session, {"direct_after": True, "direct_host": True})
        tasks._queue_direct_after_folder_failure(session, {"direct_after": True, "herder_archive": "a.tar.gz"})
        tasks._queue_direct_after_folder_failure(session, {"direct_after": True, "destination_id": "nope"})
        tasks._queue_direct_after_folder_failure(session, {"direct_after": True, "destination_id": 0})
        tasks._queue_direct_after_folder_failure(session, {"direct_after": True, "destination_id": 99999})
        tasks._queue_direct_after_folder_failure(
            session,
            {"direct_after": True, "destination_id": dest.id, "scope": "checked", "user_id": 1},
        )
        _enqueue.fail = True
        tasks._queue_direct_after_folder_failure(
            session,
            {"direct_after": True, "destination_id": dest.id, "scope": 3, "user_id": "no"},
        )
        assert direct_calls

        copy_job, _cj = _add_job(
            session,
            None,
            "backup_replicate",
            details=json.dumps({"direct_after": True, "destination_id": dest.id, "direct_host": True}),
        )
        assert tasks.fail_replicate_job(99999, "stopped") is False
        assert tasks.fail_replicate_job(copy_job.id, "") is True
        assert tasks.fail_replicate_job(copy_job.id, "again") is False

        class _TimeLimitExceeded(Exception):
            pass

        class _Boom(Exception):
            pass

        sender = SimpleNamespace(name="app.tasks.housekeeping_job")
        tasks._on_housekeeping_task_failure(sender=SimpleNamespace(name="other"))
        tasks._on_housekeeping_task_failure(sender=sender, args=None)
        tasks._on_housekeeping_task_failure(sender=sender, args=(1, 2))
        tasks._on_housekeeping_task_failure(sender=sender, args=("bad", 0, "no", "retention"))
        hk, audit_hk = _add_job(session, sid, "retention")
        tasks._on_housekeeping_task_failure(
            sender=sender,
            args=(hk.id, 0, audit_hk.id, "retention"),
            exception=_TimeLimitExceeded("stopped"),
        )
        hk2, audit_hk2 = _add_job(session, sid, "herder_backup")
        tasks._on_housekeeping_task_failure(
            sender=sender,
            args=(hk2.id, 0, audit_hk2.id, "herder_backup"),
            exception=_Boom("hit the time limit"),
        )
        session.expire_all()
        assert session.get(Job, hk.id).status == "failed"

        def _exec_ok(*a):
            return None

        def _exec_bad(*a):
            raise RuntimeError("undo failed")

        assert tasks._migrate_dual_lock_task(
            _Self(), 99999, sid, did, 1, job_type="service_migrate_undo", execute=_exec_ok, running_message="restarted", log_name="undo"
        )["status"] == "skipped"
        mig_cancel, audit_mc = _add_job(session, sid, "service_migrate_undo", status="cancelled")
        assert tasks._migrate_dual_lock_task(
            _Self(), mig_cancel.id, sid, did, audit_mc.id, job_type="service_migrate_undo", execute=_exec_ok, running_message="restarted", log_name="undo"
        )["status"] == "cancelled"
        mig_done, audit_md = _add_job(session, sid, "service_migrate_undo", status="success")
        assert tasks._migrate_dual_lock_task(
            _Self(), mig_done.id, sid, did, audit_md.id, job_type="service_migrate_undo", execute=_exec_ok, running_message="restarted", log_name="undo"
        )["reason"] == "success"
        mig_run, audit_mr = _add_job(session, sid, "service_migrate_undo", status="running")
        restarted = tasks._migrate_dual_lock_task(
            _Self(), mig_run.id, sid, did, audit_mr.id, job_type="service_migrate_undo", execute=_exec_ok, running_message="restarted", log_name="undo"
        )
        assert restarted["reason"] == "worker_restart"

        lock_state["dual"] = None
        mig_wait, audit_mw = _add_job(session, sid, "service_migrate_undo")
        with pytest.raises(Retry):
            tasks._migrate_dual_lock_task(
                _Self(retries=0), mig_wait.id, sid, did, audit_mw.id, job_type="service_migrate_undo", execute=_exec_ok, running_message="restarted", log_name="undo"
            )
        mig_time, audit_mt = _add_job(session, sid, "service_migrate_undo")
        timed_m = tasks._migrate_dual_lock_task(
            _Self(retries=tasks._LOCK_MAX_RETRIES),
            mig_time.id,
            sid,
            did,
            audit_mt.id,
            job_type="service_migrate_undo",
            execute=_exec_ok,
            running_message="restarted",
            log_name="undo",
        )
        assert timed_m["status"] == "failed"
        lock_state["dual"] = ("ta", "tb")
        mig_ok, audit_mo = _add_job(session, sid, "service_migrate_undo")
        lock_state["release_fails"] = True
        assert tasks._migrate_dual_lock_task(
            _Self(), mig_ok.id, sid, did, audit_mo.id, job_type="service_migrate_undo", execute=_exec_ok, running_message="restarted", log_name="undo"
        )["status"] == "ok"
        mig_err, audit_me = _add_job(session, sid, "service_migrate_undo")
        err = tasks._migrate_dual_lock_task(
            _Self(), mig_err.id, sid, did, audit_me.id, job_type="service_migrate_undo", execute=_exec_bad, running_message="restarted", log_name="undo"
        )
        assert err["status"] == "error"
        lock_state["release_fails"] = False

        from app.services.jobs import _finish as real_finish

        monkeypatch.setattr("app.services.jobs._finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("finish")))
        mig_close, audit_cl = _add_job(session, sid, "service_migrate_undo")
        closed = tasks._migrate_dual_lock_task(
            _Self(), mig_close.id, sid, did, audit_cl.id, job_type="service_migrate_undo", execute=_exec_bad, running_message="restarted", log_name="undo"
        )
        assert closed["status"] == "error"
        monkeypatch.setattr("app.services.jobs._finish", real_finish)

        monkeypatch.setattr("app.services.jobs_migrate._execute_service_migrate", lambda *a, **k: None)
        move_missing = _run(tasks.service_migrate, _Self(), 99999, sid, did, "web", 1)
        assert move_missing["status"] == "skipped"
        move_cancel, audit_c = _add_job(session, sid, "service_migrate", status="cancelled")
        assert _run(tasks.service_migrate, _Self(), move_cancel.id, sid, did, "web", audit_c.id)["status"] == "cancelled"
        move_skip, audit_s = _add_job(session, sid, "service_migrate", status="success")
        assert _run(tasks.service_migrate, _Self(), move_skip.id, sid, did, "web", audit_s.id)["status"] == "skipped"
        move_run, audit_r = _add_job(session, sid, "service_migrate", status="running")
        assert _run(tasks.service_migrate, _Self(), move_run.id, sid, did, "web", audit_r.id)["reason"] == "worker_restart"

        lock_state["dual"] = None
        move_wait, audit_ww = _add_job(session, sid, "service_migrate")
        with pytest.raises(Retry):
            _run(tasks.service_migrate, _Self(retries=0), move_wait.id, sid, did, "web", audit_ww.id)
        move_time, audit_tt = _add_job(session, sid, "service_migrate")
        assert _run(
            tasks.service_migrate,
            _Self(retries=tasks._LOCK_MAX_RETRIES),
            move_time.id,
            sid,
            did,
            "web",
            audit_tt.id,
        )["status"] == "failed"
        lock_state["dual"] = ("ta", "tb")
        monkeypatch.setattr(
            "app.services.jobs_migrate._execute_service_migrate",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("migrate broke")),
        )
        move_err, audit_e = _add_job(session, sid, "service_migrate")
        lock_state["release_fails"] = True
        broken = _run(tasks.service_migrate, _Self(), move_err.id, sid, did, "web", audit_e.id)
        assert broken["status"] == "error"
        assert "migrate broke" in broken["error"]
        monkeypatch.setattr("app.services.jobs._finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("finish")))
        move_close, audit_close = _add_job(session, sid, "service_migrate")
        closed_move = _run(tasks.service_migrate, _Self(), move_close.id, sid, did, "web", audit_close.id)
        assert closed_move["status"] == "error"


def _admin(engine):
    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as session:
        user = User(
            email="user@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        host_a = _server("host-a", "host-a.example.com")
        host_b = _server("host-b", "host-b.example.com")
        host_c = _server("host-c", "host-c.example.com", docker=False, ssh=False)
        session.add(user)
        session.add(host_a)
        session.add(host_b)
        session.add(host_c)
        session.commit()
        session.refresh(user)
        session.refresh(host_a)
        session.refresh(host_b)
        session.refresh(host_c)
        client.cookies.set("access_token", create_user_access_token(user))
        return client, user, host_a.id, host_b.id, host_c.id


def test_backup_routes(tmp_path, monkeypatch):
    import app.routers.server_backups as sb

    engine = _engine(tmp_path / "routes.db")
    _bind(monkeypatch, engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(sb.job_service, "_revoke_celery_task", lambda *a, **k: None)
    monkeypatch.setattr(sb.backup_svc, "stop_backup", lambda *a, **k: None)
    monkeypatch.setattr(sb.templates_mod.templates, "TemplateResponse", lambda *a, **k: HTMLResponse("restored"))

    client, user, sid, _b, _c = _admin(engine)
    try:
        made = {"source": None}

        def _create(background_tasks, session, server, job_type, user_id=None, source_filter=None, **kwargs):
            made["source"] = source_filter
            if _create.busy:
                job = SimpleNamespace(id=8, status="running", details=json.dumps({"source_filter": source_filter}))
                raise sb.job_service.BackupAlreadyRunning(job)
            return SimpleNamespace(id=7, status="pending", details=json.dumps({"source_filter": source_filter}) if source_filter else "{}")

        _create.busy = False
        monkeypatch.setattr(sb.job_service, "create_job_and_run", _create)

        missing = client.post("/servers/99999/run/backup", follow_redirects=False)
        assert missing.status_code == 404
        async_run = client.post(
            f"/servers/{sid}/run/backup",
            data={"source": "/home/user"},
            headers={"X-PiHerder-Async": "1"},
            follow_redirects=False,
        )
        assert async_run.status_code == 200
        assert async_run.json()["job_id"] == 7
        assert made["source"] == "/home/user"
        referred = client.post(
            f"/servers/{sid}/run/backup",
            headers={"referer": f"http://testserver/servers/{sid}/backups"},
            follow_redirects=False,
        )
        assert referred.status_code == 303
        assert referred.headers["location"].endswith(f"/servers/{sid}/backups")
        plain = client.post(f"/servers/{sid}/run/backup", follow_redirects=False)
        assert plain.status_code == 303
        assert plain.headers["location"].endswith(f"/servers/{sid}")
        _create.busy = True
        busy = client.post(
            f"/servers/{sid}/run/backup",
            headers={"X-PiHerder-Async": "1"},
            follow_redirects=False,
        )
        assert busy.status_code == 409
        assert busy.json()["active"] is True

        assert client.post("/servers/99999/backup/stop", follow_redirects=False).status_code == 404
        with Session(engine) as session:
            job, _audit = _add_job(session, sid, "backup", status="pending")
            jid = job.id
        stopped = client.post(
            f"/servers/{sid}/backup/stop",
            data={"job_id": str(jid), "source": "/home/user"},
            follow_redirects=False,
        )
        assert stopped.status_code == 303
        again = client.post(f"/servers/{sid}/backup/stop", follow_redirects=False)
        assert again.status_code == 303

        sb._backup_progress_http_cache.clear()
        assert client.get("/servers/99999/backup-progress").status_code == 404
        with Session(engine) as session:
            pending, _a = _add_job(session, sid, "backup", status="pending", details=None)
            done, _d = _add_job(session, sid, "backup", status="success", details=None)
            pid, did = pending.id, done.id
        db_prog = client.get(f"/servers/{sid}/backup-progress", params={"job_id": pid})
        assert db_prog.status_code == 200
        assert db_prog.json()["source"] == "db"
        calls = {"n": 0}

        def _prog(hostname):
            calls["n"] += 1
            return {"current": "copying", "log_lines": ["copied /home/user"], "last_updated": 1}

        monkeypatch.setattr(sb.backup_svc, "get_backup_progress", _prog)
        sb._backup_progress_http_cache.clear()
        redis_prog = client.get(f"/servers/{sid}/backup-progress", params={"job_id": did})
        assert redis_prog.status_code == 200
        assert redis_prog.json()["source"] == "redis"
        cached = client.get(f"/servers/{sid}/backup-progress", params={"job_id": did})
        assert cached.json()["current"] == "copying"
        assert calls["n"] == 1

        assert client.get("/servers/99999/backup/logs/stream").status_code == 404
        sleeps = {"n": 0}

        async def _sleep(_seconds):
            sleeps["n"] += 1
            if sleeps["n"] > 1:
                raise RuntimeError("stop stream")
            return None

        monkeypatch.setattr(sb.asyncio, "sleep", _sleep)
        chunks = []
        with client.stream("GET", f"/servers/{sid}/backup/logs/stream") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            try:
                for chunk in resp.iter_text():
                    chunks.append(chunk)
                    if "copied" in chunk:
                        break
            except Exception:
                pass
        assert any("copied" in chunk for chunk in chunks)

        assert client.post("/servers/99999/backup-path-rules", follow_redirects=False).status_code == 404
        rules = client.post(
            f"/servers/{sid}/backup-path-rules",
            data={"allow_paths": "/home/user\n/var/lib", "deny_paths": "/tmp"},
            follow_redirects=False,
        )
        assert rules.status_code == 303
        with Session(engine) as session:
            saved = json.loads(session.get(Server, sid).backup_path_rules)
            assert "/home/user" in saved["allow"]
            assert "/tmp" in saved["deny"]

        assert client.post("/servers/99999/backup/add", data={"new_path": "/home/user"}, follow_redirects=False).status_code == 404
        empty = client.post(f"/servers/{sid}/backup/add", data={"new_path": "  "}, follow_redirects=False)
        assert empty.status_code == 400
        denied = client.post(
            f"/servers/{sid}/backup/add",
            data={"new_path": "/etc/passwd", "dest_name": "etc"},
            follow_redirects=False,
        )
        assert denied.status_code == 400
        added = client.post(
            f"/servers/{sid}/backup/add",
            data={"new_path": "/home/user", "dest_name": "home"},
            follow_redirects=False,
        )
        assert added.status_code == 303
        duplicate = client.post(
            f"/servers/{sid}/backup/add",
            data={"new_path": "/home/user"},
            follow_redirects=False,
        )
        assert duplicate.status_code == 303

        restore_state = {"auth": False, "rc": 0}

        def _restore(server, source, dry_run=True, pull_from=None):
            return {
                "rc": restore_state["rc"],
                "error": "invalid_grant" if restore_state["auth"] else None,
                "summary": "dry" if dry_run else "applied",
                "source": source,
                "dry_run": dry_run,
                "output": "ok",
                "destination": "/home/user",
                "destination_id": getattr(pull_from, "id", None),
                "place": "drive" if pull_from else "local",
                "auth_failed": restore_state["auth"],
            }

        monkeypatch.setattr("app.services.backup_restore.restore_backup_source", _restore)
        monkeypatch.setattr("app.services.backup_restore.list_restore_candidates", lambda server: [])
        with Session(engine) as session:
            dest = BackupDestination(name="Drive", provider="drive", enabled=True, credentials_encrypted="enc")
            session.add(dest)
            session.commit()
            session.refresh(dest)
            dest_id = dest.id
        monkeypatch.setattr(
            "app.services.backup_restore.restore_places",
            lambda session, server: [{"id": dest_id, "place": "drive"}],
        )
        monkeypatch.setattr("app.services.backup_replicate.note_copy_auth_failure", lambda *a, **k: None)

        assert client.post(f"/servers/99999/backup/restore", data={"source": "/home/user"}, follow_redirects=False).status_code == 404
        bad_place = client.post(
            f"/servers/{sid}/backup/restore",
            data={"source": "/home/user", "destination_id": "nope"},
            follow_redirects=False,
        )
        assert bad_place.status_code == 200
        unknown_place = client.post(
            f"/servers/{sid}/backup/restore",
            data={"source": "/home/user", "destination_id": "99999"},
            follow_redirects=False,
        )
        assert unknown_place.status_code == 200
        dry = client.post(
            f"/servers/{sid}/backup/restore",
            data={"source": "/home/user", "dry_run": "1"},
            follow_redirects=False,
        )
        assert dry.status_code == 200
        applied = client.post(
            f"/servers/{sid}/backup/restore",
            data={"source": "/home/user", "dry_run": "0", "confirm": "1", "destination_id": str(dest_id)},
            follow_redirects=False,
        )
        assert applied.status_code == 200
        restore_state["auth"] = True
        restore_state["rc"] = 1
        auth_fail = client.post(
            f"/servers/{sid}/backup/restore",
            data={"source": "/home/user", "dry_run": "1", "destination_id": str(dest_id)},
            follow_redirects=False,
        )
        assert auth_fail.status_code == 200
        assert user.email == "user@example.com"
    finally:
        app.dependency_overrides.clear()


def test_api_files_move_and_features(tmp_path, monkeypatch):
    from app.services import host_files as hf
    import app.services.jobs as js
    from app.services.service_migrate.host_lock import HostLockError

    engine = _engine(tmp_path / "api.db")
    _bind(monkeypatch, engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(hf, "files_enabled", lambda: True)

    listing = {
        "jail": "/home/user",
        "rel": "",
        "truncated": False,
        "entries": [
            {
                "name": "notes.txt",
                "rel": "notes.txt",
                "kind": "file",
                "size": 5,
                "secretish": False,
                "escaped": False,
            }
        ],
    }

    def _list(*a, **k):
        if _list.code:
            raise FilesError(_list.code, "list failed")
        return listing

    _list.code = ""

    def _stat(*a, **k):
        if _stat.code:
            raise FilesError(_stat.code, "stat failed")
        return {"rel": "notes.txt", "size": 5, "kind": "file"}

    _stat.code = ""

    def _iter(*a, **k):
        yield b"hello"

    def _put(server, folder, name, fh, size=None, role=None):
        if _put.code:
            raise FilesError(_put.code, "put failed")
        data = fh.read()
        rel = "/".join(part for part in (folder, name) if part)
        return {"rel": rel, "bytes": len(data), "sha256": "abc", "overwrite": False}

    _put.code = ""

    def _mkdir(*a, **k):
        if _mkdir.code:
            raise FilesError(_mkdir.code, "mkdir failed")
        return {"rel": "docs"}

    _mkdir.code = ""

    def _rename(*a, **k):
        if _rename.code:
            raise FilesError(_rename.code, "rename failed")
        return {"from": "a.txt", "to": "b.txt", "rel": "b.txt"}

    _rename.code = ""

    def _remove(*a, **k):
        if _remove.code:
            raise FilesError(_remove.code, "delete failed")
        return {"rel": "notes.txt", "ok": True}

    _remove.code = ""

    monkeypatch.setattr(hf, "list_dir", _list)
    monkeypatch.setattr(hf, "stat_file", _stat)
    monkeypatch.setattr(hf, "iter_file", _iter)
    monkeypatch.setattr(hf, "put_file", _put)
    monkeypatch.setattr(hf, "mkdir", _mkdir)
    monkeypatch.setattr(hf, "rename", _rename)
    monkeypatch.setattr(hf, "remove", _remove)

    client, user, sid, did, cid = _admin(engine)
    try:
        with Session(engine) as session:
            owner = session.get(User, user.id)
            _files, files_plain = tok_svc.create_api_token(
                session, name="files", created_by=owner, scopes=["read", "files"]
            )
            _read, read_plain = tok_svc.create_api_token(
                session, name="read", created_by=owner, scopes=["read"]
            )
            _edit, edit_plain = tok_svc.create_api_token(
                session, name="edit", created_by=owner, scopes=["read", "edit"]
            )
            _os, os_plain = tok_svc.create_api_token(
                session, name="os", created_by=owner, scopes=["read", "edit", "feature:os"]
            )
            _jobs, jobs_plain = tok_svc.create_api_token(
                session, name="jobs", created_by=owner, scopes=["read", "jobs", "feature:docker"]
            )
            _jobs_os, jobs_os_plain = tok_svc.create_api_token(
                session, name="jobs-os", created_by=owner, scopes=["read", "jobs", "feature:os"]
            )
        files = {"Authorization": f"Bearer {files_plain}"}
        read = {"Authorization": f"Bearer {read_plain}"}
        edit = {"Authorization": f"Bearer {edit_plain}"}
        os_only = {"Authorization": f"Bearer {os_plain}"}
        jobs = {"Authorization": f"Bearer {jobs_plain}"}
        jobs_os = {"Authorization": f"Bearer {jobs_os_plain}"}
        base = f"/api/v1/servers/{sid}/files"

        denied = client.get(base, headers=read)
        assert denied.status_code == 403
        listed = client.get(base, headers=files)
        assert listed.status_code == 200
        assert listed.json()["entries"][0]["name"] == "notes.txt"
        for code, status in (
            ("not_found", 404),
            ("ssh", 502),
            ("denied", 403),
            ("escape", 400),
            ("jail", 400),
            ("other", 400),
        ):
            _list.code = code
            assert client.get(base, headers=files).status_code == status
        _list.code = ""

        monkeypatch.setattr(hf, "files_enabled", lambda: False)
        assert client.get(base, headers=files).status_code == 404
        monkeypatch.setattr(hf, "files_enabled", lambda: True)
        assert client.get("/api/v1/servers/99999/files", headers=files).status_code == 404
        assert client.get(f"/api/v1/servers/{cid}/files", headers=files).status_code == 400

        body = client.get(f"{base}/download", params={"p": "notes.txt"}, headers=files)
        assert body.status_code == 200
        assert body.content == b"hello"
        _stat.code = "not_found"
        assert client.get(f"{base}/download", params={"p": "missing.txt"}, headers=files).status_code == 404
        _stat.code = ""

        uploaded = client.post(
            base,
            headers=files,
            data={"p": "docs"},
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["bytes"] == 5
        _put.code = "denied"
        assert client.post(
            base,
            headers=files,
            data={"p": ""},
            files={"file": ("notes.txt", b"x", "text/plain")},
        ).status_code == 403
        _put.code = ""

        made = client.post(f"{base}/mkdir", headers=files, json={"p": "", "name": "docs"})
        assert made.status_code == 200
        assert made.json()["rel"] == "docs"
        _mkdir.code = "escape"
        assert client.post(f"{base}/mkdir", headers=files, json={"p": "", "name": "../x"}).status_code == 400
        _mkdir.code = ""

        renamed = client.post(
            f"{base}/rename",
            headers=files,
            json={"p": "", "src": "a.txt", "dest": "b.txt"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["to"] == "b.txt"
        _rename.code = "jail"
        assert client.post(
            f"{base}/rename",
            headers=files,
            json={"p": "", "src": "a.txt", "dest": "b.txt"},
        ).status_code == 400
        _rename.code = ""

        deleted = client.delete(base, params={"p": "notes.txt"}, headers=files)
        assert deleted.status_code == 200
        assert deleted.json()["rel"] == "notes.txt"
        _remove.code = "ssh"
        assert client.delete(base, params={"p": "notes.txt"}, headers=files).status_code == 502

        assert client.patch(f"/api/v1/servers/{sid}/features", headers=files, json={"backup": True}).status_code == 403
        assert client.patch("/api/v1/servers/99999/features", headers=edit, json={"backup": True}).status_code == 404
        assert client.patch(f"/api/v1/servers/{sid}/features", headers=edit, json={}).status_code == 400
        assert client.patch(f"/api/v1/servers/{sid}/features", headers=os_only, json={"docker": True}).status_code == 403

        def _sync(_server):
            raise RuntimeError("scheduler")

        monkeypatch.setattr("app.routers.server_patch.sync_server_schedules", _sync)
        changed = client.patch(
            f"/api/v1/servers/{sid}/features",
            headers=edit,
            json={"backup": False, "os_patch": True, "docker": False},
        )
        assert changed.status_code == 200
        assert changed.json()["changed"]["backup"] is False
        assert changed.json()["changed"]["os_patch"] is True
        assert changed.json()["changed"]["docker"] is False

        client.patch(
            f"/api/v1/servers/{sid}/features",
            headers=edit,
            json={"docker": True, "backup": True},
        )
        move = f"/api/v1/servers/{sid}/moves"
        body = {"dest_server_id": did, "project": "web", "confirm": True}
        monkeypatch.setattr(
            "app.services.service_migrate.host_lock.migrate_surface_allowed",
            lambda: False,
        )
        assert client.post(move, headers=jobs, json=body).status_code == 404
        monkeypatch.setattr(
            "app.services.service_migrate.host_lock.migrate_surface_allowed",
            lambda: True,
        )
        assert client.post(move, headers=jobs, json={**body, "confirm": False}).status_code == 400
        assert client.post(
            "/api/v1/servers/99999/moves",
            headers=jobs,
            json=body,
        ).status_code == 404
        assert client.post(
            move,
            headers=jobs,
            json={**body, "dest_server_id": 99999},
        ).status_code == 404
        assert client.post(move, headers=files, json=body).status_code == 403
        assert client.post(move, headers=jobs_os, json=body).status_code == 403
        assert client.post(
            f"/api/v1/servers/{cid}/moves",
            headers=jobs,
            json={**body, "dest_server_id": did},
        ).status_code == 400
        assert client.post(move, headers=jobs, json={**body, "project": "/opt/web"}).status_code == 400
        assert client.post(move, headers=jobs, json={**body, "dest_server_id": sid}).status_code == 400

        def _enqueue(*a, **k):
            mode = _enqueue.mode
            if mode == "value":
                raise ValueError("project missing")
            if mode == "lock":
                raise HostLockError("project is locked", 409)
            if mode == "active":
                raise js.JobAlreadyActive(_enqueue.job)
            if mode == "runtime":
                raise RuntimeError("broker down")
            return _enqueue.job

        with Session(engine) as session:
            active, _audit = _add_job(session, sid, "backup", status="running")
            active_id = active.id
        _enqueue.mode = "ok"
        _enqueue.job = SimpleNamespace(
            id=active_id,
            server_id=sid,
            job_type="service_migrate",
            status="running",
            details="{}",
            created_at=None,
            started_at=None,
            finished_at=None,
            worker_hostname=None,
        )
        monkeypatch.setattr("app.routers.api_v1.job_service.enqueue_service_migrate", _enqueue)
        _enqueue.mode = "value"
        assert client.post(move, headers=jobs, json=body).status_code == 400
        _enqueue.mode = "lock"
        locked = client.post(move, headers=jobs, json=body)
        assert locked.status_code in (400, 409, 500)
        _enqueue.mode = "active"
        conflict = client.post(move, headers=jobs, json=body)
        assert conflict.status_code in (400, 409, 500)
        _enqueue.mode = "runtime"
        assert client.post(move, headers=jobs, json=body).status_code in (500, 503)
        _enqueue.mode = "ok"
        _enqueue.job = SimpleNamespace(
            id=1, status="pending", job_type="service_migrate", server_id=1, details="{}"
        )
        started = client.post(move, headers=jobs, json=body)
        assert started.status_code in (202, 400, 500)
    finally:
        app.dependency_overrides.clear()
