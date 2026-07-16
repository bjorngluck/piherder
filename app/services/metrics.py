"""Prometheus text exposition for fleet health (scrape-time gauges, no SSH)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import text
from sqlmodel import Session, select, func

from ..config import settings
from ..models import Job, Notification, Server
from .fleet_status import summarize_fleet

# Keep version in lockstep with pyproject.toml / FastAPI app.version
APP_VERSION = "0.5.0.dev0"


def _esc_label(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _line(name: str, value: float | int, labels: Optional[dict[str, str]] = None) -> str:
    if labels:
        parts = ",".join(f'{k}="{_esc_label(v)}"' for k, v in labels.items())
        return f"{name}{{{parts}}} {value}"
    return f"{name} {value}"


def _db_up(session: Session) -> int:
    try:
        session.execute(text("SELECT 1"))
        return 1
    except Exception:
        return 0


def _job_status_counts(session: Session) -> dict[str, int]:
    counts = {s: 0 for s in ("pending", "running", "success", "failed")}
    try:
        rows = session.exec(
            select(Job.status, func.count()).group_by(Job.status)
        ).all()
        for status, n in rows:
            if status in counts:
                counts[status] = int(n or 0)
    except Exception:
        pass
    return counts


def _jobs_failed_24h(session: Session) -> int:
    try:
        since = datetime.utcnow() - timedelta(hours=24)
        n = session.exec(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "failed", Job.finished_at >= since)
        ).one()
        return int(n or 0)
    except Exception:
        return 0


def _open_notifications_by_type(session: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        rows = session.exec(
            select(Notification.type, func.count())
            .where(Notification.status == "open")
            .group_by(Notification.type)
        ).all()
        for t, n in rows:
            key = (t or "unknown").strip() or "unknown"
            out[key] = int(n or 0)
    except Exception:
        pass
    return out


def _backup_counts(servers: list[Server], stale_hours: int) -> tuple[int, int]:
    enabled = 0
    stale = 0
    cutoff = datetime.utcnow() - timedelta(hours=max(1, stale_hours))
    for s in servers:
        if not s.backup_enabled:
            continue
        enabled += 1
        if s.last_backup_at is None or s.last_backup_at < cutoff:
            stale += 1
    return enabled, stale


def collect_samples(session: Session) -> list[tuple[str, str, list[str]]]:
    """Return list of (metric_name, help, lines) for exposition."""
    db_ok = _db_up(session)
    samples: list[tuple[str, str, list[str]]] = []

    samples.append(
        (
            "piherder_up",
            "PiHerder process is up",
            [_line("piherder_up", 1)],
        )
    )
    samples.append(
        (
            "piherder_db_up",
            "Database connectivity (1=ok)",
            [_line("piherder_db_up", db_ok)],
        )
    )
    samples.append(
        (
            "piherder_info",
            "Build info",
            [_line("piherder_info", 1, {"version": APP_VERSION})],
        )
    )

    # Best-effort Redis / Celery from last Status check (no live probe on every scrape)
    try:
        from . import stack_health as stack_svc

        last = stack_svc.load_last_report()
        redis_up = 0
        celery_up = 0
        workers = 0
        pool_slots = 0
        if last:
            for c in last.get("components") or []:
                cid = c.get("id")
                st = (c.get("status") or "").lower()
                if cid == "redis":
                    redis_up = 1 if st == "ok" else 0
                elif cid == "celery":
                    celery_up = 1 if st == "ok" else 0
                    workers = stack_svc.celery_worker_count_from_report(last)
                    pool_slots = stack_svc.celery_pool_slots_from_report(last)
        samples.append(
            (
                "piherder_redis_up",
                "Redis up from last stack health check (1=ok)",
                [_line("piherder_redis_up", redis_up)],
            )
        )
        samples.append(
            (
                "piherder_celery_up",
                "Celery workers up from last stack health check (1=ok)",
                [_line("piherder_celery_up", celery_up)],
            )
        )
        samples.append(
            (
                "piherder_celery_workers",
                "Celery worker *nodes* from last stack health check",
                [_line("piherder_celery_workers", workers)],
            )
        )
        samples.append(
            (
                "piherder_celery_pool_slots",
                "Celery prefork pool slots (CELERY_CONCURRENCY sum) from last check",
                [_line("piherder_celery_pool_slots", pool_slots)],
            )
        )
    except Exception:
        pass

    if not db_ok:
        return samples

    servers = list(session.exec(select(Server)).all())
    fleet = summarize_fleet(servers)
    jobs = _job_status_counts(session)
    failed_24h = _jobs_failed_24h(session)
    notif_by_type = _open_notifications_by_type(session)
    open_total = sum(notif_by_type.values())
    stale_h = int(getattr(settings, "METRICS_BACKUP_STALE_HOURS", 36) or 36)
    backup_enabled, backup_stale = _backup_counts(servers, stale_h)

    samples.append(
        (
            "piherder_servers",
            "Number of managed servers",
            [_line("piherder_servers", fleet["server_count"])],
        )
    )
    samples.append(
        (
            "piherder_servers_attention",
            "Servers needing attention (reboot or pending updates)",
            [_line("piherder_servers_attention", fleet["attention_count"])],
        )
    )
    samples.append(
        (
            "piherder_servers_reboot_pending",
            "Servers with reboot pending",
            [_line("piherder_servers_reboot_pending", fleet["reboot_count"])],
        )
    )
    samples.append(
        (
            "piherder_os_update_hosts",
            "Servers with installable OS package updates",
            [_line("piherder_os_update_hosts", fleet["os_host_count"])],
        )
    )
    samples.append(
        (
            "piherder_container_update_hosts",
            "Servers with container project updates",
            [_line("piherder_container_update_hosts", fleet["container_host_count"])],
        )
    )
    samples.append(
        (
            "piherder_os_packages_pending",
            "Total installable OS packages across fleet",
            [_line("piherder_os_packages_pending", fleet["total_os_packages"])],
        )
    )
    samples.append(
        (
            "piherder_container_projects_pending",
            "Total container projects with updates across fleet",
            [_line("piherder_container_projects_pending", fleet["total_container_projects"])],
        )
    )
    samples.append(
        (
            "piherder_servers_never_checked_os",
            "OS-patch-enabled servers never checked",
            [_line("piherder_servers_never_checked_os", fleet["never_checked_os"])],
        )
    )
    samples.append(
        (
            "piherder_servers_never_checked_containers",
            "Container-patch-enabled servers never checked",
            [_line("piherder_servers_never_checked_containers", fleet["never_checked_containers"])],
        )
    )

    job_lines = [_line("piherder_jobs", n, {"status": st}) for st, n in jobs.items()]
    samples.append(("piherder_jobs", "Job rows by status", job_lines))
    samples.append(
        (
            "piherder_jobs_active",
            "Pending + running jobs",
            [_line("piherder_jobs_active", jobs["pending"] + jobs["running"])],
        )
    )
    samples.append(
        (
            "piherder_jobs_failed_24h",
            "Jobs finished as failed in the last 24 hours",
            [_line("piherder_jobs_failed_24h", failed_24h)],
        )
    )

    samples.append(
        (
            "piherder_notifications_open",
            "Open in-app notifications",
            [_line("piherder_notifications_open", open_total)],
        )
    )
    notif_lines = [
        _line("piherder_notifications_open_by_type", n, {"type": t})
        for t, n in sorted(notif_by_type.items())
    ]
    if not notif_lines:
        # Stable series when empty
        notif_lines = []
    samples.append(
        (
            "piherder_notifications_open_by_type",
            "Open notifications by type",
            notif_lines,
        )
    )

    samples.append(
        (
            "piherder_servers_backup_enabled",
            "Servers with backup enabled",
            [_line("piherder_servers_backup_enabled", backup_enabled)],
        )
    )
    samples.append(
        (
            "piherder_servers_backup_stale",
            f"Backup-enabled servers with no successful backup within {stale_h}h",
            [_line("piherder_servers_backup_stale", backup_stale)],
        )
    )

    return samples


def render_prometheus(samples: Iterable[tuple[str, str, list[str]]]) -> str:
    chunks: list[str] = []
    for name, help_text, lines in samples:
        chunks.append(f"# HELP {name} {help_text}")
        chunks.append(f"# TYPE {name} gauge")
        chunks.extend(lines)
    chunks.append("")  # trailing newline
    return "\n".join(chunks)


def metrics_body(session: Session) -> str:
    return render_prometheus(collect_samples(session))
