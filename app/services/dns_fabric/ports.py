"""Published port parsing / display for topology stack panel (G Ports).

M3 lite: well-known port → role heuristics (dns/web/db/…) for chips and ownership.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# 0.0.0.0:8080->80/tcp  |  :::443->443/tcp  |  8080/tcp  |  127.0.0.1:5432->5432/tcp
_ARROW = re.compile(
    r"(?:(?P<bind>(?:\d{1,3}\.){3}\d{1,3}|\[?[0-9a-fA-F:]+\]?|\*):)?"
    r"(?P<host>\d{1,5})->(?P<container>\d{1,5})(?:/(?P<proto>tcp|udp))?",
    re.I,
)
_BARE = re.compile(r"(?P<port>\d{1,5})(?:/(?P<proto>tcp|udp))?", re.I)

# Fixed role vocabulary (map interactivity R4 / M3)
PORT_ROLE_WEB = "web"
PORT_ROLE_DNS = "dns"
PORT_ROLE_DB = "db"
PORT_ROLE_CACHE = "cache"
PORT_ROLE_PROXY = "proxy"
PORT_ROLE_SSH = "ssh"
PORT_ROLE_METRICS = "metrics"
PORT_ROLE_OTHER = "other"

PORT_ROLE_LABELS: dict[str, str] = {
    PORT_ROLE_WEB: "Web",
    PORT_ROLE_DNS: "DNS",
    PORT_ROLE_DB: "Database",
    PORT_ROLE_CACHE: "Cache",
    PORT_ROLE_PROXY: "Proxy",
    PORT_ROLE_SSH: "SSH",
    PORT_ROLE_METRICS: "Metrics",
    PORT_ROLE_OTHER: "Other",
}

# host or container port → role (checked first)
_WELL_KNOWN_PORTS: dict[int, str] = {
    22: PORT_ROLE_SSH,
    53: PORT_ROLE_DNS,
    80: PORT_ROLE_WEB,
    443: PORT_ROLE_WEB,
    8080: PORT_ROLE_WEB,
    8443: PORT_ROLE_WEB,
    3000: PORT_ROLE_WEB,
    8000: PORT_ROLE_WEB,
    8888: PORT_ROLE_WEB,
    5432: PORT_ROLE_DB,
    3306: PORT_ROLE_DB,
    27017: PORT_ROLE_DB,
    1433: PORT_ROLE_DB,
    6379: PORT_ROLE_CACHE,
    11211: PORT_ROLE_CACHE,
    9200: PORT_ROLE_DB,  # elasticsearch-ish
    5672: PORT_ROLE_OTHER,  # amqp
    9090: PORT_ROLE_METRICS,
    9100: PORT_ROLE_METRICS,
    81: PORT_ROLE_PROXY,  # NPM admin often
    2019: PORT_ROLE_PROXY,  # caddy admin
}

_NAME_ROLE_NEEDLES: list[tuple[str, tuple[str, ...]]] = [
    (PORT_ROLE_DNS, ("pihole", "pi-hole", "unbound", "coredns", "bind9", "dnsmasq")),
    (PORT_ROLE_DB, ("postgres", "mysql", "mariadb", "mongo", "redis-stack", "database", "pgvector")),
    (PORT_ROLE_CACHE, ("redis", "memcached", "valkey", "keydb")),
    (PORT_ROLE_PROXY, ("nginx", "caddy", "traefik", "npm", "proxy", "haproxy")),
    (PORT_ROLE_METRICS, ("prometheus", "grafana", "node-exporter", "cadvisor")),
    (PORT_ROLE_WEB, ("web", "frontend", "ui", "http", "httpd", "apache")),
]


def guess_port_role(
    *,
    host_port: str | int | None = None,
    container_port: str | int | None = None,
    proto: str | None = None,
    service_name: str | None = None,
    image: str | None = None,
    container_name: str | None = None,
) -> str:
    """Heuristic port role for chips (suggest-only; no sticky storage in M3)."""
    blob = " ".join(
        filter(
            None,
            [
                (service_name or "").lower(),
                (container_name or "").lower(),
                (image or "").lower().split("/")[-1].split(":")[0],
            ],
        )
    )
    # DNS over 53 even when image is pihole (multi-port: 53 dns, 443 web)
    for raw in (host_port, container_port):
        try:
            p = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if p == 53 or (proto or "").lower() == "udp" and p == 53:
            return PORT_ROLE_DNS
        if p in _WELL_KNOWN_PORTS:
            # Prefer name-based when well-known is generic web but name is dns product
            if _WELL_KNOWN_PORTS[p] == PORT_ROLE_WEB and blob:
                for role, needles in _NAME_ROLE_NEEDLES:
                    if role == PORT_ROLE_DNS and any(n in blob for n in needles):
                        # 80/443 on pihole → still web UI, not dns
                        if p in (80, 443, 8080, 8443):
                            return PORT_ROLE_WEB
            return _WELL_KNOWN_PORTS[p]

    for role, needles in _NAME_ROLE_NEEDLES:
        for n in needles:
            if n and n in blob:
                return role
    return PORT_ROLE_OTHER


def port_role_label(role: Optional[str]) -> str:
    r = (role or PORT_ROLE_OTHER).strip().lower()
    return PORT_ROLE_LABELS.get(r, PORT_ROLE_LABELS[PORT_ROLE_OTHER])


def parse_published_ports(
    ports_display: str | None = None,
    ports: Any = None,
) -> list[dict[str, Any]]:
    """Return structured published mappings (host → container).

    Each item: host, container, proto, bind, label (e.g. ``8080→80/tcp``).
    """
    chunks: list[str] = []
    if ports_display and str(ports_display).strip() not in ("", "—", "-"):
        chunks.append(str(ports_display))
    if isinstance(ports, list):
        for p in ports:
            if p is not None and str(p).strip():
                chunks.append(str(p))
    elif isinstance(ports, str) and ports.strip() and ports not in ("—", "-"):
        chunks.append(ports)
    text = ", ".join(chunks)
    if not text.strip():
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for m in _ARROW.finditer(text):
        host = m.group("host")
        cont = m.group("container")
        proto = (m.group("proto") or "tcp").lower()
        bind = (m.group("bind") or "").strip("[]") or "*"
        key = f"{bind}:{host}->{cont}/{proto}"
        if key in seen:
            continue
        seen.add(key)
        label = f"{host}→{cont}/{proto}"
        if bind not in ("*", "0.0.0.0", "::", ""):
            label = f"{bind}:{host}→{cont}/{proto}"
        out.append(
            {
                "host": host,
                "container": cont,
                "proto": proto,
                "bind": bind,
                "label": label,
                "published": True,
            }
        )

    if out:
        return out[:16]

    # Fallback: bare container ports (often means no host publish, or host=container)
    for m in _BARE.finditer(text):
        port = m.group("port")
        proto = (m.group("proto") or "tcp").lower()
        if port in ("22",):
            continue
        key = f"{port}/{proto}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "host": port,
                "container": port,
                "proto": proto,
                "bind": "*",
                "label": f"{port}/{proto}",
                "published": False,
            }
        )
    return out[:16]


def format_ports_short(parsed: list[dict[str, Any]] | None, *, limit: int = 4) -> str:
    """Compact chip string for list rows."""
    if not parsed:
        return ""
    labels = [p["label"] for p in parsed[: max(1, limit)]]
    extra = len(parsed) - len(labels)
    s = ", ".join(labels)
    if extra > 0:
        s += f" +{extra}"
    return s


def enrich_container_ports(c: dict[str, Any]) -> dict[str, Any]:
    """Add ports_parsed + ports_short (+ role heuristics) onto a container dict."""
    parsed = parse_published_ports(c.get("ports_display"), c.get("ports"))
    svc = (
        c.get("compose_service")
        or c.get("service")
        or c.get("name")
        or ""
    )
    image = c.get("image") or ""
    cname = c.get("container_name") or c.get("name") or ""
    for p in parsed:
        role = guess_port_role(
            host_port=p.get("host"),
            container_port=p.get("container"),
            proto=p.get("proto"),
            service_name=str(svc),
            image=str(image),
            container_name=str(cname),
        )
        p["role"] = role
        p["role_label"] = port_role_label(role)
        # Chip secondary text: "443 web" style when role is useful
        if role and role != PORT_ROLE_OTHER:
            p["label_with_role"] = f"{p['label']} · {p['role_label']}"
        else:
            p["label_with_role"] = p["label"]
    c["ports_parsed"] = parsed
    c["ports_short"] = format_ports_short(parsed)
    c["ports_host"] = [p["host"] for p in parsed if p.get("published")]
    if parsed and any(p.get("published") for p in parsed):
        c["ports_summary"] = format_ports_short(
            [p for p in parsed if p.get("published")], limit=6
        )
    elif parsed:
        c["ports_summary"] = "internal only"
    else:
        c["ports_summary"] = "no host publish"
    return c
