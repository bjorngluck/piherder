"""Rules for the host backup Configure cron builder.

The dialog script in ``partials/server_detail_action_modals.html`` follows
these rules. There is no JS test runner in this repo, so the unit tests
exercise this module and check that the script still has the same guards.
"""
from __future__ import annotations


def _single_number(value: str, lo: int, hi: int) -> bool:
    text = str(value)
    if not text.isdigit() or len(text) > 2:
        return False
    number = int(text)
    return lo <= number <= hi


def builder_submitted(cron: str) -> str:
    """Cron left in the field when Configure is saved.

    A simple daily, weekly, or monthly value is rebuilt from the same
    numbers. Anything the selects cannot show exactly is returned unchanged,
    including an empty field.
    """
    text = (cron or "").strip()
    parts = text.split()
    if len(parts) < 5:
        return text
    minute, hour, dom, month, wd = parts[:5]
    if month != "*" or not _single_number(minute, 0, 59) or not _single_number(hour, 0, 23):
        return text
    if dom == "*" and wd == "*":
        return f"{int(minute)} {int(hour)} * * *"
    if dom == "*" and len(wd) == 1 and wd.isdigit() and 0 <= int(wd) <= 6:
        return f"{int(minute)} {int(hour)} * * {int(wd)}"
    if wd == "*" and _single_number(dom, 1, 31):
        return f"{int(minute)} {int(hour)} {int(dom)} * *"
    return text
