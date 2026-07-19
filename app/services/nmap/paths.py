"""Paths for nmap vuln pack volume and scan artefacts."""
from __future__ import annotations

import json
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


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / (1024**2):.1f} MB"
    return f"{n / (1024**3):.2f} GB"


# Expected vulscan offline tables (scipag/vulscan)
EXPECTED_VULSCAN_TABLES = (
    "cve.csv",
    "exploitdb.csv",
    "openvas.csv",
    "osvdb.csv",
    "scipvuldb.csv",
    "securityfocus.csv",
    "securitytracker.csv",
    "xforce.csv",
)


def vuln_pack_status(root: Path | None = None) -> dict[str, Any]:
    """Inventory vulnerability pack on the volume (sizes, completeness).

    Components:
    - **nmap-vulners**: NSE scripts (mostly query vulners.com *online* during scan)
    - **vulscan**: offline CSV tables + vulscan.nse for local CPE matching
    """
    base = root or vuln_root()
    marker = base / "READY"
    meta_path = base / "pack-meta.json"

    total_bytes = 0
    file_count = 0
    if base.is_dir():
        for p in base.rglob("*"):
            if p.is_file() and p.name not in (".gitkeep",):
                try:
                    total_bytes += p.stat().st_size
                    file_count += 1
                except OSError:
                    pass

    # --- nmap-vulners ---
    nv_dir = base / "nmap-vulners"
    nv_nse = sorted(nv_dir.rglob("*.nse")) if nv_dir.is_dir() else []
    nv_bytes = 0
    if nv_dir.is_dir():
        for p in nv_dir.rglob("*"):
            if p.is_file():
                try:
                    nv_bytes += p.stat().st_size
                except OSError:
                    pass
    has_vulners_nse = (nv_dir / "vulners.nse").is_file() or any(
        p.name == "vulners.nse" for p in nv_nse
    )

    # --- vulscan ---
    vs_dir = base / "vulscan"
    vs_nse = (vs_dir / "vulscan.nse").is_file()
    tables_present: list[dict[str, Any]] = []
    tables_missing: list[str] = []
    vs_bytes = 0
    for name in EXPECTED_VULSCAN_TABLES:
        p = vs_dir / name
        if p.is_file():
            try:
                sz = p.stat().st_size
            except OSError:
                sz = 0
            vs_bytes += sz
            tables_present.append(
                {"name": name, "bytes": sz, "human": _human_bytes(sz)}
            )
        else:
            tables_missing.append(name)
    if vs_dir.is_dir() and vs_nse:
        try:
            vs_bytes += (vs_dir / "vulscan.nse").stat().st_size
        except OSError:
            pass

    mtime = None
    if marker.is_file():
        try:
            mtime = datetime.utcfromtimestamp(marker.stat().st_mtime).isoformat() + "Z"
        except OSError:
            mtime = None

    pack_meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            pack_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(pack_meta, dict):
                pack_meta = {}
        except Exception:
            pack_meta = {}

    # Completeness levels for UI
    # basic: vulners.nse only (online vulners API during scan)
    # offline: + vulscan tables
    # full: + vulscan.nse script so offline matching can run
    if has_vulners_nse and tables_present and vs_nse and not tables_missing:
        completeness = "full"
        completeness_label = "Full (vulners + vulscan script + tables)"
    elif has_vulners_nse and tables_present and not tables_missing:
        completeness = "offline_tables"
        completeness_label = "Tables ready (vulscan.nse script missing — re-run update)"
    elif has_vulners_nse:
        completeness = "online_vulners"
        completeness_label = "Vulners scripts only (API online during deep scan)"
    elif tables_present or marker.is_file():
        completeness = "partial"
        completeness_label = "Partial pack"
    else:
        completeness = "empty"
        completeness_label = "Empty — run Update vulnerability DB"

    ready = completeness in ("full", "offline_tables", "online_vulners") or (
        marker.is_file() and (has_vulners_nse or bool(tables_present))
    )

    # --- official Exploit-DB index (optional add-on) ---
    edb_dir = base / "exploitdb"
    edb_files = []
    edb_bytes = 0
    edb_entries = 0
    edb_ready = (edb_dir / "READY").is_file()
    if edb_dir.is_dir():
        for name in ("files_exploits.csv", "files_shellcodes.csv"):
            p = edb_dir / name
            if p.is_file():
                try:
                    sz = p.stat().st_size
                except OSError:
                    sz = 0
                edb_bytes += sz
                edb_files.append({"name": name, "bytes": sz, "human": _human_bytes(sz)})
        # Prefer meta entries; else count lines in vulscan exploitdb.csv
        if (edb_dir / "READY").is_file():
            try:
                em = json.loads((edb_dir / "READY").read_text(encoding="utf-8"))
                if isinstance(em, dict):
                    edb_entries = int(em.get("entries") or 0)
            except Exception:
                edb_entries = 0
        if not edb_entries:
            exp_csv = base / "vulscan" / "exploitdb.csv"
            if exp_csv.is_file():
                try:
                    with exp_csv.open("r", encoding="utf-8", errors="replace") as fh:
                        edb_entries = sum(1 for _ in fh)
                except OSError:
                    pass

    # Upgrade completeness label when Exploit-DB add-on present
    if completeness == "full" and edb_ready and edb_files:
        completeness_label = "Full (vulners + vulscan + Exploit-DB index)"
    elif completeness == "full":
        completeness_label = "Full (vulners + vulscan; Exploit-DB index optional)"

    notes = [
        "nmap-vulners scripts call vulners.com during the scan (not a huge offline CVE dump).",
        "vulscan CSV tables are offline DBs (~40MB); they need vulscan.nse to be used.",
        "Exploit-DB add-on: official index CSVs only (not multi‑GB exploit payloads).",
    ]

    return {
        "root": str(base),
        "ready": ready,
        "marker": marker.is_file(),
        "exists": base.is_dir(),
        "updated_at": mtime or pack_meta.get("updated_at"),
        "total_bytes": total_bytes,
        "total_human": _human_bytes(total_bytes),
        "file_count": file_count,
        "completeness": completeness,
        "completeness_label": completeness_label,
        "nmap_vulners": {
            "present": has_vulners_nse,
            "nse_count": len(nv_nse),
            "nse_names": [p.name for p in nv_nse],
            "bytes": nv_bytes,
            "human": _human_bytes(nv_bytes),
            "mode": "online_api",
        },
        "vulscan": {
            "script_present": vs_nse,
            "tables_ok": len(tables_present),
            "tables_expected": len(EXPECTED_VULSCAN_TABLES),
            "tables_missing": tables_missing,
            "tables": tables_present,
            "bytes": vs_bytes,
            "human": _human_bytes(vs_bytes),
            "mode": "offline",
        },
        "exploitdb": {
            "present": bool(edb_files) or edb_ready,
            "ready": edb_ready,
            "entries": edb_entries,
            "files": edb_files,
            "bytes": edb_bytes,
            "human": _human_bytes(edb_bytes),
            "mode": "index_only",
        },
        "pack_meta": pack_meta,
        "notes": notes,
        # back-compat for older templates/tests
        "present_hints": (
            (["READY"] if marker.is_file() else [])
            + (["vulners.nse"] if has_vulners_nse else [])
            + ([t["name"] for t in tables_present[:3]] if tables_present else [])
            + (["exploitdb"] if edb_files else [])
        ),
    }
