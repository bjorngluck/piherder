"""Download / refresh vulnerability NSE pack into PIHERDER_NMAP_VULN_ROOT.

Runs only on celery-worker-nmap. Pack is never baked into the image.

Layout after a full update::

    $PIHERDER_NMAP_VULN_ROOT/
      READY
      pack-meta.json
      nmap-vulners/          # vulners.nse (+ helpers) — queries vulners.com online
      vulscan/               # vulscan.nse + offline CVE CSV tables (~40MB)
      exploitdb/             # official Exploit-DB index (GitLab) + vulscan-format export
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlmodel import Session

from ...models import Job
from .job_progress import merge_job_details, stamp_line
from .paths import EXPECTED_VULSCAN_TABLES, vuln_pack_status, vuln_root
from .runtime import release_lock, touch_worker_heartbeat, try_acquire_lock

logger = logging.getLogger(__name__)

NMAP_VULNERS_TARBALL = (
    "https://github.com/vulnersCom/nmap-vulners/archive/refs/heads/master.tar.gz"
)
# Full repo (script + tables); raw CSV fallback if tarball fails
VULSCAN_TARBALL = (
    "https://github.com/scipag/vulscan/archive/refs/heads/master.tar.gz"
)
VULSCAN_RAW_BASE = "https://raw.githubusercontent.com/scipag/vulscan/master"

# Official Exploit Database indexes (Offensive Security / GitLab mirror)
# Index CSVs only — not the multi‑GB tree of exploit payloads.
EXPLOITDB_RAW_BASE = (
    "https://gitlab.com/exploit-database/exploitdb/-/raw/main"
)
EXPLOITDB_INDEX_FILES = (
    "files_exploits.csv",
    "files_shellcodes.csv",
)

LogFn = Callable[[str], None]


def _http_get(url: str, *, timeout: int = 180) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PiHerder-nmap-vuln-update/0.8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _human(n: int) -> str:
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024**2):.1f} MB"


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tf.getmembers():
        name = member.name
        while name.startswith("./"):
            name = name[2:]
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise ValueError(f"unsafe path in archive: {member.name}")
        tf.extract(member, dest, filter="data")


def _copy_tree_contents(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def download_nmap_vulners(root: Path, log: LogFn) -> dict[str, Any]:
    """Fetch nmap-vulners into root/nmap-vulners/."""
    log(stamp_line(f"Downloading nmap-vulners tarball…"))
    raw = _http_get(NMAP_VULNERS_TARBALL, timeout=180)
    log(stamp_line(f"  nmap-vulners archive: {_human(len(raw))} ({len(raw)} bytes)"))
    out_dir = root / "nmap-vulners"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _safe_extract_tar(tf, tmp_path)
            children = [p for p in tmp_path.iterdir() if p.is_dir()]
            src = children[0] if len(children) == 1 else tmp_path
            _copy_tree_contents(src, out_dir)
    nse = list(out_dir.rglob("*.nse"))
    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    log(
        stamp_line(
            f"Installed nmap-vulners: {len(nse)} .nse · {_human(total)} → {out_dir}"
        )
    )
    log(
        stamp_line(
            "Note: vulners.nse queries vulners.com API during deep scans "
            "(not a multi‑GB offline dump)."
        )
    )
    return {
        "nmap_vulners": str(out_dir),
        "nse_count": len(nse),
        "nse_names": [p.name for p in nse],
        "nmap_vulners_bytes": total,
    }


def download_vulscan(root: Path, log: LogFn) -> dict[str, Any]:
    """Fetch full scipag/vulscan (script + CSV tables) into root/vulscan/."""
    out_dir = root / "vulscan"
    log(stamp_line("Downloading vulscan tarball (script + offline tables)…"))
    try:
        raw = _http_get(VULSCAN_TARBALL, timeout=300)
        log(stamp_line(f"  vulscan archive: {_human(len(raw))} ({len(raw)} bytes)"))
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                _safe_extract_tar(tf, tmp_path)
                children = [p for p in tmp_path.iterdir() if p.is_dir()]
                src = children[0] if len(children) == 1 else tmp_path
                _copy_tree_contents(src, out_dir)
    except Exception as e:
        log(stamp_line(f"  tarball failed ({e}); falling back to raw CSV + NSE…"))
        return _download_vulscan_raw_fallback(root, log)

    ok = [n for n in EXPECTED_VULSCAN_TABLES if (out_dir / n).is_file()]
    missing = [n for n in EXPECTED_VULSCAN_TABLES if not (out_dir / n).is_file()]
    has_script = (out_dir / "vulscan.nse").is_file()
    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    # If tarball lacked tables (sparse checkout), fill from raw
    if missing:
        log(stamp_line(f"  filling {len(missing)} missing table(s) from raw GitHub…"))
        extra = _download_vulscan_tables_only(out_dir, missing, log)
        ok = list(dict.fromkeys(ok + extra.get("files_ok", [])))
        missing = [n for n in EXPECTED_VULSCAN_TABLES if not (out_dir / n).is_file()]
        total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    if not has_script:
        try:
            log(stamp_line("  fetching vulscan.nse…"))
            data = _http_get(f"{VULSCAN_RAW_BASE}/vulscan.nse", timeout=60)
            (out_dir / "vulscan.nse").write_bytes(data)
            has_script = True
            total += len(data)
            log(stamp_line(f"  wrote vulscan.nse ({_human(len(data))})"))
        except Exception as e:
            log(stamp_line(f"  vulscan.nse missing: {e}"))

    log(
        stamp_line(
            f"Installed vulscan: script={'yes' if has_script else 'NO'} · "
            f"tables {len(ok)}/{len(EXPECTED_VULSCAN_TABLES)} · {_human(total)}"
        )
    )
    for n in ok:
        p = out_dir / n
        log(stamp_line(f"  table {n}: {_human(p.stat().st_size)}"))
    if missing:
        log(stamp_line(f"  missing tables: {', '.join(missing)}"))

    return {
        "vulscan_dir": str(out_dir),
        "vulscan_script": has_script,
        "files_ok": ok,
        "files_failed": missing,
        "vulscan_bytes": total,
    }


def _download_vulscan_tables_only(
    out_dir: Path, names: list[str], log: LogFn
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok: list[str] = []
    failed: list[str] = []
    for name in names:
        url = f"{VULSCAN_RAW_BASE}/{name}"
        try:
            log(stamp_line(f"  fetching {name}…"))
            data = _http_get(url, timeout=180)
            if len(data) < 32:
                failed.append(name)
                continue
            (out_dir / name).write_bytes(data)
            ok.append(name)
            log(stamp_line(f"    wrote {name} ({_human(len(data))})"))
        except Exception as e:
            failed.append(name)
            log(stamp_line(f"    failed {name}: {e}"))
    return {"files_ok": ok, "files_failed": failed}


def _download_vulscan_raw_fallback(root: Path, log: LogFn) -> dict[str, Any]:
    out_dir = root / "vulscan"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    has_script = False
    try:
        data = _http_get(f"{VULSCAN_RAW_BASE}/vulscan.nse", timeout=60)
        (out_dir / "vulscan.nse").write_bytes(data)
        has_script = True
        log(stamp_line(f"  wrote vulscan.nse ({_human(len(data))})"))
    except Exception as e:
        log(stamp_line(f"  vulscan.nse failed: {e}"))
    tables = _download_vulscan_tables_only(
        out_dir, list(EXPECTED_VULSCAN_TABLES), log
    )
    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    return {
        "vulscan_dir": str(out_dir),
        "vulscan_script": has_script,
        "files_ok": tables.get("files_ok") or [],
        "files_failed": tables.get("files_failed") or [],
        "vulscan_bytes": total,
    }


# Back-compat name used by tests
def download_vulscan_tables(root: Path, log: LogFn) -> dict[str, Any]:
    return download_vulscan(root, log)


def _exploitdb_row_to_vulscan(row: dict[str, str]) -> str | None:
    """Convert official Exploit-DB CSV row → vulscan ``id;title`` line."""
    eid = (row.get("id") or "").strip()
    desc = (row.get("description") or "").strip()
    if not eid or not desc:
        return None
    # vulscan format: <id>;<title>  (no CSV quoting)
    desc = desc.replace("\n", " ").replace("\r", " ").replace(";", ",")
    return f"{eid};{desc}"


def convert_exploitdb_csv_to_vulscan(raw_csv: bytes) -> tuple[list[str], int]:
    """Return (vulscan lines, row_count) from official files_*.csv content."""
    text = raw_csv.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    lines: list[str] = []
    for row in reader:
        if not isinstance(row, dict):
            continue
        line = _exploitdb_row_to_vulscan(row)
        if line:
            lines.append(line)
    return lines, len(lines)


def download_exploitdb(root: Path, log: LogFn) -> dict[str, Any]:
    """Download official Exploit-DB index CSVs and feed vulscan/exploitdb.csv.

    Stores raw indexes under ``exploitdb/`` and overwrites
    ``vulscan/exploitdb.csv`` in vulscan's ``id;title`` format so deep scans
    match against the current Exploit-DB catalog (not only the older bundled table).
    """
    out_dir = root / "exploitdb"
    out_dir.mkdir(parents=True, exist_ok=True)
    vulscan_dir = root / "vulscan"
    vulscan_dir.mkdir(parents=True, exist_ok=True)

    log(stamp_line("Downloading official Exploit-DB indexes (GitLab)…"))
    all_lines: list[str] = []
    sources: list[dict[str, Any]] = []
    failed: list[str] = []
    total_raw = 0

    for name in EXPLOITDB_INDEX_FILES:
        url = f"{EXPLOITDB_RAW_BASE}/{name}"
        try:
            log(stamp_line(f"  fetching {name}…"))
            data = _http_get(url, timeout=300)
            if len(data) < 64:
                failed.append(name)
                log(stamp_line(f"    skip {name}: empty/short"))
                continue
            dest = out_dir / name
            dest.write_bytes(data)
            total_raw += len(data)
            lines, n = convert_exploitdb_csv_to_vulscan(data)
            all_lines.extend(lines)
            sources.append(
                {
                    "name": name,
                    "bytes": len(data),
                    "human": _human(len(data)),
                    "entries": n,
                }
            )
            log(
                stamp_line(
                    f"    wrote {name} ({_human(len(data))}, {n} entries)"
                )
            )
        except Exception as e:
            failed.append(name)
            log(stamp_line(f"    failed {name}: {e}"))

    # Deduplicate by id (first wins: exploits before shellcodes if same id)
    seen: set[str] = set()
    unique: list[str] = []
    for line in all_lines:
        eid = line.split(";", 1)[0]
        if eid in seen:
            continue
        seen.add(eid)
        unique.append(line)

    vulscan_path = vulscan_dir / "exploitdb.csv"
    if unique:
        # Sort numerically by id when possible
        def _sort_key(line: str) -> tuple[int, str]:
            eid = line.split(";", 1)[0]
            try:
                return (0, f"{int(eid):012d}")
            except ValueError:
                return (1, eid)

        unique.sort(key=_sort_key)
        body = "\n".join(unique) + "\n"
        vulscan_path.write_text(body, encoding="utf-8")
        log(
            stamp_line(
                f"Updated vulscan/exploitdb.csv · {len(unique)} entries · "
                f"{_human(len(body.encode('utf-8')))}"
            )
        )
    else:
        log(stamp_line("No Exploit-DB entries converted — left existing exploitdb.csv"))

    # Lightweight marker for pack status
    meta = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "source": "gitlab.com/exploit-database/exploitdb",
        "entries": len(unique),
        "sources": sources,
        "failed": failed,
        "note": "Index only (files_*.csv); exploit payloads not downloaded",
    }
    (out_dir / "READY").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "exploitdb_dir": str(out_dir),
        "exploitdb_entries": len(unique),
        "exploitdb_bytes": total_raw,
        "exploitdb_sources": sources,
        "exploitdb_failed": failed,
        "exploitdb_vulscan_csv": str(vulscan_path) if unique else None,
    }


def write_ready_marker(root: Path, meta: dict[str, Any], log: LogFn) -> Path:
    marker = root / "READY"
    status = vuln_pack_status(root)
    payload = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "source": "piherder.nmap_vuln_db_update",
        "completeness": status.get("completeness"),
        "total_human": status.get("total_human"),
        "total_bytes": status.get("total_bytes"),
        **meta,
    }
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (root / "pack-meta.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    log(
        stamp_line(
            f"Wrote READY · completeness={status.get('completeness')} · "
            f"{status.get('total_human')} ({status.get('file_count')} files)"
        )
    )
    return marker


def run_vuln_db_update(
    session: Session,
    *,
    job_id: int | None = None,
    include_vulscan: bool = True,
    include_exploitdb: bool = True,
) -> dict[str, Any]:
    """Refresh vuln pack on the mounted volume. Intended for nmap worker only."""
    touch_worker_heartbeat()
    root = vuln_root()
    holder = f"vuln-db:job:{job_id}"
    if not try_acquire_lock("vuln_db", "global", holder=holder, ttl=1800):
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary="Another vuln DB update is running",
            log_line=stamp_line("Lock busy — another update holds the lock"),
            extra={"error": "lock busy"},
        )
        return {"status": "failed", "error": "lock busy"}

    def log(msg: str) -> None:
        logger.info("vuln_db_update: %s", msg)
        merge_job_details(
            session,
            job_id,
            status="running",
            current="updating",
            summary=msg[-200:],
            log_line=msg,
        )
        touch_worker_heartbeat()

    try:
        merge_job_details(
            session,
            job_id,
            status="running",
            current="starting",
            summary="Updating vulnerability pack…",
            log_line=stamp_line(f"Vuln pack root: {root}"),
        )
        root.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = {
            "include_vulscan": include_vulscan,
            "include_exploitdb": include_exploitdb,
        }
        meta.update(download_nmap_vulners(root, log))
        if include_vulscan:
            meta.update(download_vulscan(root, log))
        else:
            log(stamp_line("Skipping vulscan (include_vulscan=false)"))
        if include_exploitdb:
            # Prefer after vulscan so exploitdb.csv lands in vulscan/
            meta.update(download_exploitdb(root, log))
        else:
            log(stamp_line("Skipping official Exploit-DB (include_exploitdb=false)"))
        write_ready_marker(root, meta, log)

        status = vuln_pack_status(root)
        edb = status.get("exploitdb") or {}
        summary = (
            f"Vuln pack {status.get('completeness')} · "
            f"{status.get('total_human')} · "
            f"vulners nse={meta.get('nse_count', 0)} · "
            f"vulscan tables={len(meta.get('files_ok') or [])}/"
            f"{len(EXPECTED_VULSCAN_TABLES)} · "
            f"script={'yes' if meta.get('vulscan_script') else 'no'} · "
            f"exploitdb={edb.get('entries') or meta.get('exploitdb_entries') or 0} entries"
        )
        merge_job_details(
            session,
            job_id,
            status="success",
            current="completed",
            summary=summary,
            log_line=stamp_line(summary),
            extra={"vuln_pack": status, "meta": meta, "result_snippet": summary},
        )
        return {"status": "success", "vuln_pack": status, "meta": meta}
    except Exception as e:
        logger.exception("vuln_db_update failed")
        err = str(e)[:500]
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=f"Vuln pack update failed: {err}",
            log_line=stamp_line(f"ERROR: {err}"),
            extra={"error": err},
        )
        return {"status": "failed", "error": err}
    finally:
        release_lock("vuln_db", "global", holder=holder)


def enqueue_vuln_db_update(
    session: Session,
    *,
    user_id: int | None = None,
    include_vulscan: bool = True,
    include_exploitdb: bool = True,
) -> Job:
    """Create Job and dispatch to Celery queue ``nmap``."""
    from ...celery_app import celery

    job = Job(
        server_id=None,
        job_type="nmap_vuln_db_update",
        status="pending",
        details=json.dumps(
            {
                "current": "queued",
                "summary": "Queued vulnerability pack update on nmap worker",
                "user_id": user_id,
                "include_vulscan": include_vulscan,
                "include_exploitdb": include_exploitdb,
                "log_lines": [stamp_line("Queued on nmap queue")],
            },
            separators=(",", ":"),
        ),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    async_result = celery.send_task(
        "app.tasks.nmap_vuln_db_update",
        kwargs={
            "job_id": job.id,
            "include_vulscan": include_vulscan,
            "include_exploitdb": include_exploitdb,
        },
        queue="nmap",
    )
    job.celery_task_id = async_result.id
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
