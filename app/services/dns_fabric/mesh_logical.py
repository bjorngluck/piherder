"""Logical / path map SVG layout (Path map)."""
from __future__ import annotations

from typing import Any

def _build_logical_mesh_svg(flows: list[dict[str, Any]]) -> dict[str, Any]:
    """Full logical mesh: URLs left, NPM hub center, destinations right."""
    if not flows:
        return {"width": 900, "height": 400, "nodes": [], "edges": [], "hub": None}

    n = len(flows)
    # Slightly taller rows when dense to reduce edge/label collisions
    row_h = 56 if n > 12 else 52
    pad_top = 70
    width = 1000
    height = pad_top + n * row_h + 40
    x_url, x_hub, x_dest = 160, 500, 820
    hub_y = pad_top + (n * row_h) / 2 - 10

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # NPM hub (only if any flow uses it)
    has_npm = any(f.get("via_npm") for f in flows)
    if has_npm:
        nodes.append(
            {
                "id": "hub-npm",
                "kind": "hub",
                "label": "NPM",
                "sub": "reverse proxy",
                "x": x_hub,
                "y": hub_y,
            }
        )

    npm_path_ids: list[Any] = []
    for i, f in enumerate(flows):
        y = pad_top + i * row_h
        path_id = f.get("id")
        uid = f"u{path_id if path_id is not None else i}"
        did = f"d{path_id if path_id is not None else i}"
        chain = " → ".join(
            p
            for p in (
                f.get("url"),
                (f"via {f.get('npm_edge')}" if f.get("via_npm") and f.get("npm_edge") else None),
                f.get("dest_summary") or f.get("dest_host"),
            )
            if p
        )
        nodes.append(
            {
                "id": uid,
                "kind": "url",
                "label": (f.get("url") or "")[:32],
                "sub": "https",
                "x": x_url,
                "y": y,
                "href": f.get("href"),
                "via_npm": f.get("via_npm"),
                "path_id": path_id,
                "path_chain": chain,
                "sync_status": f.get("sync_status") or "",
                "has_cert": bool(f.get("has_cert")),
            }
        )
        dest_label = f.get("dest_container") or f.get("dest_project") or f.get("dest_host") or "—"
        dest_sub = f.get("dest_host") or ""
        if f.get("dest_project") and f.get("dest_container"):
            dest_sub = f"{f.get('dest_host')} · {f.get('dest_project')}"
        nodes.append(
            {
                "id": did,
                "kind": "dest",
                "label": (dest_label or "")[:24],
                "sub": (dest_sub or "")[:28],
                "x": x_dest,
                "y": y,
                "href": f.get("dest_host_href") or f.get("href"),
                "path_id": path_id,
                "path_chain": chain,
                "sync_status": f.get("sync_status") or "",
                "has_cert": bool(f.get("has_cert")),
            }
        )
        if f.get("via_npm") and has_npm:
            if path_id is not None:
                npm_path_ids.append(path_id)
            edges.append(
                {
                    "x1": x_url + 90,
                    "y1": y,
                    "x2": x_hub - 50,
                    "y2": hub_y,
                    "kind": "to_npm",
                    "dashed": False,
                    "label": "",
                    "path_id": path_id,
                }
            )
            edges.append(
                {
                    "x1": x_hub + 50,
                    "y1": hub_y,
                    "x2": x_dest - 90,
                    "y2": y,
                    "kind": "from_npm",
                    "dashed": True,
                    "label": f.get("npm_edge") or "proxy",
                    "path_id": path_id,
                }
            )
        else:
            edges.append(
                {
                    "x1": x_url + 90,
                    "y1": y,
                    "x2": x_dest - 90,
                    "y2": y,
                    "kind": "direct",
                    "dashed": False,
                    "label": "direct" if f.get("path_kind") not in ("host_identity", "host_app") else "A",
                    "path_id": path_id,
                }
            )

    # Tag NPM hub with all via-proxy path ids for multi-path focus
    for n in nodes:
        if n.get("kind") == "hub":
            n["path_ids"] = npm_path_ids

    # mid labels
    for e in edges:
        e["mx"] = (e["x1"] + e["x2"]) / 2
        e["my"] = (e["y1"] + e["y2"]) / 2 - 6

    return {
        "width": width,
        "height": int(height),
        "nodes": nodes,
        "edges": edges,
        "columns": [
            {"label": "URL / name", "x": x_url},
            {"label": "Edge", "x": x_hub},
            {"label": "Destination", "x": x_dest},
        ],
    }


def _build_path_mesh(services: list[dict[str, Any]]) -> dict[str, Any]:
    """One horizontal path chain per service for the mesh diagram."""
    # Column X by hop kind
    col_x = {
        "name": 90,
        "npm": 280,
        "host": 470,
        "service": 660,
        "container": 850,
    }
    row_h = 72
    pad_top = 48
    n = max(len(services), 1)
    width = 960
    height = pad_top + n * row_h + 24

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for i, s in enumerate(services):
        y = pad_top + i * row_h + 20
        hops = s.get("hops") or []
        prev_id = None
        for hi, hop in enumerate(hops):
            kind = hop.get("kind") or "name"
            nid = f"s{s.get('id')}_{hi}_{kind}"
            x = col_x.get(kind, 90 + hi * 180)
            nodes.append(
                {
                    "id": nid,
                    "kind": kind,
                    "label": (hop.get("label") or "")[:22],
                    "sub": (hop.get("sub") or "")[:28],
                    "x": x,
                    "y": y,
                    "href": hop.get("href"),
                    "path_kind": s.get("path_kind"),
                    "row": i,
                }
            )
            if prev_id:
                edge_kind = "npm" if kind == "host" and hops[hi - 1].get("kind") == "npm" else kind
                edges.append(
                    {
                        "from": prev_id,
                        "to": nid,
                        "kind": edge_kind,
                        "label": "",
                        "dashed": kind in ("service", "container"),
                    }
                )
            prev_id = nid

    by_id = {n["id"]: n for n in nodes}
    for e in edges:
        a = by_id.get(e["from"]) or {}
        b = by_id.get(e["to"]) or {}
        e["x1"] = (a.get("x") or 0) + 70
        e["y1"] = a.get("y") or 0
        e["x2"] = (b.get("x") or 0) - 70
        e["y2"] = b.get("y") or 0
        e["mx"] = (e["x1"] + e["x2"]) / 2
        e["my"] = (e["y1"] + e["y2"]) / 2 - 4

    return {
        "width": width,
        "height": height,
        "nodes": nodes,
        "edges": edges,
        "columns": [
            {"id": "name", "label": "Name", "x": col_x["name"]},
            {"id": "npm", "label": "NPM edge", "x": col_x["npm"]},
            {"id": "host", "label": "Host", "x": col_x["host"]},
            {"id": "service", "label": "Service", "x": col_x["service"]},
            {"id": "container", "label": "Container", "x": col_x["container"]},
        ],
        "mode": "paths",
    }


