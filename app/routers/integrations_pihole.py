"""Pi-hole integration detail + DNS / bulk action routes."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Integration, Server, User
from ..security.auth import get_operator_user
from ..services import jobs as job_service
from ..services.integrations import pihole as ph
from ..services.integrations import poll as poll_svc
from ..services.integrations import registry as reg
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)

async def render_pihole_detail(request, session, user, integration: Integration):
    status = reg.parse_last_status(integration)
    tab = (request.query_params.get("tab") or "overview").strip().lower()
    hosts: list = []
    cnames: list = []
    dns_error = ""
    if tab in ("dns", "cname") and reg.pihole_password(integration):
        try:
            sess = ph.login(
                integration.base_url,
                reg.pihole_password(integration),
                tls_verify=reg.tls_verify(integration),
            )
            try:
                if tab == "dns":
                    hosts = ph.list_dns_hosts(sess)
                else:
                    cnames = ph.list_dns_cnames(sess)
            finally:
                ph.logout(sess)
        except Exception as e:
            dns_error = str(e)[:300]
    # All pihole instances for fan-out context
    others = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.id != integration.id
    ]
    servers = list(
        session.exec(select(Server).order_by(Server.sort_order, Server.name)).all()
    )
    docker_options: dict[int, list] = {
        s.id: reg.docker_inventory_options(session, s.id) for s in servers
    }
    host_binding = None
    hb_rows = reg.list_bindings(
        session, integration_id=integration.id, role=reg.ROLE_PIHOLE_HOST
    )
    if hb_rows:
        b = hb_rows[0]
        srv = session.get(Server, b.server_id)
        host_binding = {
            "id": b.id,
            "server_id": b.server_id,
            "server_name": srv.name if srv else f"#{b.server_id}",
            "docker_project": b.docker_project or "",
            "docker_container": b.docker_container or "",
        }
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_pihole_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "status": status,
            "is_primary": reg.is_pihole_primary(integration),
            "tab": tab,
            "hosts": hosts,
            "cnames": cnames,
            "dns_error": dns_error,
            "other_piholes": others,
            "servers": servers,
            "host_binding": host_binding,
            "docker_options_json": json.dumps(
                {str(k): v for k, v in docker_options.items()}
            ),
            "admin_url": ph.admin_url(integration.base_url),
            "gravity_url": ph.admin_url(integration.base_url, "/gravity"),
            "system_url": ph.admin_url(integration.base_url, "/settings/system"),
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


def _fanout_pihole_dns(
    session: Session,
    *,
    op: str,
    kind: str,
    ip: str = "",
    domain: str = "",
    target: str = "",
    scope: str = "all",
    source_id: int | None = None,
) -> list[dict]:
    """Apply DNS mutation with scope: all | this | secondaries."""
    from ..services.dns_fabric import fanout_pihole_dns

    return fanout_pihole_dns(
        session,
        op=op,
        kind=kind,
        ip=ip,
        domain=domain,
        target=target,
        scope=scope,
        source_id=source_id,
    )


def _wants_async_json(request: Request) -> bool:
    if (request.headers.get("X-PiHerder-Async") or "").strip() == "1":
        return True
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept and "text/html" not in accept


def _run_pihole_action_job(job_id: int, audit_id: int, target_ids: list[int], action: str) -> None:
    """Background worker: gravity / restartdns / flush_network on target integrations."""
    from datetime import datetime as dt

    from ..models import Job as JobModel

    labels = {
        "gravity": "Update Gravity",
        "restartdns": "Restart DNS",
        "flush_network": "Flush network table",
    }
    label = labels.get(action, action)
    try:
        with job_service._get_fresh_session() as s:
            job = s.get(JobModel, job_id)
            if not job or job.status == "cancelled":
                return
            job.status = "running"
            job.started_at = dt.utcnow()
            job_service._merge_job_details(
                job,
                current=f"Running {label}…",
                log_line=f"Starting {label} on {len(target_ids)} instance(s)…",
                done=False,
            )
            s.add(job)
            s.commit()

            targets = []
            for tid in target_ids:
                row = reg.get_integration(s, tid)
                if row and row.enabled and row.type == reg.TYPE_PIHOLE:
                    targets.append(
                        {
                            "id": row.id,
                            "name": row.name,
                            "base_url": row.base_url,
                            "password": reg.pihole_password(row),
                            "tls_verify": reg.tls_verify(row),
                        }
                    )

        results = []
        for r in targets:
            job_service._flush_job_progress(
                job_id, f"{r['name']}…", f"→ {r['name']}: {label}…"
            )
            item = {"name": r["name"], "ok": False, "error": ""}
            try:
                sess = ph.login(
                    r["base_url"],
                    r["password"],
                    tls_verify=r["tls_verify"],
                )
                try:
                    out = ph.run_action(sess, action)
                    item["ok"] = True
                    tail = f" · {(out or '')[:120]}" if out else ""
                    job_service._flush_job_progress(
                        job_id, f"{r['name']} ok", f"  {r['name']}: ok{tail}"
                    )
                finally:
                    ph.logout(sess)
            except Exception as e:
                item["error"] = str(e)[:200]
                job_service._flush_job_progress(
                    job_id,
                    f"{r['name']} failed",
                    f"  {r['name']}: failed — {item['error']}",
                )
            results.append(item)

        ok_n = sum(1 for x in results if x["ok"])
        fail = [x for x in results if not x["ok"]]
        if not results:
            status = "failed"
            summary = f"{label}: no enabled targets"
        elif not fail:
            status = "success"
            summary = f"{label}: {ok_n}/{len(results)} ok"
        else:
            status = "failed"
            summary = f"{label}: {ok_n}/{len(results)} ok · " + "; ".join(
                f"{f['name']}: {f['error']}" for f in fail
            )[:200]
        job_service._finish(
            audit_id, job_id, status, summary, job_type="pihole_action"
        )
    except Exception as e:
        logger.exception("pihole action job %s: %s", job_id, e)
        try:
            job_service._finish(
                audit_id,
                job_id,
                "failed",
                f"Pi-hole action failed: {str(e)[:300]}",
                job_type="pihole_action",
            )
        except Exception:
            pass


@router.post("/integrations/{integration_id}/pihole/dns-host")
async def pihole_dns_host(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    action: str = Form("add"),
    ip: str = Form(""),
    domain: str = Form(""),
    scope: str = Form("all"),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    ip = (ip or "").strip()
    domain = (domain or "").strip()
    if not ip or not domain:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="dns",
            error="invalid",
            detail="IP and domain required",
        )
    op = "add" if action == "add" else "delete"
    # Adds always fan out to all; deletes honour scope (this / secondaries / all)
    sc = "all" if op == "add" else (scope or "all")
    results = _fanout_pihole_dns(
        session,
        op=op,
        kind="host",
        ip=ip,
        domain=domain,
        scope=sc,
        source_id=integration_id,
    )
    if not results:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="dns",
            error="no_targets",
            detail="No matching Pi-hole instances for that scope",
        )
    ok_n = sum(1 for r in results if r["ok"])
    fail = [r for r in results if not r["ok"]]
    _audit(
        session,
        user,
        f"pihole_dns_host_{op}",
        details=f"{ip} {domain} scope={sc} ok={ok_n}/{len(results)}",
        status="success" if not fail else "partial",
    )
    detail = f"{ok_n}/{len(results)} instances ({sc})"
    if fail:
        detail += " · " + "; ".join(f"{f['name']}: {f['error']}" for f in fail)[:180]
    return _redirect(
        f"/integrations/{integration_id}",
        tab="dns",
        msg="dns_ok" if not fail else "dns_partial",
        detail=detail,
    )


@router.post("/integrations/{integration_id}/pihole/dns-cname")
async def pihole_dns_cname(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    action: str = Form("add"),
    domain: str = Form(""),
    target: str = Form(""),
    scope: str = Form("all"),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    domain = (domain or "").strip()
    target = (target or "").strip()
    if not domain or not target:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="cname",
            error="invalid",
            detail="domain and target required",
        )
    op = "add" if action == "add" else "delete"
    sc = "all" if op == "add" else (scope or "all")
    results = _fanout_pihole_dns(
        session,
        op=op,
        kind="cname",
        domain=domain,
        target=target,
        scope=sc,
        source_id=integration_id,
    )
    if not results:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="cname",
            error="no_targets",
            detail="No matching Pi-hole instances for that scope",
        )
    ok_n = sum(1 for r in results if r["ok"])
    fail = [r for r in results if not r["ok"]]
    _audit(
        session,
        user,
        f"pihole_dns_cname_{op}",
        details=f"{domain} -> {target} scope={sc} ok={ok_n}/{len(results)}",
        status="success" if not fail else "partial",
    )
    detail = f"{ok_n}/{len(results)} instances ({sc})"
    if fail:
        detail += " · " + "; ".join(f"{f['name']}: {f['error']}" for f in fail)[:180]
    return _redirect(
        f"/integrations/{integration_id}",
        tab="cname",
        msg="dns_ok" if not fail else "dns_partial",
        detail=detail,
    )


@router.post("/integrations/{integration_id}/pihole/action")
async def pihole_action(
    request: Request,
    integration_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    action: Optional[str] = Form(None),
    all_instances: Optional[str] = Form(None),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    # Form() may miss multi-button submitter after confirm/async; also read raw form
    act = (action or "").strip().lower()
    if not act:
        try:
            form = await request.form()
            act = str(form.get("action") or "").strip().lower()
        except Exception:
            act = ""
    logger.info(
        "pihole action request integration=%s action=%r all=%r user=%s async=%s",
        integration_id,
        act,
        all_instances,
        getattr(user, "id", None),
        _wants_async_json(request),
    )
    if act not in ("gravity", "restartdns", "flush_network"):
        logger.warning(
            "pihole action rejected integration=%s action=%r user=%s",
            integration_id,
            action,
            getattr(user, "id", None),
        )
        if _wants_async_json(request):
            return JSONResponse(
                {
                    "detail": "Missing or unknown action (expected gravity, restartdns, or flush_network)",
                },
                status_code=400,
            )
        return _redirect(
            f"/integrations/{integration_id}",
            tab="actions",
            error="invalid",
            detail="unknown action",
        )
    targets = [integration]
    if all_instances in ("on", "1", "true"):
        targets = [
            r
            for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
            if r.enabled
        ]
    target_ids = [int(r.id) for r in targets if r.id is not None]
    labels = {
        "gravity": "Update Gravity",
        "restartdns": "Restart DNS",
        "flush_network": "Flush network table",
    }
    label = labels.get(act, act)
    queue_msg = f"{label} queued for {len(target_ids)} instance(s)…"
    job, audit = job_service._create_queued_job_with_audit(
        session,
        server_id=None,
        job_type="pihole_action",
        queue_message=queue_msg,
        user_id=user.id,
        audit_details=f"Job #{{job_id}} · {label} · {len(target_ids)} target(s)",
        action=act,
        integration_id=integration_id,
        target_ids=target_ids,
    )
    background_tasks.add_task(
        _run_pihole_action_job, job.id, audit.id, target_ids, act
    )
    if _wants_async_json(request):
        return JSONResponse(
            {
                "job_id": job.id,
                "poll_url": f"/jobs/{job.id}",
                "status": "pending",
                "action": act,
                "detail": queue_msg,
            }
        )
    return _redirect(
        f"/integrations/{integration_id}",
        tab="actions",
        msg="action_queued",
        detail=f"Job #{job.id} · {label}",
    )


@router.post("/integrations/{integration_id}/pihole/host-bind")
async def pihole_host_bind(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    docker_project: str = Form(""),
    docker_container: str = Form(""),
):
    """Link a Pi-hole integration to a fleet host (optional Docker scope)."""
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    # One host link per Pi-hole integration — replace prior rows
    for old in reg.list_bindings(
        session, integration_id=integration_id, role=reg.ROLE_PIHOLE_HOST
    ):
        session.delete(old)
    session.commit()
    try:
        reg.set_binding(
            session,
            integration_id=integration_id,
            server_id=server_id,
            external_id="instance",
            role=reg.ROLE_PIHOLE_HOST,
            docker_project=docker_project or None,
            docker_container=docker_container or None,
            external_label=integration.name,
            external_meta={"scope": "docker" if (docker_project or "").strip() else "host"},
            last_state="up",
        )
        _audit(
            session,
            user,
            "pihole_host_bound",
            server_id=server_id,
            details=f"pihole={integration_id}",
        )
        return _redirect(
            f"/integrations/{integration_id}", tab="host", msg="host_linked"
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="host",
            error="bind_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/pihole/host-unbind")
async def pihole_host_unbind(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    binding_id: int = Form(...),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    ok = reg.clear_binding(
        session, integration_id=integration_id, server_id=0, binding_id=binding_id
    )
    if ok:
        _audit(session, user, "pihole_host_unbound", details=f"binding={binding_id}")
    return _redirect(f"/integrations/{integration_id}", tab="host", msg="host_cleared")

