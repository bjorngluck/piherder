"""Run nmap subprocess and ingest results (Celery worker only)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from sqlmodel import Session, select

from ...models import Integration, Job, NmapScanRun
from .allowlist import filter_targets, validate_cidrs
from .argv import INTENSITY_DEEP, build_nmap_argv
from .parse import parse_nmap_xml
from .paths import run_artifact_path, vuln_pack_status
from .runtime import (
    release_lock,
    set_progress,
    touch_worker_heartbeat,
    try_acquire_lock,
)
from .upsert import upsert_hosts_from_parse

logger = logging.getLogger(__name__)


def _integration_cidrs(integration: Integration) -> tuple[list[str], list[str]]:
    from ..integrations.registry import parse_config

    cfg = parse_config(integration.config_json)
    cidrs = cfg.get("cidrs") or cfg.get("lan_cidrs") or []
    if isinstance(cidrs, str):
        cidrs = [c.strip() for c in cidrs.split(",") if c.strip()]
    excludes = cfg.get("excludes") or []
    if isinstance(excludes, str):
        excludes = [c.strip() for c in excludes.split(",") if c.strip()]
    ok, _ = validate_cidrs([str(c) for c in cidrs])
    ex_ok, _ = validate_cidrs([str(c) for c in excludes])
    return ok, ex_ok


def _update_job(session: Session, job_id: int | None, status: str, details: dict) -> None:
    if not job_id:
        return
    job = session.get(Job, job_id)
    if not job:
        return
    job.status = status
    if status == "running" and not job.started_at:
        job.started_at = datetime.utcnow()
    if status in ("success", "failed", "cancelled"):
        job.finished_at = datetime.utcnow()
    prev = {}
    if job.details:
        try:
            prev = json.loads(job.details)
        except Exception:
            prev = {}
    prev.update(details)
    job.details = json.dumps(prev, separators=(",", ":"))
    session.add(job)
    session.commit()


def run_nmap_scan(
    session: Session,
    *,
    run_id: int,
    job_id: int | None = None,
    use_syn: bool = False,
    vuln_scripts: bool = False,
) -> dict[str, Any]:
    """Execute one NmapScanRun. Intended for celery-worker-nmap only."""
    touch_worker_heartbeat()
    run = session.get(NmapScanRun, run_id)
    if not run:
        return {"status": "error", "message": "run not found"}

    integration = session.get(Integration, run.integration_id)
    if not integration or not integration.enabled:
        run.status = "failed"
        run.error = "integration missing or disabled"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        _update_job(session, job_id, "failed", {"error": run.error})
        return {"status": "failed", "error": run.error}

    targets: list[str] = []
    if run.targets_json:
        try:
            raw = json.loads(run.targets_json)
            if isinstance(raw, list):
                targets = [str(t).strip() for t in raw if str(t).strip()]
        except Exception:
            targets = []

    allowed, excludes = _integration_cidrs(integration)
    if not allowed:
        run.status = "failed"
        run.error = "no configured LAN CIDRs"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        _update_job(session, job_id, "failed", {"error": run.error})
        return {"status": "failed", "error": run.error}

    if not targets:
        targets = list(allowed)

    ok_targets, rejected = filter_targets(targets, allowed, excludes=excludes)
    if not ok_targets:
        run.status = "failed"
        run.error = f"no allowed targets (rejected: {rejected[:5]})"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        _update_job(session, job_id, "failed", {"error": run.error})
        return {"status": "failed", "error": run.error}

    # Vuln scripts only for deep + pack ready + requested
    pack = vuln_pack_status()
    want_vuln = bool(vuln_scripts) and run.intensity == INTENSITY_DEEP and pack.get("ready")
    if vuln_scripts and not want_vuln:
        logger.info(
            "vuln scripts requested but pack not ready or intensity not deep "
            "(ready=%s intensity=%s)",
            pack.get("ready"),
            run.intensity,
        )

    lock_key = ",".join(sorted(ok_targets))[:200]
    holder = f"run:{run_id}:job:{job_id}"
    if not try_acquire_lock("scan", lock_key, holder=holder):
        run.status = "failed"
        run.error = "another scan holds the lock for these targets"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        _update_job(session, job_id, "failed", {"error": run.error})
        return {"status": "failed", "error": run.error}

    data_root = os.environ.get("DATA_ROOT", "/data")
    out_path = run_artifact_path(run_id, data_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run.status = "running"
    run.started_at = datetime.utcnow()
    run.error = None
    session.add(run)
    session.commit()
    _update_job(
        session,
        job_id,
        "running",
        {
            "current": "scanning",
            "run_id": run_id,
            "targets": ok_targets,
            "intensity": run.intensity,
        },
    )
    set_progress(
        job_id or 0,
        {"phase": "scanning", "run_id": run_id, "targets": ok_targets},
    )

    try:
        argv = build_nmap_argv(
            run.intensity,
            ok_targets,
            output_xml=str(out_path),
            use_syn=use_syn,
            vuln_scripts=want_vuln,
        )
        logger.info("nmap argv: %s", " ".join(argv))
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("PIHERDER_NMAP_TIMEOUT_SEC", "7200")),
            check=False,
        )
        touch_worker_heartbeat()

        if not out_path.is_file():
            # nmap may still write partial; treat missing as failure
            stderr = (proc.stderr or "")[:2000]
            run.status = "failed"
            run.error = f"nmap produced no XML (exit={proc.returncode}): {stderr}"
            run.finished_at = datetime.utcnow()
            session.add(run)
            session.commit()
            _update_job(session, job_id, "failed", {"error": run.error})
            return {"status": "failed", "error": run.error}

        xml_text = out_path.read_text(encoding="utf-8", errors="replace")
        hosts = parse_nmap_xml(xml_text)
        summary = upsert_hosts_from_parse(
            session,
            integration_id=integration.id,
            hosts=hosts,
            run_id=run_id,
            only_up=True,
        )
        hosts_up = sum(1 for h in hosts if (h.status or "").lower() == "up")
        ports_open = sum(
            1
            for h in hosts
            for p in h.ports
            if (p.state or "").lower() == "open"
        )

        # relative artifact path under DATA_ROOT
        try:
            rel = str(out_path.relative_to(Path(data_root)))
        except ValueError:
            rel = str(out_path)

        run.status = "success" if proc.returncode in (0, 1) else "failed"
        # nmap exit 1 = hosts down / no targets sometimes still OK with XML
        if proc.returncode not in (0, 1) and not hosts:
            run.status = "failed"
            run.error = (proc.stderr or f"nmap exit {proc.returncode}")[:2000]
        run.hosts_up = hosts_up
        run.hosts_total = len(hosts)
        run.ports_open = ports_open
        run.summary_json = json.dumps(summary, separators=(",", ":"))
        run.artifact_path = rel
        run.finished_at = datetime.utcnow()
        if rejected:
            run.summary_json = json.dumps(
                {**summary, "rejected_targets": rejected[:20]},
                separators=(",", ":"),
            )
        session.add(run)
        session.commit()

        final = "success" if run.status == "success" else "failed"
        _update_job(
            session,
            job_id,
            final,
            {
                "current": final,
                "run_id": run_id,
                "hosts_up": hosts_up,
                "hosts_total": len(hosts),
                "ports_open": ports_open,
                "upsert": summary,
                "error": run.error,
            },
        )
        set_progress(
            job_id or 0,
            {"phase": final, "run_id": run_id, "hosts_up": hosts_up},
        )
        return {
            "status": final,
            "run_id": run_id,
            "hosts_up": hosts_up,
            "upsert": summary,
        }
    except subprocess.TimeoutExpired:
        run.status = "failed"
        run.error = "nmap timed out"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        _update_job(session, job_id, "failed", {"error": run.error})
        return {"status": "failed", "error": run.error}
    except Exception as e:
        logger.exception("nmap scan failed")
        run.status = "failed"
        run.error = str(e)[:2000]
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        _update_job(session, job_id, "failed", {"error": run.error})
        return {"status": "failed", "error": run.error}
    finally:
        release_lock("scan", lock_key, holder=holder)


def enqueue_nmap_scan(
    session: Session,
    *,
    integration_id: int,
    intensity: str,
    targets: Sequence[str] | None = None,
    schedule_id: int | None = None,
    user_id: int | None = None,
    vuln_scripts: bool = False,
) -> tuple[Job, NmapScanRun]:
    """Create Job + NmapScanRun and dispatch to Celery queue ``nmap``."""
    from ...celery_app import celery

    job = Job(
        server_id=None,
        job_type=f"nmap_{intensity}" if intensity != "deep" else "nmap_host_deep",
        status="pending",
        details=json.dumps(
            {
                "current": "queued",
                "intensity": intensity,
                "user_id": user_id,
                "vuln_scripts": vuln_scripts,
            },
            separators=(",", ":"),
        ),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    run = NmapScanRun(
        integration_id=integration_id,
        job_id=job.id,
        schedule_id=schedule_id,
        intensity=intensity,
        targets_json=json.dumps(list(targets or []), separators=(",", ":")),
        status="pending",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    async_result = celery.send_task(
        "app.tasks.nmap_scan",
        kwargs={
            "run_id": run.id,
            "job_id": job.id,
            "vuln_scripts": vuln_scripts,
        },
        queue="nmap",
    )
    job.celery_task_id = async_result.id
    session.add(job)
    session.commit()
    session.refresh(job)
    return job, run
