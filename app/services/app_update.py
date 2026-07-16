"""Check GitHub for newer PiHerder releases (optional, soft-fail, cached)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import httpx

from ..config import settings
from .. import version_info as vi

logger = logging.getLogger(__name__)

# Process cache — one web worker is the compose default
_lock = threading.Lock()
_cache: dict[str, Any] = {
    "checked_at": 0.0,
    "ok": False,
    "error": None,
    "available": False,
    "current": vi.APP_VERSION,
    "latest": None,
    "latest_name": None,
    "html_url": None,
    "notes_url": None,
    "published_at": None,
}

# Re-check GitHub at most this often (seconds)
DEFAULT_TTL_SEC = 12 * 3600


def update_check_enabled() -> bool:
    raw = getattr(settings, "PIHERDER_UPDATE_CHECK", None)
    if raw is None:
        return True
    s = str(raw).strip().lower()
    return s not in ("0", "false", "no", "off")


def _ttl_sec() -> int:
    try:
        n = int(getattr(settings, "PIHERDER_UPDATE_CHECK_TTL_HOURS", 12) or 12)
        return max(1, min(168, n)) * 3600
    except Exception:
        return DEFAULT_TTL_SEC


def _fetch_latest_release() -> dict[str, Any]:
    """Call GitHub Releases API. Raises on hard failure."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PiHerder/{vi.get_app_version()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=8.0, follow_redirects=True) as client:
        r = client.get(vi.GITHUB_API_LATEST, headers=headers)
        if r.status_code == 404:
            # No published releases yet
            return {"tag_name": None, "empty": True}
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected GitHub response")
        return data


def refresh_update_check(*, force: bool = False) -> dict[str, Any]:
    """Refresh cache from GitHub if stale or forced. Always returns a notice dict."""
    if not update_check_enabled():
        return {
            "enabled": False,
            "available": False,
            "current": vi.get_app_version(),
            "latest": None,
            "checked_at": 0,
            "ok": True,
            "error": None,
            "message": "Update checks disabled (PIHERDER_UPDATE_CHECK=false)",
        }

    now = time.time()
    with _lock:
        age = now - float(_cache.get("checked_at") or 0)
        if not force and _cache.get("checked_at") and age < _ttl_sec():
            return _snapshot()

    current = vi.get_app_version()
    result: dict[str, Any] = {
        "enabled": True,
        "ok": False,
        "error": None,
        "available": False,
        "current": current,
        "latest": None,
        "latest_name": None,
        "html_url": vi.GITHUB_RELEASES_URL,
        "notes_url": vi.GITHUB_RELEASES_URL,
        "published_at": None,
        "checked_at": now,
    }
    try:
        data = _fetch_latest_release()
        if data.get("empty") or not data.get("tag_name"):
            result["ok"] = True
            result["error"] = None
            result["message"] = "No GitHub releases published yet"
        else:
            tag = str(data.get("tag_name") or "").strip()
            result["ok"] = True
            result["latest"] = tag
            result["latest_name"] = (data.get("name") or tag or "").strip() or tag
            result["html_url"] = data.get("html_url") or vi.release_notes_url(tag)
            result["notes_url"] = vi.release_notes_url(tag)
            result["published_at"] = data.get("published_at")
            result["available"] = vi.is_remote_newer(current, tag)
            if result["available"]:
                result["message"] = f"New version {tag} is available"
            else:
                result["message"] = "You are on the latest published release (or newer)"
    except Exception as e:
        logger.info("GitHub update check failed: %s", e)
        result["ok"] = False
        result["error"] = str(e)[:200]
        result["message"] = "Could not check for updates (offline or GitHub unreachable)"

    with _lock:
        _cache.clear()
        _cache.update(result)
    return _snapshot()


def _snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_cache)


def get_update_notice(*, force: bool = False) -> dict[str, Any]:
    """For templates: never raises; uses cache; optional force refresh."""
    try:
        if force:
            return refresh_update_check(force=True)
        with _lock:
            stale = (time.time() - float(_cache.get("checked_at") or 0)) >= _ttl_sec()
            empty = not _cache.get("checked_at")
        if empty or stale:
            # Soft background refresh if completely empty; else return stale and kick thread
            if empty:
                return refresh_update_check(force=True)
            threading.Thread(
                target=lambda: refresh_update_check(force=True),
                name="piherder-update-check",
                daemon=True,
            ).start()
            snap = _snapshot()
            snap["stale"] = True
            return snap
        return _snapshot()
    except Exception as e:
        return {
            "enabled": update_check_enabled(),
            "available": False,
            "current": vi.get_app_version(),
            "ok": False,
            "error": str(e)[:200],
            "message": "Update check error",
        }


def schedule_startup_check(delay_sec: float = 15.0) -> None:
    """Fire-and-forget check after web is up (does not block lifespan)."""
    if not update_check_enabled():
        return

    def _run():
        try:
            time.sleep(max(0.0, delay_sec))
            refresh_update_check(force=True)
        except Exception as e:
            logger.debug("startup update check: %s", e)

    threading.Thread(target=_run, name="piherder-update-check-startup", daemon=True).start()
