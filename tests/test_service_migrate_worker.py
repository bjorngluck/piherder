"""M-worker: Move on Celery, dual-host backup mutex, web recycle must not fail it."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from sqlmodel import Session, SQLModel, create_engine

from app.models import Job, Server
from app.services import jobs as job_service
from app.services import server_job_lock as lock


@pytest.fixture(autouse=True)
def _clean_local_locks():
    lock.clear_local_locks_for_tests()
    lock.reset_redis_client_for_tests()
    lock._redis_failed = True
    yield
    lock.clear_local_locks_for_tests()
    lock.reset_redis_client_for_tests()


def test_service_migrate_is_celery_owned_without_task_id():
    j = Job(job_type="service_migrate", status="running", celery_task_id="")
    assert job_service._is_celery_owned_job(j) is True
    assert "service_migrate" in job_service._CELERY_JOB_TYPES


def test_cleanup_orphan_web_jobs_keeps_running_move():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        srv = Server(name="pi", hostname="pi.local")
        s.add(srv)
        s.commit()
        s.refresh(srv)
        mig = Job(
            server_id=srv.id,
            job_type="service_migrate",
            status="running",
            details="{}",
        )
        patch = Job(
            server_id=srv.id,
            job_type="os_patch",
            status="running",
            details="{}",
        )
        s.add(mig)
        s.add(patch)
        s.commit()
        n = job_service.cleanup_orphan_web_jobs(s)
        s.refresh(mig)
        s.refresh(patch)
        assert n == 1
        assert mig.status == "running"
        assert patch.status == "failed"


def test_service_migrate_task_retries_when_dual_lock_busy():
    from app.tasks import service_migrate

    job = SimpleNamespace(
        id=9,
        status="pending",
        celery_task_id=None,
        details="{}",
    )
    mock_db = MagicMock()
    mock_db.get.return_value = job

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks.try_acquire_dual_server_lock", return_value=None),
        patch("app.tasks._update_job_status") as upd,
        patch.object(service_migrate, "retry", side_effect=Retry(message="wait")) as retry,
    ):
        with pytest.raises(Retry):
            service_migrate.run(9, 1, 2, "grafana", 3)

    retry.assert_called_once()
    assert retry.call_args.kwargs.get("countdown") == 20
    upd.assert_called()


def test_service_migrate_task_fails_redelivered_running_job():
    from app.tasks import service_migrate

    job = SimpleNamespace(id=9, status="running", celery_task_id="old", details="{}")
    mock_db = MagicMock()
    mock_db.get.return_value = job

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks.try_acquire_dual_server_lock") as acq,
        patch(
            "app.services.jobs_migrate.fail_migrate_worker_restart"
        ) as fail,
    ):
        out = service_migrate.run(9, 1, 2, "grafana", 3)

    assert out["reason"] == "worker_restart"
    fail.assert_called_once_with(9, 3)
    acq.assert_not_called()


def test_service_migrate_task_runs_execute_after_lock():
    from app.tasks import service_migrate

    job = SimpleNamespace(id=9, status="pending", celery_task_id=None, details="{}")
    mock_db = MagicMock()
    mock_db.get.return_value = job

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch(
            "app.tasks.try_acquire_dual_server_lock",
            return_value=("tok-a", "tok-b"),
        ),
        patch("app.tasks.release_dual_server_lock") as rel,
        patch("app.services.jobs_migrate._execute_service_migrate") as exe,
    ):
        out = service_migrate.run(9, 4, 5, "n8n", 8)

    assert out["status"] == "ok"
    exe.assert_called_once()
    rel.assert_called_once_with("backup", 4, "tok-a", 5, "tok-b")


def test_enqueue_uses_celery_when_not_inline(monkeypatch):
    from app.services import jobs_migrate as jm

    monkeypatch.setattr(jm, "_migrate_run_inline", lambda: False)
    delay = MagicMock()
    delay.return_value = SimpleNamespace(id="celery-mig-1")
    monkeypatch.setattr(job_service, "HAS_CELERY", True)
    monkeypatch.setattr(job_service, "service_migrate_task", SimpleNamespace(delay=delay))

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = Server(name="a", hostname="a.local")
        dest = Server(name="b", hostname="b.local")
        s.add(src)
        s.add(dest)
        s.commit()
        s.refresh(src)
        s.refresh(dest)
        src_id, dest_id = src.id, dest.id

    monkeypatch.setattr(job_service, "_get_fresh_session", lambda: Session(engine))
    job = job_service.enqueue_service_migrate(src_id, dest_id, "grafana")
    delay.assert_called_once()
    assert job.celery_task_id == "celery-mig-1"
    with Session(engine) as s:
        row = s.get(Job, job.id)
        assert row.status == "pending"
        assert row.celery_task_id == "celery-mig-1"


def test_execute_fails_when_backup_lock_held():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = Server(name="a", hostname="a.local")
        dest = Server(name="b", hostname="b.local")
        s.add(src)
        s.add(dest)
        s.commit()
        s.refresh(src)
        s.refresh(dest)
        job = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="pending",
            details='{"project":"grafana","dest_server_id":%s}' % dest.id,
        )
        audit_action = "service_migrate"
        from app.models import AuditLog

        audit = AuditLog(
            server_id=src.id,
            action=audit_action,
            status="running",
            details="Job #pending",
        )
        s.add(job)
        s.add(audit)
        s.commit()
        s.refresh(job)
        s.refresh(audit)
        jid, aid, sid, did = job.id, audit.id, src.id, dest.id

    held = lock.try_acquire_server_lock("backup", sid, holder="backup-job")
    assert held
    monkeypatch_engine = engine

    def _fresh():
        return Session(monkeypatch_engine)

    with (
        patch.object(job_service, "_get_fresh_session", _fresh),
        patch.object(job_service, "_load_server_for_job", return_value=(None, "a.local")),
    ):
        from app.services.jobs_migrate import _run_migrate_holding_locks

        _run_migrate_holding_locks(jid, sid, did, "grafana", aid)

    with Session(engine) as s:
        row = s.get(Job, jid)
        assert row.status == "failed"
        assert "backup or Move" in (row.details or "")
    assert lock.is_server_locked("backup", sid)


def test_service_migrate_task_fail_closes_unexpected_error():
    from app.tasks import service_migrate

    job = SimpleNamespace(id=9, status="pending", celery_task_id=None, details="{}")
    mock_db = MagicMock()
    mock_db.get.return_value = job

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch(
            "app.tasks.try_acquire_dual_server_lock",
            return_value=("tok-a", "tok-b"),
        ),
        patch("app.tasks.release_dual_server_lock"),
        patch(
            "app.services.jobs_migrate._execute_service_migrate",
            side_effect=RuntimeError("boom"),
        ),
        patch("app.services.jobs.service._finish") as fin,
        patch(
            "app.services.jobs.service._load_server_for_job",
            return_value=(None, "pi"),
        ),
    ):
        out = service_migrate.run(9, 1, 2, "grafana", 3)

    assert out["status"] == "error"
    fin.assert_called_once()
    assert fin.call_args.args[2] == "failed"


def test_worker_restart_no_recover_source_on_dest_up():
    """dest_up may already have dest running — do not Start source stack."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    from app.models import AuditLog

    with Session(engine) as s:
        src = Server(name="a", hostname="a.local")
        s.add(src)
        s.commit()
        s.refresh(src)
        job = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="running",
            details='{"project":"grafana","migrate_step":"dest_up"}',
        )
        audit = AuditLog(
            server_id=src.id, action="service_migrate", status="running", details=""
        )
        s.add(job)
        s.add(audit)
        s.commit()
        s.refresh(job)
        s.refresh(audit)
        jid, aid = job.id, audit.id

    def _fresh():
        return Session(engine)

    with patch.object(job_service, "_get_fresh_session", _fresh):
        job_service.fail_migrate_worker_restart(jid, aid)

    with Session(engine) as s:
        row = s.get(Job, jid)
        assert row.status == "failed"
        det = __import__("json").loads(row.details or "{}")
        assert not det.get("recover_source")


