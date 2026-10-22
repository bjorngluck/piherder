"""Container checks stopped when Apply cleared the flag and left the cron."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Server
from app.services import scheduler as sched_mod
from app.services.update_check_config import apply_global_update_checks_to_all


def _server(**kw):
    defaults = dict(
        id=4,
        name="host-a",
        backup_enabled=False,
        backup_schedule=None,
        os_check_enabled=False,
        os_check_schedule=None,
        container_check_enabled=False,
        container_check_schedule="19 0 * * *",
        os_patch_enabled=False,
        os_apply_enabled=False,
        os_apply_schedule=None,
        container_patch_enabled=False,
        container_apply_enabled=False,
        container_apply_schedule=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _sched():
    scheduler = MagicMock()
    scheduler.remove_job.side_effect = Exception("missing")
    return scheduler


def test_saved_cron_registers_while_the_fleet_switch_is_on():
    server = _server()
    scheduler = _sched()
    with patch.object(sched_mod, "_container_checks_globally_enabled", return_value=True):
        with patch("app.services.app_settings.get_app_timezone", return_value="UTC"):
            sched_mod.sync_server_cron_jobs(scheduler, True, server)
    job_ids = {c.kwargs.get("id") for c in scheduler.add_job.call_args_list}
    assert "container_check_4" in job_ids
    assert server.container_check_enabled is True


def test_saved_cron_stays_off_when_the_fleet_switch_is_off():
    server = _server()
    scheduler = _sched()
    with patch.object(sched_mod, "_container_checks_globally_enabled", return_value=False):
        with patch("app.services.app_settings.get_app_timezone", return_value="UTC"):
            sched_mod.sync_server_cron_jobs(scheduler, True, server)
    assert scheduler.add_job.call_count == 0
    assert server.container_check_enabled is False


def test_fire_enqueues_when_the_flag_is_off_and_the_fleet_switch_is_on():
    server = _server()
    session = MagicMock()
    session.get.return_value = server
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    with patch.object(sched_mod, "_container_checks_globally_enabled", return_value=True):
        with patch("app.services.scheduler.Session", return_value=session):
            with patch("app.database.engine", MagicMock()):
                with patch("app.services.jobs.enqueue_container_update_check") as enqueue:
                    sched_mod.schedule_container_check_job(4)
    enqueue.assert_called_once_with(4)
    assert server.container_check_enabled is True


def test_apply_off_clears_the_cron_so_sync_does_not_put_it_back(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gap.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Server(
                name="host-a",
                hostname="host-a.local",
                ssh_username="pi",
                created_at=datetime.utcnow(),
                container_check_enabled=True,
                container_check_schedule="19 0 * * *",
            )
        )
        session.commit()
        apply_global_update_checks_to_all(
            session,
            os_enabled=True,
            os_cron="0 3 * * *",
            container_enabled=False,
            jitter=False,
            enable_feature_flags=False,
            enable_backups=False,
        )
        row = session.exec(select(Server)).one()
        assert row.container_check_enabled is False
        assert row.container_check_schedule is None
        scheduler = _sched()
        with patch("app.services.app_settings.get_app_timezone", return_value="UTC"):
            sched_mod.sync_server_cron_jobs(scheduler, True, row)
    job_ids = {c.kwargs.get("id") for c in scheduler.add_job.call_args_list}
    assert not any(str(job_id).startswith("container_check_") for job_id in job_ids)
