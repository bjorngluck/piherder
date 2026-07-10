"""Unit tests for API token hashing, scopes, and verify helpers."""
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
    assert tok.normalize_scopes(["JOBS", "read"]) == ["jobs", "read"]
    assert tok.scopes_csv(["jobs"]) == "jobs"


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


def test_token_public_dict_active():
    now = datetime.utcnow()
    row = SimpleNamespace(
        id=1,
        name="n8n",
        token_prefix="ph_abc",
        scopes="read,jobs",
        created_by_user_id=2,
        created_at=now,
        last_used_at=None,
        revoked_at=None,
        expires_at=None,
    )
    d = tok.token_public_dict(row)
    assert d["active"] is True
    assert d["scopes"] == ["jobs", "read"]

    row.revoked_at = now
    assert tok.token_public_dict(row)["active"] is False

    row.revoked_at = None
    row.expires_at = now - timedelta(hours=1)
    assert tok.token_public_dict(row)["active"] is False


def test_verify_bearer_rejects_non_ph():
    session = MagicMock()
    assert tok.verify_bearer_token(session, "jwt.not.a.token") is None
    session.exec.assert_not_called()
