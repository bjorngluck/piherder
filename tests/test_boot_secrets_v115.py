"""Boot refuses the default database password and a Redis URL with no password."""
from __future__ import annotations

import asyncio
import logging
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


def _caplog_text(caplog) -> str:
    return " ".join(rec.getMessage() for rec in caplog.records)


def test_ping_auth_failure_refuses_boot_without_leaking_the_url(monkeypatch, caplog):
    import redis

    url = _GOOD_REDIS
    leaked = f"WRONGPASS example-secret {url}"

    class _Client:
        def ping(self):
            raise redis.exceptions.AuthenticationError(leaked)

        def close(self):
            return None

    monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: _Client())
    with caplog.at_level(logging.DEBUG, logger="app.security.boot_secrets"):
        with pytest.raises(SystemExit) as caught:
            boot.ping_redis_auth([url, "http://example.com/skip"])
    assert str(caught.value) == boot.REDIS_AUTH_FAILURE
    text = _caplog_text(caplog)
    assert boot.REDIS_AUTH_FAILURE in text
    assert "example-secret" not in text
    assert "192.0.2.20" not in text
    assert "redis://" not in text
    assert "WRONGPASS" not in text


def test_ping_wrongpass_response_refuses_boot(monkeypatch, caplog):
    import redis

    class _Client:
        def ping(self):
            raise redis.exceptions.ResponseError("WRONGPASS invalid username-password pair")

        def close(self):
            return None

    monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: _Client())
    with caplog.at_level(logging.ERROR, logger="app.security.boot_secrets"):
        with pytest.raises(SystemExit) as caught:
            boot.ping_redis_auth([_GOOD_REDIS])
    assert str(caught.value) == boot.REDIS_AUTH_FAILURE
    assert "WRONGPASS" not in _caplog_text(caplog)
    assert "example-secret" not in _caplog_text(caplog)


def test_ping_connection_error_does_not_refuse_boot(monkeypatch, caplog):
    class _Client:
        def ping(self):
            raise ConnectionError(f"refused {_GOOD_REDIS}")

        def close(self):
            return None

    monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: _Client())
    with caplog.at_level(logging.WARNING, logger="app.security.boot_secrets"):
        boot.ping_redis_auth([_GOOD_REDIS])
    text = _caplog_text(caplog)
    assert "Redis authenticated ping did not connect." in text
    assert "example-secret" not in text
    assert "192.0.2.20" not in text
    assert "redis://" not in text


def test_web_and_worker_ping_redis_before_serving(monkeypatch):
    seen: list[list[str]] = []

    def _ping(urls):
        seen.append(list(urls))
        raise SystemExit(boot.REDIS_AUTH_FAILURE)

    monkeypatch.setattr(boot, "ping_redis_auth", _ping)
    monkeypatch.delenv("PIHERDER_TESTING", raising=False)
    monkeypatch.setenv("CELERY_BROKER_URL", _GOOD_REDIS)
    monkeypatch.setenv("CELERY_RESULT_BACKEND", _GOOD_REDIS)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PIHERDER_NMAP_REDIS_URL", raising=False)
    monkeypatch.setattr("app.config.settings.DATABASE_URL", _UNIQUE_DB)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    from app.celery_app import _enforce_boot_secrets_on_worker
    from app.main import lifespan

    with pytest.raises(SystemExit) as worker_exit:
        _enforce_boot_secrets_on_worker()
    with pytest.raises(SystemExit) as web_exit:
        asyncio.run(_boot_until_stop(lifespan))
    assert str(worker_exit.value) == boot.REDIS_AUTH_FAILURE
    assert str(web_exit.value) == boot.REDIS_AUTH_FAILURE
    assert seen == [[_GOOD_REDIS], [_GOOD_REDIS]]


def test_lockfiles_pin_pyasn1_065():
    for name in ("requirements.lock.txt", "requirements.runtime.lock.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "pyasn1==0.6.5" in text
        assert "pyasn1==0.6.4" not in text
