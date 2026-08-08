"""E6 — human-readable cron / interval helpers."""
from __future__ import annotations

from app.services.cron_human import (
    describe_cron,
    describe_interval_hours,
    schedule_summary,
)


def test_describe_daily_and_hourly():
    assert "Daily at 03:00" in describe_cron("0 3 * * *")
    assert "Daily at midnight" in describe_cron("0 0 * * *") or "00:00" in describe_cron(
        "0 0 * * *"
    )
    assert "Hourly" in describe_cron("0 * * * *")
    assert "Every 6 hours" in describe_cron("0 */6 * * *")
    assert "Every 15 minutes" in describe_cron("*/15 * * * *")


def test_describe_weekly_monthly():
    d = describe_cron("30 3 * * 0")
    assert "Sunday" in d and "03:30" in d
    assert "weekdays" in describe_cron("0 9 * * 1-5").lower()
    assert "Monthly on day 1" in describe_cron("0 0 1 * *")


def test_describe_fallback_and_empty():
    assert describe_cron("") == "Not scheduled"
    assert describe_cron(None) == "Not scheduled"
    assert describe_cron("not a cron").startswith("Cron:")
    raw = "1 2 3 4 5"
    # uncommon pattern still readable via fallback
    assert describe_cron(raw).startswith("Cron:") or "at" in describe_cron(raw)


def test_describe_with_tz_hint():
    s = describe_cron("0 2 * * *", tz_hint="Europe/Amsterdam")
    assert "Europe/Amsterdam" in s
    assert "02:00" in s


def test_interval_and_summary():
    assert describe_interval_hours(1) == "Every hour"
    assert describe_interval_hours(6) == "Every 6 hours"
    assert describe_interval_hours(24) == "Every day"
    assert describe_interval_hours(48) == "Every 2 days"
    assert schedule_summary(cron="0 3 * * *").startswith("Daily")
    assert schedule_summary(interval_hours=6) == "Every 6 hours"
    assert schedule_summary() == "Not scheduled"
