"""v1.3 Stream A — alert policy, debounce, map/discovery emitters."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Notification
from app.services import alert_channels as ch
from app.services import alert_policy as apol
from app.services import notifications as ntf
from app.services.integrations import poll as poll_mod
from app.services.integrations import registry as reg


def _memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _policy(monkeypatch, payload: dict):
    monkeypatch.setattr(
        apol.app_cfg,
        "load_settings",
        lambda: {"alert_type_policy": payload},
    )


def test_catalog_defaults():
    assert apol.CATALOG_BY_ID["host_down"].default_severity == "critical"
    assert apol.CATALOG_BY_ID["host_down"].realert_hours == 24
    assert apol.CATALOG_BY_ID["nmap_new_device"].debounce_minutes == 60
    assert apol.CATALOG_BY_ID["nmap_device_offline"].default_severity == "info"
    assert apol.CATALOG_BY_ID["backup_failed"].default_severity == "critical"
    assert apol.CATALOG_BY_ID["stack_health"].default_severity is None
    assert apol.category_of("host_down") == "host"
    assert apol.category_of("nope") == "other"


def test_effective_mute_and_severity_override(monkeypatch):
    _policy(
        monkeypatch,
        {
            "categories": {"updates": {"enabled": False}},
            "types": {"backup_failed": {"severity": "warning"}},
        },
    )
    os_p = apol.effective("os_updates")
    assert os_p.enabled is False
    bak = apol.effective("backup_failed")
    assert bak.enabled is True
    assert bak.severity == "warning"
    assert apol.resolve_severity(bak, "critical") == "warning"
    hint = apol.effective("stack_health")
    assert hint.severity is None
    assert apol.resolve_severity(hint, "critical") == "critical"


def test_upsert_mute_resolves(monkeypatch):
    _policy(monkeypatch, {"types": {"os_updates": {"enabled": False}}})
    session, _engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
        ntf.upsert_notification(
            session,
            fingerprint="fp-os",
            type="os_updates",
            title="OS",
            severity="warning",
        )
        # seed an open row then mute
        _policy(monkeypatch, {})
        ntf.upsert_notification(
            session,
            fingerprint="fp-os",
            type="os_updates",
            title="OS",
            severity="warning",
        )
        assert ntf.open_count(session) == 1
        _policy(monkeypatch, {"types": {"os_updates": {"enabled": False}}})
        out = ntf.upsert_notification(
            session,
            fingerprint="fp-os",
            type="os_updates",
            title="OS",
            severity="warning",
        )
        assert out is None
        assert ntf.open_count(session) == 0


def test_upsert_severity_from_policy(monkeypatch):
    _policy(monkeypatch, {"types": {"os_updates": {"severity": "info"}}})
    session, _engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
        n = ntf.upsert_notification(
            session,
            fingerprint="fp-sev",
            type="os_updates",
            title="OS",
            severity="warning",
        )
        assert n is not None
        assert n.severity == "info"


def test_debounce_suppresses_reopen(monkeypatch):
    _policy(
        monkeypatch,
        {"types": {"os_updates": {"debounce_minutes": 30}}},
    )
    session, _engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
        n = ntf.upsert_notification(
            session,
            fingerprint="fp-deb",
            type="os_updates",
            title="OS",
        )
        assert ntf.dismiss(session, n.id) is True
        again = ntf.upsert_notification(
            session,
            fingerprint="fp-deb",
            type="os_updates",
            title="OS still",
        )
        assert again is None
        assert ntf.open_count(session) == 0

        closed = session.exec(
            select(Notification).where(Notification.fingerprint == "fp-deb")
        ).first()
        closed.dismissed_at = datetime.utcnow() - timedelta(minutes=45)
        closed.updated_at = closed.dismissed_at
        session.add(closed)
        session.commit()
        opened = ntf.upsert_notification(
            session,
            fingerprint="fp-deb",
            type="os_updates",
            title="OS later",
        )
        assert opened is not None
        assert opened.status == "open"


def test_realert_fires_webhook_once(monkeypatch):
    _policy(
        monkeypatch,
        {"types": {"backup_failed": {"realert_hours": 1}}},
    )
    session, _engine = _memory_session()
    calls = {"n": 0}

    def fake_webhook(*_a, **_k):
        calls["n"] += 1

    with patch.object(ntf, "_maybe_webhook", fake_webhook), patch.object(
        ntf, "_maybe_email"
    ), patch.object(ntf, "_maybe_push"):
        ntf.upsert_notification(
            session,
            fingerprint="fp-re",
            type="backup_failed",
            title="Backup failed",
            severity="critical",
        )
        assert calls["n"] == 1
        ntf.upsert_notification(
            session,
            fingerprint="fp-re",
            type="backup_failed",
            title="Backup failed",
            severity="critical",
        )
        assert calls["n"] == 1
        row = session.exec(
            select(Notification).where(Notification.fingerprint == "fp-re")
        ).first()
        merged = ntf._parse_payload(row.payload)
        merged["_last_notified_at"] = (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
        row.payload = __import__("json").dumps(merged)
        session.add(row)
        session.commit()
        ntf.upsert_notification(
            session,
            fingerprint="fp-re",
            type="backup_failed",
            title="Backup failed still",
            severity="critical",
        )
        assert calls["n"] == 2


def test_notify_channels_false_skips_webhook(monkeypatch):
    _policy(monkeypatch, {})
    session, _engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook") as wh, patch.object(ntf, "_maybe_push"):
        ntf.notify_nmap_new_device(
            session,
            device_id=3,
            integration_id=1,
            label="cam1",
            ip="192.168.1.20",
        )
        assert wh.call_count == 0
        rows = ntf.list_notifications(session, type="nmap_new_device")
        assert len(rows) == 1
        assert "host-d-3" in (rows[0].link_url or "")
        ntf.notify_nmap_new_digest(
            session, integration_id=1, count=3, sample_names=["cam1", "pi"]
        )
        assert wh.call_count == 1
        assert "3 new" in (wh.call_args.kwargs.get("message") or wh.call_args[0][0] or "")


def test_list_filter_severity_category_and_bulk_dismiss(monkeypatch):
    _policy(monkeypatch, {})
    session, _engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
        ntf.upsert_notification(
            session, fingerprint="a", type="os_updates", title="OS", severity="warning"
        )
        ntf.upsert_notification(
            session,
            fingerprint="b",
            type="backup_failed",
            title="Bak",
            severity="critical",
        )
        ntf.upsert_notification(
            session,
            fingerprint="c",
            type="nmap_new_device",
            title="New",
            severity="warning",
        )
        warns = ntf.list_notifications(session, severity="warning")
        assert {r.type for r in warns} == {"os_updates", "nmap_new_device"}
        disc = ntf.list_notifications(session, category="discovery")
        assert [r.type for r in disc] == ["nmap_new_device"]
        n = ntf.dismiss_matching(session, category="updates")
        assert n == 1
        assert ntf.open_count(session) == 2


def test_webhook_category_allowlist(monkeypatch):
    monkeypatch.setattr(
        ch,
        "webhook_config",
        lambda: {
            "url": "https://hook.example/x",
            "number": "",
            "recipients_raw": "[]",
            "secret": "",
            "events_notifications": True,
            "events_jobs": True,
            "events_backup": True,
            "min_severity": "info",
            "notify_categories": ["host", "cert"],
        },
    )
    r = ch.send_webhook(
        "hi", event="notification", severity="critical", category="discovery"
    )
    assert r.get("skipped") is True
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("app.services.alert_channels.httpx.post", return_value=mock_resp) as post:
        r2 = ch.send_webhook(
            "hi",
            event="notification",
            severity="critical",
            category="host",
            notif_type="host_down",
        )
    assert r2.get("ok") is True
    body = post.call_args.kwargs["json"]
    assert body["type"] == "host_down"
    assert body["category"] == "host"


def test_parse_allowlist_empty_is_all():
    assert apol.parse_allowlist([]) is None
    assert apol.parse_allowlist(["_none"]) == []
    assert apol.category_allowed("host", None) is True
    assert apol.category_allowed("host", []) is False
    assert apol.category_allowed("host", ["host"]) is True


def test_notify_transition_ssh_is_host_down(monkeypatch):
    calls = {}

    def fake_upsert(session, **kwargs):
        calls.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(poll_mod.notif_svc, "upsert_notification", fake_upsert)
    integ = SimpleNamespace(id=1, name="Home")
    binding = SimpleNamespace(
        integration_id=1,
        server_id=9,
        role=reg.ROLE_SSH,
        external_id="2",
        external_label="rpi SSH",
    )
    session = MagicMock()
    session.get.return_value = SimpleNamespace(name="rpi5")
    poll_mod._notify_transition(session, integ, binding, "up", "down")
    assert calls["type"] == "host_down"
    assert "n:host-9" in (calls.get("link_url") or "")


def test_notify_transition_service_stays_integration(monkeypatch):
    calls = {}

    def fake_upsert(session, **kwargs):
        calls.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(poll_mod.notif_svc, "upsert_notification", fake_upsert)
    integ = SimpleNamespace(id=1, name="Home")
    binding = SimpleNamespace(
        integration_id=1,
        server_id=9,
        role=reg.ROLE_SERVICE,
        external_id="8",
        external_label="App",
    )
    session = MagicMock()
    session.get.return_value = SimpleNamespace(name="rpi5")
    poll_mod._notify_transition(session, integ, binding, "up", "down")
    assert calls["type"] == "integration_monitor_down"


def test_map_infra_down_and_up(monkeypatch):
    session, _engine = _memory_session()
    settings_blob = {
        "alert_type_policy": {},
        "network_gateway_kuma_external_id": "gw1",
        "network_public_kuma_external_id": "",
        "network_kuma_integration_id": "",
    }
    monkeypatch.setattr(apol.app_cfg, "load_settings", lambda: settings_blob)
    monkeypatch.setattr(
        "app.services.app_settings.load_settings", lambda: settings_blob
    )
    gw = SimpleNamespace(id="gw1", name="Router", status="down")
    with patch.object(poll_mod.kuma, "find_monitor", return_value=gw), patch.object(
        ntf, "_maybe_webhook"
    ), patch.object(ntf, "_maybe_push"):
        poll_mod._notify_map_infra(
            session, SimpleNamespace(id=1), [gw]
        )
    rows = ntf.list_notifications(session, type="map_infra_down")
    assert len(rows) == 1
    assert rows[0].fingerprint == "map_infra:gateway"
    gw.status = "up"
    with patch.object(poll_mod.kuma, "find_monitor", return_value=gw):
        poll_mod._notify_map_infra(session, SimpleNamespace(id=1), [gw])
    assert ntf.open_count(session) == 0


def test_nmap_offline_helpers(monkeypatch):
    _policy(monkeypatch, {})
    session, _engine = _memory_session()
    with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
        ntf.notify_nmap_device_offline(
            session,
            device_id=4,
            integration_id=1,
            label="printer",
            ip="192.168.1.9",
        )
        rows = ntf.list_notifications(session, type="nmap_device_offline")
        assert len(rows) == 1
        assert rows[0].severity == "info"
        ntf.resolve_nmap_new_device(session, 3)
        ntf.resolve_nmap_device_offline(session, 4)
        assert ntf.open_count(session) == 0


def test_policy_from_form_preserves_type_overrides(monkeypatch):
    _policy(
        monkeypatch,
        {"types": {"stack_container_down": {"enabled": False}}},
    )
    form = {
        "cat_enabled_host": "1",
        "cat_severity_host": "critical",
        "cat_debounce_host": "15",
        "cat_realert_host": "24",
    }
    for cid, _lab in apol.CATEGORIES:
        form.setdefault(f"cat_enabled_{cid}", "1" if cid != "discovery" else "")
        form.setdefault(f"cat_severity_{cid}", "default")
        form.setdefault(f"cat_debounce_{cid}", "0")
        form.setdefault(f"cat_realert_{cid}", "0")
    out = apol.policy_from_form(form)
    assert out["categories"]["discovery"]["enabled"] is False
    assert out["types"]["stack_container_down"]["enabled"] is False
    assert out["categories"]["host"]["severity"] == "critical"
