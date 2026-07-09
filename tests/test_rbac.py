"""Unit tests for RBAC helpers (no HTTP / DB required)."""
from __future__ import annotations

from types import SimpleNamespace

from app.security.auth import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
    _admin_only_path,
    _viewer_write_allowed,
    normalize_role,
    role_at_least,
    user_role,
)


def _user(role: str | None) -> SimpleNamespace:
    return SimpleNamespace(role=role)


def test_normalize_role_defaults_and_invalid():
    assert normalize_role(None) == ROLE_ADMIN
    assert normalize_role("") == ROLE_ADMIN
    assert normalize_role("nope") == ROLE_ADMIN
    assert normalize_role("VIEWER") == ROLE_VIEWER
    assert normalize_role("operator") == ROLE_OPERATOR
    assert VALID_ROLES == {ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}


def test_user_role_and_rank():
    assert user_role(_user("viewer")) == ROLE_VIEWER
    assert user_role(_user(None)) == ROLE_ADMIN
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
    assert not _admin_only_path("/auth/account")
    assert not _admin_only_path("/servers")
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
