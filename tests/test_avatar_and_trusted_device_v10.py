"""Avatar cache isolation + trusted-device cookie survival across logout."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import TrustedDevice, User
from app.security import auth as auth_sec
from app.services import avatars as avatar_svc


# --- avatar storage / URL ----------------------------------------------------


def test_delete_avatar_files_does_not_touch_other_user_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(avatar_svc.settings, "DATA_ROOT", str(tmp_path))
    d = tmp_path / "avatars"
    d.mkdir()
    (d / "1.jpg").write_bytes(b"a")
    (d / "10.jpg").write_bytes(b"b")
    (d / "11.png").write_bytes(b"c")
    avatar_svc.delete_avatar_files(1)
    assert not (d / "1.jpg").exists()
    assert (d / "10.jpg").exists()
    assert (d / "11.png").exists()


def test_save_avatar_replaces_only_own_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(avatar_svc.settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(avatar_svc.settings, "AVATAR_MAX_BYTES", 2_000_000)
    # Minimal JPEG (magic only is enough for detect; policy uses magic bytes)
    jpeg = b"\xff\xd8\xff" + b"\x00" * 64
    png_other = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    d = tmp_path / "avatars"
    d.mkdir()
    (d / "1.png").write_bytes(png_other)
    (d / "10.jpg").write_bytes(jpeg)

    rel = avatar_svc.save_avatar(1, jpeg)
    assert rel == "avatars/1.jpg"
    assert (d / "1.jpg").is_file()
    assert not (d / "1.png").exists()
    assert (d / "10.jpg").is_file()  # other user intact


def test_avatar_img_url_is_user_scoped_and_busted(tmp_path, monkeypatch):
    monkeypatch.setattr(avatar_svc.settings, "DATA_ROOT", str(tmp_path))
    u = SimpleNamespace(
        id=10,
        avatar_path="avatars/10.jpg",
        updated_at=datetime(2026, 7, 28, 12, 0, 0),
    )
    # File missing → no URL (letter fallback)
    assert avatar_svc.avatar_img_url(u) == ""

    # With file present
    d = tmp_path / "avatars"
    d.mkdir()
    (d / "10.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
    url = avatar_svc.avatar_img_url(u)
    assert url.startswith("/auth/me/avatar?u=10&v=")
    assert "u=10" in url
    # Different user id → different query even if same path string
    u2 = SimpleNamespace(id=1, avatar_path="avatars/10.jpg", updated_at=u.updated_at)
    url2 = avatar_svc.avatar_img_url(u2)
    assert "u=1" in url2
    assert url != url2


# --- trusted device helpers --------------------------------------------------


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'td.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def test_trusted_cookie_name_per_user():
    assert auth_sec.trusted_cookie_name(1) == "trusted_device_1"
    assert auth_sec.trusted_cookie_name(10) == "trusted_device_10"
    assert auth_sec.trusted_cookie_name(1) != auth_sec.trusted_cookie_name(10)


def test_read_trusted_device_prefers_per_user_over_legacy():
    cookies = {
        "trusted_device": "legacy",
        "trusted_device_3": "mine",
    }
    assert auth_sec.read_trusted_device_token(cookies, 3) == "mine"
    assert auth_sec.read_trusted_device_token(cookies, 9) == "legacy"


def test_ensure_trusted_device_no_duplicate(engine):
    with Session(engine) as s:
        u = User(
            email="a@example.com",
            hashed_password="x",
            role="admin",
            totp_enabled=True,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = int(u.id)

        raw1, d1, created1 = auth_sec.ensure_trusted_device(
            s, uid, None, label="Browser"
        )
        assert created1 is True
        raw2, d2, created2 = auth_sec.ensure_trusted_device(
            s, uid, raw1, label="Browser"
        )
        assert created2 is False
        assert raw2 == raw1
        assert d2.id == d1.id
        n = len(list(s.exec(select(TrustedDevice).where(TrustedDevice.user_id == uid))))
        assert n == 1


def test_logout_keeps_trusted_cookie_headers():
    """Regression: logout must not Set-Cookie-delete trusted_device*."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # Hit logout without auth — still exercises cookie clears
    r = client.get("/auth/logout", follow_redirects=False)
    assert r.status_code in (302, 303)
    # Collect Set-Cookie headers
    set_cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else []
    if not set_cookies:
        # httpx / starlette: multiple set-cookie
        raw = r.headers.get("set-cookie") or ""
        set_cookies = [raw] if raw else []
        # Also try multi
        for k, v in r.headers.multi_items() if hasattr(r.headers, "multi_items") else []:
            if k.lower() == "set-cookie":
                set_cookies.append(v)
    joined = " ".join(set_cookies).lower()
    # access_token / pending_2fa may be cleared; trusted_device must not
    assert "trusted_device" not in joined
