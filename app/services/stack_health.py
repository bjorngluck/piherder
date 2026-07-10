"""PiHerder stack health: web, DB, Redis, Celery, scheduler, disk.

Used by Settings → Status tab and a scheduled APScheduler poll.
Alerts only on healthy→unhealthy (and resolve when healthy again).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session
from sqlalchemy import text

from ..config import settings
from ..database import engine
from . import app_settings as app_cfg
from . import notifications as notif_svc

logger = logging.getLogger(__name__)

STACK_HEALTH_SETTINGS_KEY = "stack_health"
# Disk free below this fraction of total → warn; below half of that → fail
DISK_WARN_RATIO = 0.10
DISK_FAIL_RATIO = 0.05


def _component(
    id_: str,
    label: str,
    status: str,
    *,
    message: str = "",
    detail: Optional[dict] = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "status": status,  # ok | warn | fail | unknown
        "message": (message or "")[:300],
        "detail": detail or {},
    }


def _overall(components: list[dict[str, Any]]) -> str:
    statuses = {(c.get("status") or "").lower() for c in components}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses or "unknown" in statuses:
        return "warn"
    return "ok"


def check_web() -> dict[str, Any]:
    """Running this check from the web process implies web is up."""
    return _component("web", "Web (FastAPI)", "ok", message="process answering")


def check_db() -> dict[str, Any]:
    try:
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
        return _component("db", "PostgreSQL", "ok", message="SELECT 1 ok")
    except Exception as e:
        return _component("db", "PostgreSQL", "fail", message=str(e)[:200])


def check_redis() -> dict[str, Any]:
    try:
        import redis

        url = (
            os.getenv("CELERY_BROKER_URL")
            or os.getenv("REDIS_URL")
            or "redis://localhost:6379/0"
        )
        client = redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        pong = client.ping()
        client.close()
        if pong:
            return _component("redis", "Redis", "ok", message="PING ok")
        return _component("redis", "Redis", "fail", message="PING returned false")
    except Exception as e:
        return _component("redis", "Redis", "fail", message=str(e)[:200])


def check_celery() -> dict[str, Any]:
    """Ping worker *nodes* and report prefork pool concurrency (slots).

    One compose ``celery-worker`` container with ``CELERY_CONCURRENCY=2`` is
    **one** node and **two** pool slots — not two separate worker containers.
    """
    try:
        from ..celery_app import celery

        # Short timeout so Status page stays responsive
        inspector = celery.control.inspect(timeout=3.0)
        ping = inspector.ping() if inspector else None
        if not ping:
            return _component(
                "celery",
                "Celery worker(s)",
                "fail",
                message="no workers responded to ping",
                detail={"workers": 0, "pool_slots": 0, "nodes": []},
            )
        names = sorted(ping.keys())
        stats = {}
        try:
            stats = inspector.stats() or {}
        except Exception:
            stats = {}

        nodes: list[dict[str, Any]] = []
        pool_slots = 0
        for name in names:
            st = stats.get(name) or {}
            pool = st.get("pool") or {}
            try:
                mc = int(pool.get("max-concurrency") or 0)
            except Exception:
                mc = 0
            procs = pool.get("processes") or []
            n_proc = len(procs) if isinstance(procs, (list, tuple)) else 0
            if mc <= 0 and n_proc > 0:
                mc = n_proc
            pool_slots += max(mc, 0)
            nodes.append(
                {
                    "name": name,
                    "concurrency": mc,
                    "processes": n_proc,
                }
            )

        # Prefer reporting slots when known (what operators set via CELERY_CONCURRENCY)
        if pool_slots > 0:
            msg = (
                f"{len(names)} node(s) · {pool_slots} pool slot(s) "
                f"(CELERY_CONCURRENCY / prefork)"
            )
        else:
            msg = f"{len(names)} worker node(s) (pool size unknown)"

        return _component(
            "celery",
            "Celery worker(s)",
            "ok",
            message=msg[:300],
            detail={
                "workers": len(names),
                "pool_slots": pool_slots,
                "names": names[:20],
                "nodes": nodes[:20],
            },
        )
    except Exception as e:
        return _component(
            "celery",
            "Celery worker(s)",
            "fail",
            message=str(e)[:200],
            detail={"workers": 0, "pool_slots": 0},
        )


def check_scheduler(scheduler=None, has_scheduler: bool = False) -> dict[str, Any]:
    if not has_scheduler or scheduler is None:
        return _component(
            "scheduler",
            "APScheduler",
            "warn",
            message="scheduler library unavailable",
        )
    try:
        running = bool(getattr(scheduler, "running", False))
        jobs = []
        try:
            jobs = list(scheduler.get_jobs()) if running else []
        except Exception:
            jobs = []
        if not running:
            return _component(
                "scheduler",
                "APScheduler",
                "fail",
                message="not running",
                detail={"job_count": 0},
            )
        return _component(
            "scheduler",
            "APScheduler",
            "ok",
            message=f"running · {len(jobs)} job(s)",
            detail={"job_count": len(jobs)},
        )
    except Exception as e:
        return _component("scheduler", "APScheduler", "fail", message=str(e)[:200])


def _fmt_bytes(n: int | float) -> str:
    try:
        from .backup_profiles import human_size

        return human_size(int(n))
    except Exception:
        return f"{int(n)} B"


def _tree_used_bytes(path: Path, *, timeout_sec: float = 20.0) -> tuple[Optional[int], str]:
    """Directory tree size under path. Prefer ``du -sb``; timeout on huge trees."""
    import subprocess

    try:
        if not path.exists():
            return None, "path missing"
        # GNU du -sb is fast enough on moderate trees; timeout protects huge fleets
        proc = subprocess.run(
            ["du", "-sb", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first = proc.stdout.strip().split()[0]
            return int(first), "du"
        # Fallback: sum immediate children only (shallow) if full du fails
        total = 0
        for child in path.iterdir():
            try:
                if child.is_file():
                    total += child.stat().st_size
                elif child.is_dir():
                    cproc = subprocess.run(
                        ["du", "-sb", str(child)],
                        capture_output=True,
                        text=True,
                        timeout=max(5.0, timeout_sec / 2),
                        check=False,
                    )
                    if cproc.returncode == 0 and cproc.stdout.strip():
                        total += int(cproc.stdout.strip().split()[0])
            except Exception:
                continue
        return total, "shallow"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError:
        # no du binary — walk shallow
        try:
            total = 0
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except Exception:
                        pass
                # cap walk depth-ish by time not available; stop after many files
                if total > 0 and root.count(os.sep) - str(path).count(os.sep) > 4:
                    break
            return total, "walk"
        except Exception as e:
            return None, str(e)[:80]
    except Exception as e:
        return None, str(e)[:80]


def _top_level_usage(path: Path, *, limit: int = 12, per_child_timeout: float = 60.0) -> list[dict[str, Any]]:
    """Largest immediate children under path (for backup root breakdown).

    One ``du -sb`` per top-level name — can take a while on huge host trees.
    """
    import subprocess

    rows: list[tuple[int, str]] = []
    try:
        if not path.is_dir():
            return []
        for child in path.iterdir():
            try:
                if child.is_symlink():
                    continue
                if child.is_file():
                    rows.append((child.stat().st_size, child.name))
                    continue
                proc = subprocess.run(
                    ["du", "-sb", str(child)],
                    capture_output=True,
                    text=True,
                    timeout=per_child_timeout,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    rows.append((int(proc.stdout.strip().split()[0]), child.name))
            except Exception:
                continue
        rows.sort(key=lambda x: x[0], reverse=True)
        return [
            {"name": name, "bytes": size, "human": _fmt_bytes(size)}
            for size, name in rows[:limit]
        ]
    except Exception:
        return []


def _mount_free_component(
    id_: str,
    label: str,
    path: Path,
    *,
    aliases: list[str],
) -> dict[str, Any]:
    """Filesystem free space for the mount that holds path."""
    try:
        if not path.exists():
            return _component(
                id_,
                label,
                "warn",
                message=f"path missing: {path}",
                detail={"path": str(path), "aliases": aliases},
            )
        usage = shutil.disk_usage(str(path))
        free_ratio = usage.free / usage.total if usage.total else 0.0
        used_on_fs = usage.total - usage.free
        msg = (
            f"{_fmt_bytes(usage.free)} free of {_fmt_bytes(usage.total)} "
            f"({free_ratio:.0%} free) · mount used {_fmt_bytes(used_on_fs)}"
        )
        if aliases:
            msg += f" · paths: {', '.join(aliases)}"
        detail = {
            "path": str(path),
            "aliases": aliases,
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "used_bytes": used_on_fs,
            "free_ratio": round(free_ratio, 4),
            "st_dev": path.stat().st_dev if path.exists() else None,
        }
        if free_ratio < DISK_FAIL_RATIO:
            return _component(id_, label, "fail", message=msg, detail=detail)
        if free_ratio < DISK_WARN_RATIO:
            return _component(id_, label, "warn", message=msg, detail=detail)
        return _component(id_, label, "ok", message=msg, detail=detail)
    except Exception as e:
        return _component(
            id_, label, "warn", message=str(e)[:200], detail={"path": str(path)}
        )


def _tree_usage_component(
    id_: str,
    label: str,
    path: Path,
    *,
    with_children: bool = False,
) -> dict[str, Any]:
    """How much data lives under this directory (content usage, not free space)."""
    try:
        if not path.exists():
            return _component(
                id_,
                label,
                "warn",
                message=f"path missing: {path}",
                detail={"path": str(path)},
            )
        tree_bytes, method = _tree_used_bytes(path)
        children: list[dict[str, Any]] = []
        if with_children:
            children = _top_level_usage(path)
        if tree_bytes is None:
            return _component(
                id_,
                label,
                "warn",
                message=f"could not measure tree size ({method}) under {path}",
                detail={"path": str(path), "method": method, "children": children},
            )
        msg = f"{_fmt_bytes(tree_bytes)} used under {path}"
        if method == "timeout":
            msg += " (partial/timeout)"
        elif method == "shallow":
            msg += " (shallow estimate)"
        if children:
            top = ", ".join(f"{c['name']} {_fmt_bytes(c['bytes'])}" for c in children[:5])
            if top:
                msg += f" · top: {top}"
                if len(children) > 5:
                    msg += "…"
        detail = {
            "path": str(path),
            "tree_bytes": tree_bytes,
            "method": method,
            "children": children,
        }
        # Soft thresholds on absolute size are not meaningful; always ok if measured
        return _component(id_, label, "ok", message=msg[:300], detail=detail)
    except Exception as e:
        return _component(
            id_, label, "warn", message=str(e)[:200], detail={"path": str(path)}
        )


def check_disks(*, include_tree_usage: bool = False) -> list[dict[str, Any]]:
    """Mount free space (deduped by device).

    Full ``du`` tree scans (especially top-level host folders under backups)
    are expensive on multi-TB disks — omitted by default. Use
    :func:`collect_backup_tree_usage` from the Status tab “View details” path.
    """
    targets = [
        ("backups", "backups", settings.BACKUP_ROOT or "/backups"),
        ("data", "data", settings.DATA_ROOT or "/data"),
        ("herder", "herder backups", settings.HERDER_BACKUP_ROOT or "/herder_backups"),
    ]

    # Group paths by mount device so free space is not repeated three times
    by_dev: dict[int, dict[str, Any]] = {}
    path_meta: list[tuple[str, str, Path]] = []
    for key, short, raw in targets:
        p = Path(raw)
        path_meta.append((key, short, p))
        try:
            dev = p.stat().st_dev if p.exists() else -1
        except Exception:
            dev = -1
        bucket = by_dev.setdefault(
            dev,
            {"paths": [], "primary": p, "keys": []},
        )
        bucket["paths"].append(str(p))
        bucket["keys"].append(key)
        if key == "backups":
            bucket["primary"] = p

    out: list[dict[str, Any]] = []
    for dev, info in by_dev.items():
        keys = info["keys"]
        # Prefer backups path as the label when present
        if "backups" in keys:
            label = "Mount free · backups disk"
            cid = "disk_mount_backups"
        elif len(keys) == 1:
            label = f"Mount free · {keys[0]}"
            cid = f"disk_mount_{keys[0]}"
        else:
            label = "Mount free · shared data"
            cid = f"disk_mount_{dev if dev >= 0 else 'unknown'}"
        out.append(
            _mount_free_component(
                cid,
                label,
                info["primary"],
                aliases=info["paths"],
            )
        )

    if include_tree_usage:
        for key, short, p in path_meta:
            out.append(
                _tree_usage_component(
                    f"disk_used_{key}",
                    f"Storage used · {short}",
                    p,
                    with_children=(key == "backups"),
                )
            )
    return out


def collect_backup_tree_usage(*, limit: int = 24) -> dict[str, Any]:
    """Expensive ``du`` of BACKUP_ROOT + top-level host folders (lazy Status UI).

    Returns a JSON-ready dict (not a stack-health component list).
    """
    path = Path(settings.BACKUP_ROOT or "/backups")
    started = datetime.utcnow().isoformat() + "Z"
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "error": "path missing",
            "checked_at": started,
            "tree_bytes": None,
            "tree_human": None,
            "children": [],
            "method": None,
        }
    # Longer timeout for large fleet trees (1TB+ secondary disks)
    tree_bytes, method = _tree_used_bytes(path, timeout_sec=120.0)
    children = _top_level_usage(path, limit=limit)
    return {
        "ok": tree_bytes is not None,
        "path": str(path),
        "error": None if tree_bytes is not None else f"could not measure ({method})",
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "tree_bytes": tree_bytes,
        "tree_human": _fmt_bytes(tree_bytes) if tree_bytes is not None else None,
        "children": children,
        "method": method,
        "child_count": len(children),
    }


def collect_stack_health(
    *,
    scheduler=None,
    has_scheduler: bool = False,
    include_tree_usage: bool = False,
) -> dict[str, Any]:
    components = [
        check_web(),
        check_db(),
        check_redis(),
        check_celery(),
        check_scheduler(scheduler=scheduler, has_scheduler=has_scheduler),
        *check_disks(include_tree_usage=include_tree_usage),
    ]
    checked_at = datetime.utcnow().isoformat() + "Z"
    return {
        "checked_at": checked_at,
        "overall": _overall(components),
        "components": components,
    }


def load_last_report() -> Optional[dict[str, Any]]:
    cfg = app_cfg.load_settings()
    raw = cfg.get(STACK_HEALTH_SETTINGS_KEY)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def save_report(report: dict[str, Any]) -> dict[str, Any]:
    app_cfg.save_settings({STACK_HEALTH_SETTINGS_KEY: report})
    return report


def apply_stack_health_notifications(session: Session, report: dict[str, Any]) -> None:
    """Upsert/resolve one notification per component on fail; clear when ok/warn.

    Warn (e.g. low disk) also alerts at severity warning; fail → critical.
    Only open when status is fail or warn; resolve when ok/unknown.
    """
    for c in report.get("components") or []:
        cid = (c.get("id") or "unknown").strip() or "unknown"
        status = (c.get("status") or "").lower()
        label = c.get("label") or cid
        fp = f"stack_health:{cid}"
        if status in ("fail", "warn"):
            sev = "critical" if status == "fail" else "warning"
            notif_svc.upsert_notification(
                session,
                fingerprint=fp,
                type="stack_health",
                title=f"PiHerder stack: {label}",
                body=(c.get("message") or status)[:400],
                link_url="/herder-backups?tab=status",
                severity=sev,
                payload={"component": cid, "status": status},
            )
        else:
            notif_svc.resolve_by_fingerprint(session, fp)


def run_stack_health_check(
    session: Optional[Session] = None,
    *,
    scheduler=None,
    has_scheduler: bool = False,
    notify: bool = True,
) -> dict[str, Any]:
    """Collect, persist, optionally notify. Owns a short session if none given."""
    report = collect_stack_health(scheduler=scheduler, has_scheduler=has_scheduler)
    save_report(report)
    if notify:
        own = session is None
        if own:
            session = Session(engine)
        try:
            assert session is not None
            apply_stack_health_notifications(session, report)
            session.commit()
        except Exception as e:
            logger.warning("stack health notify failed: %s", e)
            if own and session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
        finally:
            if own and session is not None:
                session.close()
    return report


def celery_worker_count_from_report(report: Optional[dict[str, Any]]) -> int:
    """Worker *nodes* that answered ping (not pool concurrency)."""
    if not report:
        return 0
    for c in report.get("components") or []:
        if c.get("id") == "celery":
            detail = c.get("detail") or {}
            try:
                return int(detail.get("workers") or 0)
            except Exception:
                return 0
    return 0


def celery_pool_slots_from_report(report: Optional[dict[str, Any]]) -> int:
    """Sum of prefork max-concurrency across nodes (CELERY_CONCURRENCY)."""
    if not report:
        return 0
    for c in report.get("components") or []:
        if c.get("id") == "celery":
            detail = c.get("detail") or {}
            try:
                slots = int(detail.get("pool_slots") or 0)
            except Exception:
                slots = 0
            if slots > 0:
                return slots
            # Fallback: node count if older reports lack pool_slots
            try:
                return int(detail.get("workers") or 0)
            except Exception:
                return 0
    return 0
