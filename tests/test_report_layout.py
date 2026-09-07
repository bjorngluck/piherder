"""v1.5 N3a — Reports pin / hide / reorder (cookie layout)."""
from __future__ import annotations

import json

from app.services import report_layout as rl


def test_default_all_visible_in_1_3_order():
    d = rl.default_layout()
    assert d["order"] == list(rl.CARD_IDS)
    assert d["hidden"] == []
    assert d["pinned"] == []
    assert rl.visible_ids(d) == list(rl.CARD_IDS)
    assert rl.is_default(d)


def test_hide_lan_keeps_others():
    n = rl.apply_action(rl.default_layout(), "hide", "lan")
    assert "lan" not in rl.visible_ids(n)
    assert "lan" in n["hidden"]
    assert rl.visible_ids(n) == ["backups", "os_patch", "docker", "console"]
    assert not rl.is_default(n)


def test_cannot_hide_last_card():
    n = rl.default_layout()
    for cid in ("os_patch", "lan", "docker", "console"):
        n = rl.apply_action(n, "hide", cid)
    assert rl.visible_ids(n) == ["backups"]
    stuck = rl.apply_action(n, "hide", "backups")
    assert rl.visible_ids(stuck) == ["backups"]


def test_pin_moves_to_front():
    n = rl.apply_action(rl.default_layout(), "pin", "docker")
    assert n["order"][0] == "docker"
    assert "docker" in n["pinned"]
    vis = rl.cards_for_template(n)["visible"]
    assert vis[0]["id"] == "docker"
    assert vis[0]["pinned"] is True
    assert vis[0]["first"] is True


def test_up_down_swap_visible():
    n = rl.apply_action(rl.default_layout(), "down", "backups")
    assert rl.visible_ids(n)[0] == "os_patch"
    assert rl.visible_ids(n)[1] == "backups"
    n = rl.apply_action(n, "up", "backups")
    assert rl.visible_ids(n)[0] == "backups"


def test_reset_restores_default():
    n = rl.apply_action(rl.default_layout(), "hide", "lan")
    n = rl.apply_action(n, "pin", "console")
    n = rl.apply_action(n, "reset", "")
    assert rl.is_default(n)


def test_cookie_roundtrip_and_junk():
    n = rl.apply_action(rl.default_layout(), "hide", "lan")
    raw = rl.encode_cookie(n)
    assert rl.parse_cookie(raw)["hidden"] == ["lan"]
    assert rl.parse_cookie("not-json") == rl.default_layout()
    assert rl.parse_cookie(None) == rl.default_layout()
    extra = json.dumps({"order": ["backups", "nope", "lan"], "hidden": ["nope"]})
    parsed = rl.parse_cookie(extra)
    assert "nope" not in parsed["order"]
    assert "docker" in parsed["order"]


def test_show_unhides():
    n = rl.apply_action(rl.default_layout(), "hide", "lan")
    n = rl.apply_action(n, "show", "lan")
    assert "lan" in rl.visible_ids(n)
    assert n["hidden"] == []
