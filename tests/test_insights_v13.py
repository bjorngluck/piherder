"""v1.3 Stream N — fleet-health registry + Reports board (no SSH)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Integration,
    Job,
    ManagedCertificate,
    NmapDevice,
    Notification,
    Server,
)
from app.services import insights as ins


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


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


def test_registry_ids_frozen():
    assert ins.WIDGET_IDS == (
        "alerts_by_severity",
        "backups_stale",
        "certs_expiring",
        "jobs_failed_24h",
        "nmap_queue",
        "map_infra",
        "docker_fleet",
    )
    assert tuple(w.id for w in ins.WIDGETS) == ins.WIDGET_IDS


def test_backup_counts_stale():
    now = datetime.utcnow()
    servers = [
        Server(name="a", hostname="a", backup_enabled=True, last_backup_at=now - timedelta(hours=1)),
        Server(name="b", hostname="b", backup_enabled=True, last_backup_at=now - timedelta(hours=48)),
        Server(name="c", hostname="c", backup_enabled=True, last_backup_at=None),
        Server(name="d", hostname="d", backup_enabled=False, last_backup_at=None),
    ]
    enabled, stale = ins.backup_counts(servers, stale_hours=36)
    assert enabled == 3
    assert stale == 2


def test_collect_board_empty():
    session, _engine = _memory_session()
    board = ins.collect_board(session)
    assert [w["id"] for w in board] == list(ins.WIDGET_IDS)
    by_id = {w["id"]: w for w in board}
    assert by_id["alerts_by_severity"]["value"] == 0
    assert by_id["backups_stale"]["value"] == 0
    assert by_id["certs_expiring"]["value"] == 0
    assert by_id["jobs_failed_24h"]["value"] == 0
    assert by_id["nmap_queue"]["value"] == 0
    assert by_id["map_infra"]["value"] == 0
    assert by_id["docker_fleet"]["value"] == 0
    assert by_id["docker_fleet"]["hot"] is False


def test_alerts_by_severity_and_map_infra():
    session, _engine = _memory_session()
    now = datetime.utcnow()
    session.add(
        Notification(
            type="host_down",
            severity="critical",
            title="pi-1 SSH down",
            fingerprint="fp-host",
            status="open",
            updated_at=now,
        )
    )
    session.add(
        Notification(
            type="map_infra_down",
            severity="warning",
            title="Gateway down",
            fingerprint="fp-gw",
            status="open",
            updated_at=now,
        )
    )
    session.add(
        Notification(
            type="os_updates",
            severity="info",
            title="OS updates",
            fingerprint="fp-os",
            status="open",
            updated_at=now,
        )
    )
    session.add(
        Notification(
            type="host_down",
            severity="critical",
            title="old",
            fingerprint="fp-old",
            status="resolved",
            updated_at=now,
        )
    )
    session.commit()
    alerts = ins.collect_alerts_by_severity(session)
    assert alerts["value"] == 3
    parts = {p["l"]: p["n"] for p in alerts["parts"]}
    assert parts["critical"] == 1
    assert parts["warning"] == 1
    assert parts["info"] == 1
    assert alerts["hot"] is True
    assert len(alerts["rows"]) == 3

    infra = ins.collect_map_infra(session)
    assert infra["value"] == 2
    parts = {p["l"]: p["n"] for p in infra["parts"]}
    assert parts["host"] == 1
    assert parts["map infra"] == 1
    labels = [i["label"] for i in infra["rows"]]
    assert "pi-1 SSH down" in labels
    assert "Gateway down" in labels


def test_certs_window_30d():
    session, _engine = _memory_session()
    now = datetime.utcnow()
    session.add(ManagedCertificate(name="soon", not_after=now + timedelta(days=10)))
    session.add(ManagedCertificate(name="ok", not_after=now + timedelta(days=90)))
    session.add(ManagedCertificate(name="dead", not_after=now - timedelta(days=2)))
    session.add(ManagedCertificate(name="undated", not_after=None))
    session.commit()
    card = ins.collect_certs_expiring(session)
    assert card["value"] == 2
    names = [i["label"] for i in card["rows"]]
    assert names[0] == "dead"
    assert "soon" in names
    assert "ok" not in names
    parts = {p["l"]: p["n"] for p in card["parts"]}
    assert parts["expired"] == 1


def test_jobs_failed_24h_window():
    session, _engine = _memory_session()
    now = datetime.utcnow()
    session.add(
        Job(job_type="backup", status="failed", finished_at=now - timedelta(hours=2), details="rsync")
    )
    session.add(
        Job(
            job_type="backup",
            status="failed",
            finished_at=now - timedelta(hours=40),
            details="old",
        )
    )
    session.add(Job(job_type="backup", status="success", finished_at=now - timedelta(hours=1)))
    session.commit()
    assert ins.count_jobs_failed_24h(session) == 1
    card = ins.collect_jobs_failed_24h(session)
    assert card["value"] == 1
    assert card["rows"][0]["label"] == "backup"
    assert card["href"] == "/jobs?status=failed"


def test_nmap_new_and_offline():
    session, _engine = _memory_session()
    integ = Integration(type="nmap", name="LAN", base_url="", enabled=True, config_json="{}")
    session.add(integ)
    session.commit()
    session.refresh(integ)
    iid = integ.id
    session.add(
        NmapDevice(
            integration_id=iid,
            identity_key="ip:10.0.0.2",
            ip_address="10.0.0.2",
            display_name="cam",
            state="new",
        )
    )
    session.add(
        NmapDevice(
            integration_id=iid,
            identity_key="ip:10.0.0.3",
            ip_address="10.0.0.3",
            hostname="old.lan",
            state="stale",
        )
    )
    session.add(
        NmapDevice(
            integration_id=iid,
            identity_key="ip:10.0.0.4",
            ip_address="10.0.0.4",
            state="known",
        )
    )
    session.commit()
    card = ins.collect_nmap_queue(session)
    assert card["value"] == 2
    parts = {p["l"]: p["n"] for p in card["parts"]}
    assert parts["new"] == 1
    assert parts["offline"] == 1
    assert card["href"] == f"/integrations/{iid}"
    labels = [i["label"] for i in card["rows"]]
    assert "cam" in labels
    assert "old.lan" in labels


def test_docker_fleet_totals_and_stale():
    session, _engine = _memory_session()
    payload = {
        "v": 2,
        "projects": [
            {
                "name": "edge",
                "containers": [
                    {"name": "npm", "running": True},
                    {"name": "app", "running": False},
                ],
            }
        ],
        "orphan_containers": [{"name": "stray", "running": True}],
        "meta": {"project_count": 1, "container_count": 3},
    }
    ok = _server(
        session,
        name="ok-host",
        hostname="ok",
        container_patch_enabled=True,
        docker_inventory_json=json.dumps(payload),
        docker_inventory_status="ok",
        docker_inventory_at=datetime.utcnow(),
    )
    stale = _server(
        session,
        name="stale-host",
        hostname="stale",
        container_patch_enabled=True,
        docker_inventory_json=json.dumps(
            {
                "v": 2,
                "projects": [{"name": "one", "containers": [{"name": "c", "running": True}]}],
                "orphan_containers": [],
                "meta": {"project_count": 1, "container_count": 1},
            }
        ),
        docker_inventory_status="stale",
    )
    _server(session, name="haos", hostname="haos", container_patch_enabled=False)
    card = ins.collect_docker_fleet(session)
    assert card["id"] == "docker_fleet"
    assert card["value"] == 4
    parts = {p["l"]: p["n"] for p in card["parts"]}
    assert parts["running"] == 3
    assert parts["stacks"] == 2
    assert parts["stale/never"] == 1
    assert card["hot"] is True
    assert card["rows"][0]["label"] == "stale-host"
    hrefs = [i["href"] for i in card["rows"]]
    assert f"/servers/{stale.id}/docker" in hrefs
    assert f"/servers/{ok.id}/docker" in hrefs


def test_backups_stale_items_link_to_host():
    session, _engine = _memory_session()
    s = _server(
        session,
        name="bak",
        hostname="bak",
        backup_enabled=True,
        last_backup_at=None,
    )
    card = ins.collect_backups_stale(session)
    assert card["value"] == 1
    assert card["rows"][0]["href"] == f"/servers/{s.id}/backups"
