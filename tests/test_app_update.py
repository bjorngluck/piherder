"""App version compare + update-check cache helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.version_info import is_remote_newer, parse_version_tuple, release_notes_url
from app.services import app_update


def test_parse_version_tuple():
    assert parse_version_tuple("v0.4.0") == ((0, 4, 0), "")
    nums, pre = parse_version_tuple("0.5.0.dev0")
    assert nums == (0, 5, 0)
    assert "dev" in pre


def test_is_remote_newer_stable_vs_dev():
    assert is_remote_newer("0.4.0", "0.5.0") is True
    assert is_remote_newer("0.5.0", "0.4.0") is False
    # Same base: clean release is newer than .dev
    assert is_remote_newer("0.5.0.dev0", "0.5.0") is True
    assert is_remote_newer("0.5.0", "0.5.0.dev0") is False
    # Dev ahead of last published
    assert is_remote_newer("0.5.0.dev0", "0.4.0") is False


def test_release_notes_url():
    assert "releases/tag/v0.5.0" in release_notes_url("0.5.0")
    assert "releases/tag/v0.5.0" in release_notes_url("v0.5.0")


def test_refresh_update_check_newer(monkeypatch):
    monkeypatch.setattr(app_update, "update_check_enabled", lambda: True)
    payload = {
        "tag_name": "v9.9.9",
        "name": "v9.9.9",
        "html_url": "https://github.com/bjorngluck/piherder/releases/tag/v9.9.9",
        "published_at": "2026-01-01T00:00:00Z",
    }
    with patch.object(app_update, "_fetch_latest_release", return_value=payload):
        with patch("app.services.app_update.vi.get_app_version", return_value="0.1.0"):
            n = app_update.refresh_update_check(force=True)
    assert n["ok"] is True
    assert n["available"] is True
    assert n["latest"] == "v9.9.9"
    assert "v9.9.9" in (n.get("notes_url") or "")


def test_refresh_update_check_disabled(monkeypatch):
    monkeypatch.setattr(app_update, "update_check_enabled", lambda: False)
    n = app_update.refresh_update_check(force=True)
    assert n["available"] is False
    assert n.get("enabled") is False


def test_fetch_failure_soft(monkeypatch):
    monkeypatch.setattr(app_update, "update_check_enabled", lambda: True)
    with patch.object(app_update, "_fetch_latest_release", side_effect=RuntimeError("net")):
        n = app_update.refresh_update_check(force=True)
    assert n["ok"] is False
    assert n["available"] is False
    assert n.get("error")
