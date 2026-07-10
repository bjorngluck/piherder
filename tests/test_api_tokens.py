"""Unit tests for API token hashing, scopes, IP allowlists, and feature gates."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import api_tokens as tok


def test_normalize_scopes_defaults_and_filter():
    assert tok.normalize_scopes(None) == ["jobs", "read"]
    assert tok.normalize_scopes("read") == ["read"]
    assert tok.normalize_scopes("jobs,read,evil") == ["jobs", "read"]
    assert tok.normalize_scopes(["JOBS", "read", "edit"]) == ["edit", "jobs", "read"]
    assert tok.scopes_csv(["jobs"]) == "jobs"


def test_feature_scopes_and_capability_fallback():
    # feature-only → adds read capability
    assert "read" in tok.normalize_scopes(["feature:backup"])
    assert "feature:backup" in tok.normalize_scopes(["feature:backup"])
    full = tok.normalize_scopes("read,jobs,edit,feature:os,feature:docker")
    assert full == [
        "edit",
        "feature:docker",
        "feature:os",
        "jobs",
        "read",
    ]


def test_feature_keys_allowed():
    assert tok.feature_keys_allowed({"read", "jobs"}) is None  # unrestricted
    assert tok.feature_keys_allowed({"read", "feature:backup"}) == {"backup"}
    assert tok.token_allows_feature({"read", "jobs"}, "docker") is True
    assert tok.token_allows_feature({"read", "feature:backup"}, "docker") is False
    assert tok.token_allows_feature({"read", "feature:backup"}, "backup") is True


def test_hash_and_generate():
    plain = tok.generate_plaintext_token()
    assert plain.startswith("ph_")
    assert len(plain) > 20
    h1 = tok.hash_token(plain)
    h2 = tok.hash_token(plain)
    assert h1 == h2
    assert h1 != tok.hash_token(plain + "x")


def test_token_has_scope():
    row = SimpleNamespace(scopes="read")
    assert tok.token_has_scope(row, "read")
    assert not tok.token_has_scope(row, "jobs")


def test_cidrs_normalize_and_match():
    assert tok.normalize_allowed_cidrs("10.0.0.1, 192.168.0.0/24") == [
        "10.0.0.1/32",
        "192.168.0.0/24",
    ]
    assert tok.normalize_allowed_cidrs("not-an-ip") == []
    assert tok.client_ip_allowed([], "1.2.3.4") is True
    assert tok.client_ip_allowed(["10.0.0.0/8"], "10.1.2.3") is True
    assert tok.client_ip_allowed(["10.0.0.0/8"], "11.0.0.1") is False
    assert tok.client_ip_allowed(["192.168.1.10"], "192.168.1.10") is True
    assert tok.client_ip_allowed(["192.168.1.10"], None) is False


def test_extract_client_ip():
    assert tok.extract_client_ip({"X-Forwarded-For": "1.1.1.1, 2.2.2.2"}, "9.9.9.9") == "1.1.1.1"
    assert tok.extract_client_ip({"X-Real-IP": "8.8.8.8"}, "9.9.9.9") == "8.8.8.8"
    assert tok.extract_client_ip({}, "9.9.9.9") == "9.9.9.9"


def test_token_public_dict_active():
    now = datetime.utcnow()
    row = SimpleNamespace(
        id=1,
        name="n8n",
        token_prefix="ph_abc",
        scopes="read,jobs,feature:backup",
        allowed_cidrs='["10.0.0.0/8"]',
        created_by_user_id=2,
        created_at=now,
        last_used_at=None,
        revoked_at=None,
        expires_at=None,
    )
    d = tok.token_public_dict(row)
    assert d["active"] is True
    assert "feature:backup" in d["scopes"]
    assert d["allowed_features"] == ["backup"]
    assert d["allowed_cidrs"] == ["10.0.0.0/8"]

    row.revoked_at = now
    assert tok.token_public_dict(row)["active"] is False

    row.revoked_at = None
    row.expires_at = now - timedelta(hours=1)
    assert tok.token_public_dict(row)["active"] is False


def test_lookup_rejects_non_ph():
    session = MagicMock()
    assert tok.lookup_active_token(session, "jwt.not.a.token") is None
    session.exec.assert_not_called()


def test_api_meta_dict_has_endpoints():
    meta = tok.api_meta_dict()
    assert meta["version"] == "v1"
    assert "read" in meta["scopes"]
    assert "edit" in meta["scopes"]
    assert any(e["path"] == "/api/v1/servers/{id}/features" for e in meta["endpoints"])
