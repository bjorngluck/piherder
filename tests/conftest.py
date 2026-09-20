"""Pytest fixtures — pure unit tests (no live SSH / Postgres required).

Important: with ``pytest --cov=app``, the app package may import *before*
``pytest_configure`` if env is incomplete. We therefore:

1. Install a valid Fernet key at **module import time** (this file loads early)
2. Re-run the same logic in ``pytest_configure``
3. Rebind ``app.config.settings.PIHERDER_MASTER_KEY`` if Settings was already built
"""
from __future__ import annotations

import os

import pytest


def _ensure_test_env() -> str:
    """Ensure PIHERDER_MASTER_KEY is a valid Fernet key; return it."""
    from cryptography.fernet import Fernet

    raw = (os.environ.get("PIHERDER_MASTER_KEY") or "").strip()
    # CI historically set KEY="" which is truthy for `if not get` only when missing —
    # empty string must also be treated as "not set".
    if raw:
        try:
            Fernet(raw.encode() if isinstance(raw, str) else raw)
            key = raw
        except Exception:
            key = Fernet.generate_key().decode()
            os.environ["PIHERDER_MASTER_KEY"] = key
    else:
        key = Fernet.generate_key().decode()
        os.environ["PIHERDER_MASTER_KEY"] = key

    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = (
            "postgresql://piherder:piherder@localhost:5432/piherder"
        )
    if not (os.environ.get("SECRET_KEY") or "").strip():
        os.environ["SECRET_KEY"] = "ci-unit-test-secret-key-not-for-production-use"

    # If Settings was already constructed (e.g. via --cov import order), patch it.
    try:
        from app.config import settings

        if getattr(settings, "PIHERDER_MASTER_KEY", None) != key:
            try:
                settings.PIHERDER_MASTER_KEY = key
            except Exception:
                object.__setattr__(settings, "PIHERDER_MASTER_KEY", key)
    except Exception:
        pass
    return key


def _patch_testclient_cookies() -> None:
    """httpx TestClient ignores ``cookies=`` on get/post in this image.

    Apply per-request ``cookies=`` onto the client jar so existing tests keep
    working. New tests can still use ``client.cookies.set``.
    """
    try:
        from starlette.testclient import TestClient as StarletteTC
    except Exception:
        return
    if getattr(StarletteTC, "_piherder_cookie_patch", False):
        return

    orig = StarletteTC.request

    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        extra = kwargs.pop("cookies", None)
        if extra is not None:
            jar = getattr(self, "cookies", None)
            if jar is not None:
                try:
                    jar.clear()
                except Exception:
                    pass
                for k, v in extra.items():
                    try:
                        jar.set(k, v)
                    except TypeError:
                        jar.set(k, v, domain="testserver")
        return orig(self, method, url, **kwargs)

    StarletteTC.request = request
    StarletteTC._piherder_cookie_patch = True


# Run at conftest import — earliest reliable hook for unit suite
_ensure_test_env()
_patch_testclient_cookies()


def pytest_configure():
    """Re-assert env before collection (covers empty CI KEY= and late imports)."""
    _ensure_test_env()
    _patch_testclient_cookies()


@pytest.fixture(autouse=True)
def _unit_skip_force_2fa_wall(monkeypatch):
    """Compose/demo images may enable force-2FA; HTTP unit tests are not that path.

    Tests that need the enroll wall can override this fixture.
    """
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies",
        lambda *a, **k: False,
    )
