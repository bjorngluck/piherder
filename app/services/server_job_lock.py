"""Per-server job mutex for multi-worker Celery.

Guarantees at most one active backup (or patch) **per server** across workers,
while allowing parallel jobs **across** different servers.

Primary backend: Redis (same broker as Celery). Fallback: process-local
threading locks so unit tests and single-process runs still serialize.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Literal

logger = logging.getLogger(__name__)

LockKind = Literal["backup", "patch"]

# Must cover longest rsync; Celery task_time_limit is 7200s
DEFAULT_TTL_SEC = int(os.getenv("PIHERDER_SERVER_LOCK_TTL", "7200"))
KEY_PREFIX = "piherder:server_lock"

# Compare-and-delete / refresh token only if we still own the key
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_REFRESH_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

_redis_client = None
_redis_failed = False

# Process-local fallback: key -> (token, expires_at)
_local: dict[str, tuple[str, float]] = {}
_local_guard = threading.Lock()


def _lock_key(kind: LockKind, server_id: int) -> str:
    return f"{KEY_PREFIX}:{kind}:{int(server_id)}"


def _get_redis():
    """Shared Redis client or None if unavailable."""
    global _redis_client, _redis_failed
    if _redis_failed:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis

        url = (
            os.getenv("CELERY_BROKER_URL")
            or os.getenv("CELERY_RESULT_BACKEND")
            or "redis://localhost:6379/0"
        )
        client = redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        logger.debug("[server_lock] Redis available for cross-worker mutex")
        return _redis_client
    except Exception as e:
        logger.debug(f"[server_lock] Redis unavailable, using in-process locks: {e}")
        _redis_failed = True
        return None


def reset_redis_client_for_tests() -> None:
    """Clear cached Redis client (tests only)."""
    global _redis_client, _redis_failed
    _redis_client = None
    _redis_failed = False


def clear_local_locks_for_tests() -> None:
    """Drop process-local lock map (tests only)."""
    with _local_guard:
        _local.clear()


def _local_acquire(key: str, token: str, ttl_sec: int) -> bool:
    now = time.time()
    with _local_guard:
        cur = _local.get(key)
        if cur is not None:
            held_token, exp = cur
            if exp > now and held_token != token:
                return False
            # expired or re-entrant same token
        _local[key] = (token, now + ttl_sec)
        return True


def _local_release(key: str, token: str) -> bool:
    with _local_guard:
        cur = _local.get(key)
        if not cur:
            return False
        held_token, _exp = cur
        if held_token != token:
            return False
        del _local[key]
        return True


def _local_refresh(key: str, token: str, ttl_sec: int) -> bool:
    now = time.time()
    with _local_guard:
        cur = _local.get(key)
        if not cur or cur[0] != token:
            return False
        _local[key] = (token, now + ttl_sec)
        return True


def _local_holder(key: str) -> str | None:
    now = time.time()
    with _local_guard:
        cur = _local.get(key)
        if not cur:
            return None
        token, exp = cur
        if exp <= now:
            del _local[key]
            return None
        return token


def try_acquire_server_lock(
    kind: LockKind,
    server_id: int,
    *,
    holder: str,
    ttl_sec: int | None = None,
) -> str | None:
    """Try to acquire the per-server mutex.

    Returns a lock token on success (pass to release/refresh), or None if busy.
    ``holder`` is stored as a hint (job id / task id); the returned token is unique.
    """
    if server_id is None:
        return None
    ttl = int(ttl_sec if ttl_sec is not None else DEFAULT_TTL_SEC)
    ttl = max(30, ttl)
    key = _lock_key(kind, server_id)
    token = f"{holder}:{uuid.uuid4().hex[:12]}"

    r = _get_redis()
    if r is not None:
        try:
            ok = r.set(key, token, nx=True, ex=ttl)
            if ok:
                logger.info(
                    f"[server_lock] acquired {kind} server={server_id} holder={holder}"
                )
                return token
            return None
        except Exception as e:
            logger.warning(f"[server_lock] Redis acquire failed, falling back local: {e}")

    if _local_acquire(key, token, ttl):
        logger.debug(f"[server_lock] local acquired {kind} server={server_id}")
        return token
    return None


def release_server_lock(
    kind: LockKind,
    server_id: int,
    token: str | None,
) -> bool:
    """Release only if ``token`` still owns the lock."""
    if not token or server_id is None:
        return False
    key = _lock_key(kind, server_id)
    r = _get_redis()
    if r is not None:
        try:
            n = r.eval(_RELEASE_LUA, 1, key, token)
            if n:
                logger.info(f"[server_lock] released {kind} server={server_id}")
            return bool(n)
        except Exception as e:
            logger.warning(f"[server_lock] Redis release failed: {e}")

    ok = _local_release(key, token)
    if ok:
        logger.debug(f"[server_lock] local released {kind} server={server_id}")
    return ok


def refresh_server_lock(
    kind: LockKind,
    server_id: int,
    token: str | None,
    *,
    ttl_sec: int | None = None,
) -> bool:
    """Extend TTL if we still own the lock (long rsync)."""
    if not token or server_id is None:
        return False
    ttl = int(ttl_sec if ttl_sec is not None else DEFAULT_TTL_SEC)
    ttl = max(30, ttl)
    key = _lock_key(kind, server_id)
    r = _get_redis()
    if r is not None:
        try:
            n = r.eval(_REFRESH_LUA, 1, key, token, str(ttl))
            return bool(n)
        except Exception as e:
            logger.warning(f"[server_lock] Redis refresh failed: {e}")

    return _local_refresh(key, token, ttl)


def server_lock_holder(kind: LockKind, server_id: int) -> str | None:
    """Return current lock token (or None). Intended for diagnostics/tests."""
    if server_id is None:
        return None
    key = _lock_key(kind, server_id)
    r = _get_redis()
    if r is not None:
        try:
            return r.get(key)
        except Exception:
            pass
    return _local_holder(key)


def is_server_locked(kind: LockKind, server_id: int) -> bool:
    return server_lock_holder(kind, server_id) is not None
