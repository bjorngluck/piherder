"""Parse nmap -oX XML into structured host/port/script records."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.I)


@dataclass
class ParsedPort:
    port: int
    protocol: str
    state: str
    service: str = ""
    product: str = ""
    version: str = ""


@dataclass
class ParsedScript:
    script_id: str
    output: str
    cve_ids: list[str] = field(default_factory=list)


@dataclass
class ParsedHost:
    ip_address: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    status: str = "up"  # up | down | unknown
    os_summary: Optional[str] = None
    ports: list[ParsedPort] = field(default_factory=list)
    scripts: list[ParsedScript] = field(default_factory=list)


def _text(el: Optional[ET.Element], default: str = "") -> str:
    if el is None:
        return default
    return (el.text or "").strip() or default


def _cves_from_text(text: str) -> list[str]:
    found = _CVE_RE.findall(text or "")
    # preserve order, unique case-normalized
    seen: set[str] = set()
    out: list[str] = []
    for c in found:
        u = c.upper()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _parse_host(host_el: ET.Element) -> Optional[ParsedHost]:
    status_el = host_el.find("status")
    status = (status_el.get("state") if status_el is not None else None) or "unknown"

    ip = ""
    mac = None
    for addr in host_el.findall("address"):
        addrtype = (addr.get("addrtype") or "").lower()
        val = (addr.get("addr") or "").strip()
        if not val:
            continue
        if addrtype in ("ipv4", "ipv6") and not ip:
            ip = val
        elif addrtype == "mac":
            mac = val.upper()

    if not ip:
        return None

    hostname = None
    hostnames = host_el.find("hostnames")
    if hostnames is not None:
        for hn in hostnames.findall("hostname"):
            name = (hn.get("name") or "").strip()
            if name:
                hostname = name
                break

    os_summary = None
    os_el = host_el.find("os")
    if os_el is not None:
        match = os_el.find("osmatch")
        if match is not None:
            os_summary = (match.get("name") or "").strip() or None

    ports: list[ParsedPort] = []
    ports_el = host_el.find("ports")
    if ports_el is not None:
        for p in ports_el.findall("port"):
            try:
                port_id = int(p.get("portid") or 0)
            except ValueError:
                continue
            proto = (p.get("protocol") or "tcp").lower()
            st_el = p.find("state")
            state = (st_el.get("state") if st_el is not None else None) or "unknown"
            svc_el = p.find("service")
            service = (svc_el.get("name") if svc_el is not None else None) or ""
            product = (svc_el.get("product") if svc_el is not None else None) or ""
            version = (svc_el.get("version") if svc_el is not None else None) or ""
            ports.append(
                ParsedPort(
                    port=port_id,
                    protocol=proto,
                    state=state,
                    service=service,
                    product=product,
                    version=version,
                )
            )
            # port-level scripts
            for sc in p.findall("script"):
                sid = (sc.get("id") or "").strip()
                out = (sc.get("output") or "").strip() or _text(sc)
                if sid:
                    # attach later via host scripts list for simplicity
                    pass

    scripts: list[ParsedScript] = []
    # host-level scripts
    hostscript = host_el.find("hostscript")
    script_parents = [host_el]
    if hostscript is not None:
        script_parents.append(hostscript)
    if ports_el is not None:
        script_parents.append(ports_el)

    seen_scripts: set[str] = set()
    for parent in script_parents:
        for sc in parent.findall(".//script"):
            sid = (sc.get("id") or "").strip()
            if not sid or sid in seen_scripts:
                continue
            seen_scripts.add(sid)
            out = (sc.get("output") or "").strip()
            if not out:
                out = _text(sc)
            scripts.append(
                ParsedScript(
                    script_id=sid,
                    output=out,
                    cve_ids=_cves_from_text(out),
                )
            )

    return ParsedHost(
        ip_address=ip,
        hostname=hostname,
        mac_address=mac,
        status=status,
        os_summary=os_summary,
        ports=ports,
        scripts=scripts,
    )


def parse_nmap_xml(xml_text: str) -> list[ParsedHost]:
    """Parse nmap XML document; return hosts (up and down)."""
    if not (xml_text or "").strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"invalid nmap XML: {e}") from e

    hosts: list[ParsedHost] = []
    for host_el in root.findall("host"):
        h = _parse_host(host_el)
        if h is not None:
            hosts.append(h)
    return hosts


def open_ports(host: ParsedHost) -> list[ParsedPort]:
    return [p for p in host.ports if (p.state or "").lower() == "open"]
