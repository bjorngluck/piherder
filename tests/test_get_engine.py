"""Callers use get_engine() instead of importing the module-level engine."""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"


def test_app_modules_do_not_import_engine():
    bad: list[str] = []
    for path in _APP.rglob("*.py"):
        rel = path.relative_to(_APP).as_posix()
        if rel == "database.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "database":
                for alias in node.names:
                    if alias.name == "engine":
                        bad.append(f"{rel}:{node.lineno} imports engine")
            if isinstance(node, ast.Attribute) and node.attr == "engine":
                bad.append(f"{rel}:{node.lineno} reads .engine")
    assert bad == []


def test_get_engine_returns_the_process_engine():
    from app.database import engine, get_engine

    assert get_engine() is engine
