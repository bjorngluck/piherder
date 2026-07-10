"""Integration binding helpers (in-memory models, no Postgres required for pure logic)."""
from __future__ import annotations

from app.services.integrations import registry as reg
from app.services.integrations.poll import _notify_transition
from app.models import Integration, IntegrationBinding, Server
from unittest.mock import MagicMock


def test_role_constant():
    assert reg.ROLE_SSH == "ssh_reachability"
    assert reg.TYPE_UPTIME_KUMA == "uptime_kuma"


def test_poll_interval_clamp():
    row = Integration(
        type="uptime_kuma",
        name="k",
        base_url="http://x",
        config_json='{"poll_interval_sec": 5}',
    )
    assert reg.poll_interval_sec(row) == reg.MIN_POLL_INTERVAL_SEC
    row.config_json = '{"poll_interval_sec": 99999}'
    assert reg.poll_interval_sec(row) == reg.MAX_POLL_INTERVAL_SEC
    row.config_json = '{"poll_interval_sec": 120}'
    assert reg.poll_interval_sec(row) == 120


def test_tls_verify_default():
    row = Integration(type="uptime_kuma", name="k", base_url="http://x")
    assert reg.tls_verify(row) is True
    row.config_json = '{"tls_verify": false}'
    assert reg.tls_verify(row) is False


def test_notify_transition_down_and_up(monkeypatch):
    calls = {"upsert": 0, "resolve": 0}

    def fake_upsert(session, **kwargs):
        calls["upsert"] += 1
        assert kwargs["type"] == "integration_monitor_down"
        assert "down" in kwargs["title"].lower() or "SSH" in kwargs["title"]
        return MagicMock()

    def fake_resolve(session, fp):
        calls["resolve"] += 1
        assert fp.startswith("kuma_down:")
        return 1

    monkeypatch.setattr(
        "app.services.integrations.poll.notif_svc.upsert_notification", fake_upsert
    )
    monkeypatch.setattr(
        "app.services.integrations.poll.notif_svc.resolve_by_fingerprint", fake_resolve
    )

    integ = Integration(id=1, type="uptime_kuma", name="Home", base_url="http://k")
    binding = IntegrationBinding(
        integration_id=1,
        server_id=9,
        role=reg.ROLE_SSH,
        external_id="2",
        external_label="rpi SSH",
    )
    server = Server(id=9, name="rpi5-2", hostname="rpi5-2")
    session = MagicMock()
    session.get.return_value = server

    _notify_transition(session, integ, binding, prev="up", new_state="down")
    assert calls["upsert"] == 1
    assert calls["resolve"] == 0

    _notify_transition(session, integ, binding, prev="down", new_state="up")
    assert calls["resolve"] == 1
