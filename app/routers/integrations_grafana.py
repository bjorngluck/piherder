"""Grafana create forms + detail + preferred-name (shared integrations router)."""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import Integration, Server, User
from ..security.auth import get_current_user, get_operator_user
from ..services.integrations import grafana as gf
from ..services.integrations import poll as poll_svc
from ..services.integrations import registry as reg
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)

@router.get("/integrations/new/grafana", response_class=HTMLResponse)
async def grafana_new_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_grafana_form.html",
        context={
            "title": "Add Grafana",
            "user": user,
            "mode": "new",
            "integration": None,
            "form": {
                "name": "Grafana",
                "base_url": "",
                "poll_interval_sec": reg.DEFAULT_GRAFANA_POLL_SEC,
                "tls_verify": True,
                "enabled": True,
                "query_template": reg.DEFAULT_QT_HOST,
                "query_template_container_host": reg.DEFAULT_QT_CONTAINER_HOST,
                "query_template_container": reg.DEFAULT_QT_CONTAINER,
                "query_template_logs": reg.DEFAULT_QT_LOGS,
            },
            "has_key": False,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/grafana")
async def grafana_create(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form("Grafana"),
    base_url: str = Form(...),
    api_key: str = Form(""),
    poll_interval_sec: int = Form(reg.DEFAULT_GRAFANA_POLL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form("on"),
    query_template: str = Form(""),
    query_template_container_host: str = Form(""),
    query_template_container: str = Form(""),
    query_template_logs: str = Form(""),
):
    try:
        row = reg.create_grafana(
            session,
            name=name,
            base_url=base_url,
            api_key=api_key,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls_verify in ("on", "1", "true"),
            enabled=enabled in ("on", "1", "true") if enabled is not None else True,
            query_template=query_template,
            query_template_container_host=query_template_container_host,
            query_template_container=query_template_container,
            query_template_logs=query_template_logs,
        )
        poll_svc.poll_integration(row.id, notify=False)
        _audit(
            session,
            user,
            "integration_created",
            details=f"grafana id={row.id} name={row.name}",
        )
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect(
            "/integrations/new/grafana", error="invalid", detail=str(e)[:200]
        )
    except Exception as e:
        logger.exception("create grafana failed")
        return _redirect(
            "/integrations/new/grafana", error="save_failed", detail=str(e)[:200]
        )



async def render_grafana_detail(
    request: Request,
    session: Session,
    user: User,
    integration: Integration,
):
    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    bindings = reg.list_bindings(
        session, integration_id=integration.id, role=reg.ROLE_DASHBOARD
    )
    dashboards = reg.dashboards_from_cache(integration)
    status = reg.parse_last_status(integration)
    server_by_id = {s.id: s for s in servers}
    uid_counts: dict[str, int] = {}
    for b in bindings:
        uid = (b.external_id or "").strip()
        if uid:
            uid_counts[uid] = uid_counts.get(uid, 0) + 1
    bound_rows = []
    for b in bindings:
        srv = server_by_id.get(b.server_id)
        chip = reg._grafana_chip_dict(integration, b, server=srv)
        chip["server_name"] = srv.name if srv else f"#{b.server_id}"
        chip["uid"] = b.external_id
        uid = (b.external_id or "").strip()
        chip["same_uid_count"] = uid_counts.get(uid, 1)
        bound_rows.append(chip)

    def _sort_key(c: dict) -> tuple:
        return (
            (c.get("server_name") or "").lower(),
            (c.get("location") or "").lower(),
            (c.get("label") or "").lower(),
            c.get("id") or 0,
        )

    by_kind = {
        reg.GRAFANA_KIND_METRICS: [],
        reg.GRAFANA_KIND_CONTAINERS: [],
        reg.GRAFANA_KIND_LOGS: [],
    }
    for row in bound_rows:
        # chip dict already uses binding_grafana_kind (docker scope → containers)
        k = reg.normalize_grafana_kind(row.get("kind"))
        by_kind.setdefault(k, []).append(row)
    for k in by_kind:
        by_kind[k].sort(key=_sort_key)

    tab = (request.query_params.get("tab") or "metrics").strip().lower()
    if tab not in ("metrics", "containers", "logs", "inventory"):
        tab = "metrics"

    # Prefill bind form for clone/new only (rename is inline on each row)
    prefill = {
        "mode": (request.query_params.get("mode") or "").strip().lower(),  # clone|""
        "server_id": (request.query_params.get("server_id") or "").strip(),
        "kind": reg.normalize_grafana_kind(request.query_params.get("kind") or tab),
        "external_id": (request.query_params.get("external_id") or "").strip(),
        "docker_project": (request.query_params.get("docker_project") or "").strip(),
        "docker_container": (request.query_params.get("docker_container") or "").strip(),
        "display_name": (request.query_params.get("display_name") or "").strip(),
    }
    if prefill["mode"] != "clone":
        prefill["mode"] = ""

    docker_options: dict[int, list] = {}
    for s in servers:
        docker_options[s.id] = reg.docker_inventory_options(session, s.id)

    # Inventory rows: Grafana dashboards + preferred name + binding counts
    pref_map = reg.preferred_display_names(integration)
    uid_bind_counts: dict[str, int] = {}
    for b in bindings:
        u = (b.external_id or "").strip()
        if u:
            uid_bind_counts[u] = uid_bind_counts.get(u, 0) + 1
    inventory_rows = []
    for d in dashboards:
        u = str(d.get("uid") or "").strip()
        inventory_rows.append(
            {
                "uid": u,
                "title": d.get("title") or d.get("name") or u,
                "folder_title": d.get("folder_title") or "",
                "url": d.get("url") or "",
                "preferred_name": pref_map.get(u) or "",
                "binding_count": uid_bind_counts.get(u, 0),
            }
        )
    # Preferred names for UIDs not currently in inventory (orphan config)
    known_uids = {r["uid"] for r in inventory_rows if r["uid"]}
    for u, name in sorted(pref_map.items()):
        if u not in known_uids:
            inventory_rows.append(
                {
                    "uid": u,
                    "title": "(not in last poll inventory)",
                    "folder_title": "",
                    "url": "",
                    "preferred_name": name,
                    "binding_count": uid_bind_counts.get(u, 0),
                }
            )

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_grafana_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "can_mutate": _can_mutate(user),
            "servers": servers,
            "dashboards": dashboards,
            "inventory_rows": inventory_rows,
            "bindings": bound_rows,
            "bindings_metrics": by_kind.get(reg.GRAFANA_KIND_METRICS) or [],
            "bindings_containers": by_kind.get(reg.GRAFANA_KIND_CONTAINERS) or [],
            "bindings_logs": by_kind.get(reg.GRAFANA_KIND_LOGS) or [],
            "counts": {
                "metrics": len(by_kind.get(reg.GRAFANA_KIND_METRICS) or []),
                "containers": len(by_kind.get(reg.GRAFANA_KIND_CONTAINERS) or []),
                "logs": len(by_kind.get(reg.GRAFANA_KIND_LOGS) or []),
                "all": len(bound_rows),
            },
            "tab": tab,
            "prefill": prefill,
            "status": status,
            "has_key": reg.has_credentials(integration),
            "poll_interval_sec": reg.poll_interval_sec(integration),
            "tls_verify": reg.tls_verify(integration),
            "query_template": reg.query_template(integration),
            "query_template_container_host": reg.query_template_container_host(integration),
            "query_template_container": reg.query_template_container(integration),
            "query_template_logs": reg.query_template_logs(integration),
            "docker_options_json": json.dumps(docker_options),
            "open_url": gf.open_grafana_url(integration.base_url),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )



@router.post("/integrations/{integration_id}/preferred-name")
async def set_grafana_preferred_name(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    uid: str = Form(...),
    display_name: str = Form(""),
):
    """Set preferred PiHerder chip name for a Grafana dashboard UID (Inventory tab)."""
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    if integration.type != reg.TYPE_GRAFANA:
        raise HTTPException(400, "Preferred names are for Grafana integrations")
    try:
        updated = reg.apply_grafana_preferred_name(
            session,
            integration_id=integration_id,
            uid=uid,
            display_name=display_name,
        )
        name_bit = (display_name or "").strip()
        _audit(
            session,
            user,
            "integration_preferred_name",
            details=(
                f"integration={integration_id} uid={(uid or '')[:80]!r}"
                f" preferred={name_bit[:80]!r} bindings_synced={len(updated)}"
            ),
        )
        if name_bit:
            detail = f"{name_bit[:60]!r} · {len(updated)} binding(s) synced"
        else:
            detail = f"cleared for UID · {len(updated)} binding(s) follow Grafana title"
        return _redirect(
            f"/integrations/{integration_id}",
            msg="preferred_saved",
            detail=detail,
            scope="dashboard",
            tab="inventory",
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            error="binding_failed",
            detail=str(e)[:200],
            scope="dashboard",
            tab="inventory",
        )
    except Exception as e:
        logger.exception("grafana preferred name failed")
        return _redirect(
            f"/integrations/{integration_id}",
            error="binding_failed",
            detail=str(e)[:200],
            scope="dashboard",
            tab="inventory",
        )



