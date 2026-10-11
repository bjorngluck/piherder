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
        assert "nothing is queued" not in rows[0].body


def _settings(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.load_settings",
        lambda: {"backup_cron": "0 2 * * *", "update_check_jitter": True},
    )
    monkeypatch.setattr("app.services.app_settings.get_app_timezone", lambda: "UTC")


def test_fleet_stagger_is_unique_for_a_large_fleet():
    """Placement is a pure function of the host id. A thousand hosts do not share a minute."""
    from collections import Counter

    from app.services.update_check_config import fleet_backup_cron

    base = "0 2 * * *"
    first_hour = [fleet_backup_cron(base, i) for i in range(1, 61)]
    assert len(set(first_hour)) == 60
    for i in range(1, 30):
        minute, hour, _dom, _month, _wd = fleet_backup_cron(base, i).split()
        assert minute == str((i + 30) % 60)
        assert hour == "2"
    thousand = [fleet_backup_cron(base, i) for i in range(1, 1001)]
    assert len(set(thousand)) == 1000
    assert fleet_backup_cron(base, 1) != fleet_backup_cron(base, 61)
    # A host pushed past 23:00 shares a minute. It does not wrap to 00:00.
    late = fleet_backup_cron("0 23 * * *", 60)
    assert late.split()[1] == "23"
    assert late in {fleet_backup_cron("0 23 * * *", i) for i in range(60)}
    # A list or a step hour stays as written. Hosts 60 apart share the minute.
    assert fleet_backup_cron("0 2,14 * * *", 61).split()[1] == "2,14"
    assert fleet_backup_cron("0 2,14 * * *", 61) == fleet_backup_cron("0 2,14 * * *", 1)
    assert fleet_backup_cron("0 */6 * * *", 5).split()[1] == "*/6"
    weekly = fleet_backup_cron("0 2 * * 1", 61)
    assert weekly.split()[1] == "2"
    assert weekly.split()[4] == "1"
    # The fleet clock is one time. Hosts still do not share that exact minute.
    from app.services.update_check_config import host_backup_cron

    same_base = [host_backup_cron(base, i, jitter=False) for i in range(1, 61)]
    assert len(set(same_base)) == 60
    # Past a full day of minutes, a few hosts share a slot. They do not pile onto one minute.
    crowded = Counter(fleet_backup_cron(base, i) for i in range(1, 2001))
    assert max(crowded.values()) <= 2


def test_scheduler_uses_fleet_time_without_writing_it(monkeypatch):
    from app.services import scheduler as sched_mod

    _settings(monkeypatch)
    seen = []
    real = sched_mod._cron_trigger

    def _wrap(cron, timezone=None):
        seen.append(cron)
        return real(cron, timezone=timezone)

    monkeypatch.setattr(sched_mod, "_cron_trigger", _wrap)

    class _Sched:
        def __init__(self):
            self.added = 0

        def add_job(self, *args, **kwargs):
            self.added += 1

        def remove_job(self, _job_id):
            return None

    inherited = Server(id=5, name="host-a", hostname="host-a.local", backup_enabled=True)
    sched_mod.sync_server_cron_jobs(_Sched(), True, inherited)
    assert seen == ["35 2 * * *"]
    assert inherited.backup_schedule is None

    seen.clear()
    own = Server(
        id=5,
        name="host-a",
        hostname="host-a.local",
        backup_enabled=True,
        backup_schedule="15 6 * * *",
    )
    sched_mod.sync_server_cron_jobs(_Sched(), True, own)
    assert seen == ["15 6 * * *"]

    seen.clear()
    off = Server(
        id=5,
        name="host-a",
        hostname="host-a.local",
        backup_enabled=False,
        backup_schedule="15 6 * * *",
    )
    sched = _Sched()
    sched_mod.sync_server_cron_jobs(sched, True, off)
    assert seen == []
    assert sched.added == 0

    seen.clear()
    later = Server(id=8, name="host-b", hostname="host-b.local", backup_enabled=False)
    sched_mod.sync_server_cron_jobs(_Sched(), True, later)
    later.backup_enabled = True
    sched_mod.sync_server_cron_jobs(_Sched(), True, later)
    assert seen == ["38 2 * * *"]
    assert later.backup_schedule is None


