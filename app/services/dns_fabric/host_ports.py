"""Host port inventory — Docker published ∪ nmap observed + sticky annotations (M4)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import NmapDevice, PortAnnotation, Server
from .. import docker_inventory as inv_svc
from .ports import (
    PORT_ROLE_LABELS,
    PORT_ROLE_OTHER,
    PORT_ROLE_SSH,
    enrich_container_ports,
    guess_port_role,
    parse_published_ports,
    port_role_label,
)

# Default noise ports hidden unless show_noise / sticky unhide
_NOISE_PORTS = frozenset({22})

VALID_ROLE_KEYS = frozenset(PORT_ROLE_LABELS.keys())


def _norm_proto(raw: Any) -> str:
    p = (str(raw or "tcp")).strip().lower()
    return p if p in ("tcp", "udp") else "tcp"


def _port_key(host_port: int | str, proto: str) -> str:
    return f"{int(host_port)}/{_norm_proto(proto)}"


def load_annotations_for_server(
    session: Session, server_id: int
) -> dict[str, PortAnnotation]:
    rows = session.exec(
        select(PortAnnotation).where(PortAnnotation.server_id == int(server_id))
    ).all()
    out: dict[str, PortAnnotation] = {}
    for r in rows:
        out[_port_key(r.host_port, r.proto)] = r
    return out


def load_annotations_for_device(
    session: Session, nmap_device_id: int
) -> dict[str, PortAnnotation]:
    rows = session.exec(
        select(PortAnnotation).where(
            PortAnnotation.nmap_device_id == int(nmap_device_id)
        )
    ).all()
    out: dict[str, PortAnnotation] = {}
    for r in rows:
        out[_port_key(r.host_port, r.proto)] = r
    return out


def apply_sticky_to_parsed(
    parsed: list[dict[str, Any]],
    annotations: dict[str, PortAnnotation] | None,
) -> list[dict[str, Any]]:
    """Overlay sticky role/label on ports_parsed entries (mutates list items)."""
    if not annotations:
        return parsed
    for p in parsed:
        try:
            hp = int(str(p.get("host") or 0))
        except (TypeError, ValueError):
            continue
        if not p.get("published"):
            # sticky still applies to host-facing published ports primarily
            key = _port_key(hp, p.get("proto") or "tcp")
        else:
            key = _port_key(hp, p.get("proto") or "tcp")
        ann = annotations.get(key)
        if not ann:
            continue
        if ann.role_key and ann.role_key in VALID_ROLE_KEYS:
            p["role"] = ann.role_key
            p["role_label"] = port_role_label(ann.role_key)
            p["role_sticky"] = True
            if ann.role_key != PORT_ROLE_OTHER:
                p["label_with_role"] = f"{p['label']} · {p['role_label']}"
        if ann.label:
            p["sticky_label"] = ann.label
        if ann.note:
            p["sticky_note"] = ann.note
        if ann.hide:
            p["sticky_hide"] = True
    return parsed


def _linked_nmap_device(session: Session, server_id: int) -> NmapDevice | None:
    return session.exec(
        select(NmapDevice)
        .where(NmapDevice.linked_server_id == int(server_id))
        .where(NmapDevice.state != "ignored")
    ).first()


def _nmap_open_ports(device: NmapDevice | None) -> list[dict[str, Any]]:
    if not device or not device.ports_json:
        return []
    try:
        ports = json.loads(device.ports_json)
        if not isinstance(ports, list):
            return []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for p in ports:
        if not isinstance(p, dict):
            continue
        if str(p.get("state") or "").lower() != "open":
            continue
        try:
            port = int(p.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if port <= 0:
            continue
        out.append(
            {
                "host": str(port),
                "container": "",
                "proto": _norm_proto(p.get("protocol")),
                "bind": "*",
                "label": f"{port}/{_norm_proto(p.get('protocol'))}",
                "published": False,
                "source": "nmap",
                "nmap_service": (p.get("service") or "")[:64],
                "nmap_product": (p.get("product") or "")[:80],
            }
        )
    return out


def _docker_published_rows(server: Server) -> list[dict[str, Any]]:
    inv = inv_svc.parse_inventory(server) or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    projects = inv.get("projects") or []
    orphans = inv.get("orphan_containers") or []
    containers: list[dict[str, Any]] = []
    for pr in projects:
        if not isinstance(pr, dict):
            continue
        pname = (pr.get("name") or "").strip()
        for c in pr.get("containers") or []:
            if isinstance(c, dict):
                cc = dict(c)
                cc["_project"] = pname or (c.get("compose_project") or "")
                containers.append(cc)
    for c in orphans:
        if isinstance(c, dict):
            cc = dict(c)
            cc["_project"] = (c.get("compose_project") or "") or "(orphan)"
            containers.append(cc)

    for c in containers:
        parsed = parse_published_ports(c.get("ports_display"), c.get("ports"))
        svc = c.get("compose_service") or c.get("name") or ""
        image = c.get("image") or ""
        cname = c.get("name") or ""
        project = (c.get("_project") or c.get("compose_project") or "")[:200]
        for p in parsed:
            if not p.get("published"):
                continue
            try:
                hp = int(str(p["host"]))
            except (TypeError, ValueError):
                continue
            proto = _norm_proto(p.get("proto"))
            key = _port_key(hp, proto)
            if key in seen:
                # Same host port published twice — keep first, note multi
                continue
            seen.add(key)
            role = guess_port_role(
                host_port=hp,
                container_port=p.get("container"),
                proto=proto,
                service_name=str(svc),
                image=str(image),
                container_name=str(cname),
            )
            rows.append(
                {
                    "host": str(hp),
                    "host_port": hp,
                    "container": str(p.get("container") or ""),
                    "proto": proto,
                    "bind": p.get("bind") or "*",
                    "label": p["label"],
                    "published": True,
                    "source": "docker",
                    "owner_project": project,
                    "owner_container": str(cname)[:200],
                    "owner_service": str(svc)[:200],
                    "image": str(image)[:200],
                    "role": role,
                    "role_label": port_role_label(role),
                    "role_sticky": False,
                    "running": bool(c.get("running")),
                }
            )
    return rows


def build_host_port_inventory(
    session: Session,
    *,
    server_id: int | None = None,
    nmap_device_id: int | None = None,
    show_noise: bool = False,
    focus_project: str | None = None,
) -> dict[str, Any]:
    """Union of Docker published + nmap observed ports with sticky annotations.

    Returns panel payload: ports list, counts, server/device meta.
    """
    server: Server | None = None
    device: NmapDevice | None = None
    if server_id is not None:
        server = session.get(Server, int(server_id))
        if not server:
            return {"ok": False, "error": "server_not_found"}
        device = _linked_nmap_device(session, int(server_id))
        annotations = load_annotations_for_server(session, int(server_id))
    elif nmap_device_id is not None:
        device = session.get(NmapDevice, int(nmap_device_id))
        if not device:
            return {"ok": False, "error": "device_not_found"}
        if device.linked_server_id:
            server = session.get(Server, int(device.linked_server_id))
            if server:
                annotations = load_annotations_for_server(session, int(server.id))
            else:
                annotations = load_annotations_for_device(session, int(nmap_device_id))
        else:
            annotations = load_annotations_for_device(session, int(nmap_device_id))
    else:
        return {"ok": False, "error": "need_server_or_device"}

    by_key: dict[str, dict[str, Any]] = {}

    if server:
        for row in _docker_published_rows(server):
            key = _port_key(row["host_port"], row["proto"])
            by_key[key] = row

    for nmap_row in _nmap_open_ports(device):
        try:
            hp = int(nmap_row["host"])
        except (TypeError, ValueError):
            continue
        proto = nmap_row["proto"]
        key = _port_key(hp, proto)
        if key in by_key:
            by_key[key]["source"] = "both"
            by_key[key]["nmap_service"] = nmap_row.get("nmap_service") or ""
            by_key[key]["nmap_product"] = nmap_row.get("nmap_product") or ""
            by_key[key]["observed"] = True
        else:
            role = guess_port_role(
                host_port=hp,
                proto=proto,
                service_name=str(nmap_row.get("nmap_service") or ""),
            )
            by_key[key] = {
                "host": str(hp),
                "host_port": hp,
                "container": "",
                "proto": proto,
                "bind": "*",
                "label": nmap_row["label"],
                "published": False,
                "source": "nmap",
                "observed": True,
                "owner_project": "",
                "owner_container": "",
                "owner_service": "",
                "image": "",
                "role": role,
                "role_label": port_role_label(role),
                "role_sticky": False,
                "nmap_service": nmap_row.get("nmap_service") or "",
                "nmap_product": nmap_row.get("nmap_product") or "",
                "running": None,
            }

    # Sticky overlays + hide
    ports: list[dict[str, Any]] = []
    focus_proj = (focus_project or "").strip().lower()
    for key, row in by_key.items():
        ann = annotations.get(key)
        if ann:
            if ann.role_key and ann.role_key in VALID_ROLE_KEYS:
                row["role"] = ann.role_key
                row["role_label"] = port_role_label(ann.role_key)
                row["role_sticky"] = True
            if ann.label:
                row["sticky_label"] = ann.label
            if ann.note:
                row["note"] = ann.note
            if ann.owner_project:
                row["owner_project"] = ann.owner_project
                row["owner_override"] = True
            if ann.owner_container:
                row["owner_container"] = ann.owner_container
                row["owner_override"] = True
            row["hide"] = bool(ann.hide)
            row["annotation_id"] = ann.id
        else:
            row["hide"] = False
            row["annotation_id"] = None

        hp = int(row["host_port"])
        is_noise = hp in _NOISE_PORTS or row.get("role") == PORT_ROLE_SSH
        row["is_noise"] = is_noise
        if row.get("hide"):
            if not show_noise:
                continue
        elif is_noise and not show_noise:
            continue

        if focus_proj:
            op = (row.get("owner_project") or "").strip().lower()
            row["in_focus_stack"] = bool(op and op == focus_proj)
            row["other_on_host"] = bool(op and op != focus_proj) or (
                not op and row.get("source") in ("nmap", "both")
            )
        else:
            row["in_focus_stack"] = False
            row["other_on_host"] = False

        if row.get("role") and row["role"] != PORT_ROLE_OTHER:
            row["display"] = f"{row['label']} · {row['role_label']}"
        else:
            row["display"] = row["label"]
        if row.get("sticky_label"):
            row["display"] = f"{row['display']} ({row['sticky_label']})"

        ports.append(row)

    ports.sort(
        key=lambda r: (
            0 if r.get("in_focus_stack") else 1,
            0 if r.get("published") else 1,
            int(r.get("host_port") or 0),
            r.get("proto") or "tcp",
        )
    )

    published_n = sum(1 for p in ports if p.get("published"))
    observed_only = sum(1 for p in ports if p.get("source") == "nmap")
    stacks = sorted(
        {
            (p.get("owner_project") or "").strip()
            for p in ports
            if (p.get("owner_project") or "").strip()
        }
    )

    # Short map focus summary
    chips = []
    for p in ports:
        if p.get("published") or p.get("source") == "both":
            chips.append(
                f"{p['host_port']}"
                + (f" {p['role_label']}" if p.get("role") != PORT_ROLE_OTHER else "")
            )
        if len(chips) >= 4:
            break
    if not chips:
        for p in ports[:4]:
            chips.append(str(p.get("host_port")))
    extra = max(0, len(ports) - len(chips))
    summary_short = ", ".join(chips)
    if extra:
        summary_short += f" +{extra}"
    if stacks:
        summary_line = f"{len(ports)} ports · {len(stacks)} stack{'s' if len(stacks) != 1 else ''}"
        if summary_short:
            summary_line += f" · {summary_short}"
    else:
        summary_line = f"{len(ports)} port{'s' if len(ports) != 1 else ''}"
        if summary_short:
            summary_line += f" · {summary_short}"

    return {
        "ok": True,
        "server_id": int(server.id) if server and server.id else None,
        "server_name": (server.name if server else "") or "",
        "nmap_device_id": int(device.id) if device and device.id else None,
        "device_name": (
            (device.display_name or device.hostname or device.ip_address)
            if device
            else ""
        ),
        "ports": ports,
        "published_count": published_n,
        "observed_only_count": observed_only,
        "total_count": len(ports),
        "stack_names": stacks,
        "stack_count": len(stacks),
        "summary_line": summary_line,
        "summary_short": summary_short,
        "show_noise": show_noise,
        "focus_project": focus_project or "",
        "role_choices": [
            {"key": k, "label": PORT_ROLE_LABELS[k]}
            for k in (
                "web",
                "dns",
                "db",
                "cache",
                "proxy",
                "ssh",
                "metrics",
                "other",
            )
        ],
        "inventory_status": (
            getattr(server, "docker_inventory_status", None) if server else None
        ),
    }


def host_ports_summary_for_server(
    session: Session, server_id: int, *, show_noise: bool = False
) -> dict[str, Any]:
    """Lightweight map attrs: counts + short line (no full panel)."""
    inv = build_host_port_inventory(
        session, server_id=server_id, show_noise=show_noise
    )
    if not inv.get("ok"):
        return {
            "ports_count": 0,
            "stack_count": 0,
            "ports_summary": "",
            "summary_line": "",
        }
    return {
        "ports_count": inv["total_count"],
        "stack_count": inv["stack_count"],
        "ports_summary": inv["summary_short"],
        "summary_line": inv["summary_line"],
    }


def host_ports_summary_for_device(
    session: Session, nmap_device_id: int, *, show_noise: bool = False
) -> dict[str, Any]:
    """Lightweight map attrs for a discovered nmap device (cameras, etc.)."""
    inv = build_host_port_inventory(
        session, nmap_device_id=nmap_device_id, show_noise=show_noise
    )
    if not inv.get("ok"):
        return {
            "ports_count": 0,
            "stack_count": 0,
            "ports_summary": "",
            "summary_line": "",
        }
    return {
        "ports_count": inv["total_count"],
        "stack_count": inv["stack_count"],
        "ports_summary": inv["summary_short"],
        "summary_line": inv["summary_line"],
    }


def build_host_ports_expand_payload(
    session: Session,
    *,
    server_id: int | None = None,
    nmap_device_id: int | None = None,
    show_noise: bool = False,
    limit: int = 40,
    ports_per_service: int = 5,
    focus_project: str | None = None,
    focus_container: str | None = None,
) -> dict[str, Any]:
    """JSON for on-map host fan: host → service/container → ports (max N).

    Groups by compose project + container (or service name). Nmap-only ports
    land in a single **Observed** group — no duplicate obs labels on each chip.

    Accepts fleet ``server_id`` and/or discovered ``nmap_device_id`` (cameras
    and other LAN chips). Optional ``focus_project`` / ``focus_container``
    narrow the fan to one stack unit (service/path or container click).
    """
    if server_id is None and nmap_device_id is None:
        return {"ok": False, "error": "need_server_or_device"}

    inv = build_host_port_inventory(
        session,
        server_id=int(server_id) if server_id is not None else None,
        nmap_device_id=int(nmap_device_id) if nmap_device_id is not None else None,
        show_noise=show_noise,
        focus_project=focus_project,
    )
    if not inv.get("ok"):
        return inv

    max_ports = max(1, min(60, limit))
    max_show = max(1, min(8, ports_per_service))
    ports_raw = list(inv.get("ports") or [])[:max_ports]

    focus_proj = (focus_project or "").strip().lower()
    focus_cont = (focus_container or "").strip().lower()
    if focus_proj or focus_cont:
        filtered: list[dict[str, Any]] = []
        for p in ports_raw:
            op = (p.get("owner_project") or "").strip().lower()
            oc = (p.get("owner_container") or "").strip().lower()
            osvc = (p.get("owner_service") or "").strip().lower()
            if focus_cont:
                if focus_cont in (oc, osvc) and (
                    not focus_proj or not op or op == focus_proj
                ):
                    filtered.append(p)
            elif focus_proj:
                if op == focus_proj or p.get("in_focus_stack"):
                    filtered.append(p)
        # When focusing a stack, still include unowned nmap only if nothing matched
        if filtered:
            ports_raw = filtered

    # Group key → service bucket
    groups: dict[str, dict[str, Any]] = {}

    def _port_chip(p: dict[str, Any]) -> dict[str, Any]:
        hp = int(p.get("host_port") or 0)
        proto = (p.get("proto") or "tcp").lower()
        role = (p.get("role") or PORT_ROLE_OTHER).lower()
        return {
            "id": f"{hp}/{proto}",
            "host_port": hp,
            "proto": proto,
            "role": role,
            "role_label": p.get("role_label") or port_role_label(role),
            "role_sticky": bool(p.get("role_sticky")),
            "source": p.get("source") or "docker",
            "label": f"{hp}"
            + (
                f" {p.get('role_label') or port_role_label(role)}"
                if role != PORT_ROLE_OTHER
                else ""
            ),
        }

    for p in ports_raw:
        proj = (p.get("owner_project") or "").strip()
        cont = (p.get("owner_container") or "").strip()
        svc = (p.get("owner_service") or "").strip()
        # Prefer compose service for display when it differs from container name
        unit = svc or cont
        if proj:
            gkey = f"svc:{proj.lower()}::{(unit or cont or '_').lower()}"
            title = proj
            subtitle = (
                unit if unit and unit.lower() != proj.lower() else (cont or svc or "")
            )
            if subtitle and subtitle.lower() == title.lower():
                subtitle = ""
            kind = "service"
        else:
            gkey = "observed"
            title = "Observed"
            subtitle = "nmap only"
            kind = "observed"

        if gkey not in groups:
            groups[gkey] = {
                "id": gkey,
                "kind": kind,
                "project": proj,
                "container": cont,
                "service": svc or cont,
                "title": title,
                "subtitle": subtitle,
                "ports": [],
                "sources": set(),
            }
        chip = _port_chip(p)
        groups[gkey]["ports"].append(chip)
        groups[gkey]["sources"].add(str(p.get("source") or "docker"))

    services_out: list[dict[str, Any]] = []
    # Owned services first (alpha), observed last
    ordered = sorted(
        groups.values(),
        key=lambda g: (
            0 if g["kind"] == "service" else 1,
            g["title"].lower(),
            g["subtitle"].lower(),
        ),
    )
    for g in ordered:
        ports_all = g["ports"]
        shown = ports_all[:max_show]
        extra = max(0, len(ports_all) - len(shown))
        srcs = g["sources"]
        if srcs == {"nmap"}:
            source = "nmap"
        elif "nmap" in srcs and ("docker" in srcs or "both" in srcs):
            source = "both"
        elif "both" in srcs:
            source = "both"
        else:
            source = "docker"
        # Display line under title: "pihole" or "web · pihole"
        if g["kind"] == "observed":
            label = "Observed"
            detail = "not in Docker inventory"
        else:
            label = g["title"]
            detail = g["subtitle"] or g["service"] or ""
        services_out.append(
            {
                "id": g["id"],
                "kind": g["kind"],
                "project": g["project"],
                "container": g["container"],
                "service": g["service"],
                "label": label,
                "detail": detail,
                "source": source,
                "ports": shown,
                "port_count": len(ports_all),
                "ports_extra": extra,
                "port_ids": [c["id"] for c in ports_all],
            }
        )

    edges = [
        {"from": "host", "to": s["id"], "kind": "host_service"} for s in services_out
    ]

    sid = inv.get("server_id")
    did = inv.get("nmap_device_id")
    if sid is not None:
        node_id = f"host-{int(sid)}"
        panel_url = f"/dns/host-ports-panel?server_id={int(sid)}"
    elif did is not None:
        node_id = f"host-d-{int(did)}"
        panel_url = f"/dns/host-ports-panel?nmap_device_id={int(did)}"
    else:
        node_id = ""
        panel_url = "/dns/host-ports-panel"

    if focus_proj and sid is not None:
        panel_url += f"&focus_project={focus_project.strip()}"

    # Flat port list for ports-only expand (all chips, not capped per service)
    ports_flat: list[dict[str, Any]] = []
    seen_flat: set[str] = set()
    for p in ports_raw:
        chip = _port_chip(p)
        if chip["id"] in seen_flat:
            continue
        seen_flat.add(chip["id"])
        # Owner hint for ports-only list (optional subtitle)
        proj = (p.get("owner_project") or "").strip()
        unit = (p.get("owner_service") or p.get("owner_container") or "").strip()
        chip["owner"] = (
            f"{proj}/{unit}" if proj and unit and unit.lower() != proj.lower() else (proj or unit or "")
        )[:28]
        ports_flat.append(chip)

    # Compact callout: first few chips
    compact_chips = ports_flat[:6]
    compact_extra = max(0, len(ports_flat) - len(compact_chips))

    return {
        "ok": True,
        "server_id": int(sid) if sid is not None else None,
        "nmap_device_id": int(did) if did is not None else None,
        "server_name": inv.get("server_name") or "",
        "device_name": inv.get("device_name") or "",
        "node_id": node_id,
        "focus_project": (focus_project or "").strip(),
        "focus_container": (focus_container or "").strip(),
        # service-first fan (follow-on after ports-only)
        "services": services_out,
        # flat lists: ports_flat = full list for ports-only view
        "ports": ports_flat,
        "ports_flat": ports_flat,
        "stacks": [
            {
                "id": s["project"] or s["id"],
                "name": s["label"],
                "port_ids": s["port_ids"],
                "port_count": s["port_count"],
            }
            for s in services_out
            if s["kind"] == "service"
        ],
        "edges": edges,
        "total_count": inv.get("total_count")
        or sum(s["port_count"] for s in services_out)
        or len(ports_flat),
        "stack_count": sum(1 for s in services_out if s["kind"] == "service"),
        "summary_line": inv.get("summary_line") or "",
        "summary_short": inv.get("summary_short") or "",
        "compact_chips": compact_chips,
        "compact_extra": compact_extra,
        "panel_url": panel_url,
        "ports_per_service": max_show,
        # progressive: compact → ports (list) → full (service fan)
        "default_view": "compact",
    }


def upsert_port_annotation(
    session: Session,
    *,
    server_id: int | None = None,
    nmap_device_id: int | None = None,
    host_port: int,
    proto: str = "tcp",
    role_key: str | None = None,
    label: str | None = None,
    note: str | None = None,
    owner_project: str | None = None,
    owner_container: str | None = None,
    hide: bool | None = None,
    clear_role: bool = False,
    user_id: int | None = None,
) -> PortAnnotation:
    """Create or update sticky annotation. Empty role clears sticky role."""
    proto = _norm_proto(proto)
    hp = int(host_port)
    if server_id is None and nmap_device_id is None:
        raise ValueError("need_server_or_device")

    q = select(PortAnnotation).where(
        PortAnnotation.host_port == hp,
        PortAnnotation.proto == proto,
    )
    if server_id is not None:
        q = q.where(PortAnnotation.server_id == int(server_id))
    else:
        q = q.where(PortAnnotation.nmap_device_id == int(nmap_device_id))

    row = session.exec(q).first()
    if not row:
        row = PortAnnotation(
            server_id=int(server_id) if server_id is not None else None,
            nmap_device_id=int(nmap_device_id) if nmap_device_id is not None else None,
            host_port=hp,
            proto=proto,
            created_by_user_id=user_id,
        )

    if clear_role or (role_key is not None and str(role_key).strip() == ""):
        row.role_key = None
    elif role_key is not None:
        rk = str(role_key).strip().lower()
        if rk not in VALID_ROLE_KEYS:
            raise ValueError("invalid_role")
        row.role_key = rk

    if label is not None:
        row.label = (label.strip()[:80] or None)
    if note is not None:
        row.note = (note.strip()[:500] or None)
    if owner_project is not None:
        row.owner_project = (owner_project.strip()[:200] or None)
    if owner_container is not None:
        row.owner_container = (owner_container.strip()[:200] or None)
    if hide is not None:
        row.hide = bool(hide)

    row.updated_at = datetime.utcnow()
    if user_id is not None and row.created_by_user_id is None:
        row.created_by_user_id = user_id
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def enrich_with_server_annotations(
    session: Session,
    containers: list[dict[str, Any]],
    *,
    server_id: int,
) -> list[dict[str, Any]]:
    """Apply sticky port roles onto stack panel container ports_parsed."""
    anns = load_annotations_for_server(session, server_id)
    if not anns:
        return containers
    for c in containers:
        parsed = c.get("ports_parsed")
        if isinstance(parsed, list) and parsed:
            apply_sticky_to_parsed(parsed, anns)
    return containers
