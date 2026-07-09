"""Pytest fixtures — pure unit tests (no live SSH / Postgres required)."""
from __future__ import annotations

import os


def pytest_configure():
    """Set Fernet key before any app imports (Settings requires PIHERDER_MASTER_KEY)."""
    if not os.environ.get("PIHERDER_MASTER_KEY"):
        from cryptography.fernet import Fernet

        os.environ["PIHERDER_MASTER_KEY"] = Fernet.generate_key().decode()
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = "postgresql://piherder:piherder@localhost:5432/piherder"
