"""Physical network map SVG layout (Hosts map)."""
from __future__ import annotations

from typing import Any

from .core import (
    _host_is_cloud,
    _ip_in_lan,
    _is_private_ip,
    host_focus_key,
)



# Soft-cap only for extreme density; layout fans apps on multiple rings.
# Prefer showing every mapping card on the mesh (overflow marker as last resort).
PHYSICAL_MESH_MAX_APPS_PER_HOST = 48


def _build_physical_mesh_svg(
    hosts: list[dict[str, Any]],
    services: list[dict[str, Any]],
    *,
    network: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full-fleet physical SVG: Internet → Router (zone rim) → LAN zone → hosts + apps.

    Always draws the WAN/LAN spine when any fleet host exists:
      Internet (centred, no IP) ──(wan)── Router on LAN zone rim
        Router card: top = public WAN IP, bottom = private gateway IP
      LAN badge (subnet) · hosts *inside* zone · apps *outside*
      Cloud hosts (e.g. Nomad) ──(wan)── side of Internet cloud

    Dense hosts: at most PHYSICAL_MESH_MAX_APPS_PER_HOST satellites, then "+N more".
    """
    import math

    net = network or {}
    lan_subnet = (net.get("lan_subnet") or "").strip()
    gateway_ip = (net.get("gateway_ip") or "").strip()
    public_ip = (net.get("public_ip") or "").strip()
    gateway_kuma = net.get("gateway_kuma") if isinstance(net.get("gateway_kuma"), dict) else None
    public_kuma = net.get("public_kuma") if isinstance(net.get("public_kuma"), dict) else None

    # Fleet (server_id), discovered nmap devices (discovery_id), or named hosts
    named = [
        h
        for h in hosts
        if h.get("dns_name")
        or h.get("server_id") is not None
        or h.get("discovery_id") is not None
        or h.get("ip")
    ]
    if not named and not (lan_subnet or gateway_ip or public_ip):
        return {"width": 800, "height": 400, "nodes": [], "edges": [], "labels": []}

    lan_hosts: list[dict[str, Any]] = []
    cloud_hosts: list[dict[str, Any]] = []
    for h in named:
        if _host_is_cloud(h.get("ip"), lan_subnet):
            cloud_hosts.append(h)
        else:
            lan_hosts.append(h)

    # Split fleet vs discovery so fleet ring / apps stay roomy (pre-discovery layout).
    fleet_lan = [
        h
        for h in lan_hosts
        if not h.get("is_discovered") and h.get("server_id") is not None
    ]
    disc_lan = [
        h
        for h in lan_hosts
        if h.get("is_discovered") or h.get("discovery_id") is not None
    ]
    # Orphan LAN hosts without server_id and not marked discovered → treat as fleet-like
    for h in lan_hosts:
        if h in fleet_lan or h in disc_lan:
            continue
        fleet_lan.append(h)

    # Always show full Internet → gateway → LAN spine when we have any hosts
    # (or any explicit network settings). Previously only partial settings left
    # hosts floating with no edges.
    show_spine = bool(named or lan_subnet or gateway_ip or public_ip)
    show_gateway = show_spine and (
        bool(gateway_ip) or bool(lan_hosts) or bool(lan_subnet)
    )
    show_lan = show_spine and (bool(lan_hosts) or bool(lan_subnet))
    show_internet = show_spine

    # Fleet count drives zone geometry (mapped apps must not squash into a corner).
    n_fleet = len(fleet_lan)
    n_disc = len(disc_lan)
    n_lan_h = n_fleet  # ring sizing uses fleet only
    n_cloud = len(cloud_hosts)

    # --- Geometry: LAN zone holds fleet servers; apps outside; disc further out ---
    # Fleet host card half-size (matches SVG rect 124×60)
    host_hw, host_hh = 62.0, 30.0
    # Discovered: compact chips (SVG rect ~76×34)
    disc_hw, disc_hh = 38.0, 17.0
    # Inner LAN badge (circle: "LAN" + subnet) — room for CIDR text
    badge_r = 54.0

    # Host ring radius — fleet only (same formulas as pre-discovery)
    if n_fleet <= 1:
        ring_rx, ring_ry = 145.0, 115.0
    elif n_fleet <= 4:
        ring_rx = 155.0 + n_fleet * 16.0
        ring_ry = 115.0 + n_fleet * 12.0
    else:
        ring_rx = min(340.0, 140.0 + n_fleet * 22.0)
        ring_ry = min(250.0, 105.0 + n_fleet * 16.0)
    # Keep ring outside the centre badge so cards don't cover "LAN"
    ring_rx = max(ring_rx, badge_r + host_hw + 40.0)
    ring_ry = max(ring_ry, badge_r + host_hh + 32.0)

    # Zone ellipse — large enough that fleet host cards sit fully inside
    zone_rx = ring_rx + host_hw + 36.0
    zone_ry = ring_ry + host_hh + 32.0

    # Apps outside zone: wide step so mapping cards don't stack
    app_clearance = 150.0
    app_step = 152.0  # ~ card width + gap
    apps_per_ring = 5

    # Discovered multi-ring *outside* the zone (and outside typical app fan start)
    # so fleet + mapped apps keep their prior spacing.
    disc_per_ring = 18
    disc_ring_gap = disc_hw * 2.0 + 28.0
    disc_base_rx = zone_rx + app_clearance * 0.55 + disc_hw + 36.0
    disc_base_ry = zone_ry + app_clearance * 0.55 + disc_hh + 28.0
    n_disc_rings = max(1, (n_disc + disc_per_ring - 1) // disc_per_ring) if n_disc else 0
    disc_outer_rx = disc_base_rx + max(0, n_disc_rings - 1) * disc_ring_gap + disc_hw
    disc_outer_ry = disc_base_ry + max(0, n_disc_rings - 1) * disc_ring_gap + disc_hh

    outer_need = max(
        zone_rx + app_clearance + 220.0,
        disc_outer_rx + 40.0 if n_disc else 0.0,
    )

    # Internet vector-cloud half-size (layout + edge attach; no ellipse drawn)
    inet_rx, inet_ry = 82.0, 52.0
    # Dual-tone router card half-size (matches template 132×76)
    gw_hw, gw_hh = 66.0, 38.0

    width = max(980, int(2 * outer_need + 100))
    if n_cloud:
        # Extra horizontal room so cloud hosts sit clear of Internet
        width = max(
            width,
            int(2 * outer_need + 200),
            int(2 * (inet_rx + host_hw + 120 + ((n_cloud + 1) // 2) * 20)),
        )

    # Geometry: Internet (centred) → gap → Router on LAN zone rim → zone + hosts
    lan_cx = width / 2.0
    inet_x = lan_cx
    gw_x = lan_cx

    # Room above cloud for monitoring status
    inet_y = 96.0
    gap_inet_gw = 58.0  # space between Internet bottom and Router top
    gw_y = inet_y + inet_ry + gap_inet_gw + gw_hh
    # zone top = router centre (bridges WAN ↔ LAN)
    lan_cy = gw_y + zone_ry

    height = int(lan_cy + max(zone_ry + app_clearance + 280, disc_outer_ry + 120))
    if n_cloud:
        height = max(height, int(inet_y + host_hh + 80 + ((n_cloud + 1) // 2) * 70))

    def _ellipse_radius(ux: float, uy: float, rx: float, ry: float) -> float:
        """Distance from centre to ellipse boundary along unit vector (ux, uy)."""
        den = (ux / max(rx, 1e-6)) ** 2 + (uy / max(ry, 1e-6)) ** 2
        if den <= 0:
            return float(rx)
        return 1.0 / math.sqrt(den)

    def _rect_edge(
        cx: float, cy: float, hw: float, hh: float, tx: float, ty: float
    ) -> tuple[float, float]:
        """Point on axis-aligned rect border from centre toward (tx, ty).

        Matches host / app / gateway cards (rounded rects ≈ AABB for connectors).
        """
        dx = tx - cx
        dy = ty - cy
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return cx, cy - hh
        # Scale so max(|dx|/hw, |dy|/hh) == 1 at the hit edge
        sx = abs(dx) / hw if hw > 0 else 0.0
        sy = abs(dy) / hh if hh > 0 else 0.0
        t = max(sx, sy) or 1.0
        return cx + dx / t, cy + dy / t

    def _ellipse_edge(
        cx: float, cy: float, rx: float, ry: float, tx: float, ty: float
    ) -> tuple[float, float]:
        """Point on ellipse border from centre toward (tx, ty)."""
        dx = tx - cx
        dy = ty - cy
        dist = math.hypot(dx, dy) or 1.0
        ux, uy = dx / dist, dy / dist
        r = _ellipse_radius(ux, uy, rx, ry)
        return cx + ux * r, cy + uy * r

    def _link(
        ax: float,
        ay: float,
        a_shape: tuple[str, float, float],
        bx: float,
        by: float,
        b_shape: tuple[str, float, float],
        *,
        kind: str,
        dashed: bool = False,
        path_id: Any = None,
        path_ids: list | None = None,
        from_node: str | None = None,
        to_node: str | None = None,
    ) -> dict[str, Any]:
        """Edge between two node centres, clipped to each node's border."""
        a_kind, a_w, a_h = a_shape
        b_kind, b_w, b_h = b_shape
        if a_kind == "ellipse":
            x1, y1 = _ellipse_edge(ax, ay, a_w, a_h, bx, by)
        else:
            x1, y1 = _rect_edge(ax, ay, a_w, a_h, bx, by)
        if b_kind == "ellipse":
            x2, y2 = _ellipse_edge(bx, by, b_w, b_h, ax, ay)
        else:
            x2, y2 = _rect_edge(bx, by, b_w, b_h, ax, ay)
        e: dict[str, Any] = {
            "x1": round(x1, 1),
            "y1": round(y1, 1),
            "x2": round(x2, 1),
            "y2": round(y2, 1),
            "kind": kind,
            "dashed": dashed,
        }
        if path_id is not None:
            e["path_id"] = path_id
        if path_ids is not None:
            e["path_ids"] = path_ids
        if from_node:
            e["from_node"] = from_node
        if to_node:
            e["to_node"] = to_node
        return e

    # Shape half-sizes matching SVG template geometry
    shape_inet = ("ellipse", inet_rx, inet_ry)
    shape_gw = ("rect", gw_hw, gw_hh)
    shape_lan = ("ellipse", badge_r, badge_r)
    shape_host = ("rect", host_hw, host_hh)
    shape_disc = ("rect", disc_hw, disc_hh)
    app_hw, app_hh = 70.0, 22.0
    more_hw, more_hh = 48.0, 18.0
    shape_app = ("rect", app_hw, app_hh)
    shape_more = ("rect", more_hw, more_hh)

    infra_nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    if show_internet:
        # Centred cloud — no public IP text (WAN IP lives on the Router card)
        inet_href = None
        if public_kuma and public_kuma.get("open_url"):
            inet_href = public_kuma.get("open_url")
        inet_chain = "Internet"
        if public_kuma and public_kuma.get("state"):
            inet_chain = f"Internet · {public_kuma.get('state')}"
        infra_nodes.append(
            {
                "id": "infra-internet",
                "kind": "internet",
                "label": "Internet",
                "sub": "",  # keep label clean — no IP on the cloud
                "x": round(inet_x, 1),
                "y": round(inet_y, 1),
                "rx": inet_rx,
                "ry": inet_ry,
                "href": inet_href,
                "kuma": public_kuma,
                "node_id": "internet",
                "path_chain": inet_chain,
                "open_label": "Open in Kuma" if inet_href else "",
            }
        )

    if show_gateway:
        gw_href = (gateway_kuma or {}).get("open_url") or None
        # Dual-tone bridge: top = public WAN IP, bottom = private LAN gateway IP
        pub_line = (public_ip or "").strip() or "public IP —"
        lan_line = (gateway_ip or "").strip() or "gateway —"
        chain_bits = ["Router", f"WAN {pub_line}", f"LAN {lan_line}"]
        if gateway_kuma and gateway_kuma.get("state"):
            chain_bits.append(str(gateway_kuma.get("state")))
        infra_nodes.append(
            {
                "id": "infra-gateway",
                "kind": "gateway",
                "label": "Router",
                "sub": lan_line[:28],
                "public_ip": (public_ip or "").strip(),
                "lan_ip": (gateway_ip or "").strip(),
                "sub_top": pub_line[:24],
                "sub_bottom": lan_line[:24],
                "split": True,
                "x": round(gw_x, 1),
                "y": round(gw_y, 1),
                "href": gw_href,
                "kuma": gateway_kuma,
                "ip": gateway_ip,
                "node_id": "gateway",
                "path_chain": " · ".join(chain_bits),
                "open_label": "Open in Kuma" if gw_href else "",
            }
        )
        if show_internet:
            edges.append(
                _link(
                    inet_x, inet_y, shape_inet,
                    gw_x, gw_y, shape_gw,
                    kind="wan", dashed=True,
                    from_node="internet", to_node="gateway",
                )
            )

    if show_lan:
        lan_label_sub = lan_subnet or (
            "private network"
            if any(_is_private_ip(h.get("ip")) for h in lan_hosts)
            else "home LAN"
        )
        # Centre badge = "LAN" + subnet; zone_* = outer box for hosts
        infra_nodes.append(
            {
                "id": "infra-lan",
                "kind": "lan",
                "label": "LAN",
                "sub": lan_label_sub[:32],
                "subnet": lan_label_sub[:32],
                "x": round(lan_cx, 1),
                "y": round(lan_cy, 1),
                "rx": badge_r,
                "ry": badge_r,
                "zone_rx": round(zone_rx, 1),
                "zone_ry": round(zone_ry, 1),
                "href": None,
                "node_id": "lan",
                "path_chain": (
                    f"LAN · {lan_label_sub} · {n_fleet} fleet"
                    + (f" · {n_disc} discovered" if n_disc else "")
                ),
                "open_label": "",
            }
        )
        if show_gateway:
            edges.append(
                _link(
                    gw_x, gw_y, shape_gw,
                    lan_cx, lan_cy, shape_lan,
                    kind="lan", dashed=False,
                    from_node="gateway", to_node="lan",
                )
            )
        elif show_internet:
            edges.append(
                _link(
                    inet_x, inet_y, shape_inet,
                    lan_cx, lan_cy, shape_lan,
                    kind="wan", dashed=True,
                    from_node="internet", to_node="lan",
                )
            )

    host_nodes: list[dict[str, Any]] = []
    # int server_id for fleet; ("d", discovery_id) for nmap-only devices
    host_pos: dict[Any, tuple[float, float]] = {}

    def _layout_key(h: dict[str, Any]) -> Any:
        if h.get("server_id") is not None:
            return int(h["server_id"])
        if h.get("discovery_id") is not None:
            return ("d", int(h["discovery_id"]))
        return ("ip", str(h.get("ip") or id(h)))

    def _place_host(
        h: dict[str, Any],
        x: float,
        y: float,
        *,
        is_cloud: bool,
        hw: float | None = None,
        hh: float | None = None,
    ) -> None:
        key = _layout_key(h)
        host_pos[key] = (x, y)
        sid = int(h["server_id"]) if h.get("server_id") is not None else None
        is_discovered = bool(h.get("is_discovered")) or (
            sid is None and h.get("discovery_id") is not None
        )
        box_hw = float(hw if hw is not None else (disc_hw if is_discovered else host_hw))
        box_hh = float(hh if hh is not None else (disc_hh if is_discovered else host_hh))
        shape_this = ("rect", box_hw, box_hh)
        is_edge = bool(
            sid is not None
            and any(
                s.get("via_proxy") and s.get("target_server_id") == sid for s in services
            )
        )
        apps_here = (
            sum(1 for s in services if s.get("backend_server_id") == sid)
            if sid is not None
            else 0
        )
        # Backend services (land edges from app → this host).
        path_ids = (
            [
                s.get("id")
                for s in services
                if s.get("id") is not None and s.get("backend_server_id") == sid
            ]
            if sid is not None
            else []
        )
        # Services that only use this host as NPM edge (dashed app → this host).
        npm_path_ids = (
            [
                s.get("id")
                for s in services
                if s.get("id") is not None
                and s.get("via_proxy")
                and s.get("target_server_id") == sid
                and s.get("backend_server_id") != sid
            ]
            if sid is not None
            else []
        )
        name = h.get("name") or (f"#{sid}" if sid is not None else (h.get("ip") or "?"))
        ip = h.get("ip") or ""
        dns = h.get("dns_name") or ""
        if is_discovered:
            role = "discovered · LAN"
            if h.get("device_kind_label"):
                role = f"{h.get('device_kind_label')} · discovered"
        else:
            role = "cloud · Internet" if is_cloud else "LAN host"
            if is_edge:
                role = "NPM edge · " + role
        chain_parts = [name]
        if ip:
            chain_parts.append(ip)
        if dns and dns != name:
            chain_parts.append(dns)
        if h.get("mac_vendor"):
            chain_parts.append(str(h.get("mac_vendor"))[:40])
        chain_parts.append(role)
        if apps_here:
            chain_parts.append(f"{apps_here} mapped app(s)")
        if isinstance(key, tuple) and key[0] == "d":
            node_id = f"host-d-{key[1]}"
            node_html_id = f"hd{key[1]}"
        elif sid is not None:
            node_id = f"host-{sid}"
            node_html_id = f"h{sid}"
        else:
            node_id = f"host-{key}"
            node_html_id = f"hx{abs(hash(key)) % 100000}"
        # Compact label for small discovered chips — operator name first
        if is_discovered:
            raw_label = (name or ip or "?").strip()
            # Prefer fitting the full friendly name; truncate only if needed
            label_show = raw_label[:12] if len(raw_label) > 12 else raw_label
            sub_show = (ip if raw_label != ip else "")[:14]
        else:
            label_show = name
            sub_show = dns or ip or ""
        host_nodes.append(
            {
                "id": node_html_id,
                "kind": "host",
                "label": label_show,
                "sub": sub_show,
                "ip": ip,
                "x": round(x, 1),
                "y": round(y, 1),
                "hw": box_hw,
                "hh": box_hh,
                "href": h.get("href"),
                "npm_edge": is_edge,
                "is_cloud": is_cloud,
                "is_discovered": is_discovered,
                "device_kind": h.get("device_kind") or "",
                "device_kind_label": h.get("device_kind_label") or "",
                "device_kind_short": h.get("device_kind_short") or "",
                "mac_vendor": h.get("mac_vendor") or "",
                "app_count": apps_here,
                "server_id": sid,
                "discovery_id": h.get("discovery_id"),
                "path_ids": path_ids,
                "npm_path_ids": npm_path_ids,
                "node_id": node_id,
                "path_chain": " · ".join(chain_parts),
                "open_label": h.get("open_label")
                or ("Open discovery" if is_discovered else "Open host"),
            }
        )
        host_nid = node_id
        # LAN hosts → LAN badge; cloud → Internet ellipse
        if is_cloud:
            if show_internet:
                edges.append(
                    _link(
                        x, y, shape_this,
                        inet_x, inet_y, shape_inet,
                        kind="wan", dashed=True,
                        from_node=host_nid, to_node="internet",
                    )
                )
        elif show_lan:
            edges.append(
                _link(
                    x, y, shape_this,
                    lan_cx, lan_cy, shape_lan,
                    kind="lan", dashed=bool(is_discovered),
                    from_node=host_nid, to_node="lan",
                )
            )
        elif show_gateway:
            edges.append(
                _link(
                    x, y, shape_this,
                    gw_x, gw_y, shape_gw,
                    kind="lan", dashed=bool(is_discovered),
                    from_node=host_nid, to_node="gateway",
                )
            )

    # Fleet LAN hosts on ring *inside* the zone (wide top gap: Router on zone rim)
    top_gap = math.radians(85)
    for i, h in enumerate(fleet_lan):
        if n_fleet == 1:
            angle = math.pi / 2  # bottom of ring
        elif n_fleet == 0:
            continue
        else:
            span = 2 * math.pi - top_gap
            t = (i + 0.5) / n_fleet
            angle = -math.pi / 2 + top_gap / 2 + span * t
        x = lan_cx + ring_rx * math.cos(angle)
        y = lan_cy + ring_ry * math.sin(angle)
        _place_host(h, x, y, is_cloud=False, hw=host_hw, hh=host_hh)

    # Discovered devices: multi-ring outside zone, compact chips, full-circle spread
    for i, h in enumerate(disc_lan):
        ring_i = i // disc_per_ring
        idx = i % disc_per_ring
        n_on = min(disc_per_ring, n_disc - ring_i * disc_per_ring)
        # Slight phase offset per ring so chips don't stack radially
        phase = ring_i * (math.pi / disc_per_ring)
        t = (idx + 0.5) / max(n_on, 1)
        angle = -math.pi / 2 + phase + 2 * math.pi * t
        rx = disc_base_rx + ring_i * disc_ring_gap
        ry = disc_base_ry + ring_i * disc_ring_gap
        x = lan_cx + rx * math.cos(angle)
        y = lan_cy + ry * math.sin(angle)
        _place_host(h, x, y, is_cloud=False, hw=disc_hw, hh=disc_hh)

    # Cloud / VPS hosts: clear of Internet ellipse (side attachment)
    for i, h in enumerate(cloud_hosts):
        side = -1 if (i % 2 == 0) else 1  # alternate L/R, first on left
        row = i // 2
        x = inet_x + side * (inet_rx + host_hw + 56.0 + row * 28.0)
        y = inet_y + row * 68.0
        _place_host(h, x, y, is_cloud=True, hw=host_hw, hh=host_hh)

    # Apps outside the LAN zone (or outside cloud host) — multi-ring fan, all cards
    app_nodes: list[dict[str, Any]] = []
    overflow_total = 0
    by_backend: dict[int, list[dict[str, Any]]] = {}
    for s in services:
        bid = s.get("backend_server_id")
        if bid is None:
            continue
        by_backend.setdefault(int(bid), []).append(s)

    max_shown = PHYSICAL_MESH_MAX_APPS_PER_HOST
    for bid, apps in by_backend.items():
        hx, hy = host_pos.get(bid, (lan_cx, lan_cy))
        host_rec = next((n for n in host_nodes if n.get("server_id") == bid), None)
        is_cloud_host = bool(host_rec and host_rec.get("is_cloud"))
        host_nid = f"host-{bid}"

        if is_cloud_host:
            dx, dy = hx - inet_x, hy - (inet_y + 10)
            dist = math.hypot(dx, dy) or 1.0
            ux, uy = dx / dist, dy / dist
            base_out = 130.0
        else:
            dx, dy = hx - lan_cx, hy - lan_cy
            dist = math.hypot(dx, dy) or 1.0
            ux, uy = dx / dist, dy / dist
            r_zone = _ellipse_radius(ux, uy, zone_rx, zone_ry)
            base_out = max(app_clearance, (r_zone - dist) + app_clearance)

        shown = apps[:max_shown]
        hidden = apps[max_shown:]
        n_shown = len(shown)
        px0, py0 = -uy, ux  # perpendicular for fan
        for j, s in enumerate(shown):
            ring = j // apps_per_ring
            idx = j % apps_per_ring
            n_on_ring = min(apps_per_ring, n_shown - ring * apps_per_ring)
            # Centre fan on outward axis; space by full card width
            spread = (idx - (n_on_ring - 1) / 2.0) * app_step
            r_out = base_out + ring * (app_hh * 2.0 + 36.0)
            ax = hx + ux * r_out + px0 * spread
            ay = hy + uy * r_out + py0 * spread
            path_id = s.get("id")
            app_nodes.append(
                {
                    "id": f"a{path_id}",
                    "kind": "app",
                    "label": (s.get("fqdn") or "")[:28],
                    "sub": (
                        s.get("docker_container")
                        or s.get("docker_project")
                        or s.get("path_kind")
                        or ""
                    )[:22],
                    "x": round(ax, 1),
                    "y": round(ay, 1),
                    "href": s.get("dep_href") or s.get("backend_href"),
                    "path_kind": s.get("path_kind"),
                    "via_npm": s.get("via_proxy"),
                    "path_id": path_id,
                    "path_chain": s.get("path_chain") or s.get("fqdn"),
                    "sync_status": s.get("last_sync_status") or "",
                    "has_cert": bool(s.get("certificate_id") or s.get("cert_name")),
                }
            )
            edges.append(
                _link(
                    ax, ay, shape_app,
                    hx, hy, shape_host,
                    kind="land", dashed=False, path_id=path_id,
                    from_node=None, to_node=host_nid,
                )
            )
            tid = s.get("target_server_id")
            if s.get("via_proxy") and tid and int(tid) in host_pos and int(tid) != bid:
                tx, ty = host_pos[int(tid)]
                edges.append(
                    _link(
                        ax, ay, shape_app,
                        tx, ty, shape_host,
                        kind="npm", dashed=True, path_id=path_id,
                        from_node=None, to_node=f"host-{int(tid)}",
                    )
                )

        if hidden:
            overflow_total += len(hidden)
            ring = (n_shown // apps_per_ring) + 1
            r_out = base_out + ring * (app_hh * 2.0 + 36.0)
            mx = hx + ux * r_out
            my = hy + uy * r_out
            hidden_ids = [s.get("id") for s in hidden if s.get("id") is not None]
            app_nodes.append(
                {
                    "id": f"more-{bid}",
                    "kind": "more",
                    "label": f"+{len(hidden)} more",
                    "sub": "see rack card",
                    "x": round(mx, 1),
                    "y": round(my, 1),
                    "href": f"/servers/{bid}",
                    "path_ids": hidden_ids,
                    "path_chain": f"+{len(hidden)} more apps on host (rack list)",
                }
            )
            edges.append(
                _link(
                    mx, my, shape_more,
                    hx, hy, shape_host,
                    kind="land",
                    dashed=True,
                    path_ids=hidden_ids,
                    to_node=host_nid,
                )
            )

    drawn_apps = sum(1 for n in app_nodes if n.get("kind") == "app")
    discovered_count = sum(1 for n in host_nodes if n.get("is_discovered"))
    fleet_count = len(host_nodes) - discovered_count
    return {
        "width": int(width),
        "height": int(height),
        "nodes": infra_nodes + host_nodes + app_nodes,
        "edges": edges,
        "host_count": len(host_nodes),
        "fleet_count": fleet_count,
        "discovered_count": discovered_count,
        "app_count": drawn_apps,
        "app_total": drawn_apps + overflow_total,
        "overflow_count": overflow_total,
        "lan_count": len(lan_hosts),
        "cloud_count": len(cloud_hosts),
        "network": {
            "lan_subnet": lan_subnet,
            "gateway_ip": gateway_ip,
            "public_ip": public_ip,
            "gateway_kuma": gateway_kuma,
            "public_kuma": public_kuma,
        },
    }


