"""Workflow actions and Dockerfile FROM lines stay pinned."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_supply_pins.py"
_spec = importlib.util.spec_from_file_location("check_supply_pins", _PATH)
pins = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(pins)


def test_tag_uses_and_tag_from_are_unpinned():
    text = "uses: actions/checkout@v4\n"
    assert pins.unpinned_uses(text) == [(1, "actions/checkout@v4")]
    pinned = "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n"
    assert pins.unpinned_uses(pinned) == []
    assert pins.unpinned_from("FROM python:3.12-slim-bookworm\n") == [
        (1, "python:3.12-slim-bookworm")
    ]
    digest = (
        "FROM python:3.12-slim-bookworm"
        "@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337\n"
    )
    assert pins.unpinned_from(digest) == []
    assert pins.unpinned_from("# FROM python:3.12\nFROM scratch\n") == []


def test_repo_actions_and_dockerfiles_are_pinned():
    assert pins.scan_repo() == []
