"""J favourites + K cross-host jump + AB device summary helpers."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import nav_shortcuts as nav


def test_feature_href_and_label():
    assert nav.feature_href(3, "docker") == "/servers/3/docker"
    assert nav.feature_href(3, "overview") == "/servers/3"
    assert nav.feature_label("backups") == "Backups"
    with pytest.raises(ValueError):
        nav.normalize_feature("nope")


def test_app_page_allowlist():
    # #map is required so fabric opens the SVG (same as Network hub cards)
    assert nav.app_page_href("hosts_map") == "/dns/physical#map"
    assert nav.app_page_href("path_map") == "/dns/logical#map"
    assert "Hosts" in nav.app_page_label("hosts_map")
    with pytest.raises(ValueError):
        nav.normalize_app_page("random")


def test_server_has_feature_flags():
    s = SimpleNamespace(
        backup_enabled=True,
        container_patch_enabled=False,
    )
    assert nav.server_has_feature(s, "overview") is True
    assert nav.server_has_feature(s, "backups") is True
    assert nav.server_has_feature(s, "docker") is False
    assert nav.server_has_feature(s, "services") is True


def test_summarize_user_agent():
    assert "Chrome" in nav.summarize_user_agent(
        "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0.0.0 Safari/537.36"
    )
    assert "Windows" in nav.summarize_user_agent(
        "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0.0.0 Safari/537.36"
    )
    assert "Safari" in nav.summarize_user_agent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X) Version/17.0 Safari/605.1.15"
    )
    assert nav.summarize_user_agent("") == "Unknown browser"


def test_trusted_device_public():
    d = SimpleNamespace(
        id=1,
        label="Trusted device",
        user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/120.0",
        ip="10.0.0.5",
        created_at=None,
        last_used_at=None,
        expires_at=None,
    )
    pub = nav.trusted_device_public(d)
    assert pub["ip"] == "10.0.0.5"
    assert "Firefox" in pub["device_type"]
    assert "Linux" in pub["display_name"]


def test_toggle_favourite_add_and_remove():
    """toggle creates then removes a pin (mock session)."""
    store: list = []

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def first(self):
            return self._rows[0] if self._rows else None

        def all(self):
            return list(self._rows)

    session = MagicMock()
    server = SimpleNamespace(id=2, name="lab")

    def get(model, key):
        name = getattr(model, "__name__", "")
        if name == "Server":
            return server
        for r in store:
            if r.id == key:
                return r
        return None

    session.get = get
    session.exec = MagicMock(return_value=FakeResult([]))

    def add(row):
        row.id = 99
        store.append(row)

    session.add = add
    session.commit = MagicMock()
    session.refresh = MagicMock()

    res = nav.toggle_server_favourite(
        session, user_id=1, server_id=2, feature="docker"
    )
    assert res["pinned"] is True
    assert len(store) == 1
    assert store[0].feature == "docker"

    session.exec = MagicMock(return_value=FakeResult(store))
    res2 = nav.toggle_server_favourite(
        session, user_id=1, server_id=2, feature="docker"
    )
    assert res2["pinned"] is False


def test_toggle_app_page_favourite():
    store: list = []

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def first(self):
            return self._rows[0] if self._rows else None

        def all(self):
            return list(self._rows)

    session = MagicMock()
    session.exec = MagicMock(return_value=FakeResult([]))
    session.add = lambda row: (setattr(row, "id", 7), store.append(row))
    session.commit = MagicMock()
    session.refresh = MagicMock()
    r = nav.toggle_app_page_favourite(session, 1, page="hosts_map")
    assert r["pinned"] is True
    assert store[0].kind == nav.KIND_APP_PAGE
    assert store[0].feature == "hosts_map"
    session.exec = MagicMock(return_value=FakeResult(store))
    r2 = nav.toggle_app_page_favourite(session, 1, page="hosts_map")
    assert r2["pinned"] is False
