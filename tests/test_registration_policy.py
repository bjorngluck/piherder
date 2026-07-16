"""Registration open/closed rules and cookie Secure helper (no live HTTP)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.routers.auth import _registration_allowed
from app.security.auth import cookie_secure, ROLE_VIEWER, normalize_role


class _Sess:
    def __init__(self, first=None):
        self._first = first

    def exec(self, _q):
        return SimpleNamespace(first=lambda: self._first)


def test_registration_open_when_no_users():
    with patch("app.routers.auth.settings") as st:
        st.ALLOW_OPEN_REGISTRATION = False
        assert _registration_allowed(_Sess(None)) is True


def test_registration_closed_after_first_user():
    with patch("app.routers.auth.settings") as st:
        st.ALLOW_OPEN_REGISTRATION = False
        user = SimpleNamespace(email="a@b.com")
        assert _registration_allowed(_Sess(user)) is False


def test_registration_open_when_env_allows():
    with patch("app.routers.auth.settings") as st:
        st.ALLOW_OPEN_REGISTRATION = True
        user = SimpleNamespace(email="a@b.com")
        assert _registration_allowed(_Sess(user)) is True


def test_cookie_secure_from_public_url():
    with patch("app.security.auth.settings") as st:
        st.COOKIE_SECURE = None
        st.PIHERDER_PUBLIC_URL = "https://piherder.example.com:8443"
        assert cookie_secure() is True
        st.PIHERDER_PUBLIC_URL = "http://localhost:8000"
        assert cookie_secure() is False
        st.COOKIE_SECURE = "true"
        st.PIHERDER_PUBLIC_URL = "http://x"
        assert cookie_secure() is True
        st.COOKIE_SECURE = "false"
        st.PIHERDER_PUBLIC_URL = "https://x"
        assert cookie_secure() is False


def test_unknown_role_is_viewer():
    assert normalize_role("superuser") == ROLE_VIEWER
