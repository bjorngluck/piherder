"""2FA backup codes travel in an HttpOnly flash cookie, never the URL."""
from __future__ import annotations

from datetime import timedelta

from app.routers.auth import _read_backup_codes_flash, BACKUP_CODES_COOKIE
from app.security.auth import create_access_token
from app.models import User


def test_read_backup_codes_flash_roundtrip():
    user = User(id=3, email="a@b.c", hashed_password="x")
    token = create_access_token(
        {"sub": "3", "bc": True, "backup_codes": ["AAAA-1111", "BBBB-2222"]},
        expires_delta=timedelta(minutes=5),
    )
    req = type("R", (), {"cookies": {BACKUP_CODES_COOKIE: token}})()
    assert _read_backup_codes_flash(req, user) == ["AAAA-1111", "BBBB-2222"]


def test_read_backup_codes_flash_wrong_user():
    user = User(id=9, email="a@b.c", hashed_password="x")
    token = create_access_token(
        {"sub": "3", "bc": True, "backup_codes": ["AAAA-1111"]},
        expires_delta=timedelta(minutes=5),
    )
    req = type("R", (), {"cookies": {BACKUP_CODES_COOKIE: token}})()
    assert _read_backup_codes_flash(req, user) is None


def test_read_backup_codes_flash_ignores_query_style_absence():
    user = User(id=1, email="a@b.c", hashed_password="x")
    req = type("R", (), {"cookies": {}})()
    assert _read_backup_codes_flash(req, user) is None
