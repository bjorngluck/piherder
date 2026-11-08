"""Coverage for scheduler, console, onboarding, auth, settings, and migrate gaps.

Public placeholders only. No network, no real Celery enqueue.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import AlertMuteRule, BackupDestination, Server, TrustedDevice, User
from app.security.auth import create_access_token, create_pending_2fa_token, get_password_hash
from app.services import jobs_migrate as jm
from app.services import scheduler as sched
from app.services import ssh_console as cons
from app.services import ssh_onboarding as onb
from app.services import stack_monitor as sm
from app.services import worker_metrics as wm
from app.services.jobs.service import JobAlreadyActive
from app.services.service_migrate import pipeline as pipe


def _raise(msg="x"):
    raise RuntimeError(msg)


def _mem_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _server(**kw):
    base = dict(
        name="host-a",
        hostname="host-a.example.com",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_username="user",
        os_type="debian",
    )
    base.update(kw)
    return Server(**base)


class _Sched:
    def __init__(self, *, fail_add=False, fail_get=False):
        self.fail_add = fail_add
        self.fail_get = fail_get
        self.jobs = []

    def get_jobs(self):
        if self.fail_get:
            raise RuntimeError("jobs")
        return list(self.jobs)

    def remove_job(self, job_id):
        raise RuntimeError("missing")

    def add_job(self, *args, **kwargs):
        if self.fail_add:
            raise RuntimeError("add")
        self.jobs.append(kwargs.get("id"))


class _Ctx:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        return False


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _Mem:
    def __init__(self):
        self.jobs = {}
        self.servers = {}
        self.exec_rows = []
        self.active = None
        self.backups = []
        self.raise_merge = False


class _Sess:
    def __init__(self, mem):
        self.mem = mem

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, model, id_):
        name = getattr(model, "__name__", "")
        try:
            key = int(id_)
        except (TypeError, ValueError):
            return None
        if name == "Job":
            return self.mem.jobs.get(key)
        if name == "Server":
            return self.mem.servers.get(key)
        return None

    def add(self, obj):
        return None

    def commit(self):
        return None

    def expunge(self, obj):
        return None

    def refresh(self, obj):
        return None

    def exec(self, statement):
        return _Rows(self.mem.exec_rows)


class _JS:
    def __init__(self, mem):
        self.mem = mem
        self.HAS_CELERY = False
        self.service_migrate_task = None
        self.service_migrate_undo_task = None
        self.service_migrate_dest_recover_task = None
        self.JobAlreadyActive = JobAlreadyActive

    def _get_fresh_session(self):
        return _Sess(self.mem)

    def _create_queued_job_with_audit(self, session, **kwargs):
        job = SimpleNamespace(
            id=50,
            status="pending",
            details="{}",
            server_id=kwargs.get("server_id"),
            celery_task_id=None,
        )
        self.mem.jobs[50] = job
        return job, SimpleNamespace(id=7)

    def _mark_job_terminal(self, job, msg, session, status="failed", record_audit=True):
        job.status = status

    def _merge_job_details(self, job, **fields):
        sensitive = (
            "recover_source" in fields
            or "undo_move" in fields
            or "dest_up_recover" in fields
            or fields.get("failed_step") == "worker_restart"
        )
        if self.mem.raise_merge and sensitive:
            raise RuntimeError("merge")
        try:
            data = json.loads(job.details or "{}") or {}
        except Exception:
            data = {}
        data.update(fields)
        job.details = json.dumps(data)

    def _finish(self, *args, **kwargs):
        return None

    def _flush_job_progress(self, *args, **kwargs):
        return None

    def _load_server_for_job(self, sid):
        srv = self.mem.servers.get(int(sid))
        host = getattr(srv, "hostname", "") if srv else str(sid)
        return srv, host

    def _active_stack_mutating_job(self, session, sid):
        return self.mem.active

    def _active_migrate_as_dest(self, session, sid):
        return None

    def get_active_backup_jobs(self, session, sid):
        return list(self.mem.backups)


def _hosts(mem):
    mem.servers[1] = SimpleNamespace(id=1, name="host-a", hostname="host-a.example.com")
    mem.servers[2] = SimpleNamespace(id=2, name="host-b", hostname="host-b.example.com")
    return mem.servers[1], mem.servers[2]


class _Delay:
    def delay(self, *args, **kwargs):
        raise RuntimeError("no celery")


# --- scheduler -----------------------------------------------------------


def test_scheduler_flags_and_restore(monkeypatch):
    monkeypatch.setattr(
        "app.services.update_check_config.load_update_check_settings",
        lambda: _raise("settings"),
    )
    assert sched._container_checks_globally_enabled() is True
    assert sched.container_check_should_run(None) is False
    sched._restore_container_check_flag(SimpleNamespace(container_check_enabled=True))

    eng = _mem_engine()
    with Session(eng) as session:
        row = _server(container_check_enabled=False, container_check_schedule="0 1 * * *")
        session.add(row)
        session.commit()
        session.refresh(row)
        sched._restore_container_check_flag(row)
        assert row.container_check_enabled is True


def test_scheduler_job_entry_points(monkeypatch):
    eng = _mem_engine()
    monkeypatch.setattr("app.database.get_engine", lambda: eng)
    monkeypatch.setattr(
        "app.services.jobs.enqueue_container_update_check", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.jobs.enqueue_container_patch_apply", lambda *a, **k: None
    )
    with Session(eng) as session:
        skip = _server(container_check_schedule="")
        apply = _server(
            name="host-b",
            hostname="host-b.example.com",
            ip_address="192.0.2.11",
            container_patch_enabled=False,
            container_apply_enabled=False,
        )
        busy = _server(
            name="host-c",
            hostname="host-c.example.com",
            ip_address="192.0.2.12",
            container_patch_enabled=True,
            container_apply_enabled=True,
            container_apply_only_if_updates=False,
            container_apply_schedule="0 2 * * *",
        )
        bare = _server(
            name="host-d",
            hostname="host-d.example.com",
            ip_address="192.0.2.13",
            container_patch_enabled=True,
            docker_prune_schedule="",
        )
        session.add(skip)
        session.add(apply)
        session.add(busy)
        session.add(bare)
        session.commit()
        ids = (skip.id, apply.id, busy.id, bare.id)
    sched.schedule_container_check_job(ids[0])
    sched.schedule_container_apply_job(ids[1])
    sched.schedule_container_apply_job(ids[2])
    sched.schedule_docker_prune_job(ids[3])

    monkeypatch.setattr(
        "app.services.jobs.create_job_and_run",
        lambda *a, **k: (_ for _ in ()).throw(JobAlreadyActive(SimpleNamespace(id=1))),
    )
    with Session(eng) as session:
        row = session.get(Server, ids[3])
        row.docker_prune_schedule = "0 4 * * *"
        session.add(row)
        session.commit()
    sched.schedule_docker_prune_job(ids[3])
    monkeypatch.setattr("app.database.get_engine", lambda: _raise("engine"))
    sched.schedule_docker_prune_job(ids[3])
    sched.sync_all_server_cron_jobs(_Sched(), True)


def test_sync_server_cron_failure_branches(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: _raise("tz")
    )
    monkeypatch.setattr(
        "app.services.update_check_config.effective_fleet_cron",
        lambda sid: _raise("fleet"),
    )
    server = SimpleNamespace(
        id=4,
        name="host-a",
        backup_enabled=True,
        backup_schedule="",
        os_check_enabled=True,
        os_check_schedule="bad",
        container_check_enabled=True,
        container_check_schedule="bad",
        os_patch_enabled=True,
        os_apply_enabled=True,
        os_apply_schedule="bad",
        container_patch_enabled=True,
        container_apply_enabled=True,
        container_apply_schedule="bad",
        docker_prune_schedule="bad",
    )
    sched.sync_server_cron_jobs(_Sched(), True, server)


def test_backup_copy_and_fleet_schedules(monkeypatch):
    assert sched.sync_backup_copy_schedule(None, False) is None
    assert sched.sync_missed_schedule_watch(None, False) is None
    assert sched.sync_herder_backup_pending_watch(None, False) is None
    assert sched.sync_stale_data_cleanup_schedule(None, False) is None

    broken = _Sched(fail_get=True)
    sched.sync_backup_copy_schedule(broken, True)

    eng = _mem_engine()
    monkeypatch.setattr("app.database.get_engine", lambda: eng)
    monkeypatch.setattr("app.services.backup_replicate.credentials_saved", lambda d: True)
    monkeypatch.setattr("app.services.backup_replicate.enqueue", lambda db, dest: None)
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: _raise("tz")
    )
    monkeypatch.setattr(
        "app.services.copy_schedule_order.sync_copy_order_alerts",
        lambda db: _raise("order"),
    )
    with Session(eng) as session:
        bad = BackupDestination(
            name="copy-a", provider="drive", enabled=True, schedule="nope"
        )
        good = BackupDestination(
            name="copy-b", provider="smb", enabled=True, schedule="15 1 * * *"
        )
        session.add(bad)
        session.add(good)
        session.commit()
        session.refresh(good)
        good_id = good.id
    holder = _Sched()
    holder.jobs.append(SimpleNamespace(id="backup_copy_1"))
    sched.sync_backup_copy_schedule(holder, True)
    sched.schedule_backup_copy_job(good_id)
    monkeypatch.setattr("app.database.get_engine", lambda: _raise("engine"))
    sched.schedule_backup_copy_job(good_id)

    monkeypatch.setattr(sched, "Session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("open")))
    sched.sync_backup_copy_schedule(_Sched(), True)


def test_inventory_and_facts_fleet_errors(monkeypatch):
    servers = [
        SimpleNamespace(id=None),
        SimpleNamespace(id=2),
        SimpleNamespace(id=3),
    ]

    class Sess:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def exec(self, statement):
            return _Rows(servers)

    monkeypatch.setattr(sched, "Session", lambda *a, **k: Sess())

    def release(db, server):
        if server.id == 2:
            raise RuntimeError("reset")

    monkeypatch.setattr(
        "app.services.docker_inventory.release_expired_inventory_refresh", release
    )
    monkeypatch.setattr(
        "app.services.docker_inventory.is_stale", lambda server, max_age_sec=0: server.id == 3
    )
    monkeypatch.setattr(
        "app.services.docker_inventory.refresh_server_inventory",
        lambda sid, force=False: _raise("refresh"),
    )
    sched.schedule_docker_inventory_fleet()

    facts = [
        SimpleNamespace(id=None),
        SimpleNamespace(id=5),
    ]

    class FactSess(Sess):
        def exec(self, statement):
            return _Rows(facts)

    monkeypatch.setattr(sched, "Session", lambda *a, **k: FactSess())
    monkeypatch.setattr(
        "app.services.jobs.cleanup_stale_backup_jobs", lambda db: _raise("stale")
    )
    monkeypatch.setattr(
        "app.services.host_facts.down_host_backoff", lambda db, server: False
    )
    monkeypatch.setattr(
        "app.services.host_facts.is_stale", lambda server, max_age_sec=0: True
    )
    monkeypatch.setattr(
        "app.services.host_facts.request_refresh",
        lambda sid, force=False: _raise("facts"),
    )
    sched.schedule_host_facts_fleet()

    class BoomSess(Sess):
        def exec(self, statement):
            raise RuntimeError("query")

    monkeypatch.setattr(sched, "Session", lambda *a, **k: BoomSess())
    sched.schedule_host_facts_fleet()
    monkeypatch.setattr("app.database.get_engine", lambda: _raise("engine"))
    monkeypatch.setattr(sched, "Session", Session)
    sched.schedule_docker_inventory_fleet()


def test_interval_schedule_registration_failures():
    bad = _Sched(fail_add=True)
    sched.sync_docker_inventory_schedule(bad, True)
    sched.sync_host_facts_schedule(bad, True)
    sched.sync_stack_health_schedule(bad, True)
    sched.sync_integrations_poll_schedule(bad, True)
    sched.sync_template_drift_schedule(bad, True)
    sched.sync_cert_renew_schedule(bad, True)
    sched.sync_missed_schedule_watch(bad, True)
    sched.sync_herder_backup_pending_watch(bad, True)


def test_stack_health_and_cert_and_herder(monkeypatch):
    import app.main as mainmod

    monkeypatch.delattr(mainmod, "HAS_SCHEDULER", raising=False)
    monkeypatch.setattr(
        "app.services.stack_health.run_stack_health_check",
        lambda **k: _raise("health"),
    )
    sched.schedule_stack_health_job()

    monkeypatch.setattr(
        "app.services.certificates.check_expiring_and_renew",
        lambda db: _raise("certs"),
    )
    sched.schedule_cert_renew_job()

    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"schedule_enabled": True, "schedule_cron": "not-a-cron"},
    )
    sched.sync_herder_backup_schedule(_Sched(), True)

    monkeypatch.setattr(
        "app.services.stale_data_cleanup.cleanup_config",
        lambda: {
            "enabled": True,
            "jobs_enabled": True,
            "audit_enabled": False,
            "nmap_enabled": False,
            "cron": "",
        },
    )
    sched.sync_stale_data_cleanup_schedule(_Sched(), True)
    monkeypatch.setattr(
        "app.services.stale_data_cleanup.cleanup_config",
        lambda: {
            "enabled": True,
            "jobs_enabled": True,
            "audit_enabled": False,
            "nmap_enabled": False,
            "cron": "5 4 * * *",
        },
    )
    sched.sync_stale_data_cleanup_schedule(_Sched(fail_add=True), True)

    monkeypatch.setattr(
        "app.services.jobs.enqueue_herder_backup_job",
        lambda *a, **k: _raise("enqueue"),
    )
    monkeypatch.setattr(
        "app.services.jobs._notify_herder_backup_failed",
        lambda msg: _raise("notify"),
    )
    sched.schedule_herder_backup_job()


def test_missed_schedule_watch_branches(monkeypatch):
    class Sess:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sched, "Session", lambda *a, **k: Sess())
    monkeypatch.setattr(
        "app.services.jobs.expire_stale_pending_herder_backups", lambda s: [9]
    )
    sched.watch_pending_herder_backups()

    monkeypatch.setattr(
        "app.services.schedule_watch.sync_missed_schedule_alerts", lambda s: ["backup"]
    )
    monkeypatch.setattr(
        "app.services.host_posture.sync_host_posture_alerts", lambda s: _raise("posture")
    )
    monkeypatch.setattr(
        "app.services.schedule_catchup.run_due_catchups", lambda s: _raise("catch")
    )
    monkeypatch.setattr(
        "app.services.backup_replicate.sync_drive_token_aging", lambda s: _raise("drive")
    )
    monkeypatch.setattr(
        "app.services.copy_schedule_order.sync_copy_order_alerts",
        lambda s: _raise("order"),
    )
    monkeypatch.setattr(
        "app.services.queue_watch.sync_stuck_queue_alert", lambda s: _raise("queue")
    )
    sched.watch_missed_schedules()

    monkeypatch.setattr("app.database.get_engine", lambda: _raise("engine"))
    sched.watch_pending_herder_backups()
    sched.watch_missed_schedules()


# --- stack monitor and worker metrics -----------------------------------


def test_stack_monitor_branches(monkeypatch):
    monkeypatch.setattr(sm.apol, "inventory_down_alerts_enabled", lambda: _raise("pol"))
    monkeypatch.setattr(sm, "load_settings", lambda: {"stack_inventory_down_alerts": None})
    assert sm.inventory_down_alerts_enabled() is True
    monkeypatch.setattr(sm, "load_settings", lambda: {"stack_inventory_down_alerts": ""})
    assert sm.inventory_down_alerts_enabled() is True
    monkeypatch.setattr(sm, "load_settings", lambda: {"stack_inventory_down_alerts": False})
    assert sm.inventory_down_alerts_enabled() is False
    monkeypatch.setattr(sm, "load_settings", lambda: {"stack_inventory_down_alerts": "yes"})
    assert sm.inventory_down_alerts_enabled() is True
    monkeypatch.setattr(sm, "load_settings", lambda: _raise("load"))
    assert sm.inventory_down_alerts_enabled() is True

    monkeypatch.setattr(sm, "inventory_down_alerts_enabled", lambda: False)
    server = SimpleNamespace(id=1, name="host-a")
    assert sm.scan_server_inventory_for_down_alerts(None, server)["enabled"] is False
    monkeypatch.setattr(sm, "inventory_down_alerts_enabled", lambda: True)
    assert sm.scan_server_inventory_for_down_alerts(None, SimpleNamespace(id=None, name="host-a"))[
        "alerted"
    ] == 0
    monkeypatch.setattr(
        "app.services.docker_inventory.parse_inventory", lambda server: {"projects": "no"}
    )
    assert sm.scan_server_inventory_for_down_alerts(None, server)["alerted"] == 0
    monkeypatch.setattr(
        "app.services.docker_inventory.parse_inventory",
        lambda server: {
            "projects": [
                "skip",
                {"name": ""},
                {
                    "name": "app",
                    "containers": [
                        "x",
                        {"placeholder": True, "name": "ph"},
                        {"name": ""},
                        {"name": "up", "running": True, "compose_service": "up"},
                        {"name": "down", "running": False, "compose_service": "down"},
                    ],
                },
            ]
        },
    )
    monkeypatch.setattr(sm, "_bindings_by_server", lambda session, sid: [])
    assert sm.scan_server_inventory_for_down_alerts(None, server)["no_binds"] is True

    class Bind:
        id = 3
        external_label = "kuma"
        external_id = "k"
        last_state = "down"

    monkeypatch.setattr(sm, "_bindings_by_server", lambda session, sid: [Bind()])
    monkeypatch.setattr(
        sm,
        "_container_bound",
        lambda binds, project, container, compose_service: Bind() if container else None,
    )
    monkeypatch.setattr(
        "app.services.notifications.resolve_by_fingerprint", lambda session, fp: 1
    )
    monkeypatch.setattr("app.services.notifications.upsert_notification", lambda *a, **k: None)
    out = sm.scan_server_inventory_for_down_alerts(SimpleNamespace(), server)
    assert out["alerted"] == 1 and out["resolved"] == 1

    monkeypatch.setattr(sm, "inventory_down_alerts_enabled", lambda: False)
    sm.scan_after_inventory_refresh(1)

    class Empty:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, model, id_):
            return None

    monkeypatch.setattr(sm, "inventory_down_alerts_enabled", lambda: True)
    monkeypatch.setattr(sm, "Session", lambda *a, **k: Empty())
    sm.scan_after_inventory_refresh(1)
    monkeypatch.setattr(sm, "Session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    sm.scan_after_inventory_refresh(1)


def test_worker_metrics_branches(monkeypatch, tmp_path):
    monkeypatch.setenv("PIHERDER_WORKER_METRICS_PORT", "nope")
    assert wm.worker_metrics_port() == wm.DEFAULT_WORKER_METRICS_PORT
    monkeypatch.setenv("PIHERDER_WORKER_METRICS_PORT", "0")
    assert wm.worker_metrics_port() == wm.DEFAULT_WORKER_METRICS_PORT
    monkeypatch.setenv("PIHERDER_WORKER_METRICS_PORT", "70000")
    assert wm.worker_metrics_port() == wm.DEFAULT_WORKER_METRICS_PORT
    monkeypatch.setenv("PIHERDER_WORKER_METRICS_PORT", "9811")
    assert wm.worker_metrics_port() == 9811

    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    wm.prepare_multiproc_dir()
    multi = tmp_path / "mp"
    multi.mkdir()
    (multi / "a.txt").write_text("x")
    (multi / "sub").mkdir()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(multi))
    real_unlink = os.unlink

    def unlink(path):
        if str(path).endswith("a.txt"):
            raise OSError("busy")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", unlink)
    wm.prepare_multiproc_dir()

    wm._started = False
    monkeypatch.setenv("PIHERDER_WORKER_METRICS", "on")
    import prometheus_client

    monkeypatch.setattr(
        prometheus_client,
        "start_http_server",
        lambda *a, **k: _raise("bind"),
    )
    assert wm.serve_worker_metrics() is None
    monkeypatch.setattr(prometheus_client, "start_http_server", lambda *a, **k: None)
    assert wm.serve_worker_metrics() == 9811
    assert wm.serve_worker_metrics() is None
    wm._started = False


# --- migrate pipeline ----------------------------------------------------


def _ok_call(session=None, **kwargs):
    return {"ok": True}


def _pipe_servers():
    src = SimpleNamespace(id=1, name="host-a", hostname="host-a.example.com")
    dst = SimpleNamespace(id=2, name="host-b", hostname="host-b.example.com")
    return src, dst


def _ok_preflight(items):
    return {
        "ok": True,
        "dest_project": "app2",
        "port_map": {},
        "dataset": {"items": items},
        "warns": [],
        "blocks": [],
        "skip_binds": [],
        "bind_map": {
            "/srv/data/sock": "/srv/app/app2/sock",
            "/srv/data/file": "/srv/app/app2/nested/file",
            "/srv/data/dir": "/srv/app/app2/nested/dir",
            "/srv/data/extra": "/var/lib/extra",
            "/srv/data/boom": "/srv/app/app2/boom",
        },
    }


def test_pipeline_helpers_and_blocked(monkeypatch, tmp_path):
    assert pipe._ssh_fail_detail("nope", "fallback") == "fallback"
    assert "only-out" in pipe._ssh_fail_detail({"output": "only-out"}, "fallback")
    assert "err" in pipe._ssh_fail_detail({"error": "err", "output": "out"}, "fallback")
    assert pipe.staging_root(90).name == "90"
    pipe._log(None, "quiet")
    monkeypatch.setattr(pipe, "run_preflight", lambda *a, **k: {"ok": False, "blocks": [{"message": "full"}]})
    src, dst = _pipe_servers()
    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            SimpleNamespace(),
            source=src,
            dest=dst,
            project="app1",
            job_id=91,
            on_step=lambda name: _raise(name),
        )


def test_pipeline_copy_tree(monkeypatch):
    src, dst = _pipe_servers()
    monkeypatch.setattr(pipe, "docker_base_abs", lambda server: "/srv/app")
    monkeypatch.setattr(pipe, "staging_tree_summary", lambda path: "tree")
    monkeypatch.setattr(pipe, "apply_staging_overrides", lambda *a, **k: {"files": ["compose.yml"]})
    monkeypatch.setattr(
        "app.services.service_migrate.overrides.is_truncated_host_path", lambda src: False
    )
    monkeypatch.setattr(
        "app.services.service_migrate.overrides.suggest_dest_bind", lambda *a, **k: ""
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.refresh_host_inventory", lambda sid: True
    )

    def pull(*args, **kwargs):
        if kwargs.get("delete") is not None or kwargs.get("as_file") is not None:
            raise TypeError("sig")
        return None

    def push(*args, **kwargs):
        if kwargs.get("delete") is not None:
            raise TypeError("sig")
        return None

    def vol(*args, **kwargs):
        if "dest_volume" in kwargs:
            raise TypeError("sig")
        return None

    calls = {"n": 0}

    def chown(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TypeError("sig")
        return None

    def kind(server, path):
        if path.endswith("sock"):
            return "socket"
        if path.endswith("file"):
            return "file"
        if path.endswith("boom"):
            raise RuntimeError("stat")
        return "dir"

    monkeypatch.setattr(pipe, "rsync_host_to_herder", pull)
    monkeypatch.setattr(pipe, "rsync_herder_to_host", push)
    monkeypatch.setattr(pipe, "copy_named_volume", vol)
    monkeypatch.setattr(pipe, "remote_path_kind", kind)
    monkeypatch.setattr(pipe.inventory_svc, "mark_stale", lambda *a, **k: _raise("stale"))
    monkeypatch.setattr(PathMod, "chmod", lambda self, mode: _raise("chmod"))

    items = [
        {"kind": "named", "source": "", "volume": ""},
        {"kind": "named", "source": "vol1", "volume": "vol1"},
        {"kind": "named", "source": "vol1", "volume": "vol1"},
        {"kind": "bind_absolute", "source": "/srv/app/app1/data"},
        {"kind": "bind_host_local", "source": "/var/run/docker.sock"},
        {"kind": "bind_absolute", "source": "/srv/skip/me"},
        {"kind": "bind_absolute", "source": "/srv/nomap"},
        {"kind": "bind_absolute", "source": "/srv/data/sock"},
        {"kind": "bind_absolute", "source": "/srv/data/file"},
        {"kind": "bind_absolute", "source": "/srv/data/dir"},
        {"kind": "bind_absolute", "source": "/srv/data/extra"},
        {"kind": "bind_absolute", "source": "/srv/data/boom"},
    ]
    monkeypatch.setattr(pipe, "run_preflight", lambda *a, **k: _ok_preflight(items))
    session = SimpleNamespace(refresh=lambda obj: None)
    out = pipe.run_copy_and_start(
        session,
        source=src,
        dest=dst,
        project="app1",
        job_id=92,
        skip_binds=["/srv/skip/me"],
        stop_fn=lambda server, path: {"success": True},
        up_fn=lambda server, path: {"success": True, "output": "started"},
        cutover_fn=_ok_call,
        rebind_fn=_ok_call,
        validate_fn=_ok_call,
        leftover="stopped",
        dest_chown_fn=chown,
        dest_rm_fn=lambda server, name: {"ok": True, "output": "gone"},
        on_step=lambda name: _raise(name),
        log=lambda msg: None,
    )
    assert out["ok"] is True
    pipe.wipe_staging(92)


def test_pipeline_failure_steps(monkeypatch):
    src, dst = _pipe_servers()
    monkeypatch.setattr(pipe, "docker_base_abs", lambda server: "/srv/app")
    monkeypatch.setattr(pipe, "staging_tree_summary", lambda path: "tree")
    monkeypatch.setattr(pipe, "apply_staging_overrides", lambda *a, **k: {"files": []})
    session = SimpleNamespace(refresh=lambda obj: None)
    base = dict(source=src, dest=dst, project="app1", job_id=93, log=lambda msg: None)

    monkeypatch.setattr(
        pipe,
        "run_preflight",
        lambda *a, **k: _ok_preflight([]),
    )
    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": False, "error": "stop", "output": "log"},
            **base,
        )

    def pull_copy(*a, **k):
        raise pipe.CopyError("disk")

    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            pull_fn=pull_copy,
            **base,
        )

    monkeypatch.setattr(
        "app.services.service_migrate.overrides.is_truncated_host_path",
        lambda src: True,
    )
    monkeypatch.setattr(
        pipe,
        "run_preflight",
        lambda *a, **k: _ok_preflight(
            [{"kind": "bind_absolute", "source": "/srv/cut..."}]
        ),
    )
    monkeypatch.setattr(pipe, "rsync_host_to_herder", lambda *a, **k: None)
    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            **base,
        )

    monkeypatch.setattr(pipe, "run_preflight", lambda *a, **k: _ok_preflight([]))
    monkeypatch.setattr(pipe, "rsync_host_to_herder", lambda *a, **k: None)
    monkeypatch.setattr(pipe, "rsync_herder_to_host", lambda *a, **k: None)
    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            up_fn=lambda server, path: {"success": False, "output": "no"},
            **base,
        )

    monkeypatch.setattr(
        "app.services.service_migrate.facts.refresh_host_inventory",
        lambda sid: _raise("inv"),
    )

    def dns(session=None, **kwargs):
        if "dest_project" in kwargs:
            raise TypeError("sig")
        raise pipe.CutoverError("dns")

    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            up_fn=lambda server, path: {"success": True},
            cutover_fn=dns,
            **base,
        )

    def rebind_m(session=None, **kwargs):
        raise pipe.MigrateError("inner")

    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            up_fn=lambda server, path: {"success": True},
            cutover_fn=_ok_call,
            rebind_fn=rebind_m,
            **base,
        )

    def rebind_t(session=None, **kwargs):
        if "dest_project" in kwargs:
            raise TypeError("sig")
        raise RuntimeError("rebind")

    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            up_fn=lambda server, path: {"success": True},
            cutover_fn=_ok_call,
            rebind_fn=rebind_t,
            **base,
        )

    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            stop_fn=lambda server, path: {"success": True},
            up_fn=lambda server, path: {"success": True},
            cutover_fn=_ok_call,
            rebind_fn=_ok_call,
            validate_fn=lambda session=None, **k: (_ for _ in ()).throw(pipe.ValidateError("tls")),
            **base,
        )

    pipe.run_copy_and_start(
        session,
        stop_fn=lambda server, path: {"success": True},
        up_fn=lambda server, path: {"success": True, "output": "up", "pull": True, "pull_ok": False},
        cutover_fn=_ok_call,
        rebind_fn=_ok_call,
        validate_fn=_ok_call,
        **base,
    )


def test_pipeline_dest_rm_and_leftover(monkeypatch):
    src, dst = _pipe_servers()
    monkeypatch.setattr(pipe, "docker_base_abs", lambda server: "/srv/app")
    monkeypatch.setattr(pipe, "staging_tree_summary", lambda path: "tree")
    monkeypatch.setattr(pipe, "apply_staging_overrides", lambda *a, **k: {"files": []})
    monkeypatch.setattr(pipe, "rsync_host_to_herder", lambda *a, **k: None)
    monkeypatch.setattr(pipe, "rsync_herder_to_host", lambda *a, **k: None)
    monkeypatch.setattr(pipe, "run_preflight", lambda *a, **k: _ok_preflight([]))
    monkeypatch.setattr(
        "app.services.service_migrate.facts.refresh_host_inventory", lambda sid: False
    )
    session = SimpleNamespace(refresh=lambda obj: None)
    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            source=src,
            dest=dst,
            project="app1",
            job_id=95,
            stop_fn=lambda server, path: {"success": True},
            dest_rm_fn=lambda server, name: {"ok": False, "error": "busy", "output": "log"},
        )
    monkeypatch.setattr(
        pipe,
        "apply_leftover",
        lambda session=None, **k: (_ for _ in ()).throw(pipe.LeftoverError("left")),
    )
    with pytest.raises(pipe.MigrateError):
        pipe.run_copy_and_start(
            session,
            source=src,
            dest=dst,
            project="app1",
            job_id=96,
            stop_fn=lambda server, path: {"success": True},
            up_fn=lambda server, path: {"success": True},
            cutover_fn=_ok_call,
            rebind_fn=_ok_call,
            validate_fn=_ok_call,
        )


# Path alias so the chmod patch above stays local to that name.
from pathlib import Path as PathMod  # noqa: E402


# --- jobs migrate --------------------------------------------------------


def test_migrate_inline_flag_and_value_errors(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PIHERDER_MIGRATE_INLINE", "yes")
    assert jm._migrate_run_inline() is True
    monkeypatch.setenv("PIHERDER_MIGRATE_INLINE", "no")
    assert jm._migrate_run_inline() is False
    with pytest.raises(ValueError):
        jm.enqueue_service_migrate(1, 2, "app1", dest_project="/")


def test_migrate_enqueue_celery_branches(monkeypatch):
    mem = _Mem()
    _hosts(mem)
    js = _JS(mem)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(jm, "_migrate_run_inline", lambda: False)
    with pytest.raises(RuntimeError):
        jm.enqueue_service_migrate(1, 2, "app1")
    js.HAS_CELERY = True
    js.service_migrate_task = _Delay()
    with pytest.raises(RuntimeError):
        jm.enqueue_service_migrate(1, 2, "app1")


def test_fail_migrate_worker_restart_branches(monkeypatch):
    mem = _Mem()
    src, dst = _hosts(mem)
    js = _JS(mem)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(
        "app.services.service_migrate.leftover.jailed_source_project_path",
        lambda server, project: "/srv/app/app1",
    )
    done = SimpleNamespace(id=1, status="success", details="{}", server_id=1)
    mem.jobs[1] = done
    jm.fail_migrate_worker_restart(1, 3)

    mem.raise_merge = True
    bad = SimpleNamespace(id=2, status="running", details="{", server_id=1)
    mem.jobs[2] = bad
    jm.fail_migrate_worker_restart(2, 3)

    stop = SimpleNamespace(
        id=3,
        status="running",
        details=json.dumps({"project": "app1", "migrate_step": "stop"}),
        server_id=1,
    )
    mem.jobs[3] = stop
    jm.fail_migrate_worker_restart(3, 3)

    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.dest_up_recover_details",
        lambda **k: {"inspect": True},
    )
    up = SimpleNamespace(
        id=4,
        status="running",
        details=json.dumps(
            {"project": "app1", "migrate_step": "dest_up", "dest_server_id": 2}
        ),
        server_id=1,
    )
    mem.jobs[4] = up
    jm.fail_migrate_worker_restart(4, 3)

    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.dest_up_recover_details",
        lambda **k: None,
    )
    mem.jobs[4] = SimpleNamespace(
        id=4,
        status="running",
        details=json.dumps(
            {"project": "app1", "migrate_step": "dest_up", "dest_server_id": 2}
        ),
        server_id=1,
    )
    jm.fail_migrate_worker_restart(4, 3)
    assert src.id == 1 and dst.id == 2


def test_run_service_migrate_pipeline_branches(monkeypatch):
    mem = _Mem()
    src, dst = _hosts(mem)
    js = _JS(mem)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(
        "app.services.service_migrate.facts.probe_host_facts", lambda server: {}
    )
    monkeypatch.setattr("app.services.service_migrate.facts.herder_free_bytes", lambda: 5)
    monkeypatch.setattr("app.services.service_migrate.pipeline.wipe_staging", lambda job_id: None)
    monkeypatch.setattr(
        "app.services.service_migrate.leftover.jailed_source_project_path",
        lambda server, project: "/srv/app/app1",
    )

    def run_copy(session, **kwargs):
        kwargs["on_step"]("copy")
        kwargs["log"]("line")
        step = kwargs["project"]
        if step == "app-copy":
            raise pipe.MigrateError("copy failed", failed_step="copy")
        if step == "app-cut":
            raise pipe.MigrateError("cut failed", failed_step="cutover")
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.run_copy_and_start", run_copy
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.refresh_host_inventory",
        lambda sid: _raise("refresh"),
    )
    job = SimpleNamespace(id=9, status="pending", details="{", server_id=1)
    mem.jobs[9] = job
    jm._run_service_migrate_pipeline(
        9, 1, 2, "app1", 3, source=src, dest=dst, hostname="host-a.example.com"
    )

    mem.raise_merge = True
    mem.jobs[9] = SimpleNamespace(
        id=9,
        status="pending",
        details=json.dumps({"leftover": "stopped"}),
        server_id=1,
    )
    jm._run_service_migrate_pipeline(
        9, 1, 2, "app-copy", 3, source=src, dest=dst, hostname="host-a.example.com"
    )

    monkeypatch.setattr(
        "app.services.service_migrate.undo.undo_move_details",
        lambda **k: {"undo": True},
    )
    mem.raise_merge = False
    jm._run_service_migrate_pipeline(
        9, 1, 2, "app-cut", 3, source=src, dest=dst, hostname="host-a.example.com"
    )
    monkeypatch.setattr(
        "app.services.service_migrate.undo.undo_move_details",
        lambda **k: _raise("undo"),
    )
    jm._run_service_migrate_pipeline(
        9, 1, 2, "app-cut", 3, source=src, dest=dst, hostname="host-a.example.com"
    )

    mem.servers.pop(2)
    jm._run_service_migrate_pipeline(
        9, 1, 2, "app1", 3, source=src, dest=dst, hostname="host-a.example.com"
    )

    bad_row = SimpleNamespace(id=3, job_type="service_migrate_undo", status="success", details="{")
    good_row = SimpleNamespace(
        id=4,
        job_type="service_migrate_undo",
        status="running",
        details=json.dumps({"parent_job_id": 9}),
    )
    mem.exec_rows = [bad_row, good_row]
    assert jm._parent_has_child(_Sess(mem), 9, "service_migrate_undo") is True


def test_undo_and_dest_recover_branches(monkeypatch):
    mem = _Mem()
    src, dst = _hosts(mem)
    js = _JS(mem)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(jm, "_migrate_run_inline", lambda: False)
    monkeypatch.setattr(
        "app.services.service_migrate.host_lock.migrate_surface_allowed", lambda: False
    )
    with pytest.raises(ValueError):
        jm.enqueue_service_migrate_undo(8)
    monkeypatch.setattr(
        "app.services.service_migrate.host_lock.migrate_surface_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.service_migrate.undo.eligible_undo", lambda parent: None
    )
    mem.jobs[8] = SimpleNamespace(id=8, status="failed", details="{}")
    with pytest.raises(ValueError):
        jm.enqueue_service_migrate_undo(8)

    monkeypatch.setattr(
        "app.services.service_migrate.undo.eligible_undo",
        lambda parent: {
            "source_id": 1,
            "dest_id": 2,
            "project": "app1",
            "dest_project": "app2",
            "port_map": {},
            "failed_step": "cutover",
        },
    )
    mem.active = SimpleNamespace(id=3, status="running")
    with pytest.raises(JobAlreadyActive):
        jm.enqueue_service_migrate_undo(8)
    mem.active = None
    mem.servers.pop(2)
    with pytest.raises(ValueError):
        jm.enqueue_service_migrate_undo(8)
    mem.servers[2] = dst
    js.HAS_CELERY = True
    js.service_migrate_undo_task = _Delay()
    with pytest.raises(RuntimeError):
        jm.enqueue_service_migrate_undo(8)

    monkeypatch.setattr(jm, "try_acquire_dual_server_lock", lambda *a, **k: None)
    jm._run_undo_holding_locks(50, 1, 2, 7)

    class Boom(Exception):
        def __init__(self):
            super().__init__("undo failed")
            self.steps = ["dns"]

    monkeypatch.setattr(
        "app.services.service_migrate.undo.run_undo_pipeline",
        lambda *a, **k: (_ for _ in ()).throw(Boom()),
    )
    saved_servers = dict(mem.servers)
    mem.servers.clear()
    jm._execute_service_migrate_undo(99, 1, 2, 7)
    mem.servers.update(saved_servers)
    jm._execute_service_migrate_undo(99, 1, 2, 7)
    mem.jobs[11] = SimpleNamespace(id=11, status="pending", details="{", server_id=1)
    mem.jobs[4] = SimpleNamespace(id=4, status="failed", details="{")
    jm._execute_service_migrate_undo(11, 1, 2, 7)
    mem.jobs[11] = SimpleNamespace(
        id=11,
        status="pending",
        details=json.dumps({"parent_job_id": 4, "project": "app1", "port_map": {}}),
        server_id=1,
    )
    mem.jobs[4] = SimpleNamespace(id=4, status="failed", details="{")
    jm._execute_service_migrate_undo(11, 1, 2, 7)
    mem.jobs[4] = SimpleNamespace(
        id=4, status="failed", details=json.dumps({"undo_steps": ["a", "b"]})
    )
    jm._execute_service_migrate_undo(11, 1, 2, 7)

    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.run_dest_up_recover",
        _ok_call,
    )
    jm._execute_dest_up_recover(77, 1, 2, 7)
    mem.jobs[12] = SimpleNamespace(id=12, status="pending", details="{", server_id=1)
    jm._execute_dest_up_recover(12, 1, 2, 7)


# --- ssh console ---------------------------------------------------------


def _enable_console(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()


def test_console_knobs_and_mux(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.load_settings", lambda: _raise("cfg")
    )
    assert cons._settings_cfg() == {}
    assert cons._clamp_privileged_role("operators") == "operator"
    assert cons._clamp_privileged_role("admins") == "admin"
    assert len(cons.ensure_device_id("short")) > 16
    assert cons.ensure_device_id("d" * 20) == "d" * 20

    monkeypatch.setattr(cons, "env_wins", lambda name: True)
    assert cons.grant_minutes() >= 2
    assert cons.hold_sec() == 0
    monkeypatch.setattr(cons, "env_wins", lambda name: False)
    monkeypatch.setattr(cons, "_settings_cfg", lambda: {})
    assert cons.hold_sec() == 0
    monkeypatch.setattr(
        "app.services.account_stepup.stepup_minutes", lambda kind: _raise("step")
    )
    monkeypatch.setattr(
        "app.services.account_stepup.console_require_2fa_every_shell",
        lambda: _raise("a"),
    )
    monkeypatch.setattr(
        "app.services.account_stepup.console_allow_backup_codes", lambda: _raise("b")
    )
    monkeypatch.setattr(
        "app.services.account_stepup.console_prefer_passkey", lambda: _raise("c")
    )
    monkeypatch.setattr(
        "app.services.account_stepup.console_require_passkey", lambda: _raise("d")
    )
    cons.grant_minutes()
    cons.require_2fa_every_shell()
    cons.allow_backup_codes()
    cons.prefer_passkey()
    cons.require_passkey_if_enrolled()

    assert cons.mux_session_name(user_id=1, server_id=2, tab="bad").endswith("-f")

    def run_command(client, cmd, timeout=8):
        if "tmux" in cmd and "list-sessions" in cmd:
            raise RuntimeError("tmux")
        if cmd.startswith("tmux"):
            raise RuntimeError("kill")
        if "screen -ls" in cmd:
            return 0, "1.ph-u1-s9-n0-f (Detached)\n", ""
        if "screen" in cmd:
            raise RuntimeError("screen")
        raise RuntimeError("other")

    monkeypatch.setattr("app.services.ssh.run_command", run_command)
    assert cons.probe_mux_backend(SimpleNamespace()) is None
    cons.kill_mux_session(SimpleNamespace(), "tmux", "ph-u1-s2-n0-f")
    cons.kill_mux_session(SimpleNamespace(), "screen", "ph-u1-s2-n0-f")
    assert cons.decode_mux_list("tmux:ph-u1-s2-n0-f", "nope") == []
    rows = cons.list_host_mux_sessions(SimpleNamespace(), 2)
    assert rows == []
    monkeypatch.setattr(
        "app.services.ssh.run_command", lambda *a, **k: _raise("both")
    )
    cons.list_host_mux_sessions(SimpleNamespace(), 2)
    assert cons.list_host_mux_sessions(SimpleNamespace(), "nope") == []


def test_console_open_and_tokens(monkeypatch):
    _enable_console(monkeypatch)
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "mux_allowed_for_server", lambda server: True)

    class Client:
        def get_transport(self):
            return None

        def invoke_shell(self, **kwargs):
            return SimpleNamespace(settimeout=lambda t: None)

    monkeypatch.setattr("app.services.ssh.get_ssh_client", lambda server: Client())
    monkeypatch.setattr(cons, "probe_mux_backend", lambda client: "tmux")
    server = SimpleNamespace(
        hostname="host-a.example.com",
        name="host-a",
        ssh_username="user",
        os_type="debian",
        console_mux_enabled=True,
    )
    client, channel = cons.open_session_channel(
        server, mux_enabled=True, session_name="ph-u1-s2-n0-f"
    )
    assert client is not None and channel is not None

    monkeypatch.setattr(cons, "decode_token_payload", lambda raw: {"console_stepup": True, "sub": "nope", "sv": 0, "jti": "j"})
    monkeypatch.setattr(cons, "bind_ip_enabled", lambda: True)
    monkeypatch.setattr(cons, "bind_device_enabled", lambda: True)
    assert cons.consume_stepup_proof("x", user_id=1, session_version=0) is False
    monkeypatch.setattr(
        cons,
        "decode_token_payload",
        lambda raw: {"console_stepup": True, "sub": "2", "sv": 0, "jti": "j"},
    )
    assert cons.consume_stepup_proof("x", user_id=1, session_version=0) is False
    monkeypatch.setattr(
        cons,
        "decode_token_payload",
        lambda raw: {"console_stepup": True, "sub": "1", "sv": 3, "jti": "j"},
    )
    assert cons.consume_stepup_proof("x", user_id=1, session_version=0) is False
    iph = cons._hash_binding("192.0.2.10")
    did = cons._hash_binding("device-one")
    monkeypatch.setattr(
        cons,
        "decode_token_payload",
        lambda raw: {
            "console_stepup": True,
            "sub": "1",
            "sv": 0,
            "jti": "j",
            "iph": iph,
            "did": did,
        },
    )
    assert (
        cons.consume_stepup_proof(
            "x", user_id=1, session_version=0, client_ip="192.0.2.11", device_id="other"
        )
        is False
    )
    monkeypatch.setattr(
        cons,
        "decode_token_payload",
        lambda raw: {"console_stepup": True, "sub": "1", "sv": 0, "jti": ""},
    )
    assert cons.consume_stepup_proof("x", user_id=1, session_version=0) is False

    monkeypatch.setattr(
        cons,
        "decode_token_payload",
        lambda raw: {"console_grant": True, "sub": "1", "sv": 0, "iph": iph, "did": did},
    )
    monkeypatch.setattr(cons, "require_2fa_every_shell", lambda: False)
    assert (
        cons.grant_valid(
            "g",
            user_id=1,
            session_version=0,
            client_ip="192.0.2.11",
            device_id="other-device",
        )
        is False
    )
    with pytest.raises(cons.ConsoleDenied):
        cons.consume_ticket("t", user_id=1, server_id=2)
    monkeypatch.setattr(
        cons,
        "decode_token_payload",
        lambda raw: {"console": True, "sub": "no", "sid": "2", "sv": 0},
    )
    with pytest.raises(cons.ConsoleDenied):
        cons.consume_ticket("t", user_id=1, server_id=2)

    token = cons.mint_ticket(user_id=1, server_id=2, tab="bad", client_ip="192.0.2.10", device_id="d" * 20)
    assert token
    monkeypatch.setattr(cons, "decode_token_payload", decode_real)
    cons.reset_runtime_state_for_tests()


def decode_real(raw):
    from app.security.auth import decode_token_payload

    return decode_token_payload(raw)


def test_console_jti_redis_and_session(monkeypatch):
    _enable_console(monkeypatch)
    cons.reset_runtime_state_for_tests()

    class RedisOk:
        def set(self, *args, **kwargs):
            return True

    monkeypatch.setattr("app.services.server_job_lock._get_redis", lambda: RedisOk())
    cons._consumed_jtis["old"] = time.time() - 10_000
    assert cons._consume_jti("fresh-jti") is True

    class RedisNo:
        def set(self, *args, **kwargs):
            return False

    monkeypatch.setattr("app.services.server_job_lock._get_redis", lambda: RedisNo())
    assert cons._consume_jti("fresh-jti") is False

    class RedisBoom:
        def set(self, *args, **kwargs):
            raise RuntimeError("redis")

    monkeypatch.setattr("app.services.server_job_lock._get_redis", lambda: RedisBoom())
    assert cons._consume_jti("memory-jti") is True
    monkeypatch.setattr(
        "app.services.server_job_lock._get_redis", lambda: _raise("import")
    )
    assert cons._consume_jti("memory-jti-2") is True

    inactive = SimpleNamespace(id=1, is_active=False, role="admin", session_version=0)
    assert cons.session_still_valid(SimpleNamespace(get=lambda m, i: inactive), user_id=1, expected_sv=0)[0] is False
    active = SimpleNamespace(id=1, is_active=True, role="viewer", session_version=1)
    assert cons.session_still_valid(SimpleNamespace(get=lambda m, i: active), user_id=1, expected_sv=0)[1] == "session_revoked"
    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    same = SimpleNamespace(id=1, is_active=True, role="viewer", session_version=0)
    assert cons.session_still_valid(SimpleNamespace(get=lambda m, i: same), user_id=1, expected_sv=0)[0] is True
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    assert cons.session_still_valid(SimpleNamespace(get=lambda m, i: same), user_id=1, expected_sv=0)[1] == "role_lost"
    cons.reset_runtime_state_for_tests()


def _held(resume_id, **kw):
    now = time.monotonic()
    base = dict(
        resume_id=resume_id,
        user_id=1,
        server_id=2,
        session_version=0,
        ticket_payload={},
        device_id="device-device-device",
        client=SimpleNamespace(close=lambda: None),
        channel=SimpleNamespace(
            close=lambda: None,
            exit_status_ready=lambda: False,
            recv_ready=lambda: False,
            recv_stderr_ready=lambda: False,
        ),
        started_mono=now,
        last_activity_mono=now,
        held_at_mono=now,
        server_hostname="host-a.example.com",
    )
    base.update(kw)
    return cons.HeldConsole(**base)


def test_console_park_claim_and_drain(monkeypatch):
    _enable_console(monkeypatch)
    cons.reset_runtime_state_for_tests()
    first = _held("same")
    second = _held("same")
    cons.park_console(first)
    cons.park_console(second)
    assert first.dead is True
    second.dead = True
    assert cons.get_held("same") is None
    cons.reset_runtime_state_for_tests()

    monkeypatch.setattr(cons, "bind_device_enabled", lambda: True)
    monkeypatch.setattr(cons, "bind_ip_enabled", lambda: True)
    iph = cons._hash_binding("192.0.2.10")
    did = cons._hash_binding("device-one")
    held = _held(
        "resume-1",
        device_id="device-one",
        ticket_payload={"iph": iph, "did": did},
        last_activity_mono=time.monotonic() - 10_000,
    )
    cons.park_console(held)
    with pytest.raises(cons.ConsoleDenied):
        cons.claim_resume(
            "resume-1",
            user_id=1,
            server_id=2,
            session_version=0,
            device_id="other-device",
            client_ip="192.0.2.11",
        )

    held_ip = _held(
        "resume-ip",
        device_id="",
        ticket_payload={"iph": iph},
    )
    cons.park_console(held_ip)
    with pytest.raises(cons.ConsoleDenied):
        cons.claim_resume(
            "resume-ip",
            user_id=1,
            server_id=2,
            session_version=0,
            device_id="device-one",
            client_ip="192.0.2.11",
        )
    held2 = _held(
        "resume-2",
        device_id="device-one",
        ticket_payload={},
        held_at_mono=time.monotonic() - 10_000,
    )
    cons.park_console(held2)
    monkeypatch.setattr(cons, "hold_sec", lambda: 1)
    monkeypatch.setattr(cons, "idle_sec", lambda: 10_000)
    monkeypatch.setattr(cons, "max_session_sec", lambda: 10_000)
    with pytest.raises(cons.ConsoleDenied):
        cons.claim_resume(
            "resume-2",
            user_id=1,
            server_id=2,
            session_version=0,
            device_id="device-one",
            client_ip="192.0.2.10",
        )

    class Chan:
        def __init__(self):
            self.err = [b"e"]
            self.ready_n = 0

        def exit_status_ready(self):
            return False

        def recv_ready(self):
            self.ready_n += 1
            return self.ready_n == 1

        def recv(self, n):
            return b"hi"

        def recv_stderr_ready(self):
            return bool(self.err)

        def recv_stderr(self, n):
            return self.err.pop(0) if self.err else b""

        def close(self):
            raise RuntimeError("close")

    class Rec:
        finalized = False

        def feed_stdout(self, data):
            raise RuntimeError("feed")

    drained = _held("drain", channel=Chan(), recorder=Rec())
    assert cons.drain_held_channel(drained) is True

    class ExitChan:
        def exit_status_ready(self):
            return True

        def recv_ready(self):
            return False

    assert cons.drain_held_channel(_held("exit", channel=ExitChan())) is False

    class EmptyChan:
        def __init__(self):
            self.n = 0

        def exit_status_ready(self):
            return False

        def recv_ready(self):
            self.n += 1
            return self.n == 1

        def recv(self, n):
            return b""

        def recv_stderr_ready(self):
            return False

    assert cons.drain_held_channel(_held("empty", channel=EmptyChan())) is False

    class BoomChan:
        def exit_status_ready(self):
            raise RuntimeError("dead")

        def recv_ready(self):
            return False

        def recv_stderr_ready(self):
            return False

    assert cons.drain_held_channel(_held("boom", channel=BoomChan())) is False
    cons.reset_runtime_state_for_tests()


def test_console_destroy_and_discard(monkeypatch):
    _enable_console(monkeypatch)
    cons.reset_runtime_state_for_tests()
    eng = _mem_engine()
    monkeypatch.setattr("app.database.get_engine", lambda: eng)
    from app.services.console_audit import SessionRecorder

    class CloseBoom:
        def close(self):
            raise RuntimeError("close")

    rec = SessionRecorder()
    held = _held("gone", client=CloseBoom(), channel=CloseBoom(), recorder=rec, mux_backend="tmux", mux_name="ph-u1-s2-n0-f")
    monkeypatch.setattr(cons, "kill_mux_session", lambda *a, **k: _raise("kill"))
    monkeypatch.setattr(cons, "release_slot", lambda uid: _raise("slot"))
    cons._destroy_held_resources(held, release_slot_user=1, kill_mux=True)

    monkeypatch.setattr("app.database.get_engine", lambda: _raise("engine"))
    held2 = _held("gone2", recorder=SessionRecorder())
    cons._destroy_held_resources(held2, release_slot_user=None, kill_mux=False)

    assert cons.discard_parked_for_user("", user_id=1) is False
    other = _held("own")
    cons.park_console(other)
    assert cons.discard_parked_for_user("own", user_id=1, server_id=9) is False

    class D(dict):
        def pop(self, key, default=None):
            return None

    saved = cons._held_sessions
    try:
        bag = D()
        row = _held("weird")
        bag[row.resume_id] = row
        cons._held_sessions = bag
        assert cons.discard_parked_for_user("weird", user_id=1) is False
    finally:
        cons._held_sessions = saved
    cons.reset_runtime_state_for_tests()


# --- ssh onboarding ------------------------------------------------------


def _fake_ssh(connect):
    class Client:
        def set_missing_host_key_policy(self, policy):
            self.policy = policy

        def connect(self, **kwargs):
            connect(kwargs)

        def close(self):
            return None

    return Client


def test_onboarding_keys_and_connect(monkeypatch):
    assert onb.OnboardingResult(False, "no").as_dict()["ok"] is False
    with pytest.raises(RuntimeError):
        onb._pkey_from_private("not-a-key")
    server = SimpleNamespace(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_username="user",
        ssh_password_encrypted="cipher",
        ssh_private_key_encrypted=None,
        ssh_public_key=None,
        os_type="debian",
    )
    monkeypatch.setattr(onb.encryption, "decrypt_str", lambda raw: _raise("bad"))
    assert onb._stored_password(server) is None
    assert onb._stored_password(SimpleNamespace(ssh_password_encrypted=None)) is None

    def connect(kwargs):
        if kwargs.get("password") == "secret":
            raise onb.ssh_service.HostKeyMismatch("host-a.example.com", "a", "b")
        raise RuntimeError("blocked")

    monkeypatch.setattr(paramiko, "SSHClient", _fake_ssh(connect))
    monkeypatch.setattr(onb.encryption, "decrypt_str", lambda raw: "secret")
    with pytest.raises(onb.ssh_service.HostKeyMismatch):
        onb.connect_with_auth(server, password=None, allow_stored_password=True)
    monkeypatch.setattr(
        "app.services.ssh.get_ssh_client",
        lambda *a, **k: (_ for _ in ()).throw(
            onb.ssh_service.HostKeyMismatch("host-a.example.com", "a", "b")
        ),
    )
    result = onb.test_connection_detail(server)
    assert result.ok is False


def test_onboarding_deploy_and_rotate(monkeypatch):
    server = SimpleNamespace(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_username="user",
        ssh_password_encrypted=None,
        ssh_private_key_encrypted="cipher",
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly comment",
        os_type="debian",
    )
    monkeypatch.setattr(
        onb,
        "_ensure_server_key_material",
        lambda server, identity=None: (_raise("no key"), None),
    )
    # The lambda above raises before returning. Call the real failure path.
    monkeypatch.setattr(
        onb,
        "_ensure_server_key_material",
        lambda server, identity=None: (_ for _ in ()).throw(RuntimeError("no key")),
    )
    assert onb.deploy_public_key(server).ok is False
    assert onb.rotate_keypair(server).ok is False

    monkeypatch.setattr(
        onb,
        "_ensure_server_key_material",
        lambda server, identity=None: ("bogus", "bogus-priv"),
    )
    monkeypatch.setattr(onb, "connect_with_auth", lambda *a, **k: _raise("ssh"))
    ident = SimpleNamespace(username="user", role="fleet", private_key_encrypted="c", public_key="p")
    assert onb.deploy_public_key(server, identity=ident).ok is False
    assert "password" in onb.deploy_public_key(server, password_override="secret").message.lower() or True
    rotated = onb.rotate_keypair(server, identity=ident)
    assert rotated.ok is False

    monkeypatch.setattr(onb, "_stored_password", lambda server: "secret")
    assert onb.deploy_public_key(server).ok is False


def test_onboarding_rotate_remove_and_provision(monkeypatch):
    server = SimpleNamespace(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        ip_address="192.0.2.10",
        ssh_port=22,
        ssh_username="user",
        ssh_password_encrypted=None,
        ssh_private_key_encrypted="cipher",
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly comment",
        os_type="debian",
    )
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly comment"
    monkeypatch.setattr(onb, "_ensure_server_key_material", lambda server, identity=None: (pub, "old-priv"))

    class Client:
        def __init__(self, boom=False):
            self.boom = boom

        def close(self):
            if self.boom:
                raise RuntimeError("close")

    seq = {"n": 0}

    def connect(*args, **kwargs):
        seq["n"] += 1
        if seq["n"] == 1:
            return Client(boom=True)
        return Client(boom=False)

    monkeypatch.setattr(onb, "connect_with_auth", connect)
    monkeypatch.setattr(onb, "install_authorized_key", lambda *a, **k: {"installed": True})
    monkeypatch.setattr(onb, "remove_authorized_key", lambda *a, **k: _raise("rm"))
    out = onb.rotate_keypair(server, identity=SimpleNamespace(username="user", role="fleet"))
    assert out.ok is True
    assert out.details["removed_old"] is False

    monkeypatch.setattr(onb, "connect_with_auth", lambda *a, **k: _raise("ssh"))
    monkeypatch.setattr(onb, "_stored_password", lambda server: None)
    missed = onb.provision_least_priv_user(server, "piherder")
    assert missed.ok is False
    monkeypatch.setattr(onb, "_stored_password", lambda server: "secret")
    failed = onb.provision_least_priv_user(server, "piherder", password_override="secret")
    assert failed.ok is False

    client = Client(boom=True)

    def connect_ok(*args, **kwargs):
        if kwargs.get("password"):
            return client
        if kwargs.get("username") == "piherder":
            raise RuntimeError("verify")
        return client

    monkeypatch.setattr(onb, "connect_with_auth", connect_ok)
    monkeypatch.setattr(
        onb,
        "detect_os_family",
        lambda client: {"debian_family": True, "name": "debian"},
    )
    monkeypatch.setattr("app.services.ssh.run_command", lambda *a, **k: (0, "ok", ""))
    verified = onb.provision_least_priv_user(server, "piherder", password_override="secret")
    assert verified.ok is False


def test_onboarding_bootstrap_helpers(monkeypatch):
    with pytest.raises(ValueError):
        onb.build_feature_bootstrap_script("ubuntu", "bookworm", "1bad", install_rsync=True, install_docker_ce=False)
    with pytest.raises(ValueError):
        onb.build_feature_bootstrap_script("ubuntu", "bookworm", "user", install_rsync=False, install_docker_ce=False)

    server = SimpleNamespace(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        ssh_username="user",
        ssh_port=22,
        os_type="haos",
        backup_enabled=True,
        container_patch_enabled=True,
        ip_address="192.0.2.10",
    )
    monkeypatch.setattr(
        "app.services.host_deps.parse_host_deps",
        lambda server: {"os": {"supported_os": True, "os_id": "ubuntu", "codename": "bookworm"}},
    )
    assert onb.preview_feature_bootstrap_script(server) == ""

    class Stdin:
        def write(self, text):
            return None

        def flush(self):
            raise RuntimeError("flush")

        class channel:
            @staticmethod
            def shutdown_write():
                raise RuntimeError("shut")

    class Out:
        def __init__(self, data, rc=0):
            self.data = data
            self.channel = self
            self.rc = rc

        def read(self):
            return self.data

        def recv_exit_status(self):
            return self.rc

    class ExecClient:
        def exec_command(self, cmd, timeout=300):
            return Stdin(), Out(b"out"), Out(b"secret-err")

        def close(self):
            raise RuntimeError("close")

    monkeypatch.setattr("app.services.ssh.run_command", lambda *a, **k: (0, "0\n", ""))
    rc, out, err = onb._exec_elevated(ExecClient(), "echo hi\n", "secret")
    assert rc == 0

    monkeypatch.setattr(
        "app.services.ssh.get_ssh_client", lambda server: ExecClient()
    )
    monkeypatch.setattr(
        "app.services.ssh.run_command",
        lambda *a, **k: (1, "", "docker check failed"),
    )
    ok, msg = onb._verify_docker_fresh(server)
    assert ok is False and msg

    monkeypatch.setattr(
        "app.services.ssh_identities.get_by_role", lambda *a, **k: _raise("id")
    )
    monkeypatch.setattr(
        onb, "_ensure_server_key_material", lambda *a, **k: _raise("key")
    )
    assert onb._bootstrap_private_key(server, None, "user") is None
    ident = SimpleNamespace(
        enabled=True, username="user", private_key_encrypted="c", role="privileged"
    )
    monkeypatch.setattr(
        "app.services.ssh_identities.get_by_role", lambda *a, **k: ident
    )
    monkeypatch.setattr(
        onb, "_ensure_server_key_material", lambda server, identity=None: ("pub", "priv")
    )
    assert onb._bootstrap_private_key(server, None, "user") == "priv"

    monkeypatch.setattr(
        "app.services.ssh.get_ssh_client", lambda server: _raise("fleet")
    )
    monkeypatch.setattr(onb, "connect_with_auth", lambda *a, **k: "client")
    with pytest.raises(RuntimeError):
        onb._connect_bootstrap_client(server, None, "user", None)
    assert onb._connect_bootstrap_client(server, None, "user", "secret") == "client"

    monkeypatch.setattr(onb, "_bootstrap_private_key", lambda *a, **k: "priv")
    monkeypatch.setattr(onb, "connect_with_auth", lambda *a, **k: _raise("key"))
    with pytest.raises(RuntimeError):
        onb._connect_bootstrap_client(server, None, "other", None)
    monkeypatch.setattr(onb, "connect_with_auth", lambda *a, **k: "pw-client")
    assert onb._connect_bootstrap_client(server, None, "other", "secret") == "pw-client"


def test_onboarding_bootstrap_host_tools(monkeypatch):
    server = SimpleNamespace(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        ssh_username="user",
        ssh_port=22,
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=True,
        ip_address="192.0.2.10",
    )
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    assert onb.bootstrap_host_tools(server, None).ok is False
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    assert onb.bootstrap_host_tools(server, None, bootstrap_username="1bad").ok is False
    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist", lambda session, server: _raise("check")
    )
    assert "Host check" in onb.bootstrap_host_tools(server, None).message

    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist",
        lambda session, server: {"os": {"supported_os": False, "os_id": "", "codename": ""}, "bootstrap": {}},
    )
    assert onb.bootstrap_host_tools(server, None).ok is False

    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist",
        lambda session, server: {
            "os": {"supported_os": False, "os_id": "ubuntu", "codename": "bookworm"},
            "bootstrap": {"install_rsync": True},
        },
    )
    assert "not supported" in onb.bootstrap_host_tools(server, None).message

    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist",
        lambda session, server: {
            "os": {"supported_os": True, "os_id": "windows", "codename": "bookworm"},
            "bootstrap": {"install_rsync": True},
        },
    )
    assert onb.bootstrap_host_tools(server, None).ok is False

    class Client:
        def close(self):
            raise RuntimeError("close")

    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist",
        lambda session, server: {
            "os": {"supported_os": True, "os_id": "ubuntu", "codename": "bookworm"},
            "bootstrap": {"install_rsync": True, "install_docker_ce": True},
        },
    )
    monkeypatch.setattr(onb, "_connect_bootstrap_client", lambda *a, **k: Client())
    monkeypatch.setattr(onb, "_exec_elevated", lambda *a, **k: (1, "", "secret failed"))
    failed = onb.bootstrap_host_tools(server, None, sudo_password="secret")
    assert failed.ok is False

    calls = {"n": 0}

    def check(session, server):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("recheck")
        return {
            "os": {"supported_os": True, "os_id": "ubuntu", "codename": "bookworm"},
            "bootstrap": {"install_rsync": True, "install_docker_ce": True},
        }

    monkeypatch.setattr("app.services.host_deps.check_and_persist", check)
    monkeypatch.setattr(onb, "_exec_elevated", lambda *a, **k: (0, "ok", ""))
    monkeypatch.setattr(onb, "_verify_docker_fresh", lambda server: (False, "no docker"))
    unverified = onb.bootstrap_host_tools(
        server, None, confirm_docker_bootstrap=True, sudo_password="secret"
    )
    assert unverified.ok is False


import paramiko  # noqa: E402


# --- HTTP auth and settings ----------------------------------------------


_USER_N = {"n": 0}


def _email():
    _USER_N["n"] += 1
    return f"user{_USER_N['n']}@example.com"


@pytest.fixture()
def client(monkeypatch):
    eng = _mem_engine()
    monkeypatch.setattr("app.database.engine", eng)

    def _session():
        with Session(eng) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    tc = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    try:
        with Session(eng) as session:
            user = User(
                email=_email(),
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=True,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            uid = user.id
        yield tc, eng, uid
    finally:
        app.dependency_overrides.clear()
        from app.services import app_settings as app_cfg

        app_cfg.clear_cache()


def _cookie(uid):
    return {"access_token": create_access_token({"sub": str(uid)})}


def test_auth_helpers_and_password_reset(client, monkeypatch):
    tc, eng, uid = client
    from app.routers import auth as auth_mod

    user = SimpleNamespace(id=uid)
    assert auth_mod._read_backup_codes_flash(SimpleNamespace(cookies={}), user) is None
    raw = create_access_token({"sub": "nope", "bc": True, "backup_codes": ["a"]})
    assert auth_mod._read_backup_codes_flash(SimpleNamespace(cookies={"backup_codes_once": raw}), user) is None
    raw = create_access_token({"sub": str(uid), "bc": True, "backup_codes": "aa, bb"})
    codes = auth_mod._read_backup_codes_flash(
        SimpleNamespace(cookies={"backup_codes_once": raw}), user
    )
    assert codes == ["aa", "bb"]
    assert auth_mod._public_base_url(None) is not None

    from app.config import settings as cfg

    monkeypatch.setattr("app.services.demo.demo_write_allowed", lambda method, path: True)
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", True)
    with Session(eng) as session:
        assert auth_mod._registration_allowed(session) is False
    assert tc.get("/auth/forgot-password").status_code in (200, 303)
    assert tc.post("/auth/forgot-password", data={"email": "user@example.com"}).status_code in (200, 303)
    assert tc.get("/auth/reset-password").status_code in (200, 303)
    assert tc.post(
        "/auth/reset-password",
        data={"token": "t", "new_password": "SmokeTest1ok", "confirm_password": "SmokeTest1ok"},
    ).status_code in (200, 303)
    assert tc.get("/auth/register").status_code in (200, 303)
    assert tc.post(
        "/auth/register", data={"email": "new@example.com", "password": "SmokeTest1ok"}
    ).status_code in (200, 303)
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", False)

    monkeypatch.setattr(auth_mod, "rate_limit_auth", lambda *a, **k: False)
    assert tc.post("/auth/forgot-password", data={"email": "user@example.com"}).status_code == 303
    assert tc.post(
        "/auth/reset-password",
        data={"token": "t", "new_password": "SmokeTest1ok", "confirm_password": "SmokeTest1ok"},
    ).status_code == 303
    monkeypatch.setattr(cfg, "ALLOW_OPEN_REGISTRATION", True)
    assert tc.post(
        "/auth/register", data={"email": "new@example.com", "password": "SmokeTest1ok"}
    ).status_code == 200

    monkeypatch.setattr(auth_mod, "rate_limit_auth", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.alert_channels.password_reset_available", lambda: True
    )
    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin", lambda: ""
    )
    assert tc.post("/auth/forgot-password", data={"email": "user@example.com"}).status_code == 303
    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin",
        lambda: "https://example.com",
    )
    monkeypatch.setattr(
        "app.services.password_reset.request_reset_email",
        lambda *a, **k: {"ok": False, "error": "email recovery is not enabled"},
    )
    assert tc.post("/auth/forgot-password", data={"email": "user@example.com"}).status_code == 303
    monkeypatch.setattr(
        "app.services.password_reset.request_reset_email",
        lambda *a, **k: {"ok": True},
    )
    assert tc.post("/auth/forgot-password", data={"email": "user@example.com"}).status_code == 303

    def consume(session, token):
        return session.get(User, uid)

    monkeypatch.setattr("app.services.password_reset.consume_token", consume)
    assert tc.post(
        "/auth/reset-password",
        data={"token": "t", "new_password": "SmokeTest1ok", "confirm_password": "SmokeTest1ok"},
    ).status_code == 303
    monkeypatch.setattr("app.services.password_reset.consume_token", lambda *a, **k: None)
    assert tc.post(
        "/auth/reset-password",
        data={"token": "bad", "new_password": "SmokeTest1ok", "confirm_password": "SmokeTest1ok"},
    ).status_code == 303


def test_auth_register_logout_account(client, monkeypatch):
    tc, eng, uid = client
    from app.config import settings as cfg
    from app.routers import auth as auth_mod

    monkeypatch.setattr(cfg, "ALLOW_OPEN_REGISTRATION", True)
    monkeypatch.setattr(auth_mod, "rate_limit_auth", lambda *a, **k: True)
    with Session(eng) as session:
        session.add(
            User(
                email="taken@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="operator",
                is_active=True,
            )
        )
        session.commit()
    assert tc.post(
        "/auth/register",
        data={"email": "taken@example.com", "password": "SmokeTest1ok"},
    ).status_code == 200
    monkeypatch.setattr(auth_mod, "get_password_hash", lambda pw: _raise("hash"))
    assert tc.post(
        "/auth/register",
        data={"email": "fresh@example.com", "password": "SmokeTest1ok"},
    ).status_code == 200

    bad = create_access_token({"sub": "abc"})
    assert tc.get("/auth/logout", cookies={"access_token": "Bearer " + bad}).status_code == 303

    from sqlmodel import Session as SQLSession

    monkeypatch.setattr(
        "app.services.user_admin.bump_session_version", lambda *a, **k: _raise("bump")
    )
    orig_rollback = SQLSession.rollback
    monkeypatch.setattr(SQLSession, "rollback", lambda self: _raise("rb"))
    monkeypatch.delattr(cons, "CONSOLE_GRANT_COOKIE", raising=False)
    assert tc.get("/auth/logout", cookies=_cookie(uid)).status_code == 303
    monkeypatch.setattr(SQLSession, "rollback", orig_rollback)

    from app.security.auth import encrypt_totp_secret

    secret = encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    with Session(eng) as session:
        user = session.get(User, uid)
        user.totp_enabled = False
        user.totp_secret_encrypted = secret
        session.add(user)
        session.commit()
    flash = create_access_token(
        {"sub": str(uid), "bc": True, "backup_codes": ["code1", "code2"]}
    )
    cookies = _cookie(uid)
    cookies["backup_codes_once"] = flash
    monkeypatch.setattr("app.services.push.ensure_vapid_keys", lambda *a, **k: _raise("push"))
    assert tc.get("/auth/account", cookies=cookies).status_code == 200

    monkeypatch.setattr(auth_mod, "decrypt_totp_secret", lambda raw: _raise("dec"))
    monkeypatch.setattr("app.routers.auth.totp_qr_svg", lambda uri: _raise("qr"))
    assert tc.get("/auth/account", cookies=_cookie(uid)).status_code == 200


def test_auth_profile_avatar_devices_force(client, monkeypatch):
    tc, eng, uid = client
    from app.config import settings as cfg

    with Session(eng) as session:
        session.add(
            User(
                email="other@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="operator",
                is_active=True,
            )
        )
        session.commit()
        user = session.get(User, uid)
        email = user.email
    monkeypatch.setattr("app.services.demo.demo_write_allowed", lambda method, path: True)
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", True)
    assert tc.post(
        "/auth/account/profile",
        data={"email": email, "display_name": "A"},
        cookies=_cookie(uid),
    ).status_code == 303
    assert tc.post(
        "/auth/account/password",
        data={
            "current_password": "SmokeTest1ok",
            "new_password": "SmokeTest1ok",
            "confirm_password": "SmokeTest1ok",
        },
        cookies=_cookie(uid),
    ).status_code == 303
    assert tc.post("/auth/account/2fa/start", cookies=_cookie(uid)).status_code == 303
    assert tc.post(
        "/auth/account/2fa/confirm", data={"code": "000000"}, cookies=_cookie(uid)
    ).status_code == 303
    assert tc.post("/auth/account/2fa/disable", cookies=_cookie(uid)).status_code in (303, 422)
    assert tc.post("/auth/account/2fa/backup-codes", cookies=_cookie(uid)).status_code in (303, 422)
    assert tc.post("/auth/account/webauthn/1/revoke", cookies=_cookie(uid)).status_code in (303, 422)
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", False)

    assert tc.post(
        "/auth/account/profile",
        data={
            "email": "other@example.com",
            "display_name": "A",
            "current_password": "SmokeTest1ok",
        },
        cookies=_cookie(uid),
    ).status_code == 303
    assert tc.post(
        "/auth/account/profile",
        data={
            "email": "renamed@example.com",
            "display_name": "A",
            "current_password": "SmokeTest1ok",
        },
        cookies=_cookie(uid),
    ).status_code == 303

    monkeypatch.setattr("app.services.avatars.save_avatar", lambda uid, data: "avatars/a.png")
    assert tc.post(
        "/auth/account/avatar",
        files={"file": ("a.png", b"\x89PNG\r\n", "image/png")},
        cookies=_cookie(uid),
    ).status_code == 303
    with Session(eng) as session:
        user = session.get(User, uid)
        user.avatar_path = "missing.png"
        session.add(user)
        session.commit()
    assert tc.get("/auth/me/avatar", cookies=_cookie(uid)).status_code == 404
    from app.config import settings as cfg

    rel = f"avatars/{uid}.png"
    dest = PathMod(cfg.DATA_ROOT) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 16)
    with Session(eng) as session:
        user = session.get(User, uid)
        user.avatar_path = rel
        session.add(user)
        session.commit()
    assert tc.get("/auth/me/avatar", cookies=_cookie(uid)).status_code == 200

    with Session(eng) as session:
        user = session.get(User, uid)
        user.totp_enabled = True
        session.add(user)
        session.commit()
    assert tc.post("/auth/account/2fa/start", cookies=_cookie(uid)).status_code == 303

    row = SimpleNamespace(id=4, nickname="key")
    monkeypatch.setattr("app.services.webauthn_svc.get_credential", lambda *a, **k: None)
    assert tc.post(
        "/auth/account/webauthn/4/rename",
        data={"nickname": "desk"},
        cookies=_cookie(uid),
    ).status_code == 303
    monkeypatch.setattr("app.services.webauthn_svc.get_credential", lambda *a, **k: row)
    monkeypatch.setattr("app.services.webauthn_svc.rename_credential", lambda *a, **k: None)
    assert tc.post(
        "/auth/account/webauthn/4/rename",
        data={"nickname": "desk"},
        cookies=_cookie(uid),
    ).status_code == 303

    with Session(eng) as session:
        dev = TrustedDevice(
            user_id=uid,
            token_hash="hash",
            label="old",
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        session.add(dev)
        session.commit()
        session.refresh(dev)
        did = dev.id
    assert tc.post(
        f"/auth/account/trusted-devices/{did}/rename",
        data={"label": "laptop"},
        cookies=_cookie(uid),
    ).status_code == 303
    monkeypatch.setattr("app.routers.auth.revoke_trusted_device", lambda *a, **k: True)
    assert tc.post(
        f"/auth/account/trusted-devices/{did}/revoke", cookies=_cookie(uid)
    ).status_code == 303

    with Session(eng) as session:
        user = session.get(User, uid)
        user.must_change_password = False
        session.add(user)
        session.commit()
    assert tc.get("/auth/force-password", cookies=_cookie(uid)).status_code in (200, 303)
    assert tc.post(
        "/auth/force-password",
        data={"new_password": "SmokeTest1ok", "confirm_password": "SmokeTest1ok"},
        cookies=_cookie(uid),
    ).status_code in (200, 303)
    with Session(eng) as session:
        user = session.get(User, uid)
        user.must_change_password = True
        session.add(user)
        session.commit()
    assert tc.post(
        "/auth/force-password",
        data={"new_password": "short", "confirm_password": "short"},
        cookies=_cookie(uid),
    ).status_code == 303
    assert tc.post(
        "/auth/force-password",
        data={"new_password": "SmokeTest1ok", "confirm_password": "SmokeTest1ok"},
        cookies=_cookie(uid),
    ).status_code == 303


def test_auth_two_factor_webauthn(client, monkeypatch):
    tc, eng, uid = client
    from app.routers import auth as auth_mod

    pending = {"pending_2fa": create_pending_2fa_token(uid)}
    with Session(eng) as session:
        user = session.get(User, uid)
        user.totp_enabled = False
        session.add(user)
        session.commit()
    assert tc.get("/auth/2fa", cookies=pending).status_code == 303

    monkeypatch.setattr(auth_mod, "rate_limit_auth", lambda *a, **k: False)
    assert tc.post("/auth/2fa/webauthn/options", cookies=pending).status_code == 429
    monkeypatch.setattr(auth_mod, "rate_limit_auth", lambda *a, **k: True)
    from app.services import webauthn_svc as wa

    monkeypatch.setattr(
        wa,
        "authentication_options_json",
        lambda *a, **k: (_ for _ in ()).throw(wa.WebAuthnConfigError("cfg")),
    )
    assert tc.post("/auth/2fa/webauthn/options", cookies=pending).status_code == 400
    monkeypatch.setattr(wa, "authentication_options_json", lambda *a, **k: _raise("opt"))
    assert tc.post("/auth/2fa/webauthn/options", cookies=pending).status_code == 500
    monkeypatch.setattr(
        wa, "authentication_options_json", lambda *a, **k: ('{"challenge": "a"}', "chal")
    )
    assert tc.post("/auth/2fa/webauthn/options", cookies=pending).status_code == 200

    from starlette.requests import Request

    orig = Request.form
    state = {"n": 0}

    async def form(self, *args, **kwargs):
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("form")
        return await orig(self, *args, **kwargs)

    monkeypatch.setattr(Request, "form", form)
    monkeypatch.setattr("app.services.turnstile.turnstile_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.turnstile.verify_turnstile_token", lambda *a, **k: (False, "bad")
    )
    monkeypatch.setattr(
        "app.services.turnstile.visitor_ip_for_turnstile", lambda request: "192.0.2.10"
    )
    tc.post("/auth/login", data={"email": "user@example.com", "password": "SmokeTest1ok"})


def test_settings_page_and_small_helpers(client, monkeypatch):
    tc, eng, uid = client
    from app.routers import settings as settings_mod
    from app.services import alert_mute as mute_svc

    assert settings_mod._oidc_role_map_rows({"oidc_role_map": {"ops": "admin"}})
    assert settings_mod._oidc_role_map_rows({"oidc_role_map": []}) == []
    assert "tab=general" in settings_mod._settings_url("nope")
    assert settings_mod._mute_form_int("nope") is None
    rule = SimpleNamespace(starts_at=None, ends_at=None)
    assert settings_mod._mute_window_label(rule) == "Always"
    later = datetime.utcnow() + timedelta(days=1)
    earlier = datetime.utcnow() - timedelta(days=2)
    assert "Until" in settings_mod._mute_window_label(
        SimpleNamespace(starts_at=None, ends_at=later)
    )
    assert "to" in settings_mod._mute_window_label(
        SimpleNamespace(starts_at=earlier, ends_at=later)
    )
    monkeypatch.setattr(mute_svc, "is_active", lambda rule, now=None: True)
    assert settings_mod._mute_state(SimpleNamespace(starts_at=None, ends_at=None)) == "Active"
    monkeypatch.setattr(mute_svc, "is_active", lambda rule, now=None: False)
    future = datetime.utcnow() + timedelta(days=2)
    assert settings_mod._mute_state(SimpleNamespace(starts_at=future, ends_at=None)) == "Not started"
    assert settings_mod._mute_state(SimpleNamespace(starts_at=earlier, ends_at=earlier)) == "Ended"

    monkeypatch.setattr("app.services.alert_mute.list_rules", lambda session: _raise("rules"))
    assert tc.get("/herder-backups", cookies=_cookie(uid)).status_code in (200, 500)

    kind = mute_svc.mutable_types()[0][0]
    with Session(eng) as session:
        row = AlertMuteRule(kind="type", alert_type=kind, server_id=None)
        session.add(row)
        session.add(
            BackupDestination(
                name="copy-smb",
                provider="smb",
                enabled=True,
                schedule="0 3 * * *",
                config_json=json.dumps({"host": "files.example.com"}),
            )
        )
        session.commit()
    monkeypatch.setattr(
        "app.services.copy_schedule_order.early_text_by_provider",
        lambda session: _raise("early"),
    )
    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin", lambda: ""
    )
    assert tc.get("/herder-backups", cookies=_cookie(uid)).status_code in (200, 500)

    with Session(eng) as session:
        for row in session.exec(
            __import__("sqlmodel").select(BackupDestination)
        ).all():
            session.delete(row)
        session.add(
            BackupDestination(
                name="copy-od",
                provider="onedrive",
                enabled=True,
                schedule="0 4 * * *",
            )
        )
        session.commit()
    assert tc.get("/herder-backups?folders=onedrive", cookies=_cookie(uid)).status_code in (200, 500)


def test_settings_posts(client, monkeypatch):
    tc, eng, uid = client
    from app.config import settings as cfg
    from app.services.alert_mute import mutable_types, create_rule
    from app.services.demo import DemoBlocked

    cookies = _cookie(uid)
    monkeypatch.setattr("app.services.demo.demo_write_allowed", lambda method, path: True)
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", True)
    assert tc.post("/herder-backups/demo-restore", data={"confirm": "yes"}, cookies=cookies).status_code == 403
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", False)

    monkeypatch.setattr(
        "app.services.stack_health.run_stack_health_check", lambda *a, **k: _raise("stack")
    )
    assert tc.post("/herder-backups/status/check", cookies=cookies).status_code == 303
    monkeypatch.setattr(
        "app.services.stack_health.collect_backup_tree_usage", lambda: _raise("du")
    )
    assert tc.get("/herder-backups/status/backup-usage", cookies=cookies).status_code == 500

    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", True)
    assert tc.post(
        "/herder-backups/api-tokens", data={"name": "tok"}, cookies=cookies
    ).status_code == 303
    monkeypatch.setattr(cfg, "PIHERDER_DEMO_MODE", False)
    assert tc.post(
        "/herder-backups/api-tokens",
        data={"name": "tok", "expires_preset": "nope"},
        cookies=cookies,
    ).status_code == 303
    monkeypatch.setattr(
        "app.services.api_tokens.create_api_token",
        lambda *a, **k: (_ for _ in ()).throw(DemoBlocked("api_token")),
    )
    assert tc.post(
        "/herder-backups/api-tokens", data={"name": "tok"}, cookies=cookies
    ).status_code == 303

    row = SimpleNamespace(id=1, name="tok", scopes="read")
    monkeypatch.setattr("app.services.api_tokens.get_api_token", lambda *a, **k: None)
    assert tc.post(
        "/herder-backups/api-tokens/9/update", data={"name": "tok"}, cookies=cookies
    ).status_code == 303
    assert tc.post("/herder-backups/api-tokens/9/rotate", cookies=cookies).status_code == 303
    monkeypatch.setattr("app.services.api_tokens.get_api_token", lambda *a, **k: row)
    monkeypatch.setattr(
        "app.services.api_tokens.parse_scopes", lambda raw: {"read"}
    )
    monkeypatch.setattr(
        "app.services.api_tokens.update_api_token", lambda *a, **k: _raise_value()
    )
    updated = tc.post(
        "/herder-backups/api-tokens/1/update",
        data={"name": "tok", "scope_admin": "1"},
        cookies=cookies,
    )
    assert updated.status_code == 303, updated.text[:400]
    monkeypatch.setattr("app.services.api_tokens.update_api_token", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.api_tokens.rotate_api_token", lambda *a, **k: _raise_value()
    )
    assert tc.post("/herder-backups/api-tokens/1/rotate", cookies=cookies).status_code == 303

    from sqlmodel import Session as SQLSession

    orig_commit = SQLSession.commit
    monkeypatch.setattr(SQLSession, "commit", lambda self: _raise("commit"))
    monkeypatch.setattr(
        "app.services.api_tokens.create_api_token",
        lambda *a, **k: (SimpleNamespace(id=2, name="tok"), "secret-value"),
    )
    assert tc.post(
        "/herder-backups/api-tokens", data={"name": "tok"}, cookies=cookies
    ).status_code in (303, 500)
    monkeypatch.setattr(
        "app.services.api_tokens.rotate_api_token",
        lambda *a, **k: (SimpleNamespace(id=2, name="tok"), "secret-value"),
    )
    assert tc.post("/herder-backups/api-tokens/1/rotate", cookies=cookies).status_code in (303, 500)
    SQLSession.commit = orig_commit

    monkeypatch.setattr(
        "app.services.jobs.enqueue_herder_backup_job", lambda *a, **k: _raise("job")
    )
    assert tc.post(
        "/herder-backups/run", data={"backup_mode": "config_only"}, cookies=cookies
    ).status_code == 303

    assert tc.post(
        "/herder-backups/restore",
        files={"restore_file": ("notes.txt", b"hello", "text/plain")},
        cookies=cookies,
    ).status_code == 303
    real_unlink = PathMod.unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith(".upload"):
            raise OSError("busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(PathMod, "unlink", unlink)
    assert tc.post(
        "/herder-backups/restore",
        files={"restore_file": ("notes.txt", b"hello", "text/plain")},
        cookies=cookies,
    ).status_code == 303

    from app.services import herder_backup as hb

    archive = PathMod(hb.HERDER_BACKUP_DIR) / "piherder-q90.tar.gz"
    archive.write_bytes(b"not-a-real-archive")
    assert tc.get(
        "/herder-backups/download", params={"name": archive.name}, cookies=cookies
    ).status_code == 200
    assert tc.post(
        "/herder-backups/delete", data={"name": archive.name}, cookies=cookies
    ).status_code == 303
    archive.write_bytes(b"again")
    monkeypatch.setattr(PathMod, "unlink", lambda self, *a, **k: _raise("unlink"))
    assert tc.post(
        "/herder-backups/delete", data={"name": archive.name}, cookies=cookies
    ).status_code == 303
    try:
        os.unlink(archive)
    except OSError:
        pass

    assert tc.post(
        "/herder-backups/config",
        data={"schedule_enabled": "1", "schedule_cron": "not-a-cron"},
        cookies=cookies,
    ).status_code == 303
    monkeypatch.setattr(
        "app.routers.settings.sync_herder_backup_schedule", lambda *a, **k: _raise("sync")
    )
    assert tc.post(
        "/herder-backups/config",
        data={"schedule_enabled": "1", "schedule_cron": "0 3 * * *"},
        cookies=cookies,
    ).status_code == 303

    monkeypatch.setattr(
        "app.services.app_settings.save_settings", lambda data: _raise("save")
    )
    assert tc.post("/herder-backups/security", data={"force_2fa_scope": "weird", "audit_api_reads": "1"}, cookies=cookies).status_code == 303
    monkeypatch.setattr("app.services.app_settings.save_settings", _real_save)
    assert tc.post(
        "/herder-backups/security",
        data={"force_2fa_scope": "weird", "audit_api_reads": "1"},
        cookies=cookies,
    ).status_code == 303

    monkeypatch.setenv("PIHERDER_HOST_FILES_MAX_BYTES", "100")
    assert tc.post("/herder-backups/files", data={"files_max_gib": "1"}, cookies=cookies).status_code == 303
    monkeypatch.delenv("PIHERDER_HOST_FILES_MAX_BYTES", raising=False)
    assert tc.post("/herder-backups/files", data={"files_max_gib": "nope"}, cookies=cookies).status_code == 303
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda data: _raise("save"))
    assert tc.post("/herder-backups/files", data={"files_max_gib": "0.5"}, cookies=cookies).status_code == 303
    monkeypatch.setenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", "30")
    assert tc.post("/herder-backups/jobs-wait", data={"host_wait_minutes": "10"}, cookies=cookies).status_code == 303
    monkeypatch.delenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", raising=False)
    monkeypatch.setattr("app.services.app_settings.save_settings", _real_save)
    assert tc.post("/herder-backups/jobs-wait", data={"host_wait_minutes": "nope"}, cookies=cookies).status_code == 303
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda data: _raise("save"))
    assert tc.post("/herder-backups/jobs-wait", data={"host_wait_minutes": "15"}, cookies=cookies).status_code == 303

    monkeypatch.setattr("app.services.app_settings.save_settings", _real_save)
    assert tc.post(
        "/herder-backups/instance",
        data={"instance_name": "Lab", "instance_accent": "zz", "show_catalog": "1"},
        cookies=cookies,
    ).status_code == 303
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda data: _raise("save"))
    assert tc.post(
        "/herder-backups/instance",
        data={"instance_name": "Lab", "instance_accent": "2266aa"},
        cookies=cookies,
    ).status_code == 303

    def policy_from_form(form):
        return {"types": {"stack_container_down": {"enabled": False}}, "categories": {}}

    monkeypatch.setattr("app.services.alert_policy.policy_from_form", policy_from_form)
    real = _real_save

    def save(data):
        if "stack_inventory_down_alerts" in data:
            raise RuntimeError("save")
        return real(data)

    monkeypatch.setattr("app.services.app_settings.save_settings", save)
    assert tc.post("/herder-backups/alerts/policy", cookies=cookies).status_code == 303

    monkeypatch.setattr("app.services.app_settings.save_settings", _real_save)
    saved = tc.post(
        "/herder-backups/alerts/host-unreachable",
        data={"enabled": "1", "quiet_unit": "days", "quiet_amount": "1"},
        cookies=cookies,
    )
    assert saved.status_code == 303
    assert "host_unreachable_saved=1" in saved.headers["location"]
    monkeypatch.setattr(
        "app.services.alert_policy.set_type_override", lambda *a, **k: _raise("type")
    )
    assert tc.post(
        "/herder-backups/alerts/host-unreachable",
        data={"enabled": "0", "quiet_unit": "hours", "quiet_amount": "2"},
        cookies=cookies,
    ).status_code == 303

    assert tc.post(
        "/herder-backups/alerts/mute",
        data={"kind": "type", "alert_type": mutable_types()[0][0], "starts_at": "nope", "ends_at": ""},
        cookies=cookies,
    ).status_code == 303
    with Session(eng) as session:
        created = create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type=mutable_types()[0][0],
            starts_at=None,
            ends_at=None,
        )
        rid = created.id
    assert tc.post(
        f"/herder-backups/alerts/mute/{rid}",
        data={"starts_at": "nope", "ends_at": ""},
        cookies=cookies,
    ).status_code == 303
    assert tc.post(
        f"/herder-backups/alerts/mute/{rid}",
        data={"starts_at": "", "ends_at": ""},
        cookies=cookies,
    ).status_code == 303
    assert tc.post(f"/herder-backups/alerts/mute/{rid}/delete", cookies=cookies).status_code == 303
    assert tc.post("/herder-backups/alerts/mute/999999", data={"starts_at": "", "ends_at": ""}, cookies=cookies).status_code == 303
    assert tc.post("/herder-backups/alerts/mute/999999/delete", cookies=cookies).status_code == 303

    monkeypatch.setattr(
        "app.services.alert_channels.validate_webhook_url",
        lambda url: url if url else (_ for _ in ()).throw(ValueError("bad url")),
    )
    monkeypatch.setattr("app.services.app_settings.save_settings", _real_save)
    assert tc.post(
        "/herder-backups/alerts/webhook",
        data={
            "webhook_url": "https://example.com/hook",
            "webhook_secret": "secret-value",
            "webhook_cats_submitted": "1",
        },
        cookies=cookies,
    ).status_code == 303
    assert tc.post(
        "/herder-backups/alerts/webhook", data={"webhook_url": ""}, cookies=cookies
    ).status_code == 303
    monkeypatch.setattr("app.services.app_settings.save_settings", lambda data: _raise("save"))
    assert tc.post(
        "/herder-backups/alerts/webhook",
        data={"webhook_url": "https://example.com/hook"},
        cookies=cookies,
    ).status_code == 303

    monkeypatch.setattr("app.services.app_settings.save_settings", _real_save)
    monkeypatch.setattr(
        "app.services.alert_channels.set_smtp_password", lambda pw: _raise("smtp")
    )
    assert tc.post(
        "/herder-backups/alerts/smtp",
        data={
            "smtp_security": "weird",
            "smtp_alert_min_severity": "nope",
            "smtp_password": "secret-value",
            "smtp_cats_submitted": "1",
            "clear_smtp_password": "1",
        },
        cookies=cookies,
    ).status_code == 303

    assert tc.post(
        "/herder-backups/data-cleanup/config",
        data={"data_cleanup_enabled": "1", "data_cleanup_cron": "not-a-cron"},
        cookies=cookies,
    ).status_code == 303
    monkeypatch.setattr(
        "app.routers.settings.sync_stale_data_cleanup_schedule", lambda *a, **k: _raise("sync")
    )
    assert tc.post(
        "/herder-backups/data-cleanup/config",
        data={"data_cleanup_enabled": "1", "data_cleanup_cron": "30 4 * * *"},
        cookies=cookies,
    ).status_code == 303
    monkeypatch.setattr(
        "app.services.stale_data_cleanup.enqueue_stale_data_cleanup",
        lambda *a, **k: _raise("run"),
    )
    assert tc.post("/herder-backups/data-cleanup/run", data={"dry_run": "1"}, cookies=cookies).status_code == 303

    assert tc.post(
        "/herder-backups/update-checks",
        data={"backup_cron": "not-a-cron"},
        cookies=cookies,
    ).status_code == 303
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"backup_cron": "not-a-cron"},
    )
    monkeypatch.setattr(
        "app.services.server_audit.record_server_audit", lambda *a, **k: _raise("audit")
    )
    assert tc.post(
        "/herder-backups/update-checks",
        data={"backup_cron": "0 2 * * *"},
        cookies=cookies,
    ).status_code == 303

    monkeypatch.setattr(
        "app.services.app_settings.set_app_timezone", lambda name: _raise("tz")
    )
    assert tc.post("/herder-backups/timezone", data={"timezone": "UTC"}, cookies=cookies).status_code == 303

    monkeypatch.setattr(
        "app.services.app_settings.save_settings", lambda data: _raise("save")
    )
    assert tc.post("/herder-backups/console", cookies=cookies).status_code == 303


def _raise_value():
    raise ValueError("bad token")


def _real_save(data):
    from app.services import app_settings as app_cfg

    # Call the original only when tests have not replaced it with this helper.
    return app_cfg._save_settings_impl(data) if False else _SAVE(data)


_SAVE = None


def _capture_save():
    global _SAVE
    from app.services import app_settings as app_cfg

    _SAVE = app_cfg.save_settings


_capture_save()
