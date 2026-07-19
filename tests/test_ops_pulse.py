"""ops-hero pulse pure helpers (stream Q coverage)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.ops_pulse import (
    bar_seg,
    catalog_health,
    dual_line_pulse,
    stat,
    users_pulse,
)


def test_stat_and_bar_seg():
    assert stat(3, "run", "text-info") == {"n": 3, "l": "run", "cls": "text-info"}
    assert bar_seg(0, "ops-bar--mute", "zero")["n"] == 0.001
    assert bar_seg(5, "ops-bar--ok")["n"] == 5.0
    assert bar_seg("x", "ops-bar--mute")["n"] == 0.001


def test_dual_line_pulse_shape():
    p = dual_line_pulse(
        health="warn",
        primary=2,
        primary_label="jobs",
        bar=[bar_seg(1, "ops-bar--run")],
        line1=[stat(1, "run")],
        line2=[stat(0, "fail")],
        caption="x",
        extra_key=True,
    )
    assert p["health"] == "warn"
    assert p["primary"] == 2
    assert p["extra_key"] is True
    assert len(p["bar"]) == 1


def test_users_pulse_roles_and_2fa():
    users = [
        SimpleNamespace(role="admin", totp_enabled=True, id=1),
        SimpleNamespace(role="operator", totp_enabled=False, id=2),
        SimpleNamespace(role="viewer", totp_enabled=True, id=3),
        SimpleNamespace(role="weird", totp_enabled=False, id=4),
    ]
    p = users_pulse(users, sole_admin_ids={1})
    assert p["primary"] == 4
    assert p["health"] == "ok"
    labels = [x["l"] for x in p["line1"]]
    assert "admin" in labels
    assert "2fa on" in labels


def test_users_pulse_no_admin_is_hot():
    p = users_pulse([SimpleNamespace(role="viewer", totp_enabled=False, id=1)])
    assert p["health"] == "hot"


def test_catalog_health():
    assert catalog_health(err_n=1) == "hot"
    assert catalog_health(warn_n=2, items=1) == "warn"
    assert catalog_health(items=3) == "ok"
    assert catalog_health() == "mute"
