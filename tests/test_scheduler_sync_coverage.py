"""Deep coverage for scheduler.py — MagicMock APScheduler, no live cron/DB."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import scheduler as sched_mod


def _sched():
    s = MagicMock()
    s.remove_job.side_effect = Exception("missing")  # exercise swallow
    return s


def _server(**kw):
    defaults = dict(
        id=9,
        name="lab",
        backup_enabled=True,
        backup_schedule="0 2 * * *",
        os_check_enabled=True,
        os_check_schedule="15 3 * * *",
        container_check_enabled=True,
        container_check_schedule="30 3 * * *",
        os_patch_enabled=True,
        os_apply_enabled=True,
        os_apply_schedule="0 4 * * 0",
        container_patch_enabled=True,
        container_apply_enabled=True,
        container_apply_schedule="0 5 * * 0",
        os_apply_only_if_updates=True,
        os_updates_count=2,
        container_apply_only_if_updates=True,
        container_updates_count=1,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _db_session(server=None, servers=None, *, missing_server=False):
    db = MagicMock()
    if missing_server:
        db.get.return_value = None
    elif server is not None:
        db.get.return_value = server
    if servers is not None:
        db.exec.return_value.all.return_value = servers
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=db)


def test_remove_job_and_server_cron_ids():
    s = _sched()
    sched_mod._remove_job(s, "x")
    s.remove_job.assert_called_with("x")
    ids = sched_mod.server_cron_job_ids(3)
    assert ids == [
        "backup_3",
        "os_check_3",
        "container_check_3",
        "os_apply_3",
        "container_apply_3",
    ]


def test_unregister_server_cron_jobs():
    s = _sched()
    sched_mod.unregister_server_cron_jobs(s, True, 7)
    assert s.remove_job.call_count == 5
    # early outs
    sched_mod.unregister_server_cron_jobs(None, True, 7)
    sched_mod.unregister_server_cron_jobs(s, False, 7)
    sched_mod.unregister_server_cron_jobs(s, True, 0)


def test_sync_server_cron_jobs_all_arms():
    s = _sched()
    server = _server()
    with patch("app.services.app_settings.get_app_timezone", return_value="UTC"):
        sched_mod.sync_server_cron_jobs(s, True, server)
    job_ids = {c.kwargs.get("id") for c in s.add_job.call_args_list}
    assert "backup_9" in job_ids
    assert "os_check_9" in job_ids
    assert "container_check_9" in job_ids
    assert "os_apply_9" in job_ids
    assert "container_apply_9" in job_ids

    # disabled / missing schedule → only removes
    s2 = _sched()
    off = _server(
        backup_enabled=False,
        os_check_enabled=False,
        container_check_enabled=False,
        os_apply_enabled=False,
        container_apply_enabled=False,
    )
    sched_mod.sync_server_cron_jobs(s2, True, off)
    assert s2.add_job.call_count == 0
    assert s2.remove_job.call_count == 5

    # early outs
    sched_mod.sync_server_cron_jobs(s, False, server)
    sched_mod.sync_server_cron_jobs(s, True, None)
    sched_mod.sync_server_cron_jobs(s, True, SimpleNamespace(id=None))


def test_sync_server_cron_jobs_bad_cron_swallowed():
    s = _sched()
    server = _server(backup_schedule="not-a-cron")
    with patch("app.services.app_settings.get_app_timezone", return_value=None):
        sched_mod.sync_server_cron_jobs(s, True, server)  # must not raise


def test_sync_all_server_cron_jobs():
    s = _sched()
    servers = [_server(id=1), _server(id=2, name="b")]
    session_cls = _db_session(servers=servers)
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch.object(sched_mod, "sync_server_cron_jobs") as sync_one:
                sched_mod.sync_all_server_cron_jobs(s, True)
    assert sync_one.call_count == 2
    sched_mod.sync_all_server_cron_jobs(None, False)


def test_global_interval_syncs():
    s = _sched()
    for fn in (
        sched_mod.sync_docker_inventory_schedule,
        sched_mod.sync_stack_health_schedule,
        sched_mod.sync_integrations_poll_schedule,
        sched_mod.sync_template_drift_schedule,
        sched_mod.sync_cert_renew_schedule,
    ):
        s.reset_mock()
        s.remove_job.side_effect = Exception("missing")
        fn(s, True)
        assert s.add_job.called, fn.__name__
        fn(None, False)  # early out


def test_sync_herder_backup_schedule_paths():
    s = _sched()
    # disabled
    with patch(
        "app.services.app_settings.load_settings",
        return_value={"schedule_enabled": False},
    ):
        sched_mod.sync_herder_backup_schedule(s, True)
    assert s.add_job.call_count == 0

    # enabled no cron
    s2 = _sched()
    with patch(
        "app.services.app_settings.load_settings",
        return_value={"schedule_enabled": True, "schedule_cron": ""},
    ):
        sched_mod.sync_herder_backup_schedule(s2, True)
    assert s2.add_job.call_count == 0

    # enabled with cron
    s3 = _sched()
    with patch(
        "app.services.app_settings.load_settings",
        return_value={"schedule_enabled": True, "schedule_cron": "0 1 * * *"},
    ):
        with patch("app.services.app_settings.validate_cron_expression"):
            with patch(
                "app.services.app_settings.get_app_timezone", return_value="UTC"
            ):
                sched_mod.sync_herder_backup_schedule(s3, True)
    assert s3.add_job.called
    assert s3.add_job.call_args.kwargs.get("id") == sched_mod.HERDER_SCHEDULE_JOB_ID

    sched_mod.sync_herder_backup_schedule(None, False)


def test_sync_stale_data_cleanup_schedule_paths():
    s = _sched()
    with patch(
        "app.services.stale_data_cleanup.cleanup_config",
        return_value={"enabled": False},
    ):
        sched_mod.sync_stale_data_cleanup_schedule(s, True)
    assert s.add_job.call_count == 0

    s2 = _sched()
    with patch(
        "app.services.stale_data_cleanup.cleanup_config",
        return_value={
            "enabled": True,
            "jobs_enabled": False,
            "audit_enabled": False,
            "nmap_enabled": False,
        },
    ):
        sched_mod.sync_stale_data_cleanup_schedule(s2, True)
    assert s2.add_job.call_count == 0

    s3 = _sched()
    with patch(
        "app.services.stale_data_cleanup.cleanup_config",
        return_value={
            "enabled": True,
            "jobs_enabled": True,
            "audit_enabled": False,
            "nmap_enabled": False,
            "cron": "0 3 * * *",
        },
    ):
        with patch("app.services.app_settings.validate_cron_expression"):
            with patch(
                "app.services.app_settings.get_app_timezone", return_value="UTC"
            ):
                sched_mod.sync_stale_data_cleanup_schedule(s3, True)
    assert s3.add_job.called
    assert s3.add_job.call_args.kwargs.get("id") == sched_mod.STALE_DATA_CLEANUP_JOB_ID


def test_sync_nmap_schedules_delegates():
    with patch(
        "app.services.nmap.schedules.sync_nmap_schedules", return_value=2
    ) as inner:
        assert sched_mod.sync_nmap_schedules(MagicMock(), True) == 2
        inner.assert_called_once()
    with patch(
        "app.services.nmap.schedules.sync_nmap_schedules",
        side_effect=RuntimeError("boom"),
    ):
        assert sched_mod.sync_nmap_schedules(MagicMock(), True) == 0


def test_schedule_job_runners_enqueue():
    server = _server(id=5, backup_enabled=True, os_check_enabled=True, container_check_enabled=True)
    session_cls = _db_session(server=server)
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch("app.services.jobs.enqueue_backup_for_server") as eb:
                sched_mod.schedule_backup_job(5)
                eb.assert_called_once()
            with patch("app.services.jobs.enqueue_os_update_check") as eo:
                sched_mod.schedule_os_check_job(5)
                eo.assert_called_once_with(5)
            with patch("app.services.jobs.enqueue_container_update_check") as ec:
                sched_mod.schedule_container_check_job(5)
                ec.assert_called_once_with(5)

    # disabled / missing server
    off = _server(backup_enabled=False, os_check_enabled=False, container_check_enabled=False)
    session_cls2 = _db_session(server=off)
    with patch("app.services.scheduler.Session", session_cls2):
        with patch("app.database.engine", MagicMock()):
            with patch("app.services.jobs.enqueue_backup_for_server") as eb:
                sched_mod.schedule_backup_job(1)
                eb.assert_not_called()
            with patch("app.services.jobs.enqueue_os_update_check") as eo:
                sched_mod.schedule_os_check_job(1)
                eo.assert_not_called()

    # exception path
    with patch("app.services.scheduler.Session", side_effect=RuntimeError("db")):
        sched_mod.schedule_backup_job(1)
        sched_mod.schedule_os_check_job(1)
        sched_mod.schedule_container_check_job(1)


def test_schedule_apply_jobs_paths():
    # no_updates skip for os
    server = _server(os_updates_count=0)
    session_cls = _db_session(server=server)
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch("app.services.jobs.enqueue_os_patch_apply") as en:
                sched_mod.schedule_os_apply_job(1)
                en.assert_not_called()

    # success enqueue os
    server2 = _server(id=4)
    session_cls2 = _db_session(server=server2)
    with patch("app.services.scheduler.Session", session_cls2):
        with patch("app.database.engine", MagicMock()):
            with patch(
                "app.services.jobs.enqueue_os_patch_apply",
                return_value=SimpleNamespace(id=11),
            ) as en:
                sched_mod.schedule_os_apply_job(4)
                en.assert_called_once()

    # missing server
    session_cls3 = _db_session(missing_server=True)
    with patch("app.services.scheduler.Session", session_cls3):
        with patch("app.database.engine", MagicMock()):
            with patch("app.services.jobs.enqueue_os_patch_apply") as en:
                sched_mod.schedule_os_apply_job(99)
                en.assert_not_called()
            with patch("app.services.jobs.enqueue_container_patch_apply") as en:
                sched_mod.schedule_container_apply_job(99)
                en.assert_not_called()

    # exception
    with patch("app.services.scheduler.Session", side_effect=RuntimeError("x")):
        sched_mod.schedule_os_apply_job(1)
        sched_mod.schedule_container_apply_job(1)


def test_fleet_and_global_job_bodies():
    with patch("app.services.integrations.poll.poll_all_enabled") as p:
        sched_mod.schedule_integrations_poll_job()
        p.assert_called_once_with(notify=True)
    with patch(
        "app.services.integrations.poll.poll_all_enabled", side_effect=RuntimeError("x")
    ):
        sched_mod.schedule_integrations_poll_job()  # no raise

    with patch("app.services.stack_health.run_stack_health_check") as sh:
        with patch("app.main.HAS_SCHEDULER", True, create=True):
            with patch("app.main.scheduler", MagicMock(), create=True):
                sched_mod.schedule_stack_health_job()
        assert sh.called
    with patch(
        "app.services.stack_health.run_stack_health_check", side_effect=RuntimeError("x")
    ):
        sched_mod.schedule_stack_health_job()

    session_cls = _db_session()
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch(
                "app.services.certificates.check_expiring_and_renew",
                return_value=[{"cert_id": 1}],
            ) as cr:
                sched_mod.schedule_cert_renew_job()
                cr.assert_called_once()
    with patch("app.services.scheduler.Session", side_effect=RuntimeError("x")):
        sched_mod.schedule_cert_renew_job()

    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch(
                "app.services.service_templates.deploy.check_all_deployments_drift",
                return_value={"ok": True},
            ) as dr:
                sched_mod.run_template_drift_checks()
                dr.assert_called_once()
    with patch("app.services.scheduler.Session", side_effect=RuntimeError("x")):
        sched_mod.run_template_drift_checks()

    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch(
                "app.services.stale_data_cleanup.enqueue_stale_data_cleanup",
                return_value=SimpleNamespace(id=5),
            ) as en:
                sched_mod.schedule_stale_data_cleanup_job()
                en.assert_called_once()
    with patch("app.services.scheduler.Session", side_effect=RuntimeError("x")):
        sched_mod.schedule_stale_data_cleanup_job()


def test_schedule_docker_inventory_fleet():
    servers = [
        _server(id=1, container_patch_enabled=True),
        _server(id=2, container_patch_enabled=True),
        SimpleNamespace(id=None, container_patch_enabled=True),
    ]
    session_cls = _db_session(servers=servers)
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.database.engine", MagicMock()):
            with patch(
                "app.services.docker_inventory.is_stale", return_value=True
            ):
                with patch(
                    "app.services.docker_inventory.refresh_server_inventory"
                ) as ref:
                    # select().where returns our list via db.exec
                    sched_mod.schedule_docker_inventory_fleet()
                    assert ref.call_count >= 1

    with patch("app.services.scheduler.Session", side_effect=RuntimeError("x")):
        sched_mod.schedule_docker_inventory_fleet()


def test_schedule_herder_backup_job_success_and_fail():
    session_cls = _db_session()
    with patch(
        "app.services.app_settings.load_settings",
        return_value={"schedule_mode": "config_only"},
    ):
        with patch(
            "app.services.herder_backup.create_herder_backup",
            return_value=SimpleNamespace(name="b.tar.gz"),
        ):
            with patch("app.services.scheduler.Session", session_cls):
                with patch("app.database.engine", MagicMock()):
                    with patch(
                        "app.services.audit_write.make_audit_log",
                        return_value=MagicMock(),
                    ):
                        with patch(
                            "app.services.notifications.resolve_by_fingerprint"
                        ):
                            sched_mod.schedule_herder_backup_job()

    # failure → notification
    with patch(
        "app.services.app_settings.load_settings",
        return_value={"schedule_mode": "full"},
    ):
        with patch(
            "app.services.herder_backup.create_herder_backup",
            side_effect=RuntimeError("disk full"),
        ):
            with patch("app.services.scheduler.Session", session_cls):
                with patch("app.database.engine", MagicMock()):
                    with patch(
                        "app.services.notifications.upsert_notification"
                    ) as un:
                        sched_mod.schedule_herder_backup_job()
                        un.assert_called()


def test_cron_trigger_with_timezone():
    t = sched_mod._cron_trigger("0 1 * * *", timezone="UTC")
    assert t is not None
