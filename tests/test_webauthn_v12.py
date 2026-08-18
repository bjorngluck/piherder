"""v1.2 Stream I — WebAuthn / passkeys helpers (no live browser ceremony)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import webauthn_svc as wa


def test_resolve_rp_id_from_hostname(monkeypatch):
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", "piherder.example.com")
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", None)
    assert wa.resolve_rp_id() == "piherder.example.com"


def test_resolve_rp_id_strips_scheme_and_port(monkeypatch):
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", "https://lab.home:8443/path")
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", None)
    assert wa.resolve_rp_id() == "lab.home"


def test_resolve_rp_id_from_public_url(monkeypatch):
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", None)
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", "https://ph.example.net:8443")
    assert wa.resolve_rp_id() == "ph.example.net"
    assert wa.resolve_expected_origin() == "https://ph.example.net:8443"


def test_resolve_fallback_localhost(monkeypatch):
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", None)
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", None)
    assert wa.resolve_rp_id() == "localhost"
    assert wa.resolve_expected_origin().startswith("http://localhost")


def test_totp_active():
    assert wa.totp_active(SimpleNamespace(totp_enabled=True, totp_secret_encrypted="x"))
    assert not wa.totp_active(SimpleNamespace(totp_enabled=True, totp_secret_encrypted=None))
    assert not wa.totp_active(SimpleNamespace(totp_enabled=False, totp_secret_encrypted="x"))


def test_user_has_2fa_totp_only():
    user = SimpleNamespace(id=1, totp_enabled=True, totp_secret_encrypted="enc")
    session = MagicMock()
    assert wa.user_has_2fa(session, user) is True
    session.exec.assert_not_called()


def test_user_has_2fa_passkey_only():
    user = SimpleNamespace(id=7, totp_enabled=False, totp_secret_encrypted=None)
    session = MagicMock()
    # count_passkeys → session.exec(...).all() → list of ids
    session.exec.return_value.all.return_value = [1]
    assert wa.user_has_2fa(session, user) is True


def test_user_has_2fa_neither():
    user = SimpleNamespace(id=7, totp_enabled=False, totp_secret_encrypted=None)
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    assert wa.user_has_2fa(session, user) is False


def test_challenge_token_roundtrip():
    from app.security.auth import create_access_token  # noqa: F401 — ensure jwt key

    token = wa.mint_challenge_token(kind="reg", user_id=42, challenge_b64="dGVzdA")
    raw = wa.read_challenge_token(token, kind="reg", user_id=42)
    assert raw is not None
    # wrong kind / user rejected
    assert wa.read_challenge_token(token, kind="auth", user_id=42) is None
    assert wa.read_challenge_token(token, kind="reg", user_id=99) is None
    assert wa.read_challenge_token(None, kind="reg", user_id=42) is None


def test_credential_public_dict():
    from datetime import datetime

    row = SimpleNamespace(
        id=3,
        nickname="Yubi",
        created_at=datetime(2026, 8, 8, 12, 0, 0),
        last_used_at=None,
        aaguid=None,
        backup_eligible=True,
        backup_state=False,
    )
    d = wa.credential_public_dict(row)
    assert d["id"] == 3
    assert d["nickname"] == "Yubi"
    assert d["created_at"].startswith("2026-08-08")
    assert d["backup_eligible"] is True


def test_registration_options_json_builds(monkeypatch):
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", "localhost")
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", "http://localhost:8000")
    user = SimpleNamespace(id=1, email="a@example.com", display_name="A")
    session = MagicMock()
    # no existing credentials
    session.exec.return_value.all.return_value = []

    options_json, chal_token = wa.registration_options_json(session, user)
    assert '"challenge"' in options_json or "challenge" in options_json
    assert chal_token
    raw = wa.read_challenge_token(chal_token, kind="reg", user_id=1)
    assert raw is not None and len(raw) > 0


def test_registration_options_max_credentials(monkeypatch):
    monkeypatch.setattr(wa, "MAX_CREDENTIALS_PER_USER", 2)
    user = SimpleNamespace(id=1, email="a@example.com", display_name="A")
    session = MagicMock()
    session.exec.return_value.all.return_value = [1, 2]
    with pytest.raises(wa.WebAuthnConfigError):
        wa.registration_options_json(session, user)


def test_authentication_options_requires_creds(monkeypatch):
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", "localhost")
    user = SimpleNamespace(id=1, email="a@example.com", display_name="A")
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    with pytest.raises(wa.WebAuthnConfigError):
        wa.authentication_options_json(session, user)


def test_authentication_options_with_string_transports(monkeypatch):
    """Regression: stored transports are JSON strings; webauthn 3.x needs enums.

    Without conversion, options_to_json raises:
    AttributeError: 'str' object has no attribute 'value'
    """
    monkeypatch.setattr(wa.settings, "PIHERDER_HOSTNAME", "localhost")
    monkeypatch.setattr(wa.settings, "PIHERDER_PUBLIC_URL", "http://localhost:8000")
    user = SimpleNamespace(id=1, email="a@example.com", display_name="A")
    cred_id = wa._b64url_encode(b"test-cred-id-bytes!!")
    row = SimpleNamespace(
        credential_id=cred_id,
        transports='["internal", "hybrid", "usb"]',
    )
    session = MagicMock()
    session.exec.return_value.all.return_value = [row]

    options_json, chal_token = wa.authentication_options_json(session, user)
    assert chal_token
    assert "challenge" in options_json
    assert "allowCredentials" in options_json
    # transports serialized as string values in JSON
    assert "internal" in options_json


def test_authenticator_transports_helper():
    enums = wa._authenticator_transports(["internal", "hybrid", "nope", "cable"])
    assert enums is not None
    vals = sorted(t.value for t in enums)
    assert "internal" in vals
    assert "hybrid" in vals
    assert "nope" not in vals
    # cable normalized to hybrid (deduped)
    assert vals.count("hybrid") == 1
    assert wa._authenticator_transports(None) is None
    assert wa._authenticator_transports([]) is None


def test_user_has_second_factor_security_helper():
    from app.security.auth import user_has_second_factor

    user = SimpleNamespace(id=1, totp_enabled=True, totp_secret_encrypted="x")
    assert user_has_second_factor(MagicMock(), user) is True

    user2 = SimpleNamespace(id=2, totp_enabled=False, totp_secret_encrypted=None)
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    assert user_has_second_factor(session, user2) is False


def test_templates_user_has_2fa_includes_passkeys():
    """View-secrets / template deploy enrollment must count passkeys, not only TOTP."""
    from app.routers import templates_common as tc

    user = SimpleNamespace(id=5, totp_enabled=False, totp_secret_encrypted=None)
    session = MagicMock()
    # no passkeys
    session.exec.return_value.all.return_value = []
    assert tc._user_has_2fa(session, user) is False
    # one passkey id
    session.exec.return_value.all.return_value = [1]
    assert tc._user_has_2fa(session, user) is True

    totp_user = SimpleNamespace(id=6, totp_enabled=True, totp_secret_encrypted="enc")
    session.exec.return_value.all.return_value = []
    assert tc._user_has_2fa(session, totp_user) is True


def test_oidc_stepup_accepts_account_stepup_cookie():
    from app.security.auth import ACCOUNT_STEPUP_COOKIE, create_account_stepup_token
    from app.services import oidc_svc as oidc

    user = SimpleNamespace(id=3, totp_enabled=False, totp_secret_encrypted=None)
    session = MagicMock()
    # passkey-only
    session.exec.return_value.all.return_value = [1]
    req = MagicMock()
    req.cookies = {ACCOUNT_STEPUP_COOKIE: create_account_stepup_token(3)}
    ok, err = oidc.verify_stepup_2fa(session, user, request=req)
    assert ok is True
    assert err == ""

    bare = MagicMock()
    bare.cookies = {}
    ok2, err2 = oidc.verify_stepup_2fa(session, user, request=bare)
    assert ok2 is False
    assert err2 == "use_passkey"


def test_post_login_path_force_2fa_passkey(monkeypatch):
    from app.security import auth as auth_mod

    monkeypatch.setattr(auth_mod, "force_2fa_required", lambda: True)
    user = SimpleNamespace(id=1, must_change_password=False, totp_enabled=False)
    session = MagicMock()
    with patch.object(auth_mod, "user_has_second_factor", return_value=True):
        assert auth_mod.post_login_path(user, session) == "/"
    with patch.object(auth_mod, "user_has_second_factor", return_value=False):
        assert auth_mod.post_login_path(user, session) == "/auth/force-2fa"


def test_audit_labels_for_passkeys():
    from app.services import audit_format as af

    labels = getattr(af, "_ACTION_LABELS", {})
    assert labels.get("user_passkey_registered")
    assert labels.get("user_passkey_revoked")
    assert "Passkey" in af.format_action_label("user_passkey_registered") if hasattr(af, "format_action_label") else True
