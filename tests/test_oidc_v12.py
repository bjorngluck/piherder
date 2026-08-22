"""v1.2 Stream S — OIDC helpers (no live IdP)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest

from app.services import oidc_svc as oidc
from app.security.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER
from app.services import audit_format as af


def test_account_redir_keeps_unlink_fragment():
    from app.routers.auth_oidc import _account_redir

    r = _account_redir(error="2fa_required", fragment="account-sso-unlink-9")
    loc = r.headers.get("location") or r.headers.get("Location")
    assert loc == "/auth/account?error=2fa_required#account-sso-unlink-9"


def test_normalize_issuer():
    assert oidc.normalize_issuer("https://idp.example.com/") == "https://idp.example.com"
    assert oidc.normalize_issuer("  https://a/b  ") == "https://a/b"


def test_accepted_jwt_issuers_authentik_trailing_slash():
    configured = "https://login.example/application/o/piherder"
    advertised = "https://login.example/application/o/piherder/"
    got = oidc.accepted_jwt_issuers(configured, advertised)
    assert advertised in got
    assert configured in got
    # Settings strip the slash; ID token iss still matches
    assert oidc.accepted_jwt_issuers(configured, None) == [
        configured,
        configured + "/",
    ]


def test_map_oidc_flow_error():
    assert oidc.map_oidc_flow_error("Invalid identity token from provider (InvalidIssuerError)") == "sso_token"
    assert oidc.map_oidc_flow_error("Email address is not verified at the identity provider") == "sso_email"
    assert oidc.map_oidc_flow_error("Email domain is not allowed for SSO") == "sso_domain"
    assert oidc.map_oidc_flow_error("This SSO identity is already linked to another account") == "sso_link_conflict"
    assert oidc.map_oidc_flow_error("access denied") == "sso_denied"


def test_decode_id_token_accepts_authentik_trailing_slash(monkeypatch):
    """Regression: Authentik iss has a trailing slash; Settings store it stripped."""
    import time

    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key()
    advertised = "https://login.example/application/o/piherder/"
    configured = "https://login.example/application/o/piherder"
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": advertised,
            "aud": "client-id",
            "sub": "user-1",
            "exp": now + 300,
            "iat": now,
        },
        key,
        algorithm="RS256",
    )
    monkeypatch.setattr(
        oidc,
        "fetch_discovery",
        lambda iss: {
            "jwks_uri": "https://login.example/jwks",
            "issuer": advertised,
            "id_token_signing_alg_values_supported": ["RS256"],
        },
    )

    class _FakeJwk:
        def __init__(self, *a, **k):
            del a, k

        def get_signing_key_from_jwt(self, _tok):
            return SimpleNamespace(key=pub)

    monkeypatch.setattr(oidc, "PyJWKClient", _FakeJwk)
    claims = oidc._decode_id_token(token, issuer=configured, client_id="client-id")
    assert claims["sub"] == "user-1"
    assert claims["iss"] == advertised


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
    # claim omitted → not verified (operator can disable the require flag)
    assert not oidc.email_verified_ok(
        {"email": "a@b.com"}, {"oidc_require_email_verified": True}
    )
    assert oidc.email_verified_ok(
        {"email": "a@b.com"}, {"oidc_require_email_verified": False}
    )


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
        "user_role_changed",
        "user_role_sync_skipped",
        "user_password_removed",
        "user_password_set",
    ):
        assert af.action_label(key)
        assert af.action_label(key) != key or "_" not in key
    assert af.action_label("user_role_sync_skipped") == "SSO role sync skipped"


def test_maybe_sync_role_skips_sole_admin(monkeypatch):
    user = SimpleNamespace(id=1, role="admin")
    session = MagicMock()
    cfg = {
        "oidc_sync_roles_on_login": True,
        "oidc_role_claim": "groups",
        "oidc_role_map": {"viewers": "viewer"},
        "oidc_default_role": "viewer",
    }
    monkeypatch.setattr("app.security.auth.is_sole_admin", lambda *_a, **_k: True)
    status, mapped = oidc.maybe_sync_role(session, user, {"groups": ["viewers"]}, cfg)
    assert status == "skipped_sole_admin"
    assert mapped == "viewer"
    assert user.role == "admin"


def test_maybe_sync_role_demotes_when_not_sole(monkeypatch):
    user = SimpleNamespace(id=1, role="admin")
    session = MagicMock()
    cfg = {
        "oidc_sync_roles_on_login": True,
        "oidc_role_claim": "groups",
        "oidc_role_map": {"viewers": "viewer"},
        "oidc_default_role": "viewer",
    }
    monkeypatch.setattr("app.security.auth.is_sole_admin", lambda *_a, **_k: False)
    status, mapped = oidc.maybe_sync_role(session, user, {"groups": ["viewers"]}, cfg)
    assert status == "changed"
    assert mapped == "viewer"
    assert user.role == "viewer"


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
