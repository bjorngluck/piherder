"""Unit tests for RBAC helpers (no HTTP / DB required)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.security.auth import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
    _admin_only_path,
    _viewer_write_allowed,
    count_active_admins,
    create_access_token,
    get_admin_user,
    get_current_user,
    get_operator_user,
    is_sole_admin,
    normalize_role,
    role_at_least,
    user_role,
)


def _user(role: str | None, **kwargs) -> SimpleNamespace:
    base = dict(
        id=kwargs.pop("id", 1),
        role=role,
        is_active=kwargs.pop("is_active", True),
        must_change_password=False,
        totp_enabled=True,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class _FakeSession:
    """Mimics session.exec(...).all() returning pre-filtered active users."""

    def __init__(self, users: list):
        self._users = users

    def exec(self, _statement):
        return SimpleNamespace(all=lambda: list(self._users))

    def get(self, _model, _pk):
        return None


def test_normalize_role_defaults_and_invalid():
    # Fail-closed: unknown / empty → viewer (not admin)
    assert normalize_role(None) == ROLE_VIEWER
    assert normalize_role("") == ROLE_VIEWER
    assert normalize_role("nope") == ROLE_VIEWER
    assert normalize_role("VIEWER") == ROLE_VIEWER
    assert normalize_role("operator") == ROLE_OPERATOR
    assert normalize_role("admin") == ROLE_ADMIN
    assert VALID_ROLES == {ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}


def test_user_role_and_rank():
    assert user_role(_user("viewer")) == ROLE_VIEWER
    assert user_role(_user(None)) == ROLE_VIEWER
    assert role_at_least(_user("admin"), ROLE_ADMIN)
    assert role_at_least(_user("operator"), ROLE_OPERATOR)
    assert role_at_least(_user("operator"), ROLE_VIEWER)
    assert not role_at_least(_user("viewer"), ROLE_OPERATOR)
    assert not role_at_least(_user("viewer"), ROLE_ADMIN)
    assert role_at_least(_user("admin"), ROLE_VIEWER)


def test_viewer_write_allowlist():
    assert _viewer_write_allowed("/auth/logout")
    assert _viewer_write_allowed("/auth/account")
    assert _viewer_write_allowed("/auth/account/password")
    assert _viewer_write_allowed("/auth/2fa/enable")
    assert _viewer_write_allowed("/auth/force-password")
    assert _viewer_write_allowed("/auth/force-2fa")
    assert _viewer_write_allowed("/auth/me/avatar")
    assert _viewer_write_allowed("/notifications/dismiss/1")
    # Fleet mutations blocked for viewers
    assert not _viewer_write_allowed("/servers/1/run/backup")
    assert not _viewer_write_allowed("/servers/1/run/os_patch")
    assert not _viewer_write_allowed("/auth/users")
    assert not _viewer_write_allowed("/jobs")


def test_admin_only_paths():
    assert _admin_only_path("/auth/users")
    assert _admin_only_path("/auth/users/create")
    assert _admin_only_path("/auth/users/5/delete")
    assert _admin_only_path("/herder-backups/restore")
    assert _admin_only_path("/herder-backups/run")
    assert _admin_only_path("/herder-backups/api-tokens")
    assert not _admin_only_path("/auth/account")
    assert not _admin_only_path("/servers")
    # Settings page GET is not admin-prefix (operators may view; mutations are gated)
    assert not _admin_only_path("/herder-backups")


def test_rbac_matrix_mutating_intent():
    """Document expected matrix: viewer self-service only; admin-only users admin."""
    viewer_ok = [
        "/auth/logout",
        "/auth/account",
        "/notifications/1/dismiss",
    ]
    viewer_block = [
        "/servers/1/run/backup",
        "/servers/1/run/os_patch",
        "/auth/users",
        "/herder-backups/run",
    ]
    for path in viewer_ok:
        assert _viewer_write_allowed(path), path
    for path in viewer_block:
        assert not _viewer_write_allowed(path), path
    assert _admin_only_path("/auth/users")
    # Operators are not admin-only blocked (enforcement uses role != admin separately)
    assert not _admin_only_path("/servers/1/run/backup")


def test_count_active_admins():
    session = _FakeSession(
        [
            _user("admin", id=1),
            _user("operator", id=2),
            _user("admin", id=3),
            _user("viewer", id=4),
        ]
    )
    assert count_active_admins(session) == 2


def test_is_sole_admin_single():
    only = _user("admin", id=1)
    session = _FakeSession([only, _user("viewer", id=2)])
    assert is_sole_admin(session, only) is True
    assert is_sole_admin(session, _user("viewer", id=2)) is False


def test_is_sole_admin_two_admins():
    a1 = _user("admin", id=1)
    a2 = _user("admin", id=2)
    session = _FakeSession([a1, a2])
    assert is_sole_admin(session, a1) is False
    assert is_sole_admin(session, a2) is False


def test_is_sole_admin_operator_never():
    op = _user("operator", id=1)
    session = _FakeSession([op])
    assert is_sole_admin(session, op) is False


def test_is_sole_admin_inactive_not_in_active_set():
    """SQL filters is_active; fake session only returns active users."""
    remaining = _user("admin", id=1)
    session = _FakeSession([remaining])  # inactive admin omitted (as SQL would)
    assert count_active_admins(session) == 1
    assert is_sole_admin(session, remaining) is True


def test_get_admin_user_dependency():
    assert get_admin_user(_user("admin")) is not None
    with pytest.raises(HTTPException) as ei:
        get_admin_user(_user("operator"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        get_admin_user(_user("viewer"))
    assert ei.value.status_code == 403


def test_get_operator_user_dependency():
    assert get_operator_user(_user("admin")) is not None
    assert get_operator_user(_user("operator")) is not None
    with pytest.raises(HTTPException) as ei:
        get_operator_user(_user("viewer"))
    assert ei.value.status_code == 403


def _call_get_current_user(user, method: str, path: str):
    token = create_access_token({"sub": str(user.id)})
    request = SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        cookies={},
    )
    session = MagicMock()
    session.get.return_value = user
    with patch("app.security.auth.force_2fa_required", return_value=False):
        return get_current_user(request, token, session)


def test_get_current_user_viewer_blocked_on_fleet_post():
    with pytest.raises(HTTPException) as ei:
        _call_get_current_user(
            _user("viewer", id=9), "POST", "/servers/1/run/backup"
        )
    assert ei.value.status_code == 403
    assert "read-only" in (ei.value.detail or "").lower()


def test_get_current_user_viewer_allowed_self_service_post():
    user = _user("viewer", id=9)
    out = _call_get_current_user(user, "POST", "/auth/account")
    assert out is user


def test_get_current_user_operator_blocked_on_users_post():
    with pytest.raises(HTTPException) as ei:
        _call_get_current_user(
            _user("operator", id=3), "POST", "/auth/users/create"
        )
    assert ei.value.status_code == 403
    assert "admin" in (ei.value.detail or "").lower()


def test_get_current_user_operator_fleet_post_ok():
    user = _user("operator", id=3)
    out = _call_get_current_user(user, "POST", "/servers/1/run/backup")
    assert out is user


def test_get_current_user_viewer_get_fleet_ok():
    """GET stays open for all logged-in roles (middleware only gates mutating)."""
    user = _user("viewer", id=9)
    out = _call_get_current_user(user, "GET", "/servers/1")
    assert out is user
