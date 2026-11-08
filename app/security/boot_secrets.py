"""Startup checks for the database password and Redis URLs.

A weak setting does not stop the process. The login page and every later
page show the notice until the setting is fixed.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote, unquote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_DB_PASSWORD = "piherder"
_REDIS_SCHEMES = {"redis", "rediss"}
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_AUTH_MARKERS = ("wrongpass", "noauth", "invalid password", "authentication required")
_REDIS_ENV_KEYS = (
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "REDIS_URL",
    "PIHERDER_NMAP_REDIS_URL",
)

DB_DEFAULT_PASSWORD = (
    "The database password is still the default. "
    "Set a unique password on the Postgres role and in DATABASE_URL."
)
REDIS_NO_PASSWORD = (
    "Redis has no password. Set REDIS_PASSWORD and include it in the Redis URLs."
)
REDIS_PASSWORD_REJECTED = (
    "Redis refused the password. The URL and the Redis server do not match."
)
REDIS_AUTH_FAILURE = "Redis authentication failed."

_redis_auth_rejected = False


def reset_boot_state_for_tests() -> None:
    """Clear the in-process Redis auth flag. Tests only."""
    global _redis_auth_rejected
    _redis_auth_rejected = False


def _password(url: str) -> str | None:
    parts = urlsplit((url or "").strip())
    if parts.password is None:
        return None
    return unquote(parts.password)


def _userinfo(parts) -> str:
    if parts.username is None and parts.password is None:
        return ""
    user = quote(unquote(parts.username or ""), safe="")
    if parts.password is None:
        return f"{user}@"
    password = quote(unquote(parts.password), safe="")
    return f"{user}:{password}@"


def _hostport(parts, host: str | None = None) -> str:
    name = parts.hostname if host is None else host
    name = name or ""
    if ":" in name and not name.startswith("["):
        name = f"[{name}]"
    if parts.port:
        return f"{name}:{parts.port}"
    return name


def redis_url_has_password(url: str) -> bool:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in _REDIS_SCHEMES:
        return True
    return bool(_password(url))


def database_uses_default_password(url: str) -> bool:
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.split("+", 1)[0]
    if scheme not in {"postgresql", "postgres"}:
        return False
    return _password(url) == DEFAULT_DB_PASSWORD


def normalize_redis_url(url: str) -> str:
    """Drop userinfo when a Redis URL has no password.

    ``redis://:@host:6379/0`` would AUTH with an empty password. The normalized
    URL does not. A URL that already has a password is unchanged.
    """
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in _REDIS_SCHEMES:
        return raw
    if _password(raw):
        return raw
    if parts.username is None and parts.password is None and "@" not in (parts.netloc or ""):
        return raw
    netloc = _hostport(parts)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def rewrite_db_host_to_loopback(url: str) -> str:
    """Point host ``db`` at 127.0.0.1. Any other host is unchanged."""
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if (parts.hostname or "") != "db":
        return raw
    netloc = f"{_userinfo(parts)}{_hostport(parts, '127.0.0.1')}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def database_url_for_process(
    database_url: str,
    *,
    nmap_worker: bool,
    nmap_database_url: str,
) -> str:
    """URL this process should open.

    The nmap worker cannot resolve the compose name ``db``. An explicit
    ``PIHERDER_NMAP_DATABASE_URL`` wins. Otherwise only the host ``db`` is
    rewritten to 127.0.0.1. Sqlite and every other process keep their URL.
    """
    raw = (database_url or "").strip()
    scheme = urlsplit(raw).scheme.split("+", 1)[0]
    if scheme == "sqlite" or not nmap_worker:
        return raw
    override = (nmap_database_url or "").strip()
    if override:
        return override
    return rewrite_db_host_to_loopback(raw)


def prepare_process_urls(settings=None) -> None:
    """Normalize Redis URLs in the environment, then the nmap database URL."""
    for key in _REDIS_ENV_KEYS:
        raw = os.environ.get(key)
        if not raw:
            continue
        cleaned = normalize_redis_url(raw)
        if cleaned != raw:
            os.environ[key] = cleaned
    if settings is None:
        return
    updated = database_url_for_process(
        getattr(settings, "DATABASE_URL", "") or "",
        nmap_worker=(os.environ.get("PIHERDER_NMAP_WORKER") or "").strip() == "1",
        nmap_database_url=os.environ.get("PIHERDER_NMAP_DATABASE_URL") or "",
    )
    if updated != (getattr(settings, "DATABASE_URL", "") or ""):
        settings.DATABASE_URL = updated
        os.environ["DATABASE_URL"] = updated


def redis_urls_from_env(env: dict[str, str] | None = None) -> list[str]:
    source = os.environ if env is None else env
    broker = (source.get("CELERY_BROKER_URL") or _DEFAULT_REDIS_URL).strip() or _DEFAULT_REDIS_URL
    backend = (source.get("CELERY_RESULT_BACKEND") or _DEFAULT_REDIS_URL).strip() or _DEFAULT_REDIS_URL
    urls = [broker]
    if backend not in urls:
        urls.append(backend)
    for key in ("REDIS_URL", "PIHERDER_NMAP_REDIS_URL"):
        raw = (source.get(key) or "").strip()
        if raw and raw not in urls:
            urls.append(raw)
    return urls


def redis_auth_error(exc: BaseException) -> bool:
    """True for a rejected password. The check does not log the exception."""
    try:
        import redis

        if isinstance(exc, redis.exceptions.AuthenticationError):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_MARKERS)


def _note_redis_auth_failure() -> None:
    global _redis_auth_rejected
    _redis_auth_rejected = True
    logger.error(REDIS_AUTH_FAILURE)


def ping_redis_auth(urls: list[str]) -> None:
    """Ping each Redis URL.

    A rejected password is recorded for the banner. A connection error is
    logged without the URL or the password and does not add a notice.
    """
    import redis

    failed = False
    for url in urls:
        parts = urlsplit((url or "").strip())
        if parts.scheme not in _REDIS_SCHEMES:
            continue
        client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
        except redis.exceptions.AuthenticationError:
            failed = True
        except redis.exceptions.ResponseError as exc:
            if redis_auth_error(exc):
                failed = True
            else:
                logger.warning("Redis authenticated ping failed.")
        except Exception:
            logger.warning("Redis authenticated ping did not connect.")
        finally:
            try:
                client.close()
            except Exception:
                pass
    if failed:
        _note_redis_auth_failure()


def boot_warning_messages(
    *,
    database_url: str,
    redis_urls: list[str],
    demo: bool,
    redis_auth_rejected: bool,
) -> list[str]:
    """Notice text. Sqlite is the unit-test engine and produces none."""
    if urlsplit((database_url or "").strip()).scheme.split("+", 1)[0] == "sqlite":
        return []
    messages: list[str] = []
    if not demo and database_uses_default_password(database_url):
        messages.append(DB_DEFAULT_PASSWORD)
    if any(not redis_url_has_password(url) for url in redis_urls):
        messages.append(REDIS_NO_PASSWORD)
    if redis_auth_rejected:
        messages.append(REDIS_PASSWORD_REJECTED)
    return messages


def boot_warnings() -> list[str]:
    """Notices for the banner. Recomputed on each call."""
    from ..config import settings
    from ..services.demo import demo_mode

    return boot_warning_messages(
        database_url=getattr(settings, "DATABASE_URL", "") or "",
        redis_urls=redis_urls_from_env(),
        demo=bool(demo_mode()),
        redis_auth_rejected=_redis_auth_rejected,
    )


def enforce_boot_secrets() -> None:
    """Log insecure boot settings. The process keeps running.

    A sqlite URL is the unit-test engine and is not checked.
    """
    from ..config import settings
    from ..services.demo import demo_mode

    database_url = getattr(settings, "DATABASE_URL", "") or ""
    if urlsplit(database_url).scheme.split("+", 1)[0] == "sqlite":
        return
    ping_redis_auth(redis_urls_from_env())
    for message in boot_warning_messages(
        database_url=database_url,
        redis_urls=redis_urls_from_env(),
        demo=bool(demo_mode()),
        redis_auth_rejected=_redis_auth_rejected,
    ):
        logger.warning(message)
