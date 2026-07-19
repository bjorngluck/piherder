"""P4 — stack expand payload for path-map blow-up (one stack at a time)."""
from __future__ import annotations

from typing import Any

from sqlmodel import Session

from .stack_panel import build_stack_panel


def build_stack_expand_payload(
    session: Session,
    *,
    service_id: int | None = None,
    server_id: int | None = None,
    project: str | None = None,
    visual_stack_id: int | str | None = "all",
) -> dict[str, Any]:
    """Compact JSON for client-side map expand.

    Containers from inventory; **confirmed** RuntimeEdges only (accepted/manual).
    """
    panel = build_stack_panel(
        session,
        service_id=service_id,
        server_id=server_id,
        project=project,
        visual_stack_id=visual_stack_id,
    )
    if not panel.get("ok"):
        return {
            "ok": False,
            "error": panel.get("error") or "not found",
            "code": panel.get("code") or "error",
        }

    containers = []
    for c in panel.get("containers") or []:
        role = c.get("role") or "app"
        ports = c.get("ports") or []
        vs_id = c.get("visual_stack_id")
        containers.append(
            {
                "id": c.get("compose_service") or c.get("name"),
                "name": c.get("compose_service") or c.get("name"),
                "role": role,
                "role_label": c.get("role_label") or role,
                "running": bool(c.get("running")),
                "mon_status": c.get("mon_status") or "",
                "ports": ports,
                "ports_label": ", ".join(str(p) for p in ports[:4]) if ports else "",
                "image": (c.get("image") or "")[:80],
                "kuma_state": c.get("kuma_state") or "",
                "status": c.get("status") or "",
                "order_index": c.get("order_index"),
                "tags": list(c.get("tags") or []),
                "visual_stack_id": vs_id,
                "visual_stack_name": (c.get("visual_stack_name") or "Main")[:80],
            }
        )
    # Map columns follow category vocab order (hide empty later client-side)
    category_columns = [
        {"key": c.get("key"), "label": (c.get("label") or c.get("key") or "").lower()}
        for c in (panel.get("categories") or [])
        if c.get("key")
    ]

    edges = []
    for e in panel.get("confirmed_edges") or []:
        frm = (e.get("from_container") or "").strip()
        to = (e.get("to_container") or "").strip()
        # Same-project container→container only for map fan-out
        if not frm or not to:
            continue
        if not e.get("same_project", True) and e.get("same_host"):
            # still draw if both ends named
            pass
        edges.append(
            {
                "from": frm,
                "to": to,
                "kind": e.get("kind") or "depends_on",
                "source": e.get("source") or "accepted",
            }
        )

    return {
        "ok": True,
        "path_id": panel.get("service_id"),
        "server_id": panel.get("server_id"),
        "server_name": panel.get("server_name"),
        "project": panel.get("project"),
        "fqdn": panel.get("fqdn"),
        "server_href": panel.get("server_href") or (
            f"/servers/{panel['server_id']}" if panel.get("server_id") else ""
        ),
        "docker_href": panel.get("docker_href") or "",
        "stack_href": (
            f"/dns?stack={panel['service_id']}"
            if panel.get("service_id")
            else (
                f"/dns?stack_server={panel['server_id']}&stack_project={panel['project']}"
                if panel.get("server_id") and panel.get("project")
                else "/dns"
            )
        ),
        "path_map_href": panel.get("path_map_href") or "",
        "hosts_map_href": panel.get("hosts_map_href") or "",
        "containers": containers,
        "edges": edges,
        "has_custom_order": bool(panel.get("has_custom_order")),
        "custom_order": list(panel.get("custom_order") or []),
        "category_columns": category_columns,
        "visual_stacks": panel.get("visual_stacks") or [],
        "active_visual_stack": panel.get("active_visual_stack"),
        # When All is selected and containers span 2+ view groups, map draws
        # one fan per group (logical views — not separate deploys).
        "multi_view": _multi_view_meta(containers, panel.get("visual_stacks") or []),
        "summary": {
            "container_count": len(containers),
            "edge_count": len(edges),
            "running": sum(1 for c in containers if c.get("running")),
        },
    }


def _multi_view_meta(
    containers: list[dict[str, Any]], visual_stacks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ordered view groups that have at least one container (for map multi-fan)."""
    names: dict[str, str] = {"main": "Main"}
    for vs in visual_stacks or []:
        if vs.get("id") is None:
            names["main"] = vs.get("name") or "Main"
        else:
            names[str(vs["id"])] = vs.get("name") or f"group-{vs['id']}"

    order_keys: list[str] = ["main"]
    for vs in visual_stacks or []:
        if vs.get("id") is not None:
            k = str(vs["id"])
            if k not in order_keys:
                order_keys.append(k)

    counts: dict[str, int] = {}
    for c in containers:
        vid = c.get("visual_stack_id")
        key = "main" if vid is None else str(vid)
        counts[key] = counts.get(key, 0) + 1
        if key not in names:
            names[key] = (c.get("visual_stack_name") or key)[:80]
        if key not in order_keys:
            order_keys.append(key)

    out: list[dict[str, Any]] = []
    for k in order_keys:
        n = counts.get(k, 0)
        if n <= 0:
            continue
        out.append({"key": k, "name": names.get(k, k), "count": n})
    return out
