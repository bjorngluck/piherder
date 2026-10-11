"""Fleet backup schedule: cron flag, audit wording, and the no-schedule alert."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Notification, Server
import app.services.jobs as job_svc
from app.services import insights


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_cron_backup_is_marked_scheduled_and_backup_now_is_not(monkeypatch):
    messages = []

    def _audit(*_args, **kwargs):
        messages.append(kwargs.get("message"))

    class _Task:
        id = "celery-1"

        def delay(self, *_args, **_kwargs):
            return self

    monkeypatch.setattr(job_svc, "HAS_CELERY", True)
    monkeypatch.setattr(job_svc, "backup_server", _Task())
    monkeypatch.setattr(job_svc, "record_backup_audit_event", _audit)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    engine = _engine()
    with Session(engine) as session:
        cron_host = Server(name="Cron", hostname="cron.local", backup_enabled=True)
        manual_host = Server(name="Manual", hostname="manual.local", backup_enabled=True)
        session.add(cron_host)
        session.add(manual_host)
        session.commit()
        session.refresh(cron_host)
        session.refresh(manual_host)

        cron_job = job_svc.enqueue_backup_for_server(session, cron_host, scheduled=True)
        manual_job = job_svc.enqueue_backup_for_server(session, manual_host, user_id=1)
        import json

        assert json.loads(cron_job.details)["scheduled"] is True
        assert json.loads(manual_job.details)["scheduled"] is False
        assert messages[0] == "Scheduled backup requested for all sources"
        assert messages[2] == "Backup requested for all sources"


def test_stale_alert_names_a_missing_schedule():
    engine = _engine()
    with Session(engine) as session:
        bare = Server(
            name="Bare",
            hostname="bare.local",
            backup_enabled=True,
            last_backup_at=None,
        )
        timed = Server(
            name="Timed",
            hostname="timed.local",
            backup_enabled=True,
            backup_schedule="0 2 * * *",
            last_backup_at=datetime.utcnow(),
        )
        session.add(bare)
        session.add(timed)
        session.commit()
        assert insights.sync_stale_backup_alerts(session) == 1
        rows = list(session.exec(select(Notification).where(Notification.status == "open")).all())
        assert len(rows) == 1
        assert "no schedule" in rows[0].body
        assert "nothing is queued" in rows[0].body
