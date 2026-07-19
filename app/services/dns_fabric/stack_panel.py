"""Runtime stack side panel (FEATURE_PLAN_RUNTIME_TOPOLOGY P1).

One stack at a time: containers + ports + role hint + Kuma/mute status.
No edges yet (P2/P3). Reads Docker inventory from DB only (no SSH).
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from ...models import IntegrationBinding, Server, ServiceDnsRecord
from .. import docker_inventory as inv_svc
from . import kuma_coverage as cov


# Role heuristics for display only (not stored in P1).
_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data",
        (
            "postgres",
            "postgresql",
            "timescaledb",
            "mysql",
            "mariadb",
            "mongo",
            "mongodb",
            "influx",
            "elasticsearch",
            "opensearch",
            "meilisearch",
            "pgbouncer",
            "database",
            "db",
        ),
    ),
    (
        "cache",
        ("redis", "valkey", "keydb", "memcached"),
    ),
    (
        "queue",
        ("rabbitmq", "nats", "kafka", "zookeeper", "celery", "worker", "beat", "rq"),
    ),
    (
        "edge",
        (
            "caddy",
            "nginx",
            "traefik",
            "haproxy",
            "npm",
            "nginx-proxy-manager",
            "cloudflared",
        ),
    ),
    (
        "tooling",
        ("adminer", "phpmyadmin", "pgadmin", "portainer", "watchtower"),
    ),
)


def _path_kuma_status(
    session: Session,
    *,
    fqdn: str | None,
    docker_project: str | None,
    label: str | None,
    backend_server_id: int | None,
) -> str | None:
    """covered | partial | none | n/a — path-level only, no dep scan."""
    kumas = cov.kuma_integrations_enabled(session)
    if not kumas:
        return "n/a"
    from ..integrations import registry as reg

    kuma_ids = {i.id for i in kumas if i.id is not None}
    binds = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.role.in_([reg.ROLE_SERVICE, reg.ROLE_SSH])
            )
        ).all()
    )
    binds = [b for b in binds if b.integration_id in kuma_ids]
    tokens = cov._tokens(fqdn, docker_project, label)
    best = 0
    for b in binds:
        if (b.role or "") == reg.ROLE_SSH:
            if backend_server_id and b.server_id == backend_server_id:
                best = max(best, 5)
            continue
        sc = cov._score_service_binding(
            b, tokens=tokens, docker_project=docker_project
        )
        if sc > best:
            best = sc
    if best >= 40:
        return "covered"
    if best >= 5:
        return "partial"
    return "none"


def guess_container_role(
    *,
    name: str,
    image: str,
    compose_service: str,
) -> str:
    """Heuristic role label for stack panel chips."""
    blob = " ".join(
        [
            cov._norm(name),
            cov._norm(compose_service),
            cov._norm(image),
            cov._norm(image.split("/")[-1].split(":")[0] if image else ""),
        ]
    )
    for role, needles in _ROLE_RULES:
        for n in needles:
            if n and n in blob:
                return role
    return "app"


def _find_project(
    inv: dict[str, Any] | None, project: str
) -> dict[str, Any] | None:
    """Exact project name match only (case-insensitive).

    Soft/substring matching conflated e.g. ``piherder`` with ``piherder-e2e``
    and polluted stack expand. Deploy identity stays the compose project name.
    """
    if not inv or not project:
        return None
    want = project.strip().lower()
    if not want:
        return None
    for p in inv.get("projects") or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if name.lower() == want:
            return p
    return None


def _list_projects_on_server(inv: dict[str, Any] | None) -> list[str]:
    """Compose projects for empty-state picker only.

    Skips label-only stubs (e.g. ephemeral ``-p`` test projects) — those stay
    on the Docker page. Fabric UI is about view groups inside a linked project.
    """
    out: list[str] = []
    for p in (inv or {}).get("projects") or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if p.get("label_only"):
            continue
        out.append(name)
    return out


def resolve_stack_target(
    session: Session,
    *,
    service_id: int | None = None,
    server_id: int | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Resolve which host/project (and optional path) the panel shows."""
    rec: ServiceDnsRecord | None = None
    if service_id is not None:
        rec = session.get(ServiceDnsRecord, int(service_id))
        if not rec:
            return {"ok": False, "error": "Service path not found", "code": "not_found"}

    sid = server_id
    proj = (project or "").strip() or None
    fqdn = None
    label = None
    path_kind = None
    kuma_path = None
    stack_deployment_id = None
    service_record_id = None

    if rec is not None:
        service_record_id = rec.id
        fqdn = rec.fqdn
        label = rec.label or rec.docker_project or rec.fqdn
        sid = sid or rec.backend_server_id
        proj = proj or (rec.docker_project or "").strip() or None
        stack_deployment_id = rec.stack_deployment_id
        try:
            from .core import build_access_path_for_record

            path = build_access_path_for_record(session, rec, persist_links=False)
            path_kind = path.get("path_kind")
            if not proj:
                proj = (path.get("docker_project") or "").strip() or None
        except Exception:
            pass
        # Lightweight path-level Kuma chip (no full fleet audit)
        try:
            kuma_path = _path_kuma_status(
                session,
                fqdn=rec.fqdn,
                docker_project=proj or rec.docker_project,
                label=rec.label,
                backend_server_id=rec.backend_server_id,
            )
        except Exception:
            kuma_path = None

    if sid is None:
        return {
            "ok": False,
            "error": "No backend host for this path",
            "code": "no_host",
        }

    server = session.get(Server, int(sid))
    if not server:
        return {"ok": False, "error": "Host not found", "code": "not_found"}

    inv = inv_svc.parse_inventory(server)
    inv_meta = inv_svc.inventory_meta(server)
    projects_available = _list_projects_on_server(inv)

    # If no project on record, try single-project host or FQDN token match
    if not proj and inv:
        if len(projects_available) == 1:
            proj = projects_available[0]
        elif fqdn:
            tokens = cov._tokens(fqdn, None, label)
            best = None
            best_score = 0
            for pname in projects_available:
                sc = 0
                pl = pname.lower()
                for t in tokens:
                    if t == pl:
                        sc += 30
                    elif t in pl or pl in t:
                        sc += 12
                if sc > best_score:
                    best_score = sc
                    best = pname
            if best and best_score >= 12:
                proj = best

    return {
        "ok": True,
        "service_id": service_record_id,
        "fqdn": fqdn,
        "label": label or (proj or server.name),
        "path_kind": path_kind,
        "kuma_path_coverage": kuma_path,
        "stack_deployment_id": stack_deployment_id,
        "server_id": server.id,
        "server_name": server.name,
        "project": proj,
        "projects_available": projects_available,
        "inventory_meta": inv_meta,
        "has_inventory": inv is not None,
        "inventory": inv,
        "server": server,
    }


