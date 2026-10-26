"""Possible NIC change: notice, badge data, and Replace stale MAC."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models import AuditLog, Integration, NmapDevice, NmapScanSchedule, Notification, User
from app.security.auth import get_password_hash
from app.services.nmap.nic_change import nic_pairs, replace_stale_mac
from app.services.nmap.parse import ParsedHost
from app.services.nmap.upsert import upsert_hosts_from_parse
from tests.test_coverage_v17_q1 import _client, _engine


def _pair(session: Session, *, seen_ago: timedelta, interval_hours: int | None):
    integ = Integration(type="nmap", name="LAN", base_url="https://example.com")
    session.add(integ)
    session.commit()
    session.refresh(integ)
    if interval_hours:
        session.add(
            NmapScanSchedule(
                integration_id=int(integ.id),
                name="hourly",
                intensity="discovery",
                interval_hours=interval_hours,
                enabled=True,
            )
        )
    old = NmapDevice(
        integration_id=int(integ.id),
        identity_key="mac:AA:BB:CC:DD:EE:01",
        ip_address="192.0.2.10",
        hostname="camera.example.com",
        mac_address="AA:BB:CC:DD:EE:01",
        display_name="Front camera",
        kind_override="camera",
        notes="by the door",
        state="stale",
        last_seen_at=datetime.utcnow() - seen_ago,
    )
    session.add(old)
    session.commit()
    session.refresh(old)
    return integ, old


def test_new_mac_notifies_without_merging_or_ignoring(tmp_path):
    engine = _engine(tmp_path / "nic.db")
    with Session(engine) as session:
        integ, old = _pair(session, seen_ago=timedelta(hours=3), interval_hours=1)
        summary = upsert_hosts_from_parse(
            session,
            integration_id=int(integ.id),
            hosts=[
                ParsedHost(
                    ip_address="192.0.2.10",
                    hostname="camera.example.com",
                    mac_address="aa:bb:cc:dd:ee:02",
                    status="up",
                )
            ],
        )
        assert summary["created"] == 1
        session.refresh(old)
        assert old.state == "stale"
        assert old.mac_address == "AA:BB:CC:DD:EE:01"
        rows = list(session.exec(select(NmapDevice)).all())
        assert len(rows) == 2
        fresh = next(row for row in rows if row.id != old.id)
        assert fresh.mac_address == "AA:BB:CC:DD:EE:02"
        assert fresh.identity_key.startswith("mac:")
        note = session.exec(
            select(Notification).where(Notification.type == "lan_discovery")
        ).first()
        assert note is not None
        assert note.severity == "info"
        assert "Possible NIC change" in (note.title or "")
        pairs = nic_pairs(session, int(integ.id))
        assert (fresh.id, old.id) in {(a.id, b.id) for a, b in pairs}


def test_recent_or_different_host_is_not_a_nic_change(tmp_path):
    engine = _engine(tmp_path / "nic-quiet.db")
    with Session(engine) as session:
        integ, old = _pair(session, seen_ago=timedelta(minutes=10), interval_hours=1)
        upsert_hosts_from_parse(
            session,
            integration_id=int(integ.id),
            hosts=[
                ParsedHost(
                    ip_address="192.0.2.10",
                    hostname="camera.example.com",
                    mac_address="AA:BB:CC:DD:EE:02",
                    status="up",
                )
            ],
        )
        assert nic_pairs(session, int(integ.id)) == []
        assert (
            session.exec(select(Notification).where(Notification.type == "lan_discovery")).first()
            is None
        )
        other = NmapDevice(
            integration_id=int(integ.id),
            identity_key="mac:AA:BB:CC:DD:EE:03",
            ip_address="192.0.2.11",
            hostname="other.example.com",
            mac_address="AA:BB:CC:DD:EE:03",
            state="stale",
            last_seen_at=datetime.utcnow() - timedelta(hours=5),
        )
        session.add(other)
        session.commit()
        assert nic_pairs(session, int(integ.id)) == []


def test_replace_copies_custom_fields_and_writes_one_audit(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "nic-replace.db")
    client, _uid, _sid = _client(engine, monkeypatch)
    try:
        with Session(engine) as session:
            integ, old = _pair(session, seen_ago=timedelta(hours=3), interval_hours=1)
            upsert_hosts_from_parse(
                session,
                integration_id=int(integ.id),
                hosts=[
                    ParsedHost(
                        ip_address="192.0.2.10",
                        hostname="Camera.example.com",
                        mac_address="AA:BB:CC:DD:EE:02",
                        status="up",
                    )
                ],
            )
            fresh = session.exec(
                select(NmapDevice).where(NmapDevice.mac_address == "AA:BB:CC:DD:EE:02")
            ).first()
            assert fresh is not None
            iid, new_id, old_id = int(integ.id), int(fresh.id), int(old.id)

        page = client.get(f"/integrations/{iid}?tab=devices&device={new_id}")
        assert page.status_code == 200, page.text[:400]
        assert "possible NIC change" in page.text
        assert 'data-testid="replace-stale-mac"' in page.text

        posted = client.post(
            f"/integrations/{iid}/nmap/device/{new_id}/replace-stale-mac",
            data={"stale_device_id": str(old_id)},
            follow_redirects=False,
        )
        assert posted.status_code == 303, posted.text[:400]

        with Session(engine) as session:
            fresh = session.get(NmapDevice, new_id)
            stale = session.get(NmapDevice, old_id)
            assert fresh is not None and stale is not None
            assert fresh.mac_address == "AA:BB:CC:DD:EE:02"
            assert fresh.display_name == "Front camera"
            assert fresh.kind_override == "camera"
            assert fresh.notes == "by the door"
            assert stale.state == "ignored"
            audits = list(
                session.exec(
                    select(AuditLog).where(AuditLog.action == "nmap_replace_stale_mac")
                ).all()
            )
            assert len(audits) == 1
            try:
                replace_stale_mac(
                    session,
                    integration_id=iid,
                    new_device_id=new_id,
                    stale_device_id=old_id,
                )
                raised = False
            except ValueError as exc:
                raised = str(exc) == "not_a_nic_change"
            assert raised
    finally:
        from app.main import app

        app.dependency_overrides.clear()


def test_mcp_replace_stale_mac(tmp_path):
    from app.main import app
    from tests.test_mcp_discovery_devices_v111 import (
        _client,
        _engine,
        _rpc,
        _token,
        _tool_payload,
    )

    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        with Session(engine) as session:
            user = User(
                email="nic@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            token = _token(session, user, "read,edit")
            integ, old = _pair(session, seen_ago=timedelta(hours=3), interval_hours=1)
            upsert_hosts_from_parse(
                session,
                integration_id=int(integ.id),
                hosts=[
                    ParsedHost(
                        ip_address="192.0.2.10",
                        hostname="camera.example.com",
                        mac_address="AA:BB:CC:DD:EE:02",
                        status="up",
                    )
                ],
            )
            fresh = session.exec(
                select(NmapDevice).where(NmapDevice.mac_address == "AA:BB:CC:DD:EE:02")
            ).one()
            iid, new_id, old_id = int(integ.id), int(fresh.id), int(old.id)
        response = _rpc(
            client,
            token,
            "replace_stale_mac",
            {
                "integration_id": iid,
                "device_id": new_id,
                "stale_device_id": old_id,
            },
        )
        assert response.status_code == 200, response.text[:400]
        body, text = _tool_payload(response)
        assert body["isError"] is False
        assert text["stale_state"] == "ignored"
        assert text["device"]["display_name"] == "Front camera"
        listed = client.get(
            f"/api/v1/discovery/{iid}/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert listed.status_code == 200
        flags = {row["id"]: row["nic_change"] for row in listed.json()["devices"]}
        assert flags[new_id] is False
    finally:
        app.dependency_overrides.clear()
