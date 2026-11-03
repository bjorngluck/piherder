"""Boot checks for the database password and Redis URLs."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit

DEFAULT_DB_PASSWORD = "piherder"
_REDIS_SCHEMES = {"redis", "rediss"}
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def _password(url: str) -> str | None:
    parts = urlsplit((url or "").strip())
    if parts.password is None:
        return None
    return unquote(parts.password)


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


def require_boot_secrets(*, database_url: str, redis_urls: list[str], demo: bool) -> None:
    if not demo and database_uses_default_password(database_url):
        raise SystemExit(
            "DATABASE_URL uses the default password. Set a unique password. "
            "Demo mode is the only exception."
        )
    for url in redis_urls:
        if not redis_url_has_password(url):
            raise SystemExit("Redis requires a password in the URL before boot.")


def enforce_boot_secrets() -> None:
    """Refuse insecure boot. A sqlite URL is the unit-test engine and is not checked."""
    from ..config import settings
    from ..services.demo import demo_mode

    database_url = getattr(settings, "DATABASE_URL", "") or ""
    if urlsplit(database_url).scheme.split("+", 1)[0] == "sqlite":
        return
    require_boot_secrets(
        database_url=database_url,
        redis_urls=redis_urls_from_env(),
        demo=bool(demo_mode()),
    )
