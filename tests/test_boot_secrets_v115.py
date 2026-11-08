"""Insecure boot settings stay up and show a notice. They do not stop the process."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from starlette.requests import Request

from app.security import boot_secrets as boot
from app.templates import templates

ROOT = Path(__file__).resolve().parents[1]
_GOOD_REDIS = "redis://:example-secret@192.0.2.20:6379/0"
_DEFAULT_DB = "postgresql://piherder:piherder@db:5432/piherder"
_UNIQUE_DB = "postgresql://piherder:unique-secret@db:5432/piherder"
_LOOPBACK_DB = "postgresql://piherder:unique-secret@127.0.0.1:5432/piherder"
_DB_NOTICE = boot.DB_DEFAULT_PASSWORD
_REDIS_NOTICE = boot.REDIS_NO_PASSWORD
_REJECTED_NOTICE = boot.REDIS_PASSWORD_REJECTED


def _messages(**kwargs) -> list[str]:
    kwargs.setdefault("redis_auth_rejected", False)
    return boot.boot_warning_messages(**kwargs)


def test_default_db_password_warns_outside_demo_and_not_in_demo():
    outside = _messages(database_url=_DEFAULT_DB, redis_urls=[_GOOD_REDIS], demo=False)
    assert outside == [_DB_NOTICE]
    assert _messages(database_url=_DEFAULT_DB, redis_urls=[_GOOD_REDIS], demo=True) == []
    assert _messages(database_url=_UNIQUE_DB, redis_urls=[_GOOD_REDIS], demo=False) == []


def test_missing_redis_password_warns_including_demo():
    for url in (
        "redis://localhost:6379/0",
        "redis://redis:6379/0",
        "redis://:@192.0.2.20:6379/0",
        "rediss://192.0.2.20:6379/0",
    ):
        assert _REDIS_NOTICE in _messages(
            database_url=_UNIQUE_DB, redis_urls=[url], demo=False
        )
        demo = _messages(database_url=_DEFAULT_DB, redis_urls=[url], demo=True)
        assert demo == [_REDIS_NOTICE]
    assert _messages(
        database_url=_UNIQUE_DB,
        redis_urls=[
            "redis://:s3cret@192.0.2.20:6379/0",
            "rediss://user:s3cret@192.0.2.20:6380/1",
        ],
        demo=False,
    ) == []


def test_unset_redis_env_uses_the_passwordless_default():
    urls = boot.redis_urls_from_env({})
    assert urls == ["redis://localhost:6379/0"]
    assert _REDIS_NOTICE in _messages(database_url=_UNIQUE_DB, redis_urls=urls, demo=False)


def test_sqlite_database_skips_the_boot_check(monkeypatch):
    boot.reset_boot_state_for_tests()
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://192.0.2.20:6379/0")
    monkeypatch.setattr("app.config.settings.DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    boot.enforce_boot_secrets()
    assert boot.boot_warnings() == []


def test_enforce_logs_the_warning_and_keeps_running(monkeypatch, caplog):
    boot.reset_boot_state_for_tests()
    monkeypatch.setattr(boot, "ping_redis_auth", lambda urls: None)
    monkeypatch.setattr("app.config.settings.DATABASE_URL", _DEFAULT_DB)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setenv("CELERY_BROKER_URL", _GOOD_REDIS)
    monkeypatch.setenv("CELERY_RESULT_BACKEND", _GOOD_REDIS)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PIHERDER_NMAP_REDIS_URL", raising=False)

    from app.celery_app import _enforce_boot_secrets_on_worker

    with caplog.at_level(logging.DEBUG, logger="app.security.boot_secrets"):
        boot.enforce_boot_secrets()
        _enforce_boot_secrets_on_worker()
    text = _caplog_text(caplog)
    assert text.count(_DB_NOTICE) == 2
    assert "postgresql://" not in text
    assert "192.0.2.20" not in text
    assert "redis://" not in text
    assert "unique-secret" not in text
    assert "example-secret" not in text


def _caplog_text(caplog) -> str:
    return " ".join(rec.getMessage() for rec in caplog.records)


def test_ping_auth_failure_records_a_notice_without_leaking_the_url(monkeypatch, caplog):
    import redis

    boot.reset_boot_state_for_tests()
    url = _GOOD_REDIS
    leaked = f"WRONGPASS example-secret {url}"

    class _Client:
        def ping(self):
            raise redis.exceptions.AuthenticationError(leaked)

        def close(self):
            return None

    monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: _Client())
    with caplog.at_level(logging.DEBUG, logger="app.security.boot_secrets"):
        boot.ping_redis_auth([url, "http://example.com/skip"])
    assert boot._redis_auth_rejected is True
    text = _caplog_text(caplog)
    assert boot.REDIS_AUTH_FAILURE in text
    assert "example-secret" not in text
    assert "192.0.2.20" not in text
    assert "redis://" not in text
    assert "WRONGPASS" not in text
    assert _REJECTED_NOTICE in _messages(
        database_url=_UNIQUE_DB,
        redis_urls=[_GOOD_REDIS],
        demo=False,
        redis_auth_rejected=True,
    )


def test_ping_wrongpass_response_records_a_notice(monkeypatch, caplog):
    import redis

    boot.reset_boot_state_for_tests()

    class _Client:
        def ping(self):
            raise redis.exceptions.ResponseError("WRONGPASS invalid username-password pair")

        def close(self):
            return None

    monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: _Client())
    with caplog.at_level(logging.ERROR, logger="app.security.boot_secrets"):
        boot.ping_redis_auth([_GOOD_REDIS])
    assert boot._redis_auth_rejected is True
    assert "WRONGPASS" not in _caplog_text(caplog)
    assert "example-secret" not in _caplog_text(caplog)


def test_ping_connection_error_does_not_add_a_notice(monkeypatch, caplog):
    boot.reset_boot_state_for_tests()

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
    assert boot._redis_auth_rejected is False
    assert "example-secret" not in text
    assert "192.0.2.20" not in text
    assert "redis://" not in text


def test_web_and_worker_ping_redis_and_keep_running(monkeypatch):
    boot.reset_boot_state_for_tests()
    seen: list[list[str]] = []

    def _ping(urls):
        seen.append(list(urls))

    monkeypatch.setattr(boot, "ping_redis_auth", _ping)
    monkeypatch.setenv("CELERY_BROKER_URL", _GOOD_REDIS)
    monkeypatch.setenv("CELERY_RESULT_BACKEND", _GOOD_REDIS)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PIHERDER_NMAP_REDIS_URL", raising=False)
    monkeypatch.setattr("app.config.settings.DATABASE_URL", _UNIQUE_DB)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    from app.celery_app import _enforce_boot_secrets_on_worker

    _enforce_boot_secrets_on_worker()
    boot.enforce_boot_secrets()
    assert seen == [[_GOOD_REDIS], [_GOOD_REDIS]]
    assert boot.boot_warnings() == []


def test_empty_redis_password_drops_userinfo():
    assert boot.normalize_redis_url("redis://:@192.0.2.20:6379/0") == "redis://192.0.2.20:6379/0"
    assert boot.normalize_redis_url("redis://192.0.2.20:6379/0") == "redis://192.0.2.20:6379/0"
    assert boot.normalize_redis_url(_GOOD_REDIS) == _GOOD_REDIS
    assert (
        boot.normalize_redis_url("rediss://user:s3cret@192.0.2.20:6380/1")
        == "rediss://user:s3cret@192.0.2.20:6380/1"
    )


def test_nmap_worker_rewrites_only_the_db_host():
    assert (
        boot.database_url_for_process(_UNIQUE_DB, nmap_worker=True, nmap_database_url="")
        == _LOOPBACK_DB
    )
    other = "postgresql://piherder:unique-secret@192.0.2.20:5432/piherder"
    assert boot.database_url_for_process(other, nmap_worker=True, nmap_database_url="") == other
    explicit = "postgresql://piherder:unique-secret@db:5432/other"
    assert (
        boot.database_url_for_process(_UNIQUE_DB, nmap_worker=True, nmap_database_url=explicit)
        == explicit
    )
    assert boot.database_url_for_process(_UNIQUE_DB, nmap_worker=False, nmap_database_url="") == _UNIQUE_DB
    assert (
        boot.database_url_for_process(
            "sqlite:///test.db",
            nmap_worker=True,
            nmap_database_url=explicit,
        )
        == "sqlite:///test.db"
    )


def test_prepare_rewrites_nmap_url_and_empty_redis_userinfo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("PIHERDER_NMAP_WORKER", "1")
    monkeypatch.delenv("PIHERDER_NMAP_DATABASE_URL", raising=False)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://:@192.0.2.20:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://:@192.0.2.20:6379/0")
    monkeypatch.setenv("REDIS_URL", "redis://:@192.0.2.20:6379/0")

    class _Settings:
        DATABASE_URL = _UNIQUE_DB

    settings = _Settings()
    boot.prepare_process_urls(settings)
    assert settings.DATABASE_URL == _LOOPBACK_DB
    assert os.environ["DATABASE_URL"] == _LOOPBACK_DB
    assert os.environ["CELERY_BROKER_URL"] == "redis://192.0.2.20:6379/0"

    monkeypatch.setenv("PIHERDER_NMAP_DATABASE_URL", "postgresql://piherder:unique-secret@192.0.2.10:5432/piherder")
    settings.DATABASE_URL = _UNIQUE_DB
    boot.prepare_process_urls(settings)
    assert settings.DATABASE_URL == "postgresql://piherder:unique-secret@192.0.2.10:5432/piherder"

    monkeypatch.setenv("PIHERDER_NMAP_WORKER", "0")
    settings.DATABASE_URL = _UNIQUE_DB
    boot.prepare_process_urls(settings)
    assert settings.DATABASE_URL == _UNIQUE_DB


def _render_login(user):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/login",
        "raw_path": b"/login",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
    }
    return templates.get_template("login.html").render(
        request=Request(scope),
        title="Login",
        user=user,
        registration_open=False,
        demo_mode_on=False,
        password_reset_available=False,
        oidc_enabled=False,
        oidc_display_name="",
        oidc_require_sso=False,
        turnstile_on=False,
        turnstile_site_key="",
    )


def _banner(html: str) -> str:
    assert html.index('data-testid="boot-secrets-banner"') < html.index("<main")
    return html.split('data-testid="boot-secrets-banner"', 1)[1].split(
        "PiHerder is running.", 1
    )[0]


def test_login_and_signed_in_pages_show_the_boot_banner(monkeypatch):
    notices = [_DB_NOTICE, _REDIS_NOTICE]
    monkeypatch.setattr(boot, "boot_warnings", lambda: list(notices))
    logged_out = _render_login(None)
    assert 'role="alert"' in logged_out
    assert "Action needed" in logged_out
    chunk = _banner(logged_out)
    assert _DB_NOTICE in chunk
    assert _REDIS_NOTICE in chunk
    assert "<button" not in chunk
    assert "Dismiss" not in chunk
    assert "PiHerder is running. This notice stays on every page until the item above is fixed." in logged_out

    previous = templates.env.globals["get_update_notice"]
    templates.env.globals["get_update_notice"] = lambda: {"available": False}

    class _User:
        role = "viewer"
        email = "user@example.com"
        display_name = "User"
        id = 1
        avatar_path = None

    try:
        signed_in = _render_login(_User())
    finally:
        templates.env.globals["get_update_notice"] = previous
    assert "user@example.com" in signed_in
    signed_chunk = _banner(signed_in)
    assert _DB_NOTICE in signed_chunk
    assert "<button" not in signed_chunk


def test_banner_is_absent_when_there_is_nothing_to_fix(monkeypatch):
    monkeypatch.setattr(boot, "boot_warnings", lambda: [])
    html = _render_login(None)
    assert "boot-secrets-banner" not in html
    assert "Action needed" not in html


def test_lockfiles_pin_pyasn1_065():
    for name in ("requirements.lock.txt", "requirements.runtime.lock.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "pyasn1==0.6.5" in text
        assert "pyasn1==0.6.4" not in text