def build_stack_panel(
    session: Session,
    *,
    service_id: int | None = None,
    server_id: int | None = None,
    project: str | None = None,
    visual_stack_id: int | str | None = "all",
) -> dict[str, Any]:
    """Build side-panel payload for one stack (DB inventory only).

    visual_stack_id: ``\"all\"`` (default) = whole compose project;
    ``\"main\"`` / ``None`` = Main only (unassigned); int = that visual stack.
    """
    # Normalize so UI never confuses Main (filter) with missing/default All
    if visual_stack_id is None or visual_stack_id == 0 or visual_stack_id == "":
        visual_stack_id = "main"
    resolved = resolve_stack_target(
        session,
        service_id=service_id,
        server_id=server_id,
        project=project,
    )
    if not resolved.get("ok"):
        return resolved

    server: Server = resolved["server"]
    inv = resolved.get("inventory")
    proj = resolved.get("project")
    inv_meta = resolved.get("inventory_meta") or {}

    # Kuma service binds for this host
    from ..integrations import registry as reg

    binds: list[IntegrationBinding] = []
    try:
        binds = list(
            session.exec(
                select(IntegrationBinding).where(
                    IntegrationBinding.server_id == server.id,
                    IntegrationBinding.role == reg.ROLE_SERVICE,
                )
            ).all()
        )
    except Exception:
        binds = []

    patterns = cov._mute_patterns(session)
    explicit_mute: set[str] = set()
    try:
        from ..app_settings import load_settings

        raw = load_settings().get("kuma_coverage_mute_keys") or "[]"
        import json

        parsed = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        if isinstance(parsed, list):
            explicit_mute = {str(x).strip() for x in parsed if str(x).strip()}
    except Exception:
        pass

    kumas = cov.kuma_integrations_enabled(session)
    default_integ = kumas[0].id if kumas else None
    has_kuma = bool(kumas)

    containers: list[dict[str, Any]] = []
    project_row = _find_project(inv, proj) if proj else None
    project_found = project_row is not None

    if project_row:
        proj_l = (proj or "").strip().lower()
        for c in project_row.get("containers") or []:
            if not isinstance(c, dict) or c.get("placeholder"):
                continue
            # Defense: never show another compose project's containers (shared workdir)
            c_proj = (c.get("compose_project") or "").strip()
            if c_proj and proj_l and c_proj.lower() != proj_l:
                continue
            cname = (c.get("name") or c.get("compose_service") or "").strip()
            if not cname:
                continue
            csvc = (c.get("compose_service") or "").strip()
            image = (c.get("image") or "").strip()
            ports = cov._parse_host_ports(
                c.get("ports_display") or "", c.get("ports")
            )
            role = guess_container_role(
                name=cname, image=image, compose_service=csvc
            )
            is_infra = cov._is_infra_role(
                name=cname,
                image=image,
                compose_service=csvc,
                patterns=patterns,
            )
            mute_key = f"{server.id}:{proj}:{cname}"
            explicitly_muted = mute_key in explicit_mute
            bound = cov._container_bound(
                binds, project=proj or "", container=cname, compose_service=csvc
            )
            if bound:
                mon_status = "covered"
            elif explicitly_muted:
                mon_status = "muted"
            elif is_infra:
                mon_status = "infra"
            else:
                mon_status = "none"

            tcp_suggestions: list[dict[str, Any]] = []
            if mon_status in ("none", "infra") and has_kuma:
                try:
                    tcp_suggestions = cov.suggest_tcp_monitors_for_dep(
                        session,
                        ports=ports,
                        name=cname,
                        image=image,
                        limit=5,
                    )
                except Exception:
                    tcp_suggestions = []

            integ_id = (
                tcp_suggestions[0]["integration_id"]
                if tcp_suggestions
                else default_integ
            )
            bind_action = f"/integrations/{integ_id}/bindings" if integ_id else ""

            bind_sum = cov._binding_summary(bound) if bound else None
            # Prefer live container networks; fall back to compose graph network names
            networks: list[str] = []
            raw_nets = c.get("networks")
            if isinstance(raw_nets, list):
                networks = [str(n).strip() for n in raw_nets if str(n).strip()][:12]
            elif isinstance(raw_nets, str) and raw_nets.strip():
                networks = [n.strip() for n in raw_nets.replace(",", " ").split() if n.strip()][:12]
            if not networks and isinstance(project_row.get("compose_graph"), dict):
                networks = list(
                    (project_row.get("compose_graph") or {}).get("networks") or []
                )[:12]
            # Docker UI: highlight project via hash (scroll/filter target)
            docker_deep = f"/servers/{server.id}/docker"
            if proj:
                from urllib.parse import quote as _q

                docker_deep += f"?project={_q(proj)}#docker-proj-{_q(proj, safe='')}"
            containers.append(
                {
                    "name": cname,
                    "compose_service": csvc or cname,
                    "container_name": cname,
                    "compose_project": (c.get("compose_project") or proj or "")[:200],
                    "image": image,
                    "version": (c.get("version") or "")[:40],
                    "running": bool(c.get("running")),
                    "status": (c.get("status") or c.get("state") or "")[:120],
                    "ports": ports,
                    "ports_display": (c.get("ports_display") or "")[:120],
                    "role": role,
                    "role_label": {
                        "edge": "edge",
                        "app": "app",
                        "queue": "queue",
                        "data": "db",
                        "cache": "cache",
                        "tooling": "tool",
                    }.get(role, role),
                    "is_infra": is_infra,
                    "mon_status": mon_status,
                    "mute_key": mute_key,
                    "explicitly_muted": explicitly_muted,
                    "binding": bind_sum,
                    "kuma_state": (bind_sum or {}).get("state") or "",
                    "kuma_label": (bind_sum or {}).get("label") or "",
                    "kuma_href": (bind_sum or {}).get("href") or "",
                    "tcp_suggestions": tcp_suggestions,
                    "suggested_external_id": (
                        tcp_suggestions[0]["external_id"] if tcp_suggestions else ""
                    ),
                    "bind_action": bind_action,
                    "bind_integration_id": integ_id,
                    "has_pending_update": bool(c.get("has_pending_update")),
                    "networks": networks,
                    "compose_file": (project_row.get("compose_file") or "")[:240],
                    "compose_path": (project_row.get("path") or "")[:240],
                    "docker_href": docker_deep,
                    "id_short": (c.get("id") or "")[:12],
                    "tags": [],
                    "category_key": None,
                    "visual_stack_id": None,
                }
            )

    # Merge DB annotations (category override, tags, view groups, order).
    # Scoped strictly to this compose project — never share keys with
    # piherder-e2e when viewing piherder (service names like "web" collide).
    visual_stacks: list[dict] = []
    categories_vocab: list[dict] = []
    tags_vocab: list[dict] = []
    # Always a concrete token for the panel: all | main | <int id>
    active_visual: str | int = (
        "all"
        if visual_stack_id == "all"
        else (
            "main"
            if visual_stack_id in (None, "main", 0, "0", "")
            else visual_stack_id
        )
    )
    if proj and server.id is not None:
        try:
            from .. import container_annotations as ann_svc

            ann_project = ann_svc.normalize_project(proj)
            containers = ann_svc.apply_annotations_to_containers(
                session,
                containers,
                server_id=int(server.id),
                project=ann_project,
                visual_stack_id=visual_stack_id,
                guess_role=guess_container_role,
            )
            visual_stacks = ann_svc.list_visual_stacks(
                session, server_id=int(server.id), project=ann_project
            )
            categories_vocab = ann_svc.list_categories(session, enabled_only=True)
            tags_vocab = ann_svc.list_tags(session, enabled_only=True)
        except Exception:
            pass

    # Category-driven sort (vocab order) unless custom order applies
    role_rank = {
        c.get("key"): i for i, c in enumerate(categories_vocab)
    } if categories_vocab else {
        "edge": 0,
        "app": 1,
        "queue": 2,
        "cache": 3,
        "data": 4,
        "tooling": 5,
    }
    containers.sort(
        key=lambda x: (
            role_rank.get(x.get("role") or "app", 9),
            0 if x.get("running") else 1,
            (x.get("compose_service") or x.get("name") or "").lower(),
        )
    )
    custom_order: list[str] = []
    if proj and server.id is not None:
        try:
            from ..stack_order import apply_order, get_order
            from .. import container_annotations as ann_svc

            # Prefer DB annotation order; fall back to settings JSON
            custom_order = ann_svc.order_from_annotations(
                session, server_id=int(server.id), project=proj
            )
            if not custom_order:
                custom_order = get_order(int(server.id), proj)
            if custom_order:
                containers = apply_order(containers, custom_order)
        except Exception:
            custom_order = []

    running_n = sum(1 for c in containers if c.get("running"))
    covered_n = sum(1 for c in containers if c.get("mon_status") == "covered")
    gap_n = sum(1 for c in containers if c.get("mon_status") == "none")

    # P1b/P2 — compose/heuristic suggestions, then merge with persisted RuntimeEdge
    suggested_edges: list[dict[str, Any]] = []
    confirmed_edges: list[dict[str, Any]] = []
    dismissed_edge_count = 0
    compose_graph = None
    if project_row and isinstance(project_row.get("compose_graph"), dict):
        compose_graph = project_row.get("compose_graph")
    try:
        from ..compose_graph import (
            edges_from_graph,
            heuristic_edges_from_services,
            merge_edge_lists,
        )

        compose_edges = edges_from_graph(compose_graph, source="compose", confidence=85)
        roles = {
            (c.get("compose_service") or c.get("name") or ""): (c.get("role") or "app")
            for c in containers
        }
        svc_names = list(roles.keys())
        if compose_graph and compose_graph.get("service_names"):
            for sn in compose_graph["service_names"]:
                if sn not in roles:
                    svc_names.append(sn)
        # Always merge heuristics (lower confidence) so app→celery etc. appear
        # even when compose already has web→db / worker→db but no web→worker link.
        heur = heuristic_edges_from_services(svc_names, roles=roles)
        suggested_edges = merge_edge_lists(compose_edges, heur)
        known = set(svc_names)
        for c in containers:
            known.add(c.get("compose_service") or "")
            known.add(c.get("name") or "")
        known = {k for k in known if k}
        if known:
            suggested_edges = [
                e
                for e in suggested_edges
                if e.get("from") in known and e.get("to") in known
            ]

        # Cross-host candidate: fabric path NPM edge host ≠ backend
        if (
            resolved.get("service_id")
            and proj
            and server.id is not None
        ):
            try:
                from ...models import ServiceDnsRecord

                rec = session.get(ServiceDnsRecord, int(resolved["service_id"]))
                if (
                    rec
                    and rec.via_proxy
                    and rec.target_server_id
                    and rec.backend_server_id
                    and int(rec.target_server_id) != int(rec.backend_server_id)
                ):
                    # proxy host project unknown → whole-host hop as talks_to
                    suggested_edges.append(
                        {
                            "from": "(edge)",
                            "to": proj,
                            "kind": "talks_to",
                            "source": "fabric",
                            "confidence": 70,
                            "from_server_id": int(rec.target_server_id),
                            "from_project": "(npm-edge)",
                            "to_server_id": int(rec.backend_server_id),
                            "to_project": proj,
                        }
                    )
            except Exception:
                pass
    except Exception:
        suggested_edges = []

    if proj and server.id is not None:
        try:
            from ..runtime_edges import partition_for_panel

            part = partition_for_panel(
                session,
                server_id=int(server.id),
                project=proj,
                suggestions=suggested_edges,
            )
            confirmed_edges = part.get("confirmed") or []
            suggested_edges = part.get("suggested") or []
            dismissed_edge_count = int(part.get("dismissed_count") or 0)
        except Exception:
            pass

    # Panel return path for mute/bind next=
    if resolved.get("service_id"):
        panel_path = f"/dns?stack={resolved['service_id']}"
        next_url = f"/dns?stack={resolved['service_id']}"
    elif proj:
        next_url = f"/dns?stack_server={server.id}&stack_project={proj}"
        panel_path = next_url
    else:
        next_url = "/dns"
        panel_path = "/dns"

    # all service monitors for bind dropdown (lightweight)
    all_service_monitors: list[dict[str, Any]] = []
    if has_kuma:
        try:
            for integ in kumas:
                for m in reg.monitors_from_cache(integ):
                    mid = str(m.get("id") or "").strip()
                    if not mid:
                        continue
                    mtype = str(m.get("type") or "")
                    label = str(m.get("name") or mid)
                    if mtype:
                        label = f"{label} ({mtype})"
                    all_service_monitors.append(
                        {
                            "external_id": mid,
                            "label": label,
                            "integration_id": integ.id,
                            "type": mtype,
                        }
                    )
            all_service_monitors.sort(key=lambda x: (x.get("label") or "").lower())
        except Exception:
            all_service_monitors = []

    return {
        "ok": True,
        "service_id": resolved.get("service_id"),
        "fqdn": resolved.get("fqdn"),
        "label": resolved.get("label"),
        "path_kind": resolved.get("path_kind"),
        "kuma_path_coverage": resolved.get("kuma_path_coverage"),
        "stack_deployment_id": resolved.get("stack_deployment_id"),
        "server_id": server.id,
        "server_name": server.name,
        "project": proj,
        "project_found": project_found,
        "projects_available": resolved.get("projects_available") or [],
        "has_inventory": bool(inv),
        "inventory_status": inv_meta.get("status"),
        "inventory_at": inv_meta.get("at"),
        "inventory_error": inv_meta.get("error"),
        "containers": containers,
        "compose_graph": compose_graph,
        "networks": (
            list((compose_graph or {}).get("networks") or [])[:12]
            if compose_graph
            else []
        ),
        "compose_file": (
            (project_row or {}).get("compose_file") if project_row else None
        ),
        "compose_path": (project_row or {}).get("path") if project_row else None,
        "focus_container": None,  # set by route from query
        "custom_order": custom_order,
        "has_custom_order": bool(custom_order),
        "visual_stacks": visual_stacks,
        "active_visual_stack": active_visual,
        "categories": categories_vocab,
        "tags_vocab": tags_vocab,
        "suggested_edges": suggested_edges,
        "confirmed_edges": confirmed_edges,
        "dismissed_edge_count": dismissed_edge_count,
        # Containers available for manual link form
        "container_options": [
            c.get("compose_service") or c.get("name")
            for c in containers
            if (c.get("compose_service") or c.get("name"))
        ],
        "summary": {
            "total": len(containers),
            "running": running_n,
            "stopped": len(containers) - running_n,
            "kuma_covered": covered_n,
            "kuma_gaps": gap_n,
            "suggested_edges": len(suggested_edges),
            "confirmed_edges": len(confirmed_edges),
            "has_compose_graph": bool(compose_graph and compose_graph.get("depends_on")),
        },
        "has_kuma": has_kuma,
        "all_service_monitors": all_service_monitors[:80],
        "docker_href": (
            f"/servers/{server.id}/docker"
            + (
                f"?project={proj}#docker-proj-{proj}"
                if proj
                else ""
            )
        ),
        "server_href": f"/servers/{server.id}",
        "deployment_href": (
            f"/templates/deployments/{resolved['stack_deployment_id']}"
            if resolved.get("stack_deployment_id")
            else None
        ),
        "path_map_href": (
            f"/dns/logical?focus={resolved['service_id']}#map"
            if resolved.get("service_id")
            else "/dns/logical#map"
        ),
        "hosts_map_href": (
            f"/dns/physical?focus={resolved['service_id']}#map"
            if resolved.get("service_id")
            else f"/dns/physical?focus=n:host-{server.id}#map"
        ),
        "coverage_href": "/dns/coverage",
        "next_url": next_url,
        "panel_path": panel_path,
        "base_query": _stack_query(
            service_id=resolved.get("service_id"),
            server_id=server.id,
            project=proj,
        ),
        "refresh_query": _stack_query(
            service_id=resolved.get("service_id"),
            server_id=server.id,
            project=proj,
            visual_stack_id=active_visual,
        ),
    }


def _stack_query(
    *,
    service_id: Optional[int],
    server_id: int,
    project: Optional[str],
    visual_stack_id: int | str | None = "all",
) -> str:
    from urllib.parse import quote

    if service_id:
        q = f"service_id={int(service_id)}"
    else:
        q = f"server_id={int(server_id)}"
        if project:
            q += f"&project={quote(project)}"
    # Omit for default All so tab links can append their own visual_stack=
    if visual_stack_id in (None, "main", 0, "0"):
        q += "&visual_stack=main"
    elif visual_stack_id not in ("all", ""):
        q += f"&visual_stack={quote(str(visual_stack_id))}"
    return q
