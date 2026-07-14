"""Client IP resolution (Caddy XFF) + audit log client_ip attachment."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import request_ip as rip
from app.services import audit_write as aw
from app.services.backup_audit import record_backup_audit_event
from app.models import Job


def test_extract_client_ip_prefers_xff():
    assert (
        rip.extract_client_ip({"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}, "172.18.0.5")
        == "203.0.113.9"
    )


def test_extract_client_ip_x_real_ip_and_peer():
    assert rip.extract_client_ip({"X-Real-IP": "198.51.100.2"}, "172.18.0.5") == "198.51.100.2"
    assert rip.extract_client_ip({}, "172.18.0.5") == "172.18.0.5"
    assert rip.extract_client_ip({"X-Real-IP": "10.0.0.5:44321"}, None) == "10.0.0.5"


def test_client_ip_from_request_object():
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
