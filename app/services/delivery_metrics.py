"""Process-local counter for alert deliveries that did not succeed."""
from __future__ import annotations

_CHANNELS = ("webhook", "email", "push")
_COUNTS = {channel: 0 for channel in _CHANNELS}


def note_alert_delivery_failure(channel: str) -> None:
    if channel in _COUNTS:
        _COUNTS[channel] += 1


def delivery_failure_counts() -> dict[str, int]:
    return dict(_COUNTS)


def reset_delivery_failures_for_tests() -> None:
    for channel in _COUNTS:
        _COUNTS[channel] = 0


def delivery_failure_lines() -> list[str]:
    return [
        f'piherder_alert_delivery_failures_total{{channel="{channel}"}} {_COUNTS[channel]}'
        for channel in _CHANNELS
    ]
