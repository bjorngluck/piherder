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


def test_server_feature_enabled():
    srv = SimpleNamespace(
        backup_enabled=True,
        os_patch_enabled=False,
        container_patch_enabled=True,
    )
    assert tok.server_feature_enabled(srv, "backup") is True
    assert tok.server_feature_enabled(srv, "os") is False
    assert tok.server_feature_enabled(srv, "docker") is True
    assert "disabled" in tok.feature_disabled_message("os").lower()
    assert "feature:backup" in tok.feature_scope_denied_message("backup")


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
    # Port stripped (Caddy {remote} vs {remote_host})
    assert tok.extract_client_ip({"X-Real-IP": "10.0.0.5:44321"}, "9.9.9.9") == "10.0.0.5"
    assert tok.extract_client_ip({"X-Forwarded-For": "[2001:db8::1]:9999"}, None) == "2001:db8::1"


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


def test_update_api_token_scopes_and_cidrs():
    session = MagicMock()
    row = SimpleNamespace(
        id=1,
        name="old",
        scopes="read",
        allowed_cidrs=None,
        revoked_at=None,
        token_prefix="ph_old",
        token_hash="hash1",
    )
    session.add = MagicMock()
    session.commit = MagicMock()
    session.refresh = MagicMock()

    out = tok.update_api_token(
        session,
        row,
        name="n8n",
        scopes=["read", "jobs", "feature:backup"],
        allowed_cidrs="10.0.0.0/8",
        update_cidrs=True,
    )
    assert out.name == "n8n"
    assert "jobs" in out.scopes
    assert "feature:backup" in out.scopes
    assert out.allowed_cidrs is not None
    assert "10.0.0.0/8" in out.allowed_cidrs


def test_update_rejects_revoked():
    session = MagicMock()
    row = SimpleNamespace(revoked_at=datetime.utcnow(), name="x")
    with pytest.raises(ValueError, match="revoked"):
        tok.update_api_token(session, row, name="y")


def test_rotate_api_token_changes_hash():
    session = MagicMock()
    old_hash = tok.hash_token("ph_oldsecretvalue000000000000000000")
    row = SimpleNamespace(
        id=1,
        name="n8n",
        token_prefix="ph_old",
        token_hash=old_hash,
        revoked_at=None,
        expires_at=None,
    )
    session.add = MagicMock()
    session.commit = MagicMock()
    session.refresh = MagicMock()

    updated, plain = tok.rotate_api_token(session, row)
    assert plain.startswith("ph_")
    assert updated.token_hash != old_hash
    assert updated.token_hash == tok.hash_token(plain)
    assert updated.token_prefix == plain[:12]
    assert updated.name == "n8n"


def test_rotate_rejects_revoked():
    session = MagicMock()
    row = SimpleNamespace(revoked_at=datetime.utcnow(), expires_at=None)
    with pytest.raises(ValueError, match="revoked"):
        tok.rotate_api_token(session, row)


def test_api_meta_dict_has_endpoints():
    meta = tok.api_meta_dict()
    assert meta["version"] == "v1"
    assert "read" in meta["scopes"]
    assert "edit" in meta["scopes"]
    assert any(e["path"] == "/api/v1/servers/{id}/features" for e in meta["endpoints"])


def test_diagnose_plaintext_token_ok_and_ip():
    session = MagicMock()
    plain = tok.generate_plaintext_token()
    row = SimpleNamespace(
        id=9,
        name="n8n",
        token_prefix=plain[:12],
        token_hash=tok.hash_token(plain),
        scopes="read,jobs,feature:backup",
        allowed_cidrs='["10.0.0.0/8"]',
        created_by_user_id=1,
        created_at=datetime.utcnow(),
        last_used_at=None,
        revoked_at=None,
        expires_at=None,
    )
    session.exec = MagicMock(return_value=MagicMock(first=MagicMock(return_value=row)))
    session.add = MagicMock()
    session.commit = MagicMock()
    session.refresh = MagicMock()

    bad = tok.diagnose_plaintext_token(session, plain, client_ip="8.8.8.8", touch_last_used=False)
    assert bad["ok"] is False
    assert bad["error"] == "ip_not_allowed"
    assert bad["token"]["id"] == 9

    good = tok.diagnose_plaintext_token(session, plain, client_ip="10.1.2.3", touch_last_used=True)
    assert good["ok"] is True
    assert "read" in good["scopes"]
    assert good["has_jobs"] is True
    assert good["token"]["name"] == "n8n"
    session.commit.assert_called()  # last_used touch


def test_normalize_token_list_status():
    assert tok.normalize_token_list_status(None) == "active"
    assert tok.normalize_token_list_status("revoked") == "revoked"
    assert tok.normalize_token_list_status("ALL") == "all"
    assert tok.normalize_token_list_status("nope") == "active"
    assert tok.normalize_token_list_status(None, include_revoked=True) == "all"
    assert tok.normalize_token_list_status(None, include_revoked=False) == "active"


def test_list_api_tokens_status_filter():
    session = MagicMock()
    active = SimpleNamespace(id=1, revoked_at=None, created_at=datetime.utcnow())
    revoked = SimpleNamespace(id=2, revoked_at=datetime.utcnow(), created_at=datetime.utcnow())
    session.exec = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[active, revoked])))

    assert [t.id for t in tok.list_api_tokens(session, status="active")] == [1]
    assert [t.id for t in tok.list_api_tokens(session, status="revoked")] == [2]
    assert [t.id for t in tok.list_api_tokens(session, status="all")] == [1, 2]
    # legacy flag
    assert len(tok.list_api_tokens(session, include_revoked=True)) == 2
    assert len(tok.list_api_tokens(session, include_revoked=False)) == 1

    counts = tok.count_api_tokens_by_status(session)
    assert counts == {"active": 1, "revoked": 1, "all": 2}


def test_diagnose_rejects_empty_and_unknown():
    session = MagicMock()
    session.exec = MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    empty = tok.diagnose_plaintext_token(session, "", client_ip="1.1.1.1")
    assert empty["ok"] is False
    assert empty["error"] == "missing_token"
    unknown = tok.diagnose_plaintext_token(session, "ph_notarealtokenvalue000000000000", client_ip="1.1.1.1")
    assert unknown["ok"] is False
    assert unknown["error"] == "invalid_or_revoked"
