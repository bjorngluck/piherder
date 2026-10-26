"""Version pin helper used by scripts/check_release_sync.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_release_sync.py"
_SPEC = importlib.util.spec_from_file_location("check_release_sync", _PATH)
sync = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(sync)


def test_pin_problems_accepts_every_listed_file():
    files = {name: "package 1.12.1" for name in sync.PIN_FILES}
    assert sync.pin_problems("1.12.1", files) == []


def test_pin_problems_names_a_file_that_omits_the_version():
    files = {name: "package 1.12.1" for name in sync.PIN_FILES}
    files["SECURITY.md"] = "current is 1.12.0"
    assert sync.pin_problems("1.12.1", files) == ["SECURITY.md does not pin 1.12.1"]


def test_pin_problems_requires_every_listed_file():
    files = {name: "package 1.12.1" for name in sync.PIN_FILES}
    del files["wiki/index.md"]
    assert sync.pin_problems("1.12.1", files) == ["wiki/index.md does not pin 1.12.1"]