def test_worker_restart_recover_source_only_on_copy_step():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    from app.models import AuditLog

    with Session(engine) as s:
        src = Server(name="a", hostname="a.local")
        s.add(src)
        s.commit()
        s.refresh(src)
        job = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="running",
            details='{"project":"grafana","migrate_step":"cutover"}',
        )
        audit = AuditLog(
            server_id=src.id, action="service_migrate", status="running", details=""
        )
        s.add(job)
        s.add(audit)
        s.commit()
        s.refresh(job)
        s.refresh(audit)
        jid, aid = job.id, audit.id

    def _fresh():
        return Session(engine)

    with patch.object(job_service, "_get_fresh_session", _fresh):
        job_service.fail_migrate_worker_restart(jid, aid)

    with Session(engine) as s:
        row = s.get(Job, jid)
        assert row.status == "failed"
        det = __import__("json").loads(row.details or "{}")
        assert not det.get("recover_source")


def test_worker_restart_recover_source_on_copy_step():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    from app.models import AuditLog

    with Session(engine) as s:
        src = Server(name="a", hostname="a.local")
        s.add(src)
        s.commit()
        s.refresh(src)
        job = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="running",
            details='{"project":"grafana","migrate_step":"copy"}',
        )
        audit = AuditLog(
            server_id=src.id, action="service_migrate", status="running", details=""
        )
        s.add(job)
        s.add(audit)
        s.commit()
        s.refresh(job)
        s.refresh(audit)
        jid, aid, sid = job.id, audit.id, src.id

    def _fresh():
        return Session(engine)

    with (
        patch.object(job_service, "_get_fresh_session", _fresh),
        patch(
            "app.services.service_migrate.leftover.jailed_source_project_path",
            return_value="/home/pi/docker/grafana",
        ),
    ):
        job_service.fail_migrate_worker_restart(jid, aid)

    with Session(engine) as s:
        row = s.get(Job, jid)
        assert row.status == "failed"
        det = __import__("json").loads(row.details or "{}")
        assert det.get("recover_source", {}).get("server_id") == sid


def test_stale_cleanup_includes_service_migrate():
    from datetime import datetime, timedelta

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        srv = Server(name="a", hostname="a.local")
        s.add(srv)
        s.commit()
        s.refresh(srv)
        old = Job(
            server_id=srv.id,
            job_type="service_migrate",
            status="running",
            details="{}",
            created_at=datetime.utcnow() - timedelta(hours=5),
        )
        s.add(old)
        s.commit()
        n = job_service.cleanup_stale_backup_jobs(s, max_age_minutes=60)
        s.refresh(old)
        assert n == 1
        assert old.status == "failed"
