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
    """Full-fleet physical SVG: Internet → Router → LAN fan → apps.

    Always draws the WAN/LAN spine when any fleet host exists:
      Internet (centred) ──(wan)── Router on LAN zone rim
      LAN badge (centre) · fleet hosts on a **fan/ring** · apps outside
      Discovered chips on **outer rings**; zone has two sizes:
        - compact = fleet only (default when Discovered is off)
        - expanded = includes discovery (Discovered toggle on)

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

    # Split fleet vs discovery — fleet fan stays fixed; discovery sits on outer rings
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
    for h in lan_hosts:
        if h in fleet_lan or h in disc_lan:
            continue
        fleet_lan.append(h)

    show_spine = bool(named or lan_subnet or gateway_ip or public_ip)
    show_gateway = show_spine and (
        bool(gateway_ip) or bool(lan_hosts) or bool(lan_subnet)
    )
    show_lan = show_spine and (bool(lan_hosts) or bool(lan_subnet))
    show_internet = show_spine

    n_fleet = len(fleet_lan)
    n_disc = len(disc_lan)
    n_lan_h = n_fleet
    n_cloud = len(cloud_hosts)

    # --- Fan geometry: compact fleet ring · outer discovery · dual zone sizes ---
    host_hw, host_hh = 62.0, 30.0
    disc_hw, disc_hh = 38.0, 17.0
    badge_r = 54.0
    fleet_top_gap = math.radians(85)
    fleet_span = 2.0 * math.pi - fleet_top_gap

    # Fleet fan radius — scales with count (condensed for few hosts)
    if n_fleet <= 0:
        ring_rx = ring_ry = 0.0
    elif n_fleet == 1:
        ring_rx, ring_ry = 128.0, 102.0
    elif n_fleet == 2:
        ring_rx, ring_ry = 138.0, 108.0
    elif n_fleet <= 4:
        ring_rx = 128.0 + n_fleet * 14.0
        ring_ry = 100.0 + n_fleet * 10.0
    else:
        min_arc = host_hw * 2.05
        r_need = (n_fleet * min_arc) / max(fleet_span, 1e-6)
        ring_rx = min(340.0, max(badge_r + host_hw + 28.0, r_need * 1.10))
        ring_ry = min(250.0, max(badge_r + host_hh + 22.0, r_need * 0.86))
    if n_fleet >= 1:
        ring_rx = max(ring_rx, badge_r + host_hw + 28.0)
        ring_ry = max(ring_ry, badge_r + host_hh + 22.0)

    # Compact zone = fleet cards fully inside (hosts-only / Discovered off)
    zone_rx_compact = (
        (ring_rx + host_hw + 30.0) if n_fleet >= 1 else (badge_r + 48.0)
    )
    zone_ry_compact = (
        (ring_ry + host_hh + 26.0) if n_fleet >= 1 else (badge_r + 40.0)
    )
    if n_fleet == 0 and n_disc == 0:
        zone_rx_compact = badge_r + 48.0
        zone_ry_compact = badge_r + 40.0

    # Discovered multi-ring *outside* compact zone, *inside* expanded zone
    disc_per_ring = 16
    disc_ring_gap = disc_hw * 2.0 + 22.0
    if n_fleet >= 1:
        disc_base_rx = zone_rx_compact + disc_hw + 18.0
        disc_base_ry = zone_ry_compact + disc_hh + 14.0
    else:
        disc_base_rx = badge_r + disc_hw + 40.0
        disc_base_ry = badge_r + disc_hh + 32.0
    n_disc_rings = max(1, (n_disc + disc_per_ring - 1) // disc_per_ring) if n_disc else 0
    disc_outer_rx = (
        disc_base_rx + max(0, n_disc_rings - 1) * disc_ring_gap if n_disc else 0.0
    )
    disc_outer_ry = (
        disc_base_ry + max(0, n_disc_rings - 1) * disc_ring_gap if n_disc else 0.0
    )

    # Expanded zone = includes discovery chips (Discovered on)
    if n_disc:
        zone_rx_full = max(zone_rx_compact, disc_outer_rx + disc_hw + 28.0)
        zone_ry_full = max(zone_ry_compact, disc_outer_ry + disc_hh + 24.0)
    else:
        zone_rx_full = zone_rx_compact
        zone_ry_full = zone_ry_compact

    # Full zone for canvas / default paint; compact positions for Discovered-off
    zone_rx, zone_ry = zone_rx_full, zone_ry_full

    app_clearance_c = 120.0 if n_fleet <= 2 else 140.0
    app_clearance_f = 150.0 if n_disc else app_clearance_c
    app_step = 152.0
    apps_per_ring = 5
    outer_need = zone_rx_full + app_clearance_f + 200.0

    inet_rx, inet_ry = 82.0, 52.0
    gw_hw, gw_hh = 66.0, 38.0
    gap_inet_gw = 58.0

    width = max(980, int(2 * outer_need + 100))
    if n_cloud:
        width = max(
            width,
            int(2 * outer_need + 200),
            int(2 * (inet_rx + host_hw + 120 + ((n_cloud + 1) // 2) * 20)),
        )

    # Shared LAN centre (badge + fan hub). Router sits on the *active* zone rim:
    # full → top of expanded ellipse; compact → top of fleet-only ellipse.
    lan_cx = width / 2.0
    inet_x_f = inet_x_c = lan_cx
    gw_x_f = gw_x_c = lan_cx

    inet_y_f = 96.0
    gw_y_f = inet_y_f + inet_ry + gap_inet_gw + gw_hh
    lan_cy = gw_y_f + zone_ry_full

    gw_y_c = lan_cy - zone_ry_compact  # router on compact LAN / Internet boundary
    inet_y_c = gw_y_c - gap_inet_gw - gw_hh - inet_ry

    # Default (full network view) spine aliases used by edge helpers below
    inet_x, inet_y = inet_x_f, inet_y_f
    gw_x, gw_y = gw_x_f, gw_y_f
    zone_cx, zone_cy = lan_cx, lan_cy
    badge_x, badge_y = lan_cx, lan_cy

    height = int(lan_cy + zone_ry_full + app_clearance_f + 280)
    if n_cloud:
        height = max(
            height,
            int(max(inet_y_f, inet_y_c) + host_hh + 80 + ((n_cloud + 1) // 2) * 70),
        )

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
        # Optional compact-mode centres (same shapes); full is ax/ay/bx/by
        compact: tuple[float, float, float, float] | None = None,
    ) -> dict[str, Any]:
        """Edge between two node centres, clipped to each node's border.

        When ``compact`` is (ax_c, ay_c, bx_c, by_c), also store dual endpoints
        so the Hosts map toggle can shrink Internet/Router/apps without reflow.
        """
        a_kind, a_w, a_h = a_shape
        b_kind, b_w, b_h = b_shape

        def _ends(
            pax: float, pay: float, pbx: float, pby: float
        ) -> tuple[float, float, float, float]:
            if a_kind == "ellipse":
                x1, y1 = _ellipse_edge(pax, pay, a_w, a_h, pbx, pby)
            else:
                x1, y1 = _rect_edge(pax, pay, a_w, a_h, pbx, pby)
            if b_kind == "ellipse":
                x2, y2 = _ellipse_edge(pbx, pby, b_w, b_h, pax, pay)
            else:
                x2, y2 = _rect_edge(pbx, pby, b_w, b_h, pax, pay)
            return x1, y1, x2, y2

        x1, y1, x2, y2 = _ends(ax, ay, bx, by)
        e: dict[str, Any] = {
            "x1": round(x1, 1),
            "y1": round(y1, 1),
            "x2": round(x2, 1),
            "y2": round(y2, 1),
            "kind": kind,
            "dashed": dashed,
            "x1_full": round(x1, 1),
            "y1_full": round(y1, 1),
            "x2_full": round(x2, 1),
            "y2_full": round(y2, 1),
        }
        if compact is not None:
            cax, cay, cbx, cby = compact
            cx1, cy1, cx2, cy2 = _ends(cax, cay, cbx, cby)
            e["x1_compact"] = round(cx1, 1)
            e["y1_compact"] = round(cy1, 1)
            e["x2_compact"] = round(cx2, 1)
            e["y2_compact"] = round(cy2, 1)
            e["layout_dual"] = True
        else:
            e["x1_compact"] = e["x1_full"]
            e["y1_compact"] = e["y1_full"]
            e["x2_compact"] = e["x2_full"]
            e["y2_compact"] = e["y2_full"]
            e["layout_dual"] = False
        if path_id is not None:
            e["path_id"] = path_id
        if path_ids is not None:
            e["path_ids"] = path_ids
        if from_node:
            e["from_node"] = from_node
        if to_node:
            e["to_node"] = to_node
        return e

    def _xy_dual(
        x_f: float, y_f: float, x_c: float, y_c: float
    ) -> dict[str, float]:
        """Absolute full coords + dual pair for client toggle transforms."""
        return {
            "x": round(x_f, 1),
            "y": round(y_f, 1),
            "x_full": round(x_f, 1),
            "y_full": round(y_f, 1),
            "x_compact": round(x_c, 1),
            "y_compact": round(y_c, 1),
            "layout_dual": abs(x_f - x_c) > 0.05 or abs(y_f - y_c) > 0.05,
        }

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
                "sub": "",
                "rx": inet_rx,
                "ry": inet_ry,
                "href": inet_href,
                "kuma": public_kuma,
                "node_id": "internet",
                "path_chain": inet_chain,
                "open_label": "Open in Kuma" if inet_href else "",
                **_xy_dual(inet_x_f, inet_y_f, inet_x_c, inet_y_c),
            }
        )

    if show_gateway:
        gateway_label = (net.get("gateway_label") or "").strip()
        gateway_disc_href = (net.get("gateway_href") or "").strip()
        gw_href = (
            (gateway_kuma or {}).get("open_url")
            or gateway_disc_href
            or None
        )
        # Dual-tone bridge: top = public WAN IP, bottom = private LAN gateway IP
        # Position: on LAN zone rim (full when network on, compact when off)
        pub_line = (public_ip or "").strip() or "public IP —"
        lan_line = (gateway_ip or "").strip() or "gateway —"
        router_label = gateway_label or "Router"
        chain_bits = [router_label, f"WAN {pub_line}", f"LAN {lan_line}"]
        if gateway_kuma and gateway_kuma.get("state"):
            chain_bits.append(str(gateway_kuma.get("state")))
        open_lbl = ""
        if (gateway_kuma or {}).get("open_url"):
            open_lbl = "Open in Kuma"
        elif gateway_disc_href:
            open_lbl = "Open discovery"
        infra_nodes.append(
            {
                "id": "infra-gateway",
                "kind": "gateway",
                "label": router_label[:18] if len(router_label) > 18 else router_label,
                "sub": lan_line[:28],
                "public_ip": (public_ip or "").strip(),
                "lan_ip": (gateway_ip or "").strip(),
                "sub_top": pub_line[:24],
                "sub_bottom": lan_line[:24],
                "split": True,
                "href": gw_href,
                "kuma": gateway_kuma,
                "ip": gateway_ip,
                "node_id": "gateway",
                "path_chain": " · ".join(chain_bits),
                "open_label": open_lbl,
                **_xy_dual(gw_x_f, gw_y_f, gw_x_c, gw_y_c),
            }
        )
        if show_internet:
            edges.append(
                _link(
                    inet_x_f, inet_y_f, shape_inet,
                    gw_x_f, gw_y_f, shape_gw,
                    kind="wan", dashed=True,
                    from_node="internet", to_node="gateway",
                    compact=(inet_x_c, inet_y_c, gw_x_c, gw_y_c),
                )
            )

    if show_lan:
        lan_label_sub = lan_subnet or (
            "private network"
            if any(_is_private_ip(h.get("ip")) for h in lan_hosts)
            else "home LAN"
        )
        # Centre badge fixed; zone ellipse compact/full for toggle
        infra_nodes.append(
            {
                "id": "infra-lan",
                "kind": "lan",
                "label": "LAN",
                "sub": lan_label_sub[:32],
                "subnet": lan_label_sub[:32],
                "rx": badge_r,
                "ry": badge_r,
                "zone_cx": round(zone_cx, 1),
                "zone_cy": round(zone_cy, 1),
                "zone_rx": round(zone_rx_full, 1),
                "zone_ry": round(zone_ry_full, 1),
                "zone_rx_compact": round(zone_rx_compact, 1),
                "zone_ry_compact": round(zone_ry_compact, 1),
                "zone_rx_full": round(zone_rx_full, 1),
                "zone_ry_full": round(zone_ry_full, 1),
                "href": None,
                "node_id": "lan",
                "path_chain": (
                    f"LAN · {lan_label_sub} · {n_fleet} fleet"
                    + (f" · {n_disc} discovered" if n_disc else "")
                ),
                "open_label": "",
                **_xy_dual(lan_cx, lan_cy, lan_cx, lan_cy),
            }
        )
        if show_gateway:
            edges.append(
                _link(
                    gw_x_f, gw_y_f, shape_gw,
                    lan_cx, lan_cy, shape_lan,
                    kind="lan", dashed=False,
                    from_node="gateway", to_node="lan",
                    compact=(gw_x_c, gw_y_c, lan_cx, lan_cy),
                )
            )
        elif show_internet:
            edges.append(
                _link(
                    inet_x_f, inet_y_f, shape_inet,
                    lan_cx, lan_cy, shape_lan,
                    kind="wan", dashed=True,
                    from_node="internet", to_node="lan",
                    compact=(inet_x_c, inet_y_c, lan_cx, lan_cy),
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
        x_compact: float | None = None,
        y_compact: float | None = None,
    ) -> None:
        key = _layout_key(h)
        xc = float(x if x_compact is None else x_compact)
        yc = float(y if y_compact is None else y_compact)
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
                **_xy_dual(x, y, xc, yc),
            }
        )
        host_nid = node_id
        # LAN hosts → LAN badge; cloud → Internet ellipse (dual for spine)
        if is_cloud:
            if show_internet:
                edges.append(
                    _link(
                        x, y, shape_this,
                        inet_x_f, inet_y_f, shape_inet,
                        kind="wan", dashed=True,
                        from_node=host_nid, to_node="internet",
                        compact=(xc, yc, inet_x_c, inet_y_c),
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
                    gw_x_f, gw_y_f, shape_gw,
                    kind="lan", dashed=bool(is_discovered),
                    from_node=host_nid, to_node="gateway",
                    compact=(x, y, gw_x_c, gw_y_c),
                )
            )

    # Fleet on fan/ring inside compact zone (wide top gap for Router → LAN)
    for i, h in enumerate(fleet_lan):
        if n_fleet == 1:
            angle = math.pi / 2  # bottom of fan
        elif n_fleet == 0:
            continue
        else:
            t = (i + 0.5) / n_fleet
            angle = -math.pi / 2 + fleet_top_gap / 2 + fleet_span * t
        x = lan_cx + ring_rx * math.cos(angle)
        y = lan_cy + ring_ry * math.sin(angle)
        _place_host(h, x, y, is_cloud=False, hw=host_hw, hh=host_hh)

    # Discovered: multi-ring outside compact zone (inside expanded); full-circle fan
    for i, h in enumerate(disc_lan):
        ring_i = i // disc_per_ring
        idx = i % disc_per_ring
        n_on = min(disc_per_ring, n_disc - ring_i * disc_per_ring)
        phase = ring_i * (math.pi / disc_per_ring)
        t = (idx + 0.5) / max(n_on, 1)
        angle = -math.pi / 2 + phase + 2 * math.pi * t
        rx = disc_base_rx + ring_i * disc_ring_gap
        ry = disc_base_ry + ring_i * disc_ring_gap
        x = lan_cx + rx * math.cos(angle)
        y = lan_cy + ry * math.sin(angle)
        _place_host(h, x, y, is_cloud=False, hw=disc_hw, hh=disc_hh)

    # Cloud / VPS hosts: side of Internet — move with compact/full Internet Y
    for i, h in enumerate(cloud_hosts):
        side = -1 if (i % 2 == 0) else 1
        row = i // 2
        ox = side * (inet_rx + host_hw + 56.0 + row * 28.0)
        oy = row * 68.0
        x_f = inet_x_f + ox
        y_f = inet_y_f + oy
        x_c = inet_x_c + ox
        y_c = inet_y_c + oy
        _place_host(
            h, x_f, y_f, is_cloud=True, hw=host_hw, hh=host_hh,
            x_compact=x_c, y_compact=y_c,
        )

    # Apps fan outside compact zone (hosts-only) and full zone (network view)
    app_nodes: list[dict[str, Any]] = []
    overflow_total = 0
    by_backend: dict[int, list[dict[str, Any]]] = {}
    for s in services:
        bid = s.get("backend_server_id")
        if bid is None:
            continue
        by_backend.setdefault(int(bid), []).append(s)

    def _app_fan_basis(
        hx: float,
        hy: float,
        *,
        is_cloud_host: bool,
        zrx: float,
        zry: float,
        clearance: float,
        inet_y_ref: float,
    ) -> tuple[float, float, float, float, float]:
        """Return ux, uy, base_out, px0, py0 for app fan."""
        if is_cloud_host:
            dx, dy = hx - inet_x_f, hy - (inet_y_ref + 10)
            dist = math.hypot(dx, dy) or 1.0
            ux, uy = dx / dist, dy / dist
            base_out = 130.0
        else:
            dx, dy = hx - lan_cx, hy - lan_cy
            dist = math.hypot(dx, dy) or 1.0
            ux, uy = dx / dist, dy / dist
            r_zone = _ellipse_radius(ux, uy, zrx, zry)
            base_out = max(clearance, (r_zone - dist) + clearance)
        return ux, uy, base_out, -uy, ux

    max_shown = PHYSICAL_MESH_MAX_APPS_PER_HOST
    for bid, apps in by_backend.items():
        hx, hy = host_pos.get(bid, (lan_cx, lan_cy))
        host_rec = next((n for n in host_nodes if n.get("server_id") == bid), None)
        is_cloud_host = bool(host_rec and host_rec.get("is_cloud"))
        host_nid = f"host-{bid}"
        # Cloud host centre also moves in compact mode
        hx_c = float(host_rec["x_compact"]) if host_rec and host_rec.get("x_compact") is not None else hx
        hy_c = float(host_rec["y_compact"]) if host_rec and host_rec.get("y_compact") is not None else hy

        ux_f, uy_f, base_f, px0_f, py0_f = _app_fan_basis(
            hx, hy, is_cloud_host=is_cloud_host,
            zrx=zone_rx_full, zry=zone_ry_full, clearance=app_clearance_f,
            inet_y_ref=inet_y_f,
        )
        ux_c, uy_c, base_c, px0_c, py0_c = _app_fan_basis(
            hx_c, hy_c, is_cloud_host=is_cloud_host,
            zrx=zone_rx_compact, zry=zone_ry_compact, clearance=app_clearance_c,
            inet_y_ref=inet_y_c,
        )

        shown = apps[:max_shown]
        hidden = apps[max_shown:]
        n_shown = len(shown)
        for j, s in enumerate(shown):
            ring = j // apps_per_ring
            idx = j % apps_per_ring
            n_on_ring = min(apps_per_ring, n_shown - ring * apps_per_ring)
            spread = (idx - (n_on_ring - 1) / 2.0) * app_step
            r_out_f = base_f + ring * (app_hh * 2.0 + 36.0)
            r_out_c = base_c + ring * (app_hh * 2.0 + 36.0)
            ax_f = hx + ux_f * r_out_f + px0_f * spread
            ay_f = hy + uy_f * r_out_f + py0_f * spread
            ax_c = hx_c + ux_c * r_out_c + px0_c * spread
            ay_c = hy_c + uy_c * r_out_c + py0_c * spread
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
                    "href": s.get("dep_href") or s.get("backend_href"),
                    "path_kind": s.get("path_kind"),
                    "via_npm": s.get("via_proxy"),
                    "path_id": path_id,
                    "path_chain": s.get("path_chain") or s.get("fqdn"),
                    "sync_status": s.get("last_sync_status") or "",
                    "has_cert": bool(s.get("certificate_id") or s.get("cert_name")),
                    **_xy_dual(ax_f, ay_f, ax_c, ay_c),
                }
            )
            edges.append(
                _link(
                    ax_f, ay_f, shape_app,
                    hx, hy, shape_host,
                    kind="land", dashed=False, path_id=path_id,
                    from_node=None, to_node=host_nid,
                    compact=(ax_c, ay_c, hx_c, hy_c),
                )
            )
            tid = s.get("target_server_id")
            if s.get("via_proxy") and tid and int(tid) in host_pos and int(tid) != bid:
                tx, ty = host_pos[int(tid)]
                t_rec = next(
                    (n for n in host_nodes if n.get("server_id") == int(tid)), None
                )
                tx_c = float(t_rec["x_compact"]) if t_rec else tx
                ty_c = float(t_rec["y_compact"]) if t_rec else ty
                edges.append(
                    _link(
                        ax_f, ay_f, shape_app,
                        tx, ty, shape_host,
                        kind="npm", dashed=True, path_id=path_id,
                        from_node=None, to_node=f"host-{int(tid)}",
                        compact=(ax_c, ay_c, tx_c, ty_c),
                    )
                )

        if hidden:
            overflow_total += len(hidden)
            ring = (n_shown // apps_per_ring) + 1
            r_out_f = base_f + ring * (app_hh * 2.0 + 36.0)
            r_out_c = base_c + ring * (app_hh * 2.0 + 36.0)
            mx_f = hx + ux_f * r_out_f
            my_f = hy + uy_f * r_out_f
            mx_c = hx_c + ux_c * r_out_c
            my_c = hy_c + uy_c * r_out_c
            hidden_ids = [s.get("id") for s in hidden if s.get("id") is not None]
            app_nodes.append(
                {
                    "id": f"more-{bid}",
                    "kind": "more",
                    "label": f"+{len(hidden)} more",
                    "sub": "see rack card",
                    "href": f"/servers/{bid}",
                    "path_ids": hidden_ids,
                    "path_chain": f"+{len(hidden)} more apps on host (rack list)",
                    **_xy_dual(mx_f, my_f, mx_c, my_c),
                }
            )
            edges.append(
                _link(
                    mx_f, my_f, shape_more,
                    hx, hy, shape_host,
                    kind="land",
                    dashed=True,
                    path_ids=hidden_ids,
                    to_node=host_nid,
                    compact=(mx_c, my_c, hx_c, hy_c),
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


