"""Pytest fixtures — pure unit tests (no live SSH / Postgres required).

Important: with ``pytest --cov=app``, the app package may import *before*
``pytest_configure`` if env is incomplete. We therefore:

1. Point the database, broker, and settings directory at throwaway test
   targets at **module import time**, before the app package is imported
2. Install a valid Fernet key at the same moment
3. Re-run the same logic in ``pytest_configure`` and in a session fixture
4. Rebind ``app.config.settings`` if Settings was already built
5. Refuse to start the session if the resolved targets are not test ones

``DATABASE_URL`` is never left on the default Postgres URL. On a machine that
also runs PiHerder that URL is the live database.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Created before any app import. The path contains "test" so the session guard
# can recognise it. tempfile lands under the system temp dir, not a home path.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="piherder-test-"))
_TEST_DATA = _TEST_ROOT / "data"
_TEST_BACKUPS = _TEST_ROOT / "backups"
_TEST_HERDER = _TEST_ROOT / "herder_backups"
for _dir in (_TEST_DATA, _TEST_BACKUPS, _TEST_HERDER):
    _dir.mkdir(parents=True, exist_ok=True)

_TEST_DATABASE_URL = f"sqlite:///{_TEST_ROOT / 'test.db'}"
# Closed port. Connection refused is immediate, and the URL contains "test".
_TEST_BROKER_URL = "redis://127.0.0.1:1/test"
_TEST_SETTINGS_PATH = str(_TEST_DATA)

import pytest


def _force_test_env() -> None:
    """Override process env before the app reads it. Always, even if already set."""
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
    os.environ["CELERY_BROKER_URL"] = _TEST_BROKER_URL
    os.environ["CELERY_RESULT_BACKEND"] = _TEST_BROKER_URL
    os.environ["REDIS_URL"] = _TEST_BROKER_URL
    os.environ["DATA_ROOT"] = _TEST_SETTINGS_PATH
    os.environ["BACKUP_ROOT"] = str(_TEST_BACKUPS)
    os.environ["HERDER_BACKUP_ROOT"] = str(_TEST_HERDER)
    # Permits the sqlite process engine. App code must not read this for anything else.
    os.environ["PIHERDER_TESTING"] = "1"
    # A copied shell may carry libpq variables that name a live server.
    for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
        os.environ.pop(key, None)


def _is_test_target(value: str) -> bool:
    """True only for a value this file sets. A substring such as latest is not enough."""
    return (value or "") in {
        _TEST_DATABASE_URL,
        _TEST_BROKER_URL,
        _TEST_SETTINGS_PATH,
        str(_TEST_BACKUPS),
        str(_TEST_HERDER),
    }


def assert_test_targets(
    database_url: str | None = None,
    broker_url: str | None = None,
    settings_path: str | None = None,
) -> None:
    """Fail if a database, broker, or settings path is not a test target.

    The message names the variable. It does not echo the value.
    """
    if database_url is None:
        database_url = os.environ.get("DATABASE_URL", "")
    if broker_url is None:
        broker_url = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL") or ""
    if settings_path is None:
        settings_path = os.environ.get("DATA_ROOT", "")
    checks = (
        ("DATABASE_URL", database_url),
        ("CELERY_BROKER_URL", broker_url),
        ("DATA_ROOT", settings_path),
    )
    for name, value in checks:
        if not _is_test_target(value):
            raise RuntimeError(f"{name}: tests refuse to run against a non-test target")


def _set_setting(settings, name: str, value: str) -> None:
    try:
        setattr(settings, name, value)
    except Exception:
        object.__setattr__(settings, name, value)


def _rebind_imported_engines(new_engine) -> None:
    """Modules that did ``from app.database import engine`` keep the old object."""
    import sys

    import app.database as database

    old = getattr(database, "engine", None)
    database.engine = new_engine
    if old is None or old is new_engine:
        return
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            if getattr(mod, "engine", None) is old:
                mod.engine = new_engine
        except Exception:
            continue
    try:
        old.dispose()
    except Exception:
        pass


def _apply_runtime_isolation() -> None:
    """Rebind an app that was imported against a non-test target."""
    _force_test_env()
    try:
        from app.config import settings
    except Exception:
        return
    _set_setting(settings, "DATABASE_URL", _TEST_DATABASE_URL)
    _set_setting(settings, "DATA_ROOT", _TEST_SETTINGS_PATH)
    _set_setting(settings, "BACKUP_ROOT", str(_TEST_BACKUPS))
    _set_setting(settings, "HERDER_BACKUP_ROOT", str(_TEST_HERDER))

    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine

    import app.database as database

    if not _is_test_target(str(database.engine.url)):
        replacement = create_engine(
            _TEST_DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        _rebind_imported_engines(replacement)

    _install_celery_enqueue_stub()

    try:
        import app.main as main

        sched = getattr(main, "scheduler", None)
        if sched is not None and not getattr(sched, "_piherder_test_stub", False):
            if getattr(sched, "running", False):
                try:
                    sched.shutdown(wait=False)
                except Exception:
                    pass

            def _do_not_start(*_args, **_kwargs):
                return None

            sched.start = _do_not_start
            sched._piherder_test_stub = True
    except Exception:
        pass

    try:
        from app.services import app_settings as cfg

        root = Path(_TEST_SETTINGS_PATH)

        def _sandbox_settings_files():
            return [
                root / "herder-config.json",
                root / ".herder-backup-config.json",
            ]

        cfg._legacy_file_paths = _sandbox_settings_files
        cfg._cache = None
        cfg._migrated_files = False
    except Exception:
        pass

    try:
        from app.services import herder_backup as hb

        hb.HERDER_BACKUP_DIR = Path(str(_TEST_HERDER))
    except Exception:
        pass


_CELERY_ENQUEUE_CALLS: list[tuple] = []


def _install_celery_enqueue_stub() -> None:
    """Record delay and apply_async, then refuse unless a test replaced them.

    A test that needs the task body calls task.run(). It does not call delay().
    """
    from celery.app.task import Task

    from app.celery_app import celery

    celery.conf.broker_url = _TEST_BROKER_URL
    celery.conf.result_backend = _TEST_BROKER_URL
    celery.conf.task_always_eager = False
    celery.conf.task_eager_propagates = False

    if not isinstance(getattr(Task, "_piherder_enqueue_calls", None), list):
        Task._piherder_enqueue_calls = _CELERY_ENQUEUE_CALLS  # type: ignore[attr-defined]

    def _blocked(self, *args, **kwargs):
        name = getattr(self, "name", None) or type(self).__name__
        Task._piherder_enqueue_calls.append((str(name), args, kwargs))  # type: ignore[attr-defined]
        raise RuntimeError(
            f"{name}: tests refuse to enqueue Celery work. "
            "Mock delay or apply_async, or call the task body with run()."
        )

    Task.delay = _blocked  # type: ignore[method-assign]
    Task.apply_async = _blocked  # type: ignore[method-assign]

    def _blocked_send(name, args=None, kwargs=None, **_options):
        Task._piherder_enqueue_calls.append((str(name), args, kwargs))  # type: ignore[attr-defined]
        raise RuntimeError(
            f"{name}: tests refuse to enqueue Celery work. "
            "Mock delay or apply_async, or call the task body with run()."
        )

    celery.send_task = _blocked_send  # type: ignore[method-assign]


def _assert_scheduler_blocked() -> None:
    """The suite stub is the scheduler block. It must be installed and not running."""
    import app.main as main

    sched = getattr(main, "scheduler", None)
    if sched is None:
        assert getattr(main, "HAS_SCHEDULER", False) is False
        return
    assert getattr(sched, "_piherder_test_stub", False) is True
    assert getattr(sched, "running", False) is not True


def assert_runtime_targets() -> None:
    """Fail the session if the imported engine, broker, or settings path drifted."""
    assert_test_targets()
    from app.celery_app import celery
    from app.config import settings
    import app.database as database

    if not _is_test_target(str(database.engine.url)):
        raise RuntimeError("DATABASE_URL: tests refuse to run against a non-test target")
    broker = str(getattr(celery.conf, "broker_url", "") or "")
    if not _is_test_target(broker):
        raise RuntimeError("CELERY_BROKER_URL: tests refuse to run against a non-test target")
    data_root = str(getattr(settings, "DATA_ROOT", "") or "")
    if not _is_test_target(data_root):
        raise RuntimeError("DATA_ROOT: tests refuse to run against a non-test target")
    _assert_scheduler_blocked()


def _ensure_test_env() -> str:
    """Ensure PIHERDER_MASTER_KEY is a valid Fernet key; return it."""
    _force_test_env()
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

    if not (os.environ.get("SECRET_KEY") or "").strip():
        os.environ["SECRET_KEY"] = "ci-unit-test-secret-key-not-for-production-use"

    # If Settings was already constructed (e.g. via --cov import order), patch it.
    try:
        from app.config import settings

        if getattr(settings, "PIHERDER_MASTER_KEY", None) != key:
            _set_setting(settings, "PIHERDER_MASTER_KEY", key)
        _set_setting(settings, "DATABASE_URL", _TEST_DATABASE_URL)
        _set_setting(settings, "DATA_ROOT", _TEST_SETTINGS_PATH)
        _set_setting(settings, "BACKUP_ROOT", str(_TEST_BACKUPS))
        _set_setting(settings, "HERDER_BACKUP_ROOT", str(_TEST_HERDER))
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


# Run at conftest import, the earliest reliable hook for the unit suite.
# Env is forced above, before this import of the app.
_ensure_test_env()
_apply_runtime_isolation()
_patch_testclient_cookies()


def pytest_configure():
    """Re-assert env before collection (covers empty CI KEY= and late imports)."""
    _ensure_test_env()
    _apply_runtime_isolation()
    _patch_testclient_cookies()


@pytest.fixture(scope="session", autouse=True)
def _isolate_suite_from_live_services():
    """Throwaway database, settings dir, and broker. Fail if any target is live."""
    _ensure_test_env()
    _apply_runtime_isolation()
    assert_runtime_targets()
    import app.models  # noqa: F401  # register tables
    from sqlmodel import SQLModel

    import app.database as database

    SQLModel.metadata.create_all(database.engine)
    yield


@pytest.fixture(autouse=True)
def _unit_skip_force_2fa_wall(request, monkeypatch):
    """Compose/demo images may enable force-2FA; HTTP unit tests are not that path.

    ``test_account_stepup_v13`` is the enroll-wall suite — leave it alone.
    """
    nodeid = getattr(request.node, "nodeid", "") or ""
    if "test_account_stepup" in nodeid:
        return
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies",
        lambda *a, **k: False,
    )
