"""Published port parsing / display for topology stack panel (G Ports)."""
from __future__ import annotations

import re
from typing import Any


# 0.0.0.0:8080->80/tcp  |  :::443->443/tcp  |  8080/tcp  |  127.0.0.1:5432->5432/tcp
_ARROW = re.compile(
    r"(?:(?P<bind>(?:\d{1,3}\.){3}\d{1,3}|\[?[0-9a-fA-F:]+\]?|\*):)?"
    r"(?P<host>\d{1,5})->(?P<container>\d{1,5})(?:/(?P<proto>tcp|udp))?",
    re.I,
)
_BARE = re.compile(r"(?P<port>\d{1,5})(?:/(?P<proto>tcp|udp))?", re.I)


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
    """Add ports_parsed + ports_short onto a container dict (in place + return)."""
    parsed = parse_published_ports(c.get("ports_display"), c.get("ports"))
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
