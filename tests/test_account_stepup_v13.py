"""v1.3 slice 1 Deep — force-2FA scope, step-up helper, IdP MFA fail-closed."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.security import auth as auth_mod
from app.services import account_stepup as step
from app.services import app_settings as cfg
from app.services import oidc_svc as oidc


@pytest.fixture(autouse=True)
def _memory_settings(monkeypatch):
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()
    yield store
    cfg.clear_cache()


def _user(**kwargs):
    defaults = dict(
        id=3,
        role="viewer",
        totp_enabled=False,
        totp_secret_encrypted=None,
        hashed_password="x",
        must_change_password=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_legacy_force_2fa_true_means_all(_memory_settings):
    cfg.save_settings({"force_2fa": True})
    assert step.force_2fa_scope() == "all"
    assert step.force_2fa_required() is True


def test_force_2fa_scope_admins_skips_viewer(_memory_settings):
    cfg.save_settings({"force_2fa_scope": "admins", "force_2fa": False})
    assert step.force_2fa_applies(_user(role="viewer")) is False
    assert step.force_2fa_applies(_user(role="admin")) is True
    assert step.force_2fa_applies(_user(role="operator")) is False


def test_force_2fa_operators_includes_admin(_memory_settings):
    cfg.save_settings({"force_2fa_scope": "operators"})
    assert step.force_2fa_applies(_user(role="operator")) is True
    assert step.force_2fa_applies(_user(role="admin")) is True
    assert step.force_2fa_applies(_user(role="viewer")) is False


def test_grace_skips_wall(_memory_settings):
    cfg.save_settings(
        {
            "force_2fa_scope": "all",
            "force_2fa_grace_days": 7,
            "force_2fa_grace_since": datetime.utcnow().isoformat() + "Z",
        }
    )
    assert step.force_2fa_applies(_user(role="viewer")) is False
    cfg.save_settings(
        {
            "force_2fa_scope": "all",
            "force_2fa_grace_days": 7,
            "force_2fa_grace_since": (datetime.utcnow() - timedelta(days=8)).isoformat()
            + "Z",
        }
    )
    assert step.force_2fa_applies(_user(role="viewer")) is True


def test_grace_clamp_home_lab(_memory_settings):
    assert step._as_int(90, 0, step.GRACE_DAYS_MIN, step.GRACE_DAYS_MAX) == 60
    assert step._as_int(-3, 0, step.GRACE_DAYS_MIN, step.GRACE_DAYS_MAX) == 0


def test_verify_stepup_password_when_no_2fa(monkeypatch, _memory_settings):
    user = _user()
    session = MagicMock()
    monkeypatch.setattr("app.services.webauthn_svc.user_has_2fa", lambda s, u: False)
    monkeypatch.setattr(oidc, "password_login_allowed", lambda u: True)
    monkeypatch.setattr(auth_mod, "verify_password", lambda pw, hashed: pw == "GoodPass1x")
    ok, err = step.verify_stepup(session, user, password="GoodPass1x")
    assert ok is True
    ok2, err2 = step.verify_stepup(session, user, password="nope")
    assert ok2 is False
    assert err2 == "password_required"


def test_verify_stepup_passkey_cookie(monkeypatch, _memory_settings):
    user = _user()
    session = MagicMock()
    session.exec.return_value.all.return_value = [1]
    monkeypatch.setattr(
        "app.services.webauthn_svc.user_has_2fa", lambda s, u: True
    )
    monkeypatch.setattr(
        "app.services.webauthn_svc.has_passkeys", lambda s, uid: True
    )
    monkeypatch.setattr(
        "app.services.webauthn_svc.totp_active", lambda u: False
    )
    tok = auth_mod.create_account_stepup_token(3)
    req = MagicMock()
    req.cookies = {auth_mod.ACCOUNT_STEPUP_COOKIE: tok}
    ok, err = step.verify_stepup(session, user, request=req)
    assert ok is True
    bare = MagicMock()
    bare.cookies = {}
    ok2, err2 = step.verify_stepup(session, user, request=bare)
    assert ok2 is False
    assert err2 == "use_passkey"


def test_oidc_wrapper_still_works(monkeypatch, _memory_settings):
    user = _user()
    session = MagicMock()
    session.exec.return_value.all.return_value = [1]
    monkeypatch.setattr(
        "app.services.webauthn_svc.user_has_2fa", lambda s, u: True
    )
    monkeypatch.setattr(
        "app.services.webauthn_svc.has_passkeys", lambda s, uid: True
    )
    monkeypatch.setattr(
        "app.services.webauthn_svc.totp_active", lambda u: False
    )
    req = MagicMock()
    req.cookies = {auth_mod.ACCOUNT_STEPUP_COOKIE: auth_mod.create_account_stepup_token(3)}
    ok, err = oidc.verify_stepup_2fa(session, user, request=req)
    assert ok is True


def test_idp_mfa_fail_closed(_memory_settings):
    assert step.idp_mfa_satisfies_login({"amr": ["mfa"]}) is False  # option off
    cfg.save_settings({"oidc_idp_mfa_satisfies_login_2fa": True, "oidc_idp_mfa_claim": "amr"})
    assert step.idp_mfa_satisfies_login(None) is False
    assert step.idp_mfa_satisfies_login({}) is False
    assert step.idp_mfa_satisfies_login({"amr": ["pwd"]}) is False
    assert step.idp_mfa_satisfies_login({"amr": ["pwd", "otp"]}) is True
    assert step.idp_mfa_satisfies_login({"amr": ["mfa"]}) is True


def test_login_trusted_skip_default_on(_memory_settings):
    assert step.login_trusted_skip_2fa() is True
    cfg.save_settings({"login_trusted_skip_2fa": False})
    assert step.login_trusted_skip_2fa() is False


def test_post_login_path_scope(monkeypatch, _memory_settings):
    cfg.save_settings({"force_2fa_scope": "admins"})
    viewer = _user(role="viewer")
    admin = _user(role="admin")
    session = MagicMock()
    monkeypatch.setattr(auth_mod, "user_has_second_factor", lambda s, u: False)
    assert auth_mod.post_login_path(viewer, session) == "/"
    assert auth_mod.post_login_path(admin, session) == "/auth/force-2fa"
