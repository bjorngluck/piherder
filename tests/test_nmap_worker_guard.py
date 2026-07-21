"""Worker fence: nmap tasks must not run on web / main celery."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.services.nmap.worker_guard import ensure_nmap_worker_runtime, nmap_binary_path


def test_guard_refuses_when_marker_disabled():
    with patch.dict(os.environ, {"PIHERDER_NMAP_WORKER": "0"}, clear=False):
        with pytest.raises(RuntimeError, match="disabled"):
            ensure_nmap_worker_runtime()


def test_guard_refuses_when_nmap_missing():
    with patch.dict(os.environ, {"PIHERDER_NMAP_WORKER": "1"}, clear=False):
        with patch("app.services.nmap.worker_guard.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="nmap binary not found"):
                ensure_nmap_worker_runtime()


def test_guard_ok_when_binary_and_worker():
    with patch.dict(os.environ, {"PIHERDER_NMAP_WORKER": "1"}, clear=False):
        with patch(
            "app.services.nmap.worker_guard.shutil.which", return_value="/usr/bin/nmap"
        ):
            assert ensure_nmap_worker_runtime() == "/usr/bin/nmap"


def test_nmap_binary_path_uses_which():
    with patch("app.services.nmap.worker_guard.shutil.which", return_value="/bin/nmap"):
        assert nmap_binary_path() == "/bin/nmap"