def test_api_patch_resyncs_after_commit_and_reports_the_fleet_time(monkeypatch):
    from types import SimpleNamespace

    from app.routers.api_v1 import ApiAuth, ServerFeaturesBody, patch_server_features

    _settings(monkeypatch)
    engine = _engine()
    events = []

    def _sync(server):
        events.append("sync")
        with Session(engine) as other:
            row = other.get(Server, server.id)
            assert row.backup_enabled is True
            assert not (row.backup_schedule or "").strip()

    monkeypatch.setattr("app.routers.server_patch.sync_server_schedules", _sync)

    with Session(engine) as session:
        server = Server(id=5, name="host-a", hostname="host-a.local", backup_enabled=False)
        session.add(server)
        session.commit()

        def _commit():
            events.append("commit")
            Session.commit(session)

        session.commit = _commit
        auth = ApiAuth(SimpleNamespace(created_by_user_id=None, id=None, name="agent", scopes="edit"))
        result = patch_server_features(
            5,
            ServerFeaturesBody(backup=True),
            session,
            auth,
        )

    assert events == ["commit", "sync"]
    body = result["server"]
    assert body["backup_schedule"] is None
    assert body["effective_backup_schedule"] == "35 2 * * *"
    assert body["backup_schedule_source"] == "fleet"
    assert body["next_backup_run"] is None
    assert body["backup_scheduled"] is None
    assert "features" in body
    assert body["features"]["backup"] is True


def test_api_own_schedule_wins_and_off_has_no_effective_time(monkeypatch):
    from app.routers.api_v1 import _server_public

    _settings(monkeypatch)
    own = Server(
        id=4,
        name="host-a",
        hostname="host-a.local",
        backup_enabled=True,
        backup_schedule="15 6 * * *",
    )
    payload = _server_public(own)
    assert payload["backup_schedule"] == "15 6 * * *"
    assert payload["effective_backup_schedule"] == "15 6 * * *"
    assert payload["backup_schedule_source"] == "host"
    assert payload["next_backup_run"] is None
    assert payload["backup_scheduled"] is None

    off = Server(
        id=4,
        name="host-a",
        hostname="host-a.local",
        backup_enabled=False,
        backup_schedule="15 6 * * *",
    )
    quiet = _server_public(off)
    assert quiet["backup_schedule"] == "15 6 * * *"
    assert quiet["effective_backup_schedule"] is None
    assert quiet["backup_schedule_source"] is None
    assert quiet["next_backup_run"] is None
    assert quiet["backup_scheduled"] is False


def test_api_next_run_comes_from_the_job_and_clears_when_removed(monkeypatch):
    from datetime import timezone
    from types import SimpleNamespace

    from app.routers.api_v1 import ApiAuth, ServerFeaturesBody, _server_public, patch_server_features

    _settings(monkeypatch)

    class _Job:
        def __init__(self, when):
            self.next_run_time = when

    class _Sched:
        running = True

        def __init__(self):
            self.jobs = {}

        def add_job(self, *args, **kwargs):
            del args
            self.jobs[kwargs["id"]] = _Job(datetime(2026, 10, 12, 2, 35, tzinfo=timezone.utc))

        def remove_job(self, job_id):
            self.jobs.pop(job_id, None)

        def get_job(self, job_id):
            return self.jobs.get(job_id)

    sched = _Sched()
    monkeypatch.setattr("app.main.scheduler", sched)
    monkeypatch.setattr("app.main.HAS_SCHEDULER", True)
    engine = _engine()
    with Session(engine) as session:
        server = Server(id=5, name="host-a", hostname="host-a.local", backup_enabled=False)
        session.add(server)
        session.commit()
        auth = ApiAuth(SimpleNamespace(created_by_user_id=None, id=None, name="agent", scopes="edit"))
        result = patch_server_features(
            5,
            ServerFeaturesBody(backup=True),
            session,
            auth,
        )
        body = result["server"]
        assert body["backup_scheduled"] is True
        assert body["next_backup_run"] == "2026-10-12T02:35:00+00:00"
        assert body["effective_backup_schedule"] == "35 2 * * *"
        assert body["backup_schedule"] is None
        sched.remove_job("backup_5")
        session.refresh(server)
        quiet = _server_public(server)
        assert server.backup_enabled is True
        assert quiet["backup_scheduled"] is False
        assert quiet["next_backup_run"] is None
        assert quiet["effective_backup_schedule"] == "35 2 * * *"


