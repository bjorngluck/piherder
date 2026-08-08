"""v1.2 Stream S — OIDC helpers (no live IdP)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import oidc_svc as oidc
from app.security.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER
from app.services import audit_format as af


def test_normalize_issuer():
    assert oidc.normalize_issuer("https://idp.example.com/") == "https://idp.example.com"
    assert oidc.normalize_issuer("  https://a/b  ") == "https://a/b"


def test_map_role_highest_privilege():
    cfg = {
        "oidc_role_claim": "groups",
        "oidc_role_map": {"ops": "operator", "admins": "admin", "read": "viewer"},
        "oidc_default_role": "viewer",
    }
    assert oidc.map_role_from_claims({"groups": ["read", "ops"]}, cfg) == ROLE_OPERATOR
    assert oidc.map_role_from_claims({"groups": ["read", "admins", "ops"]}, cfg) == ROLE_ADMIN
    assert oidc.map_role_from_claims({"groups": ["unknown"]}, cfg) == ROLE_VIEWER
    assert oidc.map_role_from_claims({}, cfg) == ROLE_VIEWER


def test_map_role_nested_claim():
    cfg = {
        "oidc_role_claim": "realm_access.roles",
        "oidc_role_map": {"piherder-admin": "admin"},
        "oidc_default_role": "viewer",
    }
    claims = {"realm_access": {"roles": ["piherder-admin", "offline_access"]}}
    assert oidc.map_role_from_claims(claims, cfg) == ROLE_ADMIN


def test_pkce_pair_shape():
    v, c = oidc.pkce_pair()
    assert len(v) >= 43
    assert len(c) >= 40
    assert "=" not in c


def test_domain_allowed():
    cfg = {"oidc_allowed_email_domains": "example.com, lab.local"}
    assert oidc.domain_allowed("a@example.com", cfg)
    assert oidc.domain_allowed("b@lab.local", cfg)
    assert not oidc.domain_allowed("c@evil.com", cfg)
    assert oidc.domain_allowed("any@x.com", {"oidc_allowed_email_domains": ""})


def test_email_verified_ok():
    assert oidc.email_verified_ok({"email_verified": True})
    assert not oidc.email_verified_ok(
        {"email_verified": False}, {"oidc_require_email_verified": True}
    )
    # claim omitted → allow
    assert oidc.email_verified_ok({"email": "a@b.com"}, {"oidc_require_email_verified": True})


def test_find_user_existing_link(monkeypatch):
    user = SimpleNamespace(id=1, is_active=True, email="a@b.com", role="admin")
    ident = SimpleNamespace(user_id=1, issuer="https://idp", subject="sub1")

    session = MagicMock()
    session.get.return_value = user

    monkeypatch.setattr(oidc, "get_identity_by_iss_sub", lambda *a, **k: ident)
    monkeypatch.setattr(
        oidc,
        "oidc_settings",
        lambda: {"oidc_issuer": "https://idp", "oidc_auto_link_by_email": True},
    )

    u, reason, row = oidc.find_user_for_login(
        session, {"sub": "sub1", "email": "a@b.com"}, oidc.oidc_settings()
    )
    assert u is user
    assert reason == "existing"
    assert row is ident


def test_find_user_email_auto_link(monkeypatch):
    user = SimpleNamespace(id=2, is_active=True, email="a@b.com", role="operator")

    session = MagicMock()
    # select path for email match
    session.exec.return_value.all.return_value = [user]

    monkeypatch.setattr(oidc, "get_identity_by_iss_sub", lambda *a, **k: None)
    monkeypatch.setattr(oidc, "get_identity_for_user_issuer", lambda *a, **k: None)
    monkeypatch.setattr(
        oidc,
        "oidc_settings",
        lambda: {
            "oidc_issuer": "https://idp",
            "oidc_auto_link_by_email": True,
            "oidc_require_email_verified": True,
            "oidc_allowed_email_domains": "",
            "oidc_default_role": "viewer",
            "oidc_role_claim": "groups",
            "oidc_role_map": "{}",
        },
    )

    u, reason, row = oidc.find_user_for_login(
        session,
        {"sub": "newsub", "email": "a@b.com", "email_verified": True},
        oidc.oidc_settings(),
    )
    assert u is user
    assert reason == "email_match"
    assert row is None


def test_find_user_email_conflict_existing_no_auto(monkeypatch):
    """Email taken but auto-link off → clear error path via taken check on JIT."""
    taken = SimpleNamespace(id=3, is_active=True, email="a@b.com")
    session = MagicMock()
    # first exec: email match list empty because auto off skips; JIT taken check
    # find_user_for_login with auto off goes to JIT
    session.exec.return_value.first.return_value = taken
    session.exec.return_value.all.return_value = []

    monkeypatch.setattr(oidc, "get_identity_by_iss_sub", lambda *a, **k: None)
    cfg = {
        "oidc_issuer": "https://idp",
        "oidc_auto_link_by_email": False,
        "oidc_require_email_verified": False,
        "oidc_allowed_email_domains": "",
        "oidc_default_role": "viewer",
        "oidc_role_claim": "groups",
        "oidc_role_map": "{}",
    }
    monkeypatch.setattr(oidc, "oidc_settings", lambda: cfg)

    with pytest.raises(oidc.OidcFlowError, match="already exists"):
        oidc.find_user_for_login(
            session, {"sub": "x", "email": "a@b.com"}, cfg
        )


def test_password_login_flags():
    u = SimpleNamespace(password_login_enabled=True, hashed_password="x")
    assert oidc.password_login_allowed(u)
    u.password_login_enabled = False
    assert not oidc.password_login_allowed(u)


def test_audit_labels_for_sso():
    for key in (
        "sso_login",
        "sso_login_failed",
        "sso_link",
        "sso_unlink",
        "sso_user_provisioned",
        "user_password_removed",
        "user_password_set",
    ):
        assert af.action_label(key)
        assert af.action_label(key) != key or "_" not in key


def test_state_token_roundtrip():
    from datetime import timedelta
    from app.security.auth import create_access_token, decode_token_payload

    tok = create_access_token(
        {"oidc": True, "mode": "login", "cv": "ver", "nonce": "n", "sp": "sp1"},
        expires_delta=timedelta(minutes=5),
    )
    payload = oidc.parse_state_token(tok)
    assert payload and payload["sp"] == "sp1"
    assert payload["cv"] == "ver"
    assert decode_token_payload(tok)["mode"] == "login"


def test_create_link_conflict(monkeypatch):
    session = MagicMock()
    user = SimpleNamespace(id=1)
    other = SimpleNamespace(user_id=99, issuer="https://idp", subject="sub")

    monkeypatch.setattr(oidc, "get_identity_by_iss_sub", lambda *a, **k: other)
    with pytest.raises(oidc.OidcFlowError, match="another account"):
        oidc.create_link(
            session,
            user,
            issuer="https://idp",
            subject="sub",
            claims={"sub": "sub", "email": "a@b.com"},
        )
