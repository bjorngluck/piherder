"""Opt-in catch-up for a missed backup or image check."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.database import get_engine
from app.models import Job, Server
from app.services import schedule_catchup as catch


def _now():
    return datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc)


def _server(session: Session, **extra) -> Server:
    server = Server(
        name="Host A",
        hostname="host-a.example.com",
        ssh_username="user",
        backup_enabled=True,
        backup_schedule="0 3 * * *",
        created_at=_now() - timedelta(days=3),
    )
    for key, value in extra.items():
        setattr(server, key, value)
    session.add(server)
    session.commit()
    session.refresh(server)
    return server


def test_reason_and_disallowed_kinds():
    slot = _now() - timedelta(hours=2)
    assert catch.miss_reason(slot, [], worker_up=False, now=_now()) == "scheduler_down"
    assert catch.miss_reason(slot, [], worker_up=True, now=_now()) == "unknown"
    late = _now() - timedelta(hours=30)
    assert catch.miss_reason(late, [], worker_up=True, now=_now()) == "clock"
    assert catch.miss_reason(late, [], worker_up=True, now=_now(), cron="0 3 * * 0") == "unknown"
    assert catch.miss_reason(late, [], worker_up=False, now=_now(), cron="0 3 1 * *") == "scheduler_down"
    week_gone = _now() - timedelta(days=9)
    assert catch.miss_reason(week_gone, [], worker_up=True, now=_now(), cron="0 3 * * 0") == "clock"
    month_gap = _now() - timedelta(days=9)
    assert catch.miss_reason(month_gap, [], worker_up=True, now=_now(), cron="0 3 1 * *") == "unknown"
    future = Job(job_type="backup", status="success", created_at=_now() + timedelta(hours=2))
    assert catch.miss_reason(late, [future], worker_up=True, now=_now(), cron="0 3 * * 0") == "clock"
    running = Job(
        job_type="backup",
        status="running",
        started_at=slot - timedelta(minutes=10),
        created_at=slot - timedelta(minutes=10),
    )
    assert catch.miss_reason(slot, [running], worker_up=True, now=_now()) == "overlap"
    failed = Job(
        job_type="backup",
        status="failed",
        created_at=slot + timedelta(minutes=1),
        finished_at=slot + timedelta(minutes=5),
    )
    assert catch.miss_reason(slot, [failed], worker_up=True, now=_now()) == "error"
    assert catch.catchup_enabled(Server(name="a", hostname="h"), "docker_prune") is False
    assert catch.catchup_enabled(Server(name="a", hostname="h", backup_catchup_enabled=True), "backup") is True


def test_opt_in_enqueues_once_and_skips_the_same_slot(monkeypatch):
    catch.reset_catchup_counters()
    seen = {}

    def _enqueue(session, server, scheduled=False, details_extra=None):
        import json

        payload = {"catchup": True}
        payload.update(details_extra or {})
        job = Job(
            server_id=server.id,
            job_type="backup",
            status="pending",
            details=json.dumps(payload),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        seen["scheduled"] = scheduled
        seen["details"] = payload
        return job

    monkeypatch.setattr("app.services.jobs.enqueue_backup_for_server", _enqueue)
    slot = _now() - timedelta(hours=2)
    with Session(get_engine()) as session:
        server = _server(session, backup_catchup_enabled=True)
        first = catch.catch_up_one(
            session, server, "backup", slot, [], worker_up=False, now=_now()
        )
        assert first == "queued"
        assert seen["scheduled"] is True
        job = session.exec(
            __import__("sqlmodel").select(Job).where(Job.server_id == server.id)
        ).first()
        details = __import__("json").loads(job.details)
        assert details["catchup_reason"] == "scheduler_down"
        assert details["catchup_slot"] == seen["details"]["catchup_slot"]
        second = catch.catch_up_one(
            session, server, "backup", slot, [job], worker_up=False, now=_now()
        )
        assert second == "skipped"
        off = _server(session, hostname="host-b.example.com", name="Host B")
        assert catch.catch_up_one(
            session, off, "backup", slot, [], worker_up=False, now=_now()
        ) == "skipped"
        prune = catch.catch_up_one(
            session, server, "docker_prune", slot, [], worker_up=False, now=_now()
        )
        assert prune == "skipped"
    assert catch._COUNTS["backup"]["queued"] == 1
    assert catch._COUNTS["backup"]["skipped"] == 0
    text = "\n".join(catch.catchup_counter_lines())
    assert 'kind="backup",result="queued"' in text
    assert "docker_prune" not in text


def test_active_run_and_enqueue_failure(monkeypatch):
    catch.reset_catchup_counters()
    slot = _now() - timedelta(hours=2)
    with Session(get_engine()) as session:
        server = _server(
            session,
            hostname="host-c.example.com",
            name="Host C",
            backup_catchup_enabled=True,
        )
        active = Job(server_id=server.id, job_type="backup", status="running", details="{}")
        session.add(active)
        session.commit()
        assert catch.catch_up_one(
            session, server, "backup", slot, [active], worker_up=True, now=_now()
        ) == "skipped"

        def _boom(*_a, **_k):
            raise RuntimeError("queue")

        monkeypatch.setattr("app.services.jobs.enqueue_backup_for_server", _boom)
        other = _server(
            session,
            hostname="host-d.example.com",
            name="Host D",
            backup_catchup_enabled=True,
        )
        assert catch.catch_up_one(
            session, other, "backup", slot, [], worker_up=True, now=_now()
        ) == "failed"
    assert catch._COUNTS["backup"]["failed"] == 1


def test_pending_or_active_catchup_is_silent(monkeypatch):
    from app.models import AuditLog

    catch.reset_catchup_counters()
    slot = _now() - timedelta(hours=2)
    key = catch.slot_key(slot)

    def _busy(*_a, **_k):
        from app.services.jobs import JobAlreadyActive

        raise JobAlreadyActive(None)

    monkeypatch.setattr("app.services.jobs.enqueue_backup_for_server", _busy)
    with Session(get_engine()) as session:
        server = _server(
            session,
            hostname="host-f.example.com",
            name="Host F",
            backup_catchup_enabled=True,
        )
        pending = Job(
            server_id=server.id,
            job_type="backup",
            status="pending",
            created_at=_now(),
            details='{"catchup_slot":"%s"}' % key,
        )
        session.add(pending)
        session.commit()
        before = len(list(session.exec(select(AuditLog)).all()))
        assert catch.catch_up_one(
            session, server, "backup", slot, [], worker_up=True, now=_now()
        ) == "skipped"
        assert len(list(session.exec(select(AuditLog)).all())) == before
        session.delete(pending)
        session.commit()
        assert catch.catch_up_one(
            session, server, "backup", slot, [], worker_up=True, now=_now()
        ) == "skipped"
        after = len(list(session.exec(select(AuditLog)).all()))
        assert after == before + 1
        again = catch.catch_up_one(
            session, server, "backup", slot, [], worker_up=True, now=_now()
        )
        assert again == "skipped"
        assert len(list(session.exec(select(AuditLog)).all())) == after
    assert catch._COUNTS["backup"]["queued"] == 0
    assert catch._COUNTS["backup"]["failed"] == 0
    assert catch._COUNTS["backup"]["skipped"] == 1


def test_a_failed_catchup_is_not_retried_and_old_rows_age_out(monkeypatch):
    from app.models import AuditLog

    catch.reset_catchup_counters()
    slot = _now() - timedelta(hours=2)
    key = catch.slot_key(slot)
    called = {"n": 0}

    def _enqueue(session, server, scheduled=False, details_extra=None):
        import json

        called["n"] += 1
        job = Job(
            server_id=server.id,
            job_type="backup",
            status="pending",
            details=json.dumps(details_extra or {}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    monkeypatch.setattr("app.services.jobs.enqueue_backup_for_server", _enqueue)
    with Session(get_engine()) as session:
        server = _server(
            session,
            hostname="host-g.example.com",
            name="Host G",
            backup_catchup_enabled=True,
        )
        failed = Job(
            server_id=server.id,
            job_type="backup",
            status="failed",
            created_at=(_now() - timedelta(days=2)).replace(tzinfo=None),
            details='{"catchup_slot":"%s"}' % key,
        )
        stale = Job(
            server_id=server.id,
            job_type="backup",
            status="failed",
            created_at=(_now() - timedelta(days=40)).replace(tzinfo=None),
            details='{"catchup_slot":"2000-01-01T00:00:00Z"}',
        )
        session.add(failed)
        session.add(stale)
        session.commit()
        before = len(list(session.exec(select(AuditLog).where(AuditLog.server_id == server.id)).all()))
        assert catch.catch_up_one(
            session, server, "backup", slot, [], worker_up=True, now=_now()
        ) == "skipped"
        assert called["n"] == 0
        assert len(list(session.exec(select(AuditLog).where(AuditLog.server_id == server.id)).all())) == before
        old_slot = datetime(2000, 1, 1, tzinfo=timezone.utc)
        assert catch.catch_up_one(
            session, server, "backup", old_slot, [], worker_up=False, now=_now()
        ) == "queued"
        assert called["n"] == 1
    assert catch._COUNTS["backup"]["failed"] == 0
    assert catch._COUNTS["backup"]["queued"] == 1


def test_due_catchup_ignores_a_host_that_is_off(monkeypatch):
    catch.reset_catchup_counters()
    called = {"n": 0}

    def _enqueue(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not enqueue")

    monkeypatch.setattr("app.services.jobs.enqueue_backup_for_server", _enqueue)
    monkeypatch.setattr(
        "app.services.jobs.celery_worker_activity",
        lambda: (False, set()),
    )
    moment = _now()
    with Session(get_engine()) as session:
        for row in session.exec(select(Server)).all():
            row.backup_catchup_enabled = False
            row.container_check_catchup_enabled = False
            session.add(row)
        session.commit()
        _server(session, hostname="host-e.example.com", name="Host E", backup_catchup_enabled=False)
        ran = catch.run_due_catchups(session, now=moment)
    assert ran == 0
    assert called["n"] == 0
