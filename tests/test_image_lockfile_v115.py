"""The image installs the runtime lockfile, not the dev lockfile."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfiles_install_the_runtime_lock_only():
    for name in ("Dockerfile", "Dockerfile.nmap"):
        text = (_ROOT / name).read_text(encoding="utf-8")
        assert "requirements.runtime.lock.txt" in text
        assert "COPY requirements.lock.txt" not in text
        assert "-r requirements.lock.txt" not in text


def test_runtime_lock_has_no_dev_packages():
    runtime = (_ROOT / "requirements.runtime.lock.txt").read_text(encoding="utf-8")
    dev = (_ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    for package in ("pytest==", "ruff==", "coverage==", "pytest-cov=="):
        assert package not in runtime
        assert package in dev
