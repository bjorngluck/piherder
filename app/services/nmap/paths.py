"""Paths for nmap vuln pack volume and scan artefacts."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any


def vuln_root() -> Path:
    """Directory for Vulners / NSE vulnerability artefacts (compose volume)."""
    raw = (
        os.environ.get("PIHERDER_NMAP_VULN_ROOT")
        or os.environ.get("NMAP_VULN_ROOT")
        or "/var/lib/piherder/nmap-vuln"
    )
    return Path(raw)


def artifact_dir(data_root: str | None = None) -> Path:
    root = data_root or os.environ.get("DATA_ROOT") or "/data"
    return Path(root) / "nmap" / "runs"


def run_artifact_path(run_id: int, data_root: str | None = None) -> Path:
    return artifact_dir(data_root) / f"run-{run_id}.xml"


def vuln_pack_status(root: Path | None = None) -> dict[str, Any]:
    """Detect presence of vulnerability database artefacts.

    We treat the pack as *ready* when a marker file exists or known Vulners
    data files are present. Exact layout is documented for the update job.
    """
    base = root or vuln_root()
    marker = base / "READY"
    # Common nmap-vulners / vulscan layouts (any hit counts as present)
    candidates = [
        marker,
        base / "vulners.json",
        base / "vulners.db",
        base / "cve.csv",
        base / "scipvuldb.csv",
        base / "nmap-vulners" / "vulners.nse",
        base / "scripts" / "vulners.nse",
    ]
    present_files = [str(p.name) for p in candidates if p.is_file()]
    # Also accept non-empty directory with any .nse or .json
    extra = False
    if base.is_dir():
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".nse", ".json", ".csv", ".db"):
                extra = True
                break

    ready = bool(present_files) or extra
    mtime = None
    if marker.is_file():
        try:
            mtime = datetime.utcfromtimestamp(marker.stat().st_mtime).isoformat() + "Z"
        except OSError:
            mtime = None

    return {
        "root": str(base),
        "ready": ready,
        "marker": marker.is_file(),
        "present_hints": present_files,
        "updated_at": mtime,
        "exists": base.is_dir(),
    }
