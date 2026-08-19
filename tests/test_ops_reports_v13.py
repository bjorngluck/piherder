"""v1.3 Reports — backup dest history + OS patch rates from Job rows."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    AuditLog,
    ConsoleTranscript,
    Integration,
    Job,
    NmapDevice,
    NmapScanRun,
    Server,
)
from app.services import ops_reports as rpt


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _server(session: Session, **kwargs) -> Server:
    row = Server(
        name=kwargs.pop("name", "pi-1"),
        hostname=kwargs.pop("hostname", "pi-1.local"),
        **kwargs,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _job(
    session: Session,
    *,
    server_id: int,
    job_type: str,
    status: str,
    finished_at: datetime,
    details: dict,
) -> Job:
    row = Job(
        server_id=server_id,
        job_type=job_type,
        status=status,
        finished_at=finished_at,
        details=json.dumps(details),
    )
    session.add(row)
    session.commit()
    return row


def test_backup_dest_bytes_from_result_summary():
    details = {
        "result_summary": {
            "results": [
                {"source": "/a", "size_bytes": 100},
                {"source": "/b", "size_bytes": 50},
            ]
        }
    }
    assert rpt.backup_dest_bytes(details) == 150
    assert rpt.backup_dest_bytes({"total_size_bytes": 9}) == 9


def test_os_packages_from_apt_line():
    details = {"result_snippet": "12 upgraded, 1 newly installed, 0 to remove"}
    assert rpt.os_packages_applied(details) == 13
    assert rpt.os_packages_applied({"summary": "OS patch"}) is None


def test_backup_history_success_fail_and_growth():
    session = _memory_session()
    s1 = _server(session, name="alpha", hostname="alpha")
    now = datetime.utcnow()
    _job(
        session,
        server_id=s1.id,
        job_type="backup",
        status="success",
        finished_at=now - timedelta(days=2),
        details={"result_summary": {"results": [{"size_bytes": 1000}]}},
    )
    _job(
        session,
        server_id=s1.id,
        job_type="backup",
        status="failed",
        finished_at=now - timedelta(days=1),
        details={"error": "rsync"},
    )
    _job(
        session,
        server_id=s1.id,
        job_type="backup",
        status="success",
        finished_at=now - timedelta(hours=2),
        details={"result_summary": {"results": [{"size_bytes": 2500}]}},
    )
    card = rpt.collect_backup_history(session, days=7, now=now)
    assert card["ok"] == 2
    assert card["fail"] == 1
    assert card["runs"] == 3
    assert card["dest_grew"] is True
    assert card["hosts"][0]["name"] == "alpha"
    assert card["hosts"][0]["dest_bytes"] == 2500
    nonempty = [d for d in card["day_rows"] if d["ok"] or d["fail"]]
    assert len(nonempty) >= 2
    assert card["day_rows"][-1]["dest_bytes"] == 2500


def test_os_patch_rates_per_host():
    session = _memory_session()
    s1 = _server(session, name="p1", hostname="p1", os_patch_enabled=True)
    s2 = _server(session, name="p2", hostname="p2", os_patch_enabled=True)
    now = datetime.utcnow()
    _job(
        session,
        server_id=s1.id,
        job_type="os_patch",
        status="success",
        finished_at=now - timedelta(days=1),
        details={"result_snippet": "4 upgraded, 0 newly installed, 0 to remove"},
    )
    _job(
        session,
        server_id=s2.id,
        job_type="os_patch",
        status="failed",
        finished_at=now - timedelta(days=1),
        details={"summary": "failed"},
    )
    card = rpt.collect_os_patch_history(session, days=7, now=now)
    assert card["ok"] == 1
    assert card["fail"] == 1
    assert card["packages"] == 4
    assert card["host_denom"] == 2
    week = {r["label"]: r for r in card["rates"]}["week"]
    assert week["applies"] == 1
    assert week["applies_per_host"] == 0.5
    assert week["packages_per_host"] == 2.0


def test_collect_ops_reports_empty():
    session = _memory_session()
    data = rpt.collect_ops_reports(session, days=30)
    assert data["days"] == 30
    assert data["backup"]["empty"] is True
    assert data["os_patch"]["empty"] is True


def test_clamp_days():
    assert rpt.clamp_report_days("7") == 7
    assert rpt.clamp_report_days("99") == 30
    assert rpt.clamp_report_days(None) == 30


def test_lan_live_per_day_carry_forward():
    session = _memory_session()
    integ = Integration(type="nmap", name="LAN", base_url="", enabled=True, config_json="{}")
    session.add(integ)
    session.commit()
    session.refresh(integ)
    now = datetime.utcnow()
    session.add(
        NmapScanRun(
            integration_id=integ.id,
            intensity="discovery",
            status="success",
            hosts_up=40,
            hosts_total=40,
            finished_at=now - timedelta(days=2),
        )
    )
    session.add(
        NmapScanRun(
            integration_id=integ.id,
            intensity="discovery",
            status="failed",
            hosts_up=0,
            finished_at=now - timedelta(days=1),
        )
    )
    session.add(
        NmapScanRun(
            integration_id=integ.id,
            intensity="discovery",
            status="success",
            hosts_up=44,
            hosts_total=44,
            finished_at=now - timedelta(hours=3),
        )
    )
    session.add(
        NmapDevice(
            integration_id=integ.id,
            identity_key="ip:10.0.0.2",
            ip_address="10.0.0.2",
            state="known",
            first_seen_at=now - timedelta(days=1),
        )
    )
    session.add(
        NmapDevice(
            integration_id=integ.id,
            identity_key="ip:10.0.0.3",
            ip_address="10.0.0.3",
            state="stale",
            first_seen_at=now - timedelta(days=20),
        )
    )
    session.commit()
    card = rpt.collect_lan_history(session, days=7, now=now)
    assert card["scans_ok"] == 2
    assert card["scans_fail"] == 1
    assert card["live_last"] == 44
    assert card["live_grew"] is True
    assert card["live_now"] == 1
    assert card["stale_now"] == 1
    assert card["new_window"] == 1
    # Quiet failed day still carries 40 live from the previous success.
    quiet = [d for d in card["day_rows"] if d["scans_fail"] == 1]
    assert quiet and quiet[0]["live"] == 40
    week = {r["label"]: r for r in card["rates"]}["week"]
    assert week["max_live"] == 44
    data = rpt.collect_ops_reports(session, days=7)
    assert data["lan"]["live_last"] == 44


def test_docker_deploys_and_patches():
    session = _memory_session()
    s1 = _server(session, name="box", hostname="box", container_patch_enabled=True)
    now = datetime.utcnow()
    _job(
        session,
        server_id=s1.id,
        job_type="docker_stack_deploy",
        status="success",
        finished_at=now - timedelta(hours=3),
        details={"project": "caddy", "success": True},
    )
    _job(
        session,
        server_id=s1.id,
        job_type="container_patch",
        status="failed",
        finished_at=now - timedelta(hours=2),
        details={"summary": "pull failed"},
    )
    card = rpt.collect_docker_history(session, days=7, now=now)
    assert card["deploy_ok"] == 1
    assert card["patch_fail"] == 1
    assert card["hosts"][0]["name"] == "box"
    data = rpt.collect_ops_reports(session, days=7)
    assert data["docker"]["deploy_ok"] == 1


def test_console_sessions_duration_and_privileged():
    session = _memory_session()
    s1 = _server(session, name="edge", hostname="edge")
    now = datetime.utcnow()
    session.add(
        AuditLog(
            server_id=s1.id,
            action="ssh_console_open",
            status="success",
            details="ip=1.2.3.4 user=a@b.c identity=privileged:root audit=commands",
            started_at=now - timedelta(hours=2),
        )
    )
    session.add(
        AuditLog(
            server_id=s1.id,
            action="ssh_console_close",
            status="success",
            details="duration_sec=125 cmds=4",
            started_at=now - timedelta(hours=1, minutes=50),
        )
    )
    session.add(
        AuditLog(
            server_id=s1.id,
            action="ssh_console_denied",
            status="failed",
            details="ip=9.9.9.9",
            started_at=now - timedelta(hours=1),
        )
    )
    session.add(
        ConsoleTranscript(
            session_key="abc123",
            server_id=s1.id,
            command_count=4,
            created_at=now - timedelta(hours=2),
        )
    )
    session.commit()
    card = rpt.collect_console_history(session, days=7, now=now)
    assert card["opens"] == 1
    assert card["privileged"] == 1
    assert card["denied"] == 1
    assert card["seconds"] == 125
    assert card["cmds"] == 4
    assert card["hosts"][0]["name"] == "edge"
