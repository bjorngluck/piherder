"""Classify NSE / vulners script outputs for device UI.

Stock ``vuln`` category mixes real findings, clean negatives, and script errors
(e.g. probes for apps that are not present). Operators need that distinction.

Also attaches **port context**: explicit port from parse, or inferred from CPE /
product when the script was host-level.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence

KIND_FINDING = "finding"
KIND_CLEAR = "clear"
KIND_ERROR = "error"
KIND_INFO = "info"

_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)
_CPE_RE = re.compile(r"cpe:/[a-z]:([^:\s]+):([^:\s/]+)", re.I)

# product/service keywords → preferred services for host-level inference
_PRODUCT_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("openssh", ("ssh",)),
    ("ssh", ("ssh",)),
    ("dropbear", ("ssh",)),
    ("apache", ("http", "https", "http-proxy")),
    ("httpd", ("http", "https")),
    ("nginx", ("http", "https", "http-proxy")),
    ("openresty", ("http", "https", "http-proxy")),
    ("lighttpd", ("http", "https")),
    ("caddy", ("http", "https")),
    ("openssl", ("https", "ssl", "http")),
    ("exim", ("smtp", "submission")),
    ("postfix", ("smtp", "submission")),
    ("dovecot", ("imap", "imaps", "pop3", "pop3s")),
    ("proftpd", ("ftp", "ftp-data")),
    ("vsftpd", ("ftp",)),
    ("samba", ("microsoft-ds", "netbios-ssn", "smb")),
    ("mysql", ("mysql",)),
    ("mariadb", ("mysql",)),
    ("postgresql", ("postgresql",)),
    ("redis", ("redis",)),
    ("mongodb", ("mongodb",)),
    ("docker", ("docker",)),
    ("kubernetes", ("https", "http")),
]


def _parse_cve_ids(cve_ids_json: str | None, output: str) -> list[str]:
    ids: list[str] = []
    if cve_ids_json:
        try:
            raw = json.loads(cve_ids_json)
            if isinstance(raw, list):
                ids.extend(str(x) for x in raw if x)
        except Exception:
            pass
    for m in _CVE_RE.findall(output or ""):
        up = m.upper()
        if up not in ids:
            ids.append(up)
    return ids


def extract_cpe_products(output: str) -> list[str]:
    """Return product tokens from cpe:/a:vendor:product lines in output."""
    out: list[str] = []
    for m in _CPE_RE.finditer(output or ""):
        product = (m.group(2) or "").strip().lower()
        if product and product not in out:
            out.append(product)
    return out


def infer_port_from_output(
    output: str,
    ports: Sequence[dict[str, Any]] | None,
    *,
    script_id: str = "",
) -> dict[str, Any] | None:
    """Best-effort port match for host-level scripts using CPE/product + open ports."""
    if not ports:
        return None
    open_ports = [
        p
        for p in ports
        if str((p or {}).get("state") or "").lower() == "open"
    ]
    if not open_ports:
        open_ports = list(ports)

    products = extract_cpe_products(output or "")
    low_out = (output or "").lower()
    sid = (script_id or "").lower()

    # http-* scripts without a port: prefer first open http(s)
    if sid.startswith("http-") or "http" in sid:
        for p in open_ports:
            svc = str(p.get("service") or "").lower()
            if svc in ("http", "https", "http-proxy", "http-alt", "https-alt", "ssl/http"):
                return p
            port_n = int(p.get("port") or 0)
            if port_n in (80, 443, 8080, 8443, 8000, 8008):
                return p

    candidates: list[dict[str, Any]] = []
    for product in products:
        for hint, services in _PRODUCT_HINTS:
            if hint in product or product in hint:
                for p in open_ports:
                    svc = str(p.get("service") or "").lower()
                    prod = str(p.get("product") or "").lower()
                    if svc in services or any(h in prod for h in (hint, product)):
                        candidates.append(p)
        # direct product name in service/product fields
        for p in open_ports:
            svc = str(p.get("service") or "").lower()
            prod = str(p.get("product") or "").lower()
            if product in svc or product in prod or product.replace("_", "") in prod.replace(" ", ""):
                candidates.append(p)

    # loose: product keyword appears in output and in port product
    for p in open_ports:
        prod = str(p.get("product") or "").lower()
        if prod and len(prod) >= 3 and prod in low_out:
            candidates.append(p)

    if not candidates:
        return None
    # prefer lowest port number among matches
    candidates.sort(key=lambda p: int(p.get("port") or 0))
    return candidates[0]


def port_anchor(port: int | None, protocol: str | None = None) -> str:
    if port is None:
        return "port-host"
    proto = (protocol or "tcp").lower()
    return f"port-{port}-{proto}"


def port_label(
    port: int | None,
    protocol: str | None = None,
    *,
    service: str = "",
    product: str = "",
) -> str:
    if port is None:
        return "host"
    base = f"{port}/{(protocol or 'tcp').lower()}"
    extra = service or product
    if extra:
        return f"{base} · {extra}"
    return base


def classify_script_result(
    script_id: str,
    output: str | None = None,
    *,
    cve_ids_json: str | None = None,
    port: int | None = None,
    protocol: str | None = None,
    service: str = "",
    product: str = "",
    version: str = "",
    port_inferred: bool = False,
) -> dict[str, Any]:
    """Return classification for one script row.

    Keys: kind, label, severity, cve_ids, summary, script_id, port, protocol,
    service, product, version, port_label, port_anchor, port_inferred, target.
    """
    sid = (script_id or "").strip() or "?"
    out = output or ""
    low = out.lower()
    cves = _parse_cve_ids(cve_ids_json, out)
    plabel = port_label(port, protocol, service=service, product=product)
    panchor = port_anchor(port, protocol)
    target = plabel if port is not None else ("host" if not service else f"host · {service}")

    base_meta = {
        "port": port,
        "protocol": (protocol or "tcp") if port is not None else protocol,
        "service": service or "",
        "product": product or "",
        "version": version or "",
        "port_label": plabel,
        "port_anchor": panchor,
        "port_inferred": bool(port_inferred),
        "target": target,
    }

    def _pack(
        kind: str,
        label: str,
        *,
        severity: str | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "label": label,
            "severity": severity,
            "cve_ids": cves,
            "summary": summary,
            "script_id": sid,
            **base_meta,
        }

    # --- errors ---
    if (
        "error: script execution failed" in low
        or "script execution failed" in low
        or low.strip().startswith("error:")
        or "failed to load" in low
    ):
        return _pack(
            KIND_ERROR,
            "Script error",
            summary=_first_line(out) or "Script execution failed",
        )

    # --- explicit vulnerable ---
    if "likely vulnerable" in low:
        return _pack(
            KIND_FINDING,
            "Likely vulnerable",
            severity="medium",
            summary=_first_line(out) or "LIKELY VULNERABLE",
        )
    if re.search(r"\bvulnerable\b", low) and "not vulnerable" not in low:
        return _pack(
            KIND_FINDING,
            "Vulnerable",
            severity="high",
            summary=_first_line(out) or "VULNERABLE",
        )

    # --- vulners / CPE ---
    if cves and (
        sid.lower() in ("vulners", "vulscan")
        or "cpe:/" in low
        or "https://vulners.com" in low
    ):
        if not ("no findings" in low and sid.lower() == "vulscan" and not cves):
            products = extract_cpe_products(out)
            prod_note = f" ({', '.join(products[:2])})" if products else ""
            return _pack(
                KIND_FINDING,
                "Version / CPE match",
                severity="medium",
                summary=(
                    f"{len(cves)} CVE id(s){prod_note} — verify against real advisories"
                ),
            )

    if cves and "no findings" not in low and "couldn't find" not in low:
        return _pack(
            KIND_FINDING,
            "CVE mentioned",
            severity="low",
            summary=_first_line(out) or f"{len(cves)} CVE id(s)",
        )

    clear_phrases = (
        "couldn't find",
        "could not find",
        "no findings",
        "not vulnerable",
        "no vulnerabilities",
        "doesn't seem vulnerable",
        "does not seem vulnerable",
    )
    if any(p in low for p in clear_phrases):
        return _pack(
            KIND_CLEAR,
            "Clear",
            summary=_first_line(out) or "No findings",
        )

    return _pack(
        KIND_INFO,
        "Info",
        summary=_first_line(out) or "(no output)",
    )


def classify_script_row(
    row: Any,
    *,
    ports: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify a NmapScriptResult-like object; infer port when missing."""
    port = getattr(row, "port", None)
    protocol = getattr(row, "protocol", None)
    service = ""
    product = ""
    version = ""
    inferred = False

    # Look up port metadata from device ports list
    ports_list = list(ports or [])
    if port is not None:
        for p in ports_list:
            if int(p.get("port") or -1) == int(port) and (
                not protocol
                or str(p.get("protocol") or "tcp").lower()
                == str(protocol or "tcp").lower()
            ):
                service = str(p.get("service") or "")
                product = str(p.get("product") or "")
                version = str(p.get("version") or "")
                protocol = protocol or p.get("protocol") or "tcp"
                break
    else:
        match = infer_port_from_output(
            getattr(row, "output", None) or "",
            ports_list,
            script_id=getattr(row, "script_id", None) or "",
        )
        if match:
            port = int(match.get("port") or 0) or None
            protocol = str(match.get("protocol") or "tcp")
            service = str(match.get("service") or "")
            product = str(match.get("product") or "")
            version = str(match.get("version") or "")
            inferred = True

    return classify_script_result(
        getattr(row, "script_id", None) or "",
        getattr(row, "output", None),
        cve_ids_json=getattr(row, "cve_ids_json", None),
        port=port,
        protocol=protocol,
        service=service,
        product=product,
        version=version,
        port_inferred=inferred,
    )


