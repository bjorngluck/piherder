"""Redis locks, progress, and worker heartbeat for nmap jobs."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PREFIX = "piherder:nmap:"
HEARTBEAT_KEY = f"{_PREFIX}worker:heartbeat"
HEARTBEAT_TTL_SEC = 90
LOCK_TTL_SEC = 7200
PROGRESS_TTL_SEC = 7200


def _redis():
    import redis

    url = (
        os.environ.get("REDIS_URL")
        or os.environ.get("CELERY_BROKER_URL")
        or "redis://localhost:6379/0"
    )
    return redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)


def touch_worker_heartbeat(*, worker_id: str = "nmap") -> None:
    try:
        r = _redis()
        payload = json.dumps(
            {"worker_id": worker_id, "ts": time.time()},
            separators=(",", ":"),
        )
        r.setex(HEARTBEAT_KEY, HEARTBEAT_TTL_SEC, payload)
    except Exception as e:
        logger.debug("nmap heartbeat failed: %s", e)


def worker_online() -> dict[str, Any]:
    try:
        r = _redis()
        raw = r.get(HEARTBEAT_KEY)
        if not raw:
            return {"online": False}
        data = json.loads(raw)
        return {"online": True, **data}
    except Exception as e:
        return {"online": False, "error": str(e)[:200]}


def try_acquire_lock(kind: str, key: str, *, holder: str, ttl: int = LOCK_TTL_SEC) -> bool:
    """SET NX lock. kind is 'cidr' or 'host'."""
    try:
        r = _redis()
        lock_key = f"{_PREFIX}lock:{kind}:{key}"
        return bool(r.set(lock_key, holder, nx=True, ex=ttl))
    except Exception as e:
        logger.warning("nmap lock acquire failed: %s", e)
        return False


def release_lock(kind: str, key: str, *, holder: str) -> None:
    try:
        r = _redis()
        lock_key = f"{_PREFIX}lock:{kind}:{key}"
        val = r.get(lock_key)
        if val is not None and val.decode() == holder:
            r.delete(lock_key)
    except Exception as e:
        logger.debug("nmap lock release failed: %s", e)


def set_progress(job_id: int, payload: dict[str, Any]) -> None:
    try:
        r = _redis()
        r.setex(
            f"{_PREFIX}progress:{job_id}",
            PROGRESS_TTL_SEC,
            json.dumps(payload, separators=(",", ":")),
        )
    except Exception as e:
        logger.debug("nmap progress write failed: %s", e)


def get_progress(job_id: int) -> Optional[dict[str, Any]]:
    try:
        r = _redis()
        raw = r.get(f"{_PREFIX}progress:{job_id}")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None
