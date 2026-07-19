"""Thin cron helper for nmap schedules (avoids circular imports)."""
from __future__ import annotations


def cron_trigger(cron: str, timezone=None):
    from apscheduler.triggers.cron import CronTrigger

    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 fields")
    kwargs = dict(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )
    if timezone:
        kwargs["timezone"] = timezone
    return CronTrigger(**kwargs)
