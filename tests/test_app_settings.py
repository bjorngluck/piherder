"""Unit tests for operational app settings (DB-backed API)."""
from __future__ import annotations

import pytest

from app.services import app_settings as cfg


@pytest.fixture(autouse=True)
def _memory_settings(monkeypatch):
    """Avoid live Postgres: store settings in a dict behind the DB IO hooks."""
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()
    yield store
    cfg.clear_cache()


def test_save_load_roundtrip(_memory_settings):
    cfg.save_settings({"timezone": "Europe/London", "force_2fa": True})
    loaded = cfg.load_settings()
    assert loaded["timezone"] == "Europe/London"
    assert loaded["force_2fa"] is True
    assert loaded["keep"] == 10  # default preserved


def test_partial_merge(_memory_settings):
    cfg.save_settings({"timezone": "UTC", "keep": 20})
    cfg.save_settings({"force_2fa": True})
    loaded = cfg.load_settings()
    assert loaded["keep"] == 20
    assert loaded["force_2fa"] is True


def test_set_timezone_rejects_invalid(_memory_settings):
    with pytest.raises(ValueError, match="Invalid timezone"):
        cfg.set_app_timezone("Not/A_Real_Zone")


def test_set_timezone_ok(_memory_settings):
    cfg.set_app_timezone("Africa/Johannesburg")
    assert cfg.get_app_timezone() == "Africa/Johannesburg"


def test_describe_timezone_compact_orb():
    """Hero orb must use short offset + region — not a long city name."""
    d = cfg.describe_timezone("Africa/Johannesburg")
    assert d["iana"] == "Africa/Johannesburg"
    assert d["region"] == "AF"
    assert d["city"] == "Johannesburg"
    assert d["primary"] == d["offset"]
    assert d["primary_label"] == "AF"
    # Offset always short enough for the circle (e.g. +02, +05:30, ±0)
    assert len(d["primary"]) <= 7
    assert "Johannesburg" not in d["primary"]
    assert "Africa/Johannesburg" in d["caption"]

    utc = cfg.describe_timezone("UTC")
    assert utc["region"] == "UTC"
    assert utc["primary"] in ("±0", "+00", "UTC")
    assert len(utc["primary"]) <= 7

    ny = cfg.describe_timezone("America/New_York")
    assert ny["region"] == "AM"
    assert ny["primary"].startswith(("+", "-")) or ny["primary"] == "±0"


def test_validate_cron_ok():
    assert cfg.validate_cron_expression("0 3 * * *") == "0 3 * * *"


def test_validate_cron_bad():
    with pytest.raises(ValueError, match="5 fields"):
        cfg.validate_cron_expression("not a cron")


def test_force_2fa_enabled(_memory_settings):
    assert cfg.force_2fa_enabled() is False
    cfg.save_settings({"force_2fa": True})
    assert cfg.force_2fa_enabled() is True


def test_replace_settings_from_backup(_memory_settings):
    cfg.save_settings({"timezone": "UTC", "keep": 5})
    cfg.replace_settings({"timezone": "America/New_York", "force_2fa": True, "keep": 15})
    loaded = cfg.load_settings()
    assert loaded["timezone"] == "America/New_York"
    assert loaded["keep"] == 15


def test_format_datetime_converts_naive_and_iso(_memory_settings):
    from datetime import datetime

    cfg.set_app_timezone("Africa/Johannesburg")
    # 08:00 UTC → 10:00 SAST
    assert cfg.format_datetime_in_app_tz(
        datetime(2026, 7, 13, 8, 0, 0), "%H:%M"
    ) == "10:00"
    assert cfg.format_datetime_in_app_tz("2026-07-13T08:00:00", "%H:%M") == "10:00"
    assert cfg.format_datetime_in_app_tz("2026-07-13T08:00:00Z", "%H:%M") == "10:00"
    assert cfg.utc_isoformat(datetime(2026, 7, 13, 8, 0, 0)) == "2026-07-13T08:00:00Z"


def test_parse_utc_datetime_variants():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    dt = cfg.parse_utc_datetime("2026-07-13T08:00:00")
    assert dt is not None
    assert dt.tzinfo == ZoneInfo("UTC")
    assert dt.hour == 8
    assert cfg.parse_utc_datetime(None) is None
    assert isinstance(cfg.parse_utc_datetime(datetime(2026, 1, 1, 0, 0, 0)), datetime)
