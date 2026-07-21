"""Hard fence: nmap tasks must only run on the dedicated nmap worker.

The main web/celery image does not ship nmap. Compose must never put queue
``nmap`` on the default worker. This guard fails fast if a task is misrouted.
"""
from __future__ import annotations

import os
import shutil

_MSG_NO_BINARY = (
    "nmap binary not found. LAN scan tasks must run on celery-worker-nmap "
    "(compose profile ``nmap``, queue ``nmap`` only). "
    "Do not consume the nmap queue on the main web/celery worker."
)

_MSG_DISABLED = (
    "nmap tasks are disabled on this process "
    "(PIHERDER_NMAP_WORKER=0). Use the dedicated nmap worker image."
)


def nmap_binary_path() -> str | None:
    return shutil.which("nmap")


def ensure_nmap_worker_runtime() -> str:
    """Return path to nmap, or raise RuntimeError if this process must not scan.

    Rules:
    - ``PIHERDER_NMAP_WORKER=0|false|no`` → refuse (web can set this).
    - Missing ``nmap`` on PATH → refuse (main image / misrouted task).
    """
    marker = (os.environ.get("PIHERDER_NMAP_WORKER") or "").strip().lower()
    if marker in ("0", "false", "no", "off"):
        raise RuntimeError(_MSG_DISABLED)
    path = nmap_binary_path()
    if not path:
        raise RuntimeError(_MSG_NO_BINARY)
    return path
