"""Human-readable cron / interval schedules (E6).

5-field cron only (minute hour day month weekday), matching
``validate_cron_expression`` and APScheduler registration elsewhere.

Does not invent a full cron language — common patterns get plain English;
everything else keeps the raw expression with a short prefix.
"""
from __future__ import annotations

from typing import Any, Optional

# Common presets for UI selects (value = 5-field cron, label = human)
CRON_PRESETS: list[tuple[str, str]] = [
    ("0 * * * *", "Hourly (top of hour)"),
    ("0 */6 * * *", "Every 6 hours"),
    ("0 0 * * *", "Daily at midnight"),
    ("0 2 * * *", "Daily at 02:00"),
    ("0 3 * * *", "Daily at 03:00"),
    ("30 4 * * *", "Daily at 04:30"),
    ("0 0 * * 0", "Weekly (Sunday midnight)"),
    ("30 3 * * 0", "Weekly (Sunday 03:30)"),
    ("0 0 1 * *", "Monthly (1st, midnight)"),
    ("*/15 * * * *", "Every 15 minutes"),
    ("*/5 * * * *", "Every 5 minutes"),
]

_DOW = {
    "0": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
    "7": "Sunday",
    "sun": "Sunday",
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
}


def _is_star(p: str) -> bool:
    return (p or "").strip() in ("*", "?")


def _parse_step(field: str) -> Optional[int]:
    """Return N for ``*/N`` or None."""
    f = (field or "").strip()
    if f.startswith("*/"):
        rest = f[2:]
        if rest.isdigit() and int(rest) > 0:
            return int(rest)
    return None


def _clock(hour: str, minute: str) -> Optional[str]:
    h, m = (hour or "").strip(), (minute or "").strip()
    if h.isdigit() and m.isdigit():
        return f"{int(h):02d}:{int(m):02d}"
    return None


def _dow_label(field: str) -> Optional[str]:
    f = (field or "").strip().lower()
    if not f or _is_star(f):
        return None
    if f in _DOW:
        return _DOW[f]
    if f == "1-5":
        return "weekdays"
    if f == "0,6" or f == "6,0":
        return "weekends"
    # comma list of known names/numbers
    parts = [p.strip() for p in f.split(",") if p.strip()]
    if parts and all(p in _DOW or p.isdigit() for p in parts):
        names = []
        for p in parts:
            if p in _DOW:
                names.append(_DOW[p])
            elif p.isdigit() and p in _DOW:
                names.append(_DOW[p])
            else:
                return None
        return ", ".join(names)
    return None


def describe_cron(expr: str | None, *, tz_hint: str | None = None) -> str:
    """Return a short English description of a 5-field cron, or a safe fallback."""
    raw = (expr or "").strip()
    if not raw:
        return "Not scheduled"
    parts = raw.split()
    if len(parts) != 5:
        return f"Cron: {raw}"
    minute, hour, day, month, dow = parts

    # Every N minutes
    m_step = _parse_step(minute)
    if m_step and _is_star(hour) and _is_star(day) and _is_star(month) and _is_star(dow):
        if m_step == 1:
            label = "Every minute"
        else:
            label = f"Every {m_step} minutes"
        return _with_tz(label, tz_hint)

    # Hourly: minute fixed, hour * or */1
    if (
        minute.isdigit()
        and (_is_star(hour) or hour.strip() in ("*/1",))
        and _is_star(day)
        and _is_star(month)
        and _is_star(dow)
    ):
        mm = int(minute)
        label = "Hourly (top of hour)" if mm == 0 else f"Hourly at :{mm:02d}"
        return _with_tz(label, tz_hint)

    # Every N hours at minute M (*/N form)
    h_step = _parse_step(hour)
    if (
        minute.isdigit()
        and h_step
        and h_step > 1
        and _is_star(day)
        and _is_star(month)
        and _is_star(dow)
    ):
        mm = int(minute)
        label = f"Every {h_step} hours"
        if mm:
            label += f" at :{mm:02d}"
        return _with_tz(label, tz_hint)

    clock = _clock(hour, minute)
    dow_l = _dow_label(dow)

    # Daily at HH:MM
    if clock and _is_star(day) and _is_star(month) and _is_star(dow):
        return _with_tz(f"Daily at {clock}", tz_hint)

    # Weekly
    if clock and _is_star(day) and _is_star(month) and dow_l:
        if dow_l == "weekdays":
            return _with_tz(f"Weekdays at {clock}", tz_hint)
        if dow_l == "weekends":
            return _with_tz(f"Weekends at {clock}", tz_hint)
        return _with_tz(f"Weekly on {dow_l} at {clock}", tz_hint)

    # Monthly on day D
    if clock and day.isdigit() and _is_star(month) and _is_star(dow):
        d = int(day)
        return _with_tz(f"Monthly on day {d} at {clock}", tz_hint)

    # Specific month + day
    if clock and day.isdigit() and month.isdigit() and _is_star(dow):
        return _with_tz(
            f"Yearly on {int(month):02d}-{int(day):02d} at {clock}", tz_hint
        )

    return f"Cron: {raw}"


def describe_interval_hours(hours: Any) -> str:
    """Human label for nmap-style interval schedules."""
    try:
        h = int(hours)
    except (TypeError, ValueError):
        return "Interval schedule"
    if h <= 0:
        return "Interval schedule"
    if h == 1:
        return "Every hour"
    if h % 24 == 0:
        d = h // 24
        if d == 1:
            return "Every day"
        return f"Every {d} days"
    return f"Every {h} hours"


def schedule_summary(
    *,
    cron: str | None = None,
    interval_hours: Any = None,
    tz_hint: str | None = None,
    empty: str = "Not scheduled",
) -> str:
    """Prefer cron description; else interval; else *empty*."""
    c = (cron or "").strip()
    if c:
        return describe_cron(c, tz_hint=tz_hint)
    if interval_hours is not None and str(interval_hours).strip() != "":
        return describe_interval_hours(interval_hours)
    return empty


def _with_tz(label: str, tz_hint: str | None) -> str:
    tz = (tz_hint or "").strip()
    if tz and tz not in label:
        return f"{label} ({tz})"
    return label


def preset_options() -> list[dict[str, str]]:
    """JSON-friendly presets for selects / Alpine."""
    return [{"value": v, "label": lab} for v, lab in CRON_PRESETS]
