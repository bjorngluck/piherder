"""Pytest fixtures — pure unit tests (no live SSH / Postgres required).

Important: with ``pytest --cov=app``, the app package may import *before*
``pytest_configure`` if env is incomplete. We therefore:

1. Install a valid Fernet key at **module import time** (this file loads early)
2. Re-run the same logic in ``pytest_configure``
3. Rebind ``app.config.settings.PIHERDER_MASTER_KEY`` if Settings was already built
"""
from __future__ import annotations

import os


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


# Run at conftest import — earliest reliable hook for unit suite
_ensure_test_env()


def pytest_configure():
    """Re-assert env before collection (covers empty CI KEY= and late imports)."""
    _ensure_test_env()
