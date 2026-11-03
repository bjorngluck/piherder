"""Boot refuses the default database password and a Redis URL with no password."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.security import boot_secrets as boot

ROOT = Path(__file__).resolve().parents[1]
_GOOD_REDIS = "redis://:example-secret@192.0.2.20:6379/0"
_DEFAULT_DB = "postgresql://piherder:piherder@db:5432/piherder"
_UNIQUE_DB = "postgresql://piherder:unique-secret@db:5432/piherder"


def test_default_db_password_fails_outside_demo_and_passes_in_demo():
    with pytest.raises(SystemExit):
        boot.require_boot_secrets(
            database_url=_DEFAULT_DB,
            redis_urls=[_GOOD_REDIS],
            demo=False,
        )
    boot.require_boot_secrets(
        database_url=_DEFAULT_DB,
        redis_urls=[_GOOD_REDIS],
        demo=True,
    )
    boot.require_boot_secrets(
        database_url=_UNIQUE_DB,
        redis_urls=[_GOOD_REDIS],
        demo=False,
    )


def test_missing_redis_password_fails_including_demo():
    for url in (
        "redis://localhost:6379/0",
        "redis://redis:6379/0",
        "redis://:@192.0.2.20:6379/0",
        "rediss://192.0.2.20:6379/0",
    ):
        with pytest.raises(SystemExit):
            boot.require_boot_secrets(
                database_url=_UNIQUE_DB,
                redis_urls=[url],
                demo=False,
            )
        with pytest.raises(SystemExit):
            boot.require_boot_secrets(
                database_url=_DEFAULT_DB,
                redis_urls=[url],
                demo=True,
            )
    boot.require_boot_secrets(
        database_url=_UNIQUE_DB,
        redis_urls=["redis://:s3cret@192.0.2.20:6379/0", "rediss://user:s3cret@192.0.2.20:6380/1"],
        demo=False,
    )


def test_unset_redis_env_uses_the_passwordless_default():
    urls = boot.redis_urls_from_env({})
    assert urls == ["redis://localhost:6379/0"]
    with pytest.raises(SystemExit):
        boot.require_boot_secrets(database_url=_UNIQUE_DB, redis_urls=urls, demo=False)


def test_lifespan_and_worker_fail_closed(monkeypatch):
    monkeypatch.delenv("PIHERDER_TESTING", raising=False)
    monkeypatch.setenv("CELERY_BROKER_URL", _GOOD_REDIS)
    monkeypatch.setenv("CELERY_RESULT_BACKEND", _GOOD_REDIS)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PIHERDER_NMAP_REDIS_URL", raising=False)
    monkeypatch.setattr("app.config.settings.DATABASE_URL", _DEFAULT_DB)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    from app.celery_app import _enforce_boot_secrets_on_worker
    from app.main import lifespan

    with pytest.raises(SystemExit):
        _enforce_boot_secrets_on_worker()
    with pytest.raises(SystemExit):
        asyncio.run(_boot_until_stop(lifespan))

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://192.0.2.20:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://192.0.2.20:6379/0")
    with pytest.raises(SystemExit):
        _enforce_boot_secrets_on_worker()


async def _boot_until_stop(lifespan):
    async with lifespan(object()):
        return None


def test_sqlite_database_skips_the_boot_check(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://192.0.2.20:6379/0")
    monkeypatch.setattr("app.config.settings.DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    boot.enforce_boot_secrets()


def test_lockfiles_pin_pyasn1_065():
    for name in ("requirements.lock.txt", "requirements.runtime.lock.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "pyasn1==0.6.5" in text
        assert "pyasn1==0.6.4" not in text