def test_backup_scheduled_is_unknown_without_a_running_scheduler(monkeypatch):
    from app.routers.api_v1 import _server_public

    _settings(monkeypatch)
    host = Server(id=5, name="host-a", hostname="host-a.local", backup_enabled=True)
    monkeypatch.setattr("app.main.HAS_SCHEDULER", False)
    monkeypatch.setattr("app.main.scheduler", None)
    missing = _server_public(host)
    assert missing["backup_scheduled"] is None
    assert missing["next_backup_run"] is None
    assert missing["effective_backup_schedule"] == "35 2 * * *"

    monkeypatch.setattr("app.main.HAS_SCHEDULER", True)
    monkeypatch.setattr(
        "app.main.scheduler",
        type("Idle", (), {"running": False, "get_job": lambda self, _job: None})(),
    )
    idle = _server_public(host)
    assert idle["backup_scheduled"] is None
    assert idle["next_backup_run"] is None


def test_base_change_moves_an_inheriting_hosts_job(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.models import AuditLog
    from app.routers.settings import save_update_check_defaults
    from app.services import scheduler as sched_mod

    store = {
        "backup_cron": "0 2 * * *",
        "update_check_jitter": True,
        "os_check_global_enabled": True,
        "os_check_cron": "0 0 * * *",
        "container_check_global_enabled": True,
        "container_check_cron": "0 0 * * *",
    }

    def _load():
        return dict(store)

    def _save(partial):
        store.update(partial)
        return dict(store)

    monkeypatch.setattr("app.services.app_settings.load_settings", _load)
    monkeypatch.setattr("app.services.app_settings.save_settings", _save)
    monkeypatch.setattr("app.services.app_settings.get_app_timezone", lambda: "UTC")
    seen = []
    real = sched_mod._cron_trigger

    def _wrap(cron, timezone=None):
        seen.append(cron)
        return real(cron, timezone=timezone)

    monkeypatch.setattr(sched_mod, "_cron_trigger", _wrap)

    class _Sched:
        def add_job(self, *args, **kwargs):
            del args, kwargs

        def remove_job(self, job_id):
            del job_id

        def get_jobs(self):
            return []

    monkeypatch.setattr("app.main.scheduler", _Sched())
    monkeypatch.setattr("app.main.HAS_SCHEDULER", True)
    engine = _engine()
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.routers.settings.engine", engine)
    with Session(engine) as session:
        host = Server(id=5, name="host-a", hostname="host-a.local", backup_enabled=True)
        session.add(host)
        session.commit()

    def _save_form(cron):
        return asyncio.run(
            save_update_check_defaults(
                os_check_global_enabled="on",
                os_check_cron="0 0 * * *",
                container_check_global_enabled="on",
                container_check_cron="0 0 * * *",
                update_check_jitter="on",
                backup_cron=cron,
                apply_to_all=None,
                enable_feature_flags=None,
                enable_backups=None,
                user=SimpleNamespace(id=7),
            )
        )

    _save_form("0 4 * * *")
    assert seen == ["35 4 * * *"]
    with Session(engine) as session:
        host = session.get(Server, 5)
        assert host.backup_schedule is None
        assert host.backup_enabled is True
        rows = list(session.exec(select(AuditLog).where(AuditLog.action == "fleet_update_checks")))
        assert len(rows) == 1
        assert rows[0].user_id == 7
        assert rows[0].server_id is None
        import json

        details = json.loads(rows[0].details)
        assert details["backup_cron"] == {"from": "0 2 * * *", "to": "0 4 * * *"}
        assert details["apply_to_all"] is False
        assert details["applied"]["backup_scheduled"] == 0
        assert "host-a" not in rows[0].details
    seen.clear()
    _save_form("0 4 * * *")
    assert seen == []


def test_startup_cleanup_keeps_the_effective_minute(monkeypatch):
    import json

    from app.models import AuditLog
    from app.services.update_check_config import (
        clear_matching_fleet_backup_schedules,
        resolve_backup_schedule,
    )

    store = {"backup_cron": "0 2 * * *", "update_check_jitter": True}

    def _load():
        return dict(store)

    def _save(partial):
        store.update(partial)
        return dict(store)

    _settings(monkeypatch)
    monkeypatch.setattr("app.services.app_settings.load_settings", _load)
    monkeypatch.setattr("app.services.app_settings.save_settings", _save)
    engine = _engine()
    with Session(engine) as session:
        matched = Server(
            id=5,
            name="host-a",
            hostname="host-a.local",
            backup_enabled=True,
            backup_schedule="35 2 * * *",
        )
        custom = Server(
            id=6,
            name="host-b",
            hostname="host-b.local",
            backup_enabled=True,
            backup_schedule="15 6 * * *",
        )
        session.add(matched)
        session.add(custom)
        session.commit()
        before = {
            row.id: resolve_backup_schedule(row)["effective_backup_schedule"]
            for row in (matched, custom)
        }
        cleared = clear_matching_fleet_backup_schedules(session)
        session.refresh(matched)
        session.refresh(custom)
        assert cleared == 1
        assert matched.backup_schedule is None
        assert resolve_backup_schedule(matched)["effective_backup_schedule"] == before[5]
        assert resolve_backup_schedule(matched)["backup_schedule_source"] == "fleet"
        assert custom.backup_schedule == "15 6 * * *"
        assert resolve_backup_schedule(custom)["effective_backup_schedule"] == before[6]
        rows = list(session.exec(select(AuditLog)))
        assert len(rows) == 1
        assert rows[0].user_id is None
        assert rows[0].server_id is None
        assert rows[0].action == "fleet_backup_schedule_inherit"
        payload = json.loads(rows[0].details)
        assert payload["cleared"] == 1
        assert "host-a" not in rows[0].details
        assert "host-b" not in rows[0].details
        assert store["fleet_backup_inherit_cleaned"] is True
        matched.backup_schedule = "35 2 * * *"
        session.add(matched)
        session.commit()
        assert clear_matching_fleet_backup_schedules(session) == 0
        session.refresh(matched)
        assert matched.backup_schedule == "35 2 * * *"
        assert len(list(session.exec(select(AuditLog)))) == 1


def test_update_server_and_wizard_resync_without_writing_a_schedule(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.routers.server_wizard import wizard_features_post, wizard_identity_post
    from app.routers.servers import add_server, update_server
    from app.services import scheduler as sched_mod

    _settings(monkeypatch)
    seen = []
    real = sched_mod._cron_trigger

    def _wrap(cron, timezone=None):
        seen.append(cron)
        return real(cron, timezone=timezone)

    monkeypatch.setattr(sched_mod, "_cron_trigger", _wrap)

    class _Sched:
        def add_job(self, *args, **kwargs):
            del args, kwargs

        def remove_job(self, job_id):
            del job_id

    monkeypatch.setattr("app.main.scheduler", _Sched())
    monkeypatch.setattr("app.main.HAS_SCHEDULER", True)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    engine = _engine()
    user = SimpleNamespace(id=3)
    with Session(engine) as session:
        created = asyncio.run(
            wizard_identity_post(
                name="host-a",
                hostname="host-a.local",
                ssh_username="pi",
                ssh_port=22,
                session=session,
                user=user,
            )
        )
        assert created.status_code == 303
        row = session.exec(select(Server)).one()
        assert row.backup_enabled is True
        assert not (row.backup_schedule or "").strip()
        assert seen == [f"{(row.id + 30) % 60} 2 * * *"]
        seen.clear()

        added = asyncio.run(
            add_server(
                name="host-b",
                hostname="host-b.local",
                ssh_username="pi",
                ssh_port=22,
                key_mode="password",
                private_key="",
                ssh_password="secret-pass",
                session=session,
                user=user,
            )
        )
        assert added.status_code == 303
        hosts = list(session.exec(select(Server).order_by(Server.id)).all())
        assert len(hosts) == 2
        assert all(host.backup_enabled is True for host in hosts)
        assert all(not (host.backup_schedule or "").strip() for host in hosts)
        assert seen == [f"{(hosts[1].id + 30) % 60} 2 * * *"]
        seen.clear()

        target = hosts[0]
        target.backup_enabled = False
        session.add(target)
        session.commit()
        asyncio.run(
            update_server(
                server_id=target.id,
                name=target.name,
                hostname=target.hostname,
                ssh_username="pi",
                ssh_port=22,
                ssh_password="",
                clear_password=None,
                docker_base_dir="~/docker",
                os_type=None,
                include_dns=None,
                dns_name="",
                dns_ip_override="",
                ip_address="",
                dns_manage_a=None,
                dns_sync_now=None,
                backup_enabled=True,
                container_patch_enabled=False,
                os_patch_enabled=False,
                console_mux_enabled=False,
                session=session,
                user=user,
            )
        )
        session.refresh(target)
        assert target.backup_enabled is True
        assert target.backup_schedule is None
        assert seen == [f"{(target.id + 30) % 60} 2 * * *"]
        seen.clear()
        asyncio.run(
            wizard_features_post(
                server_id=hosts[1].id,
                backup_enabled="on",
                os_patch_enabled=None,
                container_patch_enabled=None,
                console_mux_enabled=None,
                session=session,
                user=user,
            )
        )
        session.refresh(hosts[1])
        assert hosts[1].backup_enabled is True
        assert hosts[1].backup_schedule is None
        assert seen == [f"{(hosts[1].id + 30) % 60} 2 * * *"]


def test_configure_builder_keeps_ranges_and_lists():
    from pathlib import Path

    from app.services.backup_cron_form import builder_submitted

    assert builder_submitted("2 6 * * *") == "2 6 * * *"
    assert builder_submitted("0 6 * * 1-5") == "0 6 * * 1-5"
    assert builder_submitted("0 6 1,15 * *") == "0 6 1,15 * *"
    assert builder_submitted("*/15 * * * *") == "*/15 * * * *"
    assert builder_submitted("") == ""

    script = Path("app/templates/partials/server_detail_action_modals.html").read_text()
    start = script.index("function parseAndSetCronBuilder")
    chunk = script[start:script.index("function hideBackupConfigModal")]
    assert "_singleDow(wd)" in chunk
    assert chunk.index("_singleDow(wd)") < chunk.index("freqSel.value = 'weekly'")
    assert "useCustom" in chunk


def test_fleet_sources_save_records_old_and_new(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from app.models import AuditLog
    from app.routers.server_backups import update_backup_config
    from app.services import backup as backup_svc

    path = tmp_path / "defaults.json"
    path.write_text(json.dumps({"sources": ["/home/user/old"], "dest_root": "/backups"}))
    monkeypatch.setattr(backup_svc, "GLOBAL_BACKUP_DEFAULTS_FILE", path)
    engine = _engine()
    with Session(engine) as session:
        server = Server(
            name="host-a",
            hostname="host-a.local",
            backup_enabled=True,
            backup_schedule="0 2 * * *",
            retention_days=7,
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        asyncio.run(
            update_backup_config(
                request=SimpleNamespace(),
                server_id=server.id,
                backup_paths="/home/user/stack\n",
                retention_days=7,
                backup_schedule="0 2 * * *",
                dest_root="/mnt/share",
                folder_name="",
                scope="global",
                backup_direct_set="",
                backup_direct="",
                session=session,
                user=SimpleNamespace(id=None),
            )
        )
        rows = list(session.exec(select(AuditLog).where(AuditLog.action == "server_backup_config")))
        changed = json.loads(rows[-1].details)["changed"]
        assert changed["sources"] == {"from": ["/home/user/old"], "to": ["/home/user/stack"]}
        assert changed["dest_root"] == {"from": "/backups", "to": "/mnt/share"}
        session.refresh(server)
        assert server.backup_schedule == "0 2 * * *"
