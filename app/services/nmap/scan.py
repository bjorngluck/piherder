"""Run nmap subprocess and ingest results (Celery worker only)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from sqlmodel import Session

from ...models import Integration, Job, NmapScanRun
from .allowlist import filter_targets, validate_cidrs
from .argv import INTENSITY_DEEP, build_nmap_argv
from .job_progress import merge_job_details, stamp_line
from .parse import parse_nmap_xml
from .paths import run_artifact_path, vuln_pack_status, vuln_root
from .privileges import is_root_required_error, resolve_use_syn
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


def _log(
    session: Session,
    job_id: int | None,
    msg: str,
    *,
    status: str | None = "running",
    current: str | None = None,
    summary: str | None = None,
    extra: dict | None = None,
) -> None:
    line = stamp_line(msg) if not msg.startswith("[") else msg
    merge_job_details(
        session,
        job_id,
        status=status,
        current=current,
        summary=summary or msg[:200],
        log_line=line,
        extra=extra,
    )


def _script_args_for_preset(preset: str) -> list[str]:
    """Build --script list for deep scans from a curated preset.

    Stock nmap already ships ``vulners`` inside the ``vuln`` category. Loading the
    pack copy of vulners.nse as well raises: duplicate script ID 'vulners'.

    Presets:
    - ``none`` → no scripts
    - ``cpe`` → stock ``vulners`` only (CPE/version online API)
    - ``offline`` → pack ``vulscan.nse`` only
    - ``full`` → stock ``vuln`` + vulscan + optional http-vulners-regex
    """
    from .options import (
        SCRIPT_PRESET_CPE,
        SCRIPT_PRESET_FULL,
        SCRIPT_PRESET_NONE,
        SCRIPT_PRESET_OFFLINE,
        normalize_script_preset,
    )

    preset = normalize_script_preset(preset)
    if preset == SCRIPT_PRESET_NONE:
        return []

    root = vuln_root()
    vulscan = root / "vulscan" / "vulscan.nse"
    http_vr = root / "nmap-vulners" / "http-vulners-regex.nse"
    scripts: list[str] = []

    if preset == SCRIPT_PRESET_CPE:
        # Stock vulners only — do NOT load pack vulners.nse (duplicate id)
        scripts.append("vulners")
    elif preset == SCRIPT_PRESET_OFFLINE:
        if vulscan.is_file():
            scripts.append(str(vulscan))
        else:
            # Fall back to stock vulners if pack missing
            scripts.append("vulners")
    else:
        # full
        scripts.append("vuln")  # includes stock vulners + noisy http-* checks
        if vulscan.is_file():
            scripts.append(str(vulscan))
        if http_vr.is_file():
            scripts.append(str(http_vr))

    if not scripts:
        return []
    return ["--script", ",".join(scripts)]


def _script_args_for_vuln() -> list[str]:
    """Back-compat: full preset script args."""
    from .options import SCRIPT_PRESET_FULL

    return _script_args_for_preset(SCRIPT_PRESET_FULL)


def _nmap_script_engine_failed(output: str | None) -> bool:
    text = (output or "").lower()
    return (
        "failed to initialize the script engine" in text
        or "duplicate script id" in text
        or ("nse:" in text and "quitting!" in text and "error" in text)
    )


def _run_nmap_streaming(
    session: Session,
    job_id: int | None,
    argv: list[str],
    *,
    timeout_sec: int,
) -> subprocess.CompletedProcess[str]:
    """Run nmap, stream stdout/stderr into Job log_lines for live tracking."""
    # -v helps progress; avoid double -v if already present
    run_argv = list(argv)
    if "-v" not in run_argv and "-vv" not in run_argv:
        # insert after binary
        run_argv.insert(1, "-v")

    logger.info("nmap argv: %s", " ".join(run_argv))
    _log(session, job_id, f"exec: {' '.join(run_argv)}", current="scanning")

    proc = subprocess.Popen(
        run_argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    collected: list[str] = []
    lock = threading.Lock()
    last_flush = time.time()
    pending: list[str] = []

    def flush_pending() -> None:
        nonlocal pending, last_flush
        if not pending:
            return
        batch = pending[:]
        pending = []
        last_flush = time.time()
        merge_job_details(
            session,
            job_id,
            status="running",
            current="scanning",
            summary=batch[-1][:200],
            log_lines=batch,
            extra={"phase": "scanning"},
        )
        touch_worker_heartbeat()

    def reader() -> None:
        nonlocal last_flush, pending
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = (raw or "").rstrip()
            if not line:
                continue
            with lock:
                collected.append(line)
                # keep memory bounded
                if len(collected) > 400:
                    del collected[:-200]
                # skip extremely noisy timing lines occasionally
                pending.append(line[:500])
                if len(pending) >= 8 or (time.time() - last_flush) >= 3.0:
                    flush_pending()
        with lock:
            flush_pending()

    t = threading.Thread(target=reader, name="nmap-stdout", daemon=True)
    t.start()
    try:
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        t.join(timeout=5)
        raise
    t.join(timeout=30)
    with lock:
        out = "\n".join(collected)
    return subprocess.CompletedProcess(
        args=run_argv,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out,
        stderr="",
    )


def run_nmap_scan(
    session: Session,
    *,
    run_id: int,
    job_id: int | None = None,
    use_syn: bool | None = None,
    vuln_scripts: bool = False,
    script_preset: str | None = None,
    timing: int | None = None,
    top_ports: int | None = None,
    include_udp: bool = False,
    port_list: str | None = None,
    scan_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one NmapScanRun. Intended for celery-worker-nmap only.

    *use_syn*: ``None`` inherits integration config; ``True``/``False`` force.
    Curated options: *script_preset*, *timing*, *top_ports*, *include_udp*,
    *port_list* (or pass *scan_options* dict).
    """
    from .options import (
        SCRIPT_PRESET_NONE,
        normalize_script_preset,
        parse_scan_options,
        preset_wants_scripts,
    )

    touch_worker_heartbeat()
    run = session.get(NmapScanRun, run_id)
    if not run:
        return {"status": "error", "message": "run not found"}

    # Merge curated options (explicit kwargs win over scan_options / legacy bool)
    opts_in: dict[str, Any] = dict(scan_options or {})
    if script_preset is not None:
        opts_in["script_preset"] = script_preset
    elif "script_preset" not in opts_in:
        opts_in["script_preset"] = (
            normalize_script_preset(None, vuln_scripts_fallback=bool(vuln_scripts))
        )
    if timing is not None:
        opts_in["timing"] = timing
    if top_ports is not None:
        opts_in["top_ports"] = top_ports
    if include_udp:
        opts_in["include_udp"] = True
    if port_list is not None:
        opts_in["port_list"] = port_list
    if use_syn is not None:
        opts_in["use_syn"] = use_syn
    scan_opts = parse_scan_options(opts_in)
    effective_preset = scan_opts["script_preset"]
    want_scripts = preset_wants_scripts(effective_preset)

    integration = session.get(Integration, run.integration_id)
    if not integration or not integration.enabled:
        run.status = "failed"
        run.error = "integration missing or disabled"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=run.error,
            log_line=stamp_line(run.error),
            extra={"error": run.error, "run_id": run_id},
        )
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
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=run.error,
            log_line=stamp_line(run.error),
            extra={"error": run.error},
        )
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
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=run.error,
            log_line=stamp_line(run.error),
            extra={"error": run.error},
        )
        return {"status": "failed", "error": run.error}

    pack = vuln_pack_status()
    # Scripts only on deep; pack required for offline/full (cpe may use stock vulners)
    want_vuln = (
        want_scripts
        and run.intensity == INTENSITY_DEEP
        and (
            pack.get("ready")
            or effective_preset in ("cpe",)  # stock vulners needs no pack
        )
    )
    if want_scripts and run.intensity != INTENSITY_DEEP:
        want_vuln = False
        effective_preset = SCRIPT_PRESET_NONE
        logger.info("vuln scripts ignored — intensity %s is not deep", run.intensity)
    elif want_scripts and not want_vuln:
        logger.info(
            "vuln scripts requested but pack not ready or intensity not deep "
            "(ready=%s intensity=%s preset=%s)",
            pack.get("ready"),
            run.intensity,
            effective_preset,
        )
        effective_preset = SCRIPT_PRESET_NONE

    lock_key = ",".join(sorted(ok_targets))[:200]
    holder = f"run:{run_id}:job:{job_id}"
    if not try_acquire_lock("scan", lock_key, holder=holder):
        run.status = "failed"
        run.error = "another scan holds the lock for these targets"
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=run.error,
            log_line=stamp_line(run.error),
            extra={"error": run.error},
        )
        return {"status": "failed", "error": run.error}

    data_root = os.environ.get("DATA_ROOT", "/data")
    out_path = run_artifact_path(run_id, data_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run.status = "running"
    run.started_at = datetime.utcnow()
    run.error = None
    session.add(run)
    session.commit()
    _log(
        session,
        job_id,
        f"Starting {run.intensity} scan · targets={', '.join(ok_targets[:8])}"
        + ("…" if len(ok_targets) > 8 else ""),
        current="preparing",
        summary=f"nmap {run.intensity} · {len(ok_targets)} target(s)",
        extra={
            "run_id": run_id,
            "targets": ok_targets,
            "intensity": run.intensity,
            "phase": "preparing",
        },
    )
    set_progress(
        job_id or 0,
        {"phase": "scanning", "run_id": run_id, "targets": ok_targets},
    )

    try:
        from .config import parse_nmap_config

        cfg = parse_nmap_config(integration)
        if use_syn is None:
            want_syn = bool(cfg.get("use_syn"))
        else:
            want_syn = bool(use_syn)
        effective_syn, syn_note = resolve_use_syn(want_syn)
        if syn_note:
            logger.warning("%s", syn_note)
            _log(session, job_id, syn_note, current="preparing")

        def build_argv(*, with_syn: bool) -> list[str]:
            argv = build_nmap_argv(
                run.intensity,
                ok_targets,
                output_xml=str(out_path),
                skip_dns=bool(cfg.get("skip_dns", True)),
                use_syn=with_syn,
                include_udp=bool(scan_opts.get("include_udp")),
                vuln_scripts=False,  # handled below with pack-aware presets
                top_ports=int(scan_opts.get("top_ports") or 100),
                timing=scan_opts.get("timing"),
                port_list=scan_opts.get("port_list"),
            )
            if want_vuln and bool(cfg.get("vuln_enabled")):
                argv.extend(_script_args_for_preset(effective_preset))
            return argv

        timeout_sec = int(os.environ.get("PIHERDER_NMAP_TIMEOUT_SEC", "7200"))
        argv = build_argv(with_syn=effective_syn)
        try:
            proc = _run_nmap_streaming(
                session, job_id, argv, timeout_sec=timeout_sec
            )
        except subprocess.TimeoutExpired:
            run.status = "failed"
            run.error = "nmap timed out"
            run.finished_at = datetime.utcnow()
            session.add(run)
            session.commit()
            merge_job_details(
                session,
                job_id,
                status="failed",
                current="failed",
                summary="nmap timed out",
                log_line=stamp_line("ERROR: nmap timed out"),
                extra={"error": "nmap timed out", "run_id": run_id},
            )
            return {"status": "failed", "error": "nmap timed out"}

        touch_worker_heartbeat()

        # If SYN still failed for privileges, retry once with -sT.
        combined_err = (proc.stdout or "") + (proc.stderr or "")
        if (
            effective_syn
            and not out_path.is_file()
            and is_root_required_error(combined_err)
        ):
            _log(
                session,
                job_id,
                "SYN scan refused privileges; retrying with TCP connect (-sT)",
                current="retry_connect",
            )
            if out_path.is_file():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            effective_syn = False
            syn_note = (
                "SYN (-sS) refused by nmap (needs root); retried with TCP connect (-sT)."
            )
            proc = _run_nmap_streaming(
                session,
                job_id,
                build_argv(with_syn=False),
                timeout_sec=timeout_sec,
            )
            touch_worker_heartbeat()

        if not out_path.is_file():
            stderr = (proc.stdout or proc.stderr or "")[:2000]
            run.status = "failed"
            run.error = f"nmap produced no XML (exit={proc.returncode}): {stderr}"
            run.finished_at = datetime.utcnow()
            session.add(run)
            session.commit()
            merge_job_details(
                session,
                job_id,
                status="failed",
                current="failed",
                summary=run.error[:200],
                log_line=stamp_line(f"ERROR: {run.error[:400]}"),
                extra={"error": run.error, "run_id": run_id},
            )
            return {"status": "failed", "error": run.error}

        combined_out = (proc.stdout or "") + (proc.stderr or "")
        if _nmap_script_engine_failed(combined_out):
            # XML may still be written empty / partial — treat NSE init crash as failure
            err = combined_out.strip().splitlines()
            err_snip = "\n".join(err[-12:])[:1500]
            run.status = "failed"
            run.error = f"nmap NSE failed (exit={proc.returncode}): {err_snip}"
            run.finished_at = datetime.utcnow()
            try:
                run.artifact_path = str(out_path.relative_to(Path(data_root)))
            except ValueError:
                run.artifact_path = str(out_path)
            session.add(run)
            session.commit()
            merge_job_details(
                session,
                job_id,
                status="failed",
                current="failed",
                summary=run.error[:200],
                log_line=stamp_line(f"ERROR: {run.error[:400]}"),
                extra={"error": run.error, "run_id": run_id},
            )
            return {"status": "failed", "error": run.error}

        _log(session, job_id, "Parsing XML…", current="parsing")
        xml_text = out_path.read_text(encoding="utf-8", errors="replace")
        hosts = parse_nmap_xml(xml_text)
        _log(
            session,
            job_id,
            f"Parsed {len(hosts)} host record(s); upserting devices…",
            current="upserting",
        )
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

        try:
            rel = str(out_path.relative_to(Path(data_root)))
        except ValueError:
            rel = str(out_path)

        # exit 0 = ok; exit 1 = host down / no ports often still useful XML
        run.status = "success" if proc.returncode in (0, 1) else "failed"
        if proc.returncode not in (0, 1) and not hosts:
            run.status = "failed"
            run.error = (proc.stderr or proc.stdout or f"nmap exit {proc.returncode}")[
                :2000
            ]
        run.hosts_up = hosts_up
        run.hosts_total = len(hosts)
        run.ports_open = ports_open
        if rejected:
            summary = {**summary, "rejected_targets": rejected[:20]}
        if syn_note:
            summary = {**summary, "scan_note": syn_note, "used_syn": effective_syn}
        elif want_syn:
            summary = {**summary, "used_syn": effective_syn}
        summary = {
            **summary,
            "script_preset": effective_preset if want_vuln else SCRIPT_PRESET_NONE,
            "timing": scan_opts.get("timing"),
            "include_udp": bool(scan_opts.get("include_udp")),
            "top_ports": scan_opts.get("top_ports"),
            "port_list": scan_opts.get("port_list"),
        }
        run.summary_json = json.dumps(summary, separators=(",", ":"))
        run.artifact_path = rel
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()

        final = "success" if run.status == "success" else "failed"
        result_summary = (
            f"{run.intensity}: {hosts_up} up / {len(hosts)} hosts · "
            f"{ports_open} open ports · exit {proc.returncode}"
        )
        merge_job_details(
            session,
            job_id,
            status=final,
            current="completed" if final == "success" else "failed",
            summary=result_summary,
            log_line=stamp_line(result_summary),
            extra={
                "run_id": run_id,
                "hosts_up": hosts_up,
                "hosts_total": len(hosts),
                "ports_open": ports_open,
                "upsert": summary,
                "error": run.error,
                "result_snippet": result_summary,
                "phase": final,
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
    except Exception as e:
        logger.exception("nmap scan failed")
        run.status = "failed"
        run.error = str(e)[:2000]
        run.finished_at = datetime.utcnow()
        session.add(run)
        session.commit()
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=str(e)[:200],
            log_line=stamp_line(f"ERROR: {e}"),
            extra={"error": str(e)[:500], "run_id": run_id},
        )
        return {"status": "failed", "error": str(e)[:500]}
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
    use_syn: bool | None = None,
    script_preset: str | None = None,
    timing: int | None = None,
    top_ports: int | None = None,
    include_udp: bool = False,
    port_list: str | None = None,
    scan_options: dict[str, Any] | None = None,
) -> tuple[Job, NmapScanRun]:
    """Create Job + NmapScanRun and dispatch to Celery queue ``nmap``.

    *use_syn*: ``None`` inherits integration Prefer SYN; bool forces for this run.
    Curated options via kwargs or *scan_options* (see ``options.parse_scan_options``).
    """
    from ...celery_app import celery
    from .options import dump_scan_options, parse_scan_options

    opts_in: dict[str, Any] = dict(scan_options or {})
    if script_preset is not None:
        opts_in["script_preset"] = script_preset
    elif "script_preset" not in opts_in:
        opts_in["script_preset"] = "full" if vuln_scripts else "none"
    if timing is not None:
        opts_in["timing"] = timing
    if top_ports is not None:
        opts_in["top_ports"] = top_ports
    if include_udp:
        opts_in["include_udp"] = True
    if port_list is not None:
        opts_in["port_list"] = port_list
    if use_syn is not None:
        opts_in["use_syn"] = use_syn
    elif "use_syn" not in opts_in:
        opts_in["use_syn"] = None
    opts = dump_scan_options(parse_scan_options(opts_in))

    job = Job(
        server_id=None,
        job_type=f"nmap_{intensity}" if intensity != "deep" else "nmap_host_deep",
        status="pending",
        details=json.dumps(
            {
                "current": "queued",
                "summary": f"Queued {intensity} scan on nmap worker",
                "intensity": intensity,
                "user_id": user_id,
                "vuln_scripts": bool(opts.get("vuln_scripts")),
                "script_preset": opts.get("script_preset"),
                "use_syn": opts.get("use_syn"),
                "timing": opts.get("timing"),
                "top_ports": opts.get("top_ports"),
                "include_udp": bool(opts.get("include_udp")),
                "port_list": opts.get("port_list"),
                "schedule_id": schedule_id,
                "log_lines": [
                    stamp_line(
                        f"Queued {intensity} scan"
                        + (
                            f" · preset={opts.get('script_preset')}"
                            if opts.get("vuln_scripts")
                            else ""
                        )
                    )
                ],
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

    task_kwargs: dict[str, Any] = {
        "run_id": run.id,
        "job_id": job.id,
        "vuln_scripts": bool(opts.get("vuln_scripts")),
        "script_preset": opts.get("script_preset"),
        "timing": opts.get("timing"),
        "top_ports": opts.get("top_ports"),
        "include_udp": bool(opts.get("include_udp")),
        "port_list": opts.get("port_list"),
    }
    if opts.get("use_syn") is not None:
        task_kwargs["use_syn"] = opts["use_syn"]
    async_result = celery.send_task(
        "app.tasks.nmap_scan",
        kwargs=task_kwargs,
        queue="nmap",
    )
    job.celery_task_id = async_result.id
    # attach run_id in details for Jobs UI
    merge_job_details(
        session,
        job.id,
        status="pending",
        current="queued",
        summary=f"Queued {intensity} scan · run #{run.id}",
        log_line=stamp_line(f"run_id={run.id} task={async_result.id}"),
        extra={"run_id": run.id, "scan_options": opts},
    )
    session.refresh(job)
    return job, run