def classify_scripts(
    rows: Sequence[Any],
    *,
    ports: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Classify and sort: findings first, then errors, clear, info; then by port."""
    order = {KIND_FINDING: 0, KIND_ERROR: 1, KIND_CLEAR: 2, KIND_INFO: 3}
    out: list[dict[str, Any]] = []
    for r in rows:
        c = classify_script_row(r, ports=ports)
        c["output"] = (getattr(r, "output", None) or "")[:2500]
        c["id"] = getattr(r, "id", None)
        out.append(c)
    out.sort(
        key=lambda x: (
            order.get(x["kind"], 9),
            x.get("port") is None,
            int(x.get("port") or 0),
            x.get("script_id") or "",
        )
    )
    return out


def script_summary_counts(classified: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {
        KIND_FINDING: 0,
        KIND_CLEAR: 0,
        KIND_ERROR: 0,
        KIND_INFO: 0,
        "total": 0,
    }
    for c in classified:
        k = c.get("kind") or KIND_INFO
        if k in counts:
            counts[k] += 1
        counts["total"] += 1
    return counts


def ports_with_findings(
    ports: Sequence[dict[str, Any]],
    classified: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate port rows with finding/error counts and anchor ids."""
    by_port: dict[tuple[int, str], dict[str, int]] = {}
    for c in classified:
        if c.get("port") is None:
            continue
        if c.get("kind") not in (KIND_FINDING, KIND_ERROR):
            continue
        key = (int(c["port"]), str(c.get("protocol") or "tcp").lower())
        slot = by_port.setdefault(key, {"finding": 0, "error": 0})
        if c.get("kind") == KIND_FINDING:
            slot["finding"] += 1
        else:
            slot["error"] += 1

    out: list[dict[str, Any]] = []
    for p in ports:
        port_n = int(p.get("port") or 0)
        proto = str(p.get("protocol") or "tcp").lower()
        stats = by_port.get((port_n, proto), {"finding": 0, "error": 0})
        row = dict(p)
        row["finding_count"] = stats["finding"]
        row["error_count"] = stats["error"]
        row["has_problem"] = bool(stats["finding"] or stats["error"])
        row["anchor"] = port_anchor(port_n, proto)
        out.append(row)
    return out


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:200]
    return ""
