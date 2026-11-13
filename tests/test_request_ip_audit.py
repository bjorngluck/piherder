"""Client IP resolution (Caddy XFF) + audit log client_ip attachment."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import request_ip as rip
from app.services import audit_write as aw
from app.services.backup_audit import record_backup_audit_event
from app.models import Job


_COMPOSE_CIDRS = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32,::1/128"


def test_extract_client_ip_prefers_xff():
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
            "172.18.0.5",
            trusted_cidrs=_COMPOSE_CIDRS.split(","),
        )
        == "203.0.113.9"
    )


def test_true_client_ip_is_ignored():
    """True-Client-IP is not a trusted header. X-Forwarded-For still is."""
    nets = rip.DEFAULT_COMPOSE_TRUSTED_PROXY_CIDRS.split(",")
    assert (
        rip.extract_client_ip(
            {
                "True-Client-IP": "203.0.113.77",
                "X-Forwarded-For": "203.0.113.9",
            },
            "192.0.2.10",
            trusted_cidrs=nets,
        )
        == "203.0.113.9"
    )
    assert (
        rip.extract_client_ip(
            {"True-Client-IP": "203.0.113.77"},
            "192.0.2.10",
            trusted_cidrs=nets,
        )
        == "192.0.2.10"
    )


def test_extract_client_ip_prefers_cf_connecting_ip_over_xff():
    """A trusted proxy may still send CF-Connecting-IP. It wins over X-Forwarded-For."""
    assert (
        rip.extract_client_ip(
            {
                "CF-Connecting-IP": "203.0.113.50",
                "X-Forwarded-For": "104.16.1.1",
            },
            "172.18.0.5",
            trusted_cidrs=_COMPOSE_CIDRS.split(","),
        )
        == "203.0.113.50"
    )


def test_extract_client_ip_x_real_ip_and_peer():
    assert (
        rip.extract_client_ip(
            {"X-Real-IP": "198.51.100.2"},
            "172.18.0.5",
            trusted_cidrs=_COMPOSE_CIDRS.split(","),
        )
        == "198.51.100.2"
    )
    assert rip.extract_client_ip({}, "172.18.0.5") == "172.18.0.5"
    assert (
        rip.extract_client_ip(
            {"X-Real-IP": "10.0.0.5:44321"},
            None,
            trust_forwarded=True,
        )
        == "10.0.0.5"
    )


def test_extract_client_ip_ignores_spoofed_headers_from_untrusted_peer():
    """Direct hit on the app port must not honor attacker XFF."""
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9", "CF-Connecting-IP": "198.51.100.1"},
            "203.0.113.77",
        )
        == "203.0.113.77"
    )
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9"},
            "9.9.9.9",
            trusted_cidrs=_COMPOSE_CIDRS.split(","),
        )
        == "9.9.9.9"
    )


def test_default_proxy_cidrs_trust_the_private_network():
    nets = rip.DEFAULT_COMPOSE_TRUSTED_PROXY_CIDRS.split(",")
    assert "10.0.0.0/8" in nets
    assert "172.16.0.0/12" in nets
    assert "192.168.0.0/16" in nets
    assert "192.0.2.0/24" in nets
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9"},
            "10.1.2.3",
            trusted_cidrs=nets,
        )
        == "203.0.113.9"
    )
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9"},
            "192.0.2.20",
            trusted_cidrs=nets,
        )
        == "203.0.113.9"
    )
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9"},
            "198.51.100.8",
            trusted_cidrs=nets,
        )
        == "198.51.100.8"
    )
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9"},
            "192.0.2.10",
            trusted_cidrs=nets,
        )
        == "203.0.113.9"
    )
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "203.0.113.9"},
            "127.0.0.1",
            trusted_cidrs=nets,
        )
        == "203.0.113.9"
    )


def test_stock_caddy_clients_have_separate_login_buckets(monkeypatch):
    """Two visitors through Caddy do not share a bucket. A spoofed header does not move it."""
    from app.security import auth as auth_mod

    monkeypatch.delenv("PIHERDER_DISABLE_AUTH_RATE_LIMIT", raising=False)
    nets = rip.DEFAULT_COMPOSE_TRUSTED_PROXY_CIDRS.split(",")
    caddy = "192.0.2.10"
    left = rip.extract_client_ip(
        {"X-Forwarded-For": "203.0.113.10"}, caddy, trusted_cidrs=nets
    )
    right = rip.extract_client_ip(
        {"X-Forwarded-For": "203.0.113.11"}, caddy, trusted_cidrs=nets
    )
    assert left == "203.0.113.10"
    assert right == "203.0.113.11"
    spoofed = rip.extract_client_ip(
        {"X-Forwarded-For": "203.0.113.10", "CF-Connecting-IP": "198.51.100.9"},
        "198.51.100.8",
        trusted_cidrs=nets,
    )
    assert spoofed == "198.51.100.8"
    neighbor = rip.extract_client_ip(
        {"X-Forwarded-For": "203.0.113.10"},
        "192.0.2.20",
        trusted_cidrs=nets,
    )
    assert neighbor == "203.0.113.10"
    auth_mod._auth_attempts.clear()
    try:
        for _ in range(10):
            assert auth_mod.rate_limit_auth(f"login:{left}", max_attempts=10, window_seconds=300)
        assert not auth_mod.rate_limit_auth(f"login:{left}", max_attempts=10, window_seconds=300)
        assert auth_mod.rate_limit_auth(f"login:{right}", max_attempts=10, window_seconds=300)
        assert auth_mod.rate_limit_auth(f"login:{spoofed}", max_attempts=10, window_seconds=300)
    finally:
        auth_mod._auth_attempts.clear()


def test_unset_trusted_proxy_trusts_a_home_network_peer(monkeypatch):
    """A home lab does not set the variable. A private proxy still counts."""

    class _Settings:
        PIHERDER_TRUSTED_PROXY_CIDRS = None

    monkeypatch.setattr("app.config.settings", _Settings())
    assert rip.peer_is_trusted_proxy("172.18.0.5")
    assert rip.peer_is_trusted_proxy("100.64.0.5")
    assert rip.peer_is_trusted_proxy("192.0.2.10")
    assert not rip.peer_is_trusted_proxy("198.51.100.8")


def test_extract_client_ip_empty_cidrs_never_trusts_forwarded():
    assert (
        rip.extract_client_ip(
            {"X-Forwarded-For": "1.2.3.4"},
            "172.18.0.5",
            trusted_cidrs=[],
        )
        == "172.18.0.5"
    )


def test_parse_trusted_proxy_cidrs_skips_junk():
    nets = rip.parse_trusted_proxy_cidrs("172.16.0.0/12, not-a-cidr, 127.0.0.1/32")
    assert len(nets) == 2
    assert rip.peer_is_trusted_proxy("172.18.0.5", nets)
    assert not rip.peer_is_trusted_proxy("8.8.8.8", nets)
    assert not rip.peer_is_trusted_proxy("testclient", nets)


def test_client_ip_from_request_object(monkeypatch):
    monkeypatch.setattr(
        rip,
        "trusted_proxy_cidrs_from_settings",
        lambda: rip.parse_trusted_proxy_cidrs(_COMPOSE_CIDRS),
    )
    req = SimpleNamespace(
        headers={"x-forwarded-for": "1.2.3.4"},
        client=SimpleNamespace(host="172.18.0.2"),
    )
    assert rip.client_ip_from_request(req) == "1.2.3.4"


def test_contextvar_and_make_audit_log():
    tok = rip.set_request_client_ip("203.0.113.50")
    try:
        assert rip.get_request_client_ip() == "203.0.113.50"
        al = aw.make_audit_log(
            action="server_update",
            status="success",
            user_id=1,
            details="test",
            finished_at=datetime.utcnow(),
        )
        assert al.client_ip == "203.0.113.50"
        # Explicit override wins
        al2 = aw.make_audit_log(
            action="server_update",
            status="success",
            client_ip="198.51.100.1",
        )
        assert al2.client_ip == "198.51.100.1"
    finally:
        rip.reset_request_client_ip(tok)
    assert rip.get_request_client_ip() is None


def test_make_audit_log_no_context_null_ip():
    # Ensure clean context
    tok = rip.set_request_client_ip(None)
    try:
        al = aw.make_audit_log(action="herder_backup", status="success")
        assert al.client_ip is None
    finally:
        rip.reset_request_client_ip(tok)


def test_backup_audit_uses_job_client_ip_fallback(monkeypatch):
    """Celery workers have no request context; IP must come from job.details."""
    session = MagicMock()
    job = Job(
        id=9,
        server_id=1,
        job_type="backup",
        status="success",
        details='{"user_id": 3, "client_ip": "203.0.113.77", "source_filter": null}',
        created_at=datetime.utcnow(),
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    # No request context
    tok = rip.set_request_client_ip(None)
    try:
        from app.services import backup_audit as ba

        al = ba.record_backup_audit_from_job(session, job, "success", message="ok")
        assert al.client_ip == "203.0.113.77"
        session.add.assert_called()
    finally:
        rip.reset_request_client_ip(tok)


def test_resolve_client_ip_order():
    tok = rip.set_request_client_ip("10.0.0.1")
    try:
        assert aw.resolve_client_ip("9.9.9.9") == "9.9.9.9"
        assert aw.resolve_client_ip(None) == "10.0.0.1"
        assert aw.resolve_client_ip(None, fallback="8.8.8.8") == "10.0.0.1"
    finally:
        rip.reset_request_client_ip(tok)
    assert aw.resolve_client_ip(None, fallback="8.8.8.8") == "8.8.8.8"
