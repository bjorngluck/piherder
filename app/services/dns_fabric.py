"""End-to-end DNS fabric: host A records + service CNAMEs + mesh view.

Hosts declare a LAN FQDN (Server.dns_name) → A on all Pi-holes.
Services declare a FQDN → CNAME to a host's dns_name (often NPM edge).
Backend host may differ from DNS target (proxy topology).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..models import ManagedCertificate, Server, ServiceDnsRecord
from .audit_write import make_audit_log
from .integrations import pihole as ph
from .integrations import registry as reg

logger = logging.getLogger(__name__)

_FQDN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)
_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)


class DnsFabricError(Exception):
    def __init__(self, message: str, code: str = "error"):
        self.message = message
        self.code = code
        super().__init__(message)


def normalize_fqdn(value: str | None) -> str:
    s = (value or "").strip().lower().rstrip(".")
    return s


def is_valid_fqdn(value: str | None) -> bool:
    s = normalize_fqdn(value)
    if not s or len(s) > 253:
        return False
    # Allow single-label only if it has a dot elsewhere requirement — require ≥1 dot
    if "." not in s:
        return False
    return bool(_FQDN_RE.match(s))


def is_valid_ipv4(value: str | None) -> bool:
    return bool(_IPV4_RE.match((value or "").strip()))


def host_ip_for_dns(server: Server) -> str:
    """Resolve IP used for host A record."""
    for cand in (
        (server.dns_ip_override or "").strip(),
        (server.ip_address or "").strip(),
    ):
        if cand and is_valid_ipv4(cand):
            return cand
    # hostname itself may be an IP
    host = (server.hostname or "").strip()
    if is_valid_ipv4(host):
        return host
    return ""


def suggest_host_dns_name(server: Server, base_domain: str = "") -> str:
    base = normalize_fqdn(base_domain)
    slug = re.sub(r"[^a-z0-9-]+", "-", (server.name or server.hostname or "host").lower())
    slug = re.sub(r"-+", "-", slug).strip("-") or "host"
    if base:
        return f"{slug}.{base}"
    return ""


def _server_name_tokens(server: Server) -> set[str]:
    """Tokens useful for matching Pi-hole host entries to a fleet server."""
    tokens: set[str] = set()
    for raw in (server.name, server.hostname, server.dns_name):
        s = normalize_fqdn(raw) if raw else ""
        if not s:
            continue
        tokens.add(s)
        # short label: rpi5-1 from rpi5-1.hacknow.info or display name
        short = s.split(".")[0]
        if short:
            tokens.add(short)
        # slug variants (spaces → -)
        slug = re.sub(r"[^a-z0-9-]+", "-", s.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        if slug:
            tokens.add(slug)
            tokens.add(slug.split(".")[0])
    return {t for t in tokens if t and not is_valid_ipv4(t)}


def match_pihole_host_for_server(
    session: Session,
    server: Server,
) -> Optional[dict[str, str]]:
    """Best-effort match of this server to an existing Pi-hole local DNS A entry.

    Prefers primary Pi-hole; falls back to any enabled instance.
    Returns {domain, ip, source} or None.
    """
    rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.enabled
    ]
    if not rows:
        return None
    rows.sort(key=lambda r: (0 if reg.is_pihole_primary(r) else 1, r.id or 0))

    tokens = _server_name_tokens(server)
    known_ip = host_ip_for_dns(server)

    best: Optional[dict[str, str]] = None
    best_score = -1

    for integ in rows:
        try:
            sess = ph.login(
                integ.base_url,
                reg.pihole_password(integ),
                tls_verify=reg.tls_verify(integ),
            )
            try:
                hosts = ph.list_dns_hosts(sess)
            finally:
                ph.logout(sess)
        except Exception as e:
            logger.debug("pihole host list for match on %s: %s", integ.name, e)
            continue

        for h in hosts:
            domain = normalize_fqdn(h.get("domain") or "")
            ip = (h.get("ip") or "").strip()
            if not domain or not ip:
                continue
            score = 0
            label = domain.split(".")[0]
            if domain in tokens:
                score = 100
            elif label in tokens:
                score = 80
            elif any(t in domain for t in tokens if len(t) >= 3):
                score = 40
            if known_ip and ip == known_ip:
                score += 30
            if score > best_score:
                best_score = score
                best = {
                    "domain": domain,
                    "ip": ip,
                    "source": integ.name or "pihole",
                }
        # Prefer primary if we found anything strong enough
        if best and best_score >= 80:
            break

    return best if best and best_score >= 40 else None


def _servers_by_id(session: Session) -> dict[int, Server]:
    return {s.id: s for s in session.exec(select(Server)).all() if s.id is not None}


def _npm_proxy_hosts_cached(session: Session) -> list[dict[str, Any]]:
    """Proxy hosts from last NPM poll cache (+ binding server/project when known)."""
    from ..models import IntegrationBinding

    out: list[dict[str, Any]] = []
    npm_rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_NPM)
        if r.enabled
    ]
    role_proxy = getattr(reg, "ROLE_PROXY_HOST", "proxy_host")

    for integ in npm_rows:
        try:
            status = json.loads(integ.last_status_json or "{}")
        except Exception:
            status = {}
        hosts = status.get("proxy_hosts") or []
        if not isinstance(hosts, list):
            continue
        bind_by_ext: dict[str, IntegrationBinding] = {}
        for b in session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integ.id,
                IntegrationBinding.role == role_proxy,
            )
        ).all():
            bind_by_ext[(b.external_id or "").strip()] = b
        for h in hosts:
            if not isinstance(h, dict):
                continue
            hid = str(h.get("id") or "").strip()
            b = bind_by_ext.get(hid)
            domains = h.get("domain_names") or []
            if isinstance(domains, str):
                domains = [domains]
            meta = h.get("meta") if isinstance(h.get("meta"), dict) else {}
            out.append(
                {
                    "integration_id": integ.id,
                    "integration_name": integ.name,
                    "proxy_id": hid,
                    "domain_names": [normalize_fqdn(str(d)) for d in domains if d],
                    "forward_host": str(h.get("forward_host") or "").strip(),
                    "forward_port": h.get("forward_port"),
                    "server_id": b.server_id if b else None,
                    "docker_project": (b.docker_project if b else None)
                    or meta.get("docker_project"),
                    "label": h.get("label") or "",
                }
            )
    return out


def find_npm_host_server(session: Session) -> Optional[Server]:
    """Server where NPM stack is bound / deployed (edge for CNAMEs)."""
    from ..models import IntegrationBinding, StackDeployment

    # Prefer binding role=service on npm docker project
    for b in session.exec(
        select(IntegrationBinding).where(IntegrationBinding.role == "service")
    ).all():
        proj = (b.docker_project or "").lower()
        label = (b.external_label or "").lower()
        if "nginx" in proj or "npm" in proj or "proxy" in proj or "nginx proxy" in label:
            s = session.get(Server, b.server_id)
            if s:
                return s
    for d in session.exec(select(StackDeployment)).all():
        slug = (d.template_slug or "").lower()
        name = (d.project_name or "").lower()
        if slug in ("npm", "nginx-proxy-manager") or "nginxproxymanager" in name or name == "npm":
            s = session.get(Server, d.server_id)
            if s:
                return s
    return None


def resolve_service_dns_plan(
    session: Session,
    *,
    backend_server_id: int,
    docker_project: str | None = None,
    stack_deployment_id: int | None = None,
    fqdn: str | None = None,
    base_domain: str = "",
) -> dict[str, Any]:
    """Infer FQDN + CNAME target from fleet data (NPM, host DNS, project name).

    Operator should rarely need to pick target/backend — backend is the stack host;
    target is backend.dns_name unless NPM edges the service.
    """
    servers = _servers_by_id(session)
    backend = servers.get(int(backend_server_id))
    if not backend:
        raise DnsFabricError("Backend server not found", "not_found")

    project = (docker_project or "").strip() or None
    dep = None
    if stack_deployment_id:
        from ..models import StackDeployment

        dep = session.get(StackDeployment, stack_deployment_id)
        if dep and not project:
            project = dep.project_name

    base = normalize_fqdn(base_domain)
    explicit_fqdn = normalize_fqdn(fqdn) if fqdn else ""

    # --- FQDN from NPM proxy host bound to this server/project ---
    npm_hosts = _npm_proxy_hosts_cached(session)
    npm_hit: Optional[dict[str, Any]] = None
    for h in npm_hosts:
        if h.get("server_id") and int(h["server_id"]) != int(backend_server_id):
            # still allow match by project alone
            pass
        proj_ok = (
            not project
            or (h.get("docker_project") or "").lower() == project.lower()
            or project.lower() in (h.get("docker_project") or "").lower()
            or project.lower() in (h.get("label") or "").lower()
        )
        srv_ok = h.get("server_id") is None or int(h.get("server_id") or 0) == int(
            backend_server_id
        )
        if proj_ok and (srv_ok or project):
            if h.get("domain_names"):
                # prefer when server matches
                score = 2 if srv_ok and project and proj_ok else 1 if proj_ok else 0
                if project and (h.get("docker_project") or "").lower() == project.lower():
                    score += 5
                if h.get("server_id") and int(h["server_id"]) == int(backend_server_id):
                    score += 3
                if not npm_hit or score > npm_hit.get("_score", 0):
                    npm_hit = {**h, "_score": score}

    # Kuma service bindings — external_label sometimes is a URL/host
    kuma_hint = ""
    from ..models import IntegrationBinding

    for b in session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.server_id == backend_server_id,
            IntegrationBinding.role == "service",
        )
    ).all():
        if project and (b.docker_project or "").lower() != project.lower():
            continue
        lab = (b.external_label or b.external_id or "").strip()
        # extract host from URL-like label
        m = re.search(r"https?://([^/\s:]+)", lab, re.I)
        if m:
            kuma_hint = normalize_fqdn(m.group(1))
            break
        if "." in lab and " " not in lab:
            kuma_hint = normalize_fqdn(lab)
            break

    suggested_fqdn = explicit_fqdn
    fqdn_source = "explicit" if explicit_fqdn else ""
    if not suggested_fqdn and npm_hit and npm_hit.get("domain_names"):
        suggested_fqdn = npm_hit["domain_names"][0]
        fqdn_source = "npm"
    if not suggested_fqdn and kuma_hint:
        suggested_fqdn = kuma_hint
        fqdn_source = "kuma"
    if not suggested_fqdn and project and base:
        slug = re.sub(r"[^a-z0-9-]+", "-", project.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        suggested_fqdn = f"{slug}.{base}"
        fqdn_source = "project"

    # --- CNAME target ---
    npm_edge = find_npm_host_server(session)
    via_proxy = False
    target = backend
    target_reason = "backend host DNS name (direct)"

    if npm_hit:
        # Service is published via NPM → CNAME to NPM edge host when known
        if npm_edge and npm_edge.id != backend.id:
            target = npm_edge
            via_proxy = True
            target_reason = f"NPM edge ({npm_edge.name})"
        elif npm_edge:
            target = npm_edge
            via_proxy = npm_edge.id != backend.id
            target_reason = f"NPM host ({npm_edge.name})"
        else:
            target_reason = "NPM publishes this name; set host DNS on NPM server for CNAME target"

    target_dns = normalize_fqdn(target.dns_name) if target else ""
    backend_dns = normalize_fqdn(backend.dns_name) or ""

    # Existing Pi-hole CNAME for this FQDN (adopt)
    existing_cname = None
    if suggested_fqdn:
        try:
            existing_cname = _match_pihole_cname(session, suggested_fqdn)
        except Exception:
            existing_cname = None
        if existing_cname and existing_cname.get("target"):
            # If CNAME target matches a server dns_name, use that server as target
            tname = normalize_fqdn(existing_cname["target"])
            for s in servers.values():
                if normalize_fqdn(s.dns_name) == tname:
                    target = s
                    target_dns = tname
                    via_proxy = s.id != backend.id
                    target_reason = f"existing Pi-hole CNAME → {tname}"
                    break

    npm_hint = ""
    if npm_hit:
        npm_hint = (
            f"NPM {npm_hit.get('integration_name')}: "
            f"{', '.join(npm_hit.get('domain_names') or [])} → "
            f"{npm_hit.get('forward_host')}:{npm_hit.get('forward_port')}"
        )

    ready = bool(
        suggested_fqdn
        and target
        and target_dns
        and backend.id
    )
    blockers = []
    if not suggested_fqdn:
        blockers.append("No FQDN inferred — enter one or bind an NPM proxy host / set base domain")
    if not target_dns:
        blockers.append(
            f"Target host {target.name if target else '?'} needs a Host DNS name "
            f"(Edit server → General)"
        )
    if not backend_dns and not via_proxy:
        blockers.append(
            f"Backend host {backend.name} has no Host DNS name yet (optional if via NPM)"
        )

    path = build_access_path(
        session,
        fqdn=suggested_fqdn or "?",
        target_server_id=target.id if target else None,
        backend_server_id=backend.id,
        via_proxy=via_proxy,
        docker_project=project,
        npm_hint=npm_hint or None,
    )

    return {
        "fqdn": suggested_fqdn,
        "fqdn_source": fqdn_source,
        "backend_server_id": backend.id,
        "backend_name": backend.name,
        "backend_dns": backend_dns or None,
        "target_server_id": target.id if target else None,
        "target_name": target.name if target else None,
        "target_dns": target_dns or None,
        "via_proxy": via_proxy,
        "target_reason": target_reason,
        "docker_project": project,
        "stack_deployment_id": stack_deployment_id or (dep.id if dep else None),
        "npm_hint": npm_hint or None,
        "npm_match": {
            "domains": npm_hit.get("domain_names"),
            "forward": f"{npm_hit.get('forward_host')}:{npm_hit.get('forward_port')}",
        }
        if npm_hit
        else None,
        "existing_cname": existing_cname,
        "ready": ready,
        "blockers": blockers,
        "path_kind": path.get("path_kind"),
        "path_title": path.get("path_title"),
        "hops": path.get("hops"),
        "summary": path.get("chain")
        or _plan_summary(
            suggested_fqdn, target_dns, backend.name, via_proxy, target_reason
        ),
    }


def _plan_summary(
    fqdn: str, target_dns: str, backend_name: str, via_proxy: bool, reason: str
) -> str:
    if not fqdn:
        return "Need a service FQDN"
    if not target_dns:
        return f"{fqdn} — set host DNS on the CNAME target first"
    path = "via NPM" if via_proxy else "direct"
    return f"{fqdn}  CNAME → {target_dns}  ({path}, stack on {backend_name}) · {reason}"


def _match_pihole_cname(session: Session, fqdn: str) -> Optional[dict[str, str]]:
    name = normalize_fqdn(fqdn)
    rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.enabled
    ]
    rows.sort(key=lambda r: (0 if reg.is_pihole_primary(r) else 1, r.id or 0))
    for integ in rows:
        try:
            sess = ph.login(
                integ.base_url,
                reg.pihole_password(integ),
                tls_verify=reg.tls_verify(integ),
            )
            try:
                cnames = ph.list_dns_cnames(sess)
            finally:
                ph.logout(sess)
        except Exception:
            continue
        for c in cnames:
            if normalize_fqdn(c.get("domain")) == name:
                return {
                    "domain": name,
                    "target": normalize_fqdn(c.get("target")),
                    "source": integ.name or "pihole",
                }
    return None


def list_pihole_cnames(session: Session) -> list[dict[str, str]]:
    """All CNAME rows from primary (then others) Pi-hole — for adopt/import."""
    rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.enabled
    ]
    rows.sort(key=lambda r: (0 if reg.is_pihole_primary(r) else 1, r.id or 0))
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for integ in rows:
        try:
            sess = ph.login(
                integ.base_url,
                reg.pihole_password(integ),
                tls_verify=reg.tls_verify(integ),
            )
            try:
                cnames = ph.list_dns_cnames(sess)
            finally:
                ph.logout(sess)
        except Exception as e:
            logger.warning("list cnames from %s: %s", integ.name, e)
            continue
        for c in cnames:
            dom = normalize_fqdn(c.get("domain"))
            tgt = normalize_fqdn(c.get("target"))
            if not dom or dom in seen:
                continue
            seen.add(dom)
            out.append(
                {
                    "domain": dom,
                    "target": tgt,
                    "source": integ.name or "pihole",
                }
            )
        break  # primary inventory is enough for mapping
    return out


def _server_by_dns_name(session: Session, dns_name: str) -> Optional[Server]:
    name = normalize_fqdn(dns_name)
    if not name:
        return None
    for s in session.exec(select(Server)).all():
        if normalize_fqdn(s.dns_name) == name:
            return s
    return None


def _server_by_ip(session: Session, ip: str) -> Optional[Server]:
    ip = (ip or "").strip()
    if not ip or not is_valid_ipv4(ip):
        return None
    for s in session.exec(select(Server)).all():
        if host_ip_for_dns(s) == ip or (s.hostname or "").strip() == ip:
            return s
    return None


def plan_from_pihole_cname(
    session: Session,
    domain: str,
    target: str,
    *,
    base_domain: str = "",
) -> dict[str, Any]:
    """Build an attach plan for an existing Pi-hole CNAME (adopt, no recreate)."""
    fqdn = normalize_fqdn(domain)
    tname = normalize_fqdn(target)
    edge = _server_by_dns_name(session, tname)
    if not edge:
        raise DnsFabricError(
            f"No fleet host with DNS name {tname} (CNAME target) — set Host DNS first",
            "no_target",
        )

    # Prefer NPM binding for this domain
    npm_hit = None
    for h in _npm_proxy_hosts_cached(session):
        if fqdn in (h.get("domain_names") or []):
            npm_hit = h
            break

    backend = edge
    project = None
    if npm_hit:
        if npm_hit.get("server_id"):
            b = session.get(Server, int(npm_hit["server_id"]))
            if b:
                backend = b
        project = (npm_hit.get("docker_project") or "").strip() or None
        # forward IP may refine backend
        fwd = (npm_hit.get("forward_host") or "").strip()
        if is_valid_ipv4(fwd):
            by_ip = _server_by_ip(session, fwd)
            if by_ip:
                backend = by_ip
        else:
            by_name = _server_by_dns_name(session, fwd)
            if by_name:
                backend = by_name

    via_proxy = bool(npm_hit) or (edge.id != backend.id)
    if edge.id != backend.id:
        via_proxy = True

    # Host-direct CNAME: still resolve Docker/Kuma app layers (Grafana, etc.)
    if not project:
        layers = resolve_app_layers(
            session, int(backend.id), fqdn=fqdn, docker_project=None
        )
        project = layers.get("docker_project")

    plan = resolve_service_dns_plan(
        session,
        backend_server_id=int(backend.id),
        docker_project=project,
        fqdn=fqdn,
        base_domain=base_domain,
    )
    # Force DNS edge to Pi-hole CNAME target (source of truth)
    plan["target_server_id"] = edge.id
    plan["target_name"] = edge.name
    plan["target_dns"] = tname
    plan["via_proxy"] = via_proxy and bool(npm_hit or edge.id != backend.id)
    if not npm_hit and edge.id == backend.id:
        plan["via_proxy"] = False
    plan["docker_project"] = project or plan.get("docker_project")
    plan["adopt"] = True
    plan["pihole_existing"] = True

    path = build_access_path(
        session,
        fqdn=fqdn,
        target_server_id=edge.id,
        backend_server_id=int(backend.id),
        via_proxy=bool(plan["via_proxy"]),
        docker_project=plan.get("docker_project"),
    )
    plan["path_kind"] = path.get("path_kind")
    plan["path_title"] = path.get("path_title")
    plan["hops"] = path.get("hops")
    plan["summary"] = (
        f"ADOPT {path.get('chain') or fqdn}"
        + (" · already on Pi-hole" if True else "")
    )
    plan["ready"] = bool(plan.get("target_server_id") and plan.get("backend_server_id") and fqdn)
    return plan


def list_service_dns_candidates(session: Session, *, base_domain: str = "") -> list[dict[str, Any]]:
    """Unmapped Pi-hole CNAMEs (primary) + unmapped template deployments."""
    from ..models import StackDeployment

    existing_fqdn = {normalize_fqdn(r.fqdn) for r in list_service_records(session)}
    existing_dep = {
        r.stack_deployment_id
        for r in list_service_records(session)
        if r.stack_deployment_id
    }
    existing_proj = {
        (r.backend_server_id, (r.docker_project or "").lower())
        for r in list_service_records(session)
        if r.docker_project
    }
    out: list[dict[str, Any]] = []

    # 1) Adopt existing Pi-hole CNAMEs (main path — records already live)
    for c in list_pihole_cnames(session):
        if c["domain"] in existing_fqdn:
            continue
        try:
            plan = plan_from_pihole_cname(
                session, c["domain"], c["target"], base_domain=base_domain
            )
            ready = bool(plan.get("target_server_id") and plan.get("backend_server_id") and plan.get("fqdn"))
            plan["ready"] = ready
        except DnsFabricError as e:
            plan = {
                "ready": False,
                "blockers": [e.message],
                "summary": e.message,
                "fqdn": c["domain"],
                "adopt": True,
            }
        out.append(
            {
                "kind": "pihole_cname",
                "fqdn": c["domain"],
                "cname_target": c["target"],
                "source": c.get("source"),
                "plan": plan,
                "adopt": True,
            }
        )

    # 2) Host identity — FQDN is the host A record (no CNAME possible).
    # Only suggest when there is a host-level app signal (Kuma service w/o Docker),
    # so every Pi host is not listed as an “app”.
    for srv in session.exec(select(Server).order_by(Server.name)).all():
        hd = normalize_fqdn(srv.dns_name)
        if not hd or hd in existing_fqdn:
            continue
        if any(x.get("fqdn") == hd for x in out):
            continue
        kuma_label = None
        from ..models import IntegrationBinding

        for b in session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.server_id == srv.id,
                IntegrationBinding.role == reg.ROLE_SERVICE,
            )
        ).all():
            if not b.docker_project and not b.docker_container:
                kuma_label = (b.external_label or b.external_id or "").strip() or None
                break
        if not kuma_label:
            continue  # infrastructure host only — map via Edit if ever needed
        path = build_access_path(
            session,
            fqdn=hd,
            target_server_id=srv.id,
            backend_server_id=srv.id,
            via_proxy=False,
            record_type="a",
            label=kuma_label or srv.name,
        )
        plan = {
            "fqdn": hd,
            "target_server_id": srv.id,
            "backend_server_id": srv.id,
            "target_name": srv.name,
            "backend_name": srv.name,
            "target_dns": hd,
            "via_proxy": False,
            "host_identity": True,
            "record_type": "a",
            "ready": bool(host_ip_for_dns(srv)),
            "path_kind": path.get("path_kind"),
            "path_title": path.get("path_title"),
            "hops": path.get("hops"),
            "summary": f"HOST IDENTITY {hd} = {srv.name} (A record, no CNAME)"
            + (f" · {kuma_label}" if kuma_label else ""),
            "blockers": []
            if host_ip_for_dns(srv)
            else ["Host needs an IP for the A record"],
            "docker_project": path.get("docker_project"),
        }
        out.append(
            {
                "kind": "host_identity",
                "fqdn": hd,
                "server_id": srv.id,
                "server_name": srv.name,
                "kuma_label": kuma_label,
                "plan": plan,
                "adopt": True,
            }
        )
        existing_fqdn.add(hd)

    # 3) Template deployments not yet linked
    for dep in session.exec(select(StackDeployment).order_by(StackDeployment.project_name)).all():
        if dep.id in existing_dep:
            continue
        key = (dep.server_id, (dep.project_name or "").lower())
        if key in existing_proj:
            continue
        try:
            plan = resolve_service_dns_plan(
                session,
                backend_server_id=dep.server_id,
                docker_project=dep.project_name,
                stack_deployment_id=dep.id,
                base_domain=base_domain,
            )
        except DnsFabricError as e:
            plan = {"ready": False, "blockers": [e.message], "summary": e.message}
        # skip if FQDN already covered by pihole candidate or mapped
        pf = normalize_fqdn(plan.get("fqdn") or "")
        if pf and (pf in existing_fqdn or any(x.get("fqdn") == pf for x in out)):
            continue
        backend = session.get(Server, dep.server_id)
        out.append(
            {
                "kind": "deployment",
                "deployment_id": dep.id,
                "project_name": dep.project_name,
                "server_id": dep.server_id,
                "server_name": backend.name if backend else "?",
                "template_slug": dep.template_slug,
                "fqdn": pf or None,
                "plan": plan,
                "adopt": bool(plan.get("existing_cname")),
            }
        )
    return out


def import_pihole_cnames(
    session: Session,
    *,
    user_id: int | None = None,
    base_domain: str = "",
    fqdns: list[str] | None = None,
) -> dict[str, Any]:
    """Adopt existing Pi-hole CNAMEs into ServiceDnsRecord (no recreate required).

    Sync is attempted; Pi-hole duplicate is treated as success.
    """
    want = {normalize_fqdn(f) for f in (fqdns or []) if f} or None
    existing = {normalize_fqdn(r.fqdn) for r in list_service_records(session)}
    imported: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for c in list_pihole_cnames(session):
        dom = c["domain"]
        if want is not None and dom not in want:
            continue
        if dom in existing:
            skipped.append(dom)
            continue
        try:
            plan = plan_from_pihole_cname(
                session, c["domain"], c["target"], base_domain=base_domain
            )
            row, results = attach_service_dns_from_plan(
                session,
                plan,
                fqdn_override=dom,
                user_id=user_id,
                sync_now=True,  # duplicates → ok
            )
            imported.append(row.fqdn)
            existing.add(row.fqdn)
            # annotate sync status if all already_present
            if results and all(r.get("already_present") or r.get("ok") for r in results):
                pass
        except Exception as e:
            errors.append(f"{dom}: {e}")
            logger.warning("import cname %s: %s", dom, e)

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }


def attach_service_dns_from_plan(
    session: Session,
    plan: dict[str, Any],
    *,
    fqdn_override: str | None = None,
    user_id: int | None = None,
    sync_now: bool = True,
) -> tuple[ServiceDnsRecord, list[dict[str, Any]]]:
    fqdn = normalize_fqdn(fqdn_override or plan.get("fqdn") or "")
    if not fqdn:
        raise DnsFabricError("FQDN required", "invalid_fqdn")
    target_id = plan.get("target_server_id")
    backend_id = plan.get("backend_server_id")
    if not target_id or not backend_id:
        raise DnsFabricError("Could not resolve target/backend hosts", "incomplete")
    label = (
        plan.get("label")
        or plan.get("docker_project")
        or plan.get("backend_name")
        or fqdn
    )
    return upsert_service_record(
        session,
        fqdn=fqdn,
        target_server_id=int(target_id),
        backend_server_id=int(backend_id),
        stack_deployment_id=plan.get("stack_deployment_id"),
        docker_project=plan.get("docker_project"),
        label=label,
        managed_on_pihole=True,
        via_proxy=bool(plan.get("via_proxy")) and not plan.get("host_identity"),
        npm_hint=plan.get("npm_hint"),
        external_dns_status="checklist",
        user_id=user_id,
        sync_now=sync_now,
    )


def host_dns_form_defaults(
    session: Session,
    server: Server,
    *,
    base_domain: str = "",
    probe_pihole: bool = True,
) -> dict[str, Any]:
    """Values to prefill the Host DNS form (saved fields win; else Pi-hole / heuristics)."""
    saved_name = normalize_fqdn(server.dns_name) or ""
    saved_ip = (server.ip_address or "").strip()
    saved_override = (server.dns_ip_override or "").strip()

    suggested_name = ""
    suggested_ip = ""
    source = "saved" if saved_name or saved_ip else ""
    pihole_match: Optional[dict[str, str]] = None

    if probe_pihole and (not saved_name or not saved_ip):
        try:
            pihole_match = match_pihole_host_for_server(session, server)
        except Exception as e:
            logger.debug("pihole match skipped: %s", e)
            pihole_match = None

    if not saved_name:
        if pihole_match and pihole_match.get("domain"):
            suggested_name = pihole_match["domain"]
            source = f"pihole:{pihole_match.get('source') or 'pihole'}"
        else:
            suggested_name = suggest_host_dns_name(server, base_domain)
            if suggested_name:
                source = source or "suggested"

    if not saved_ip:
        if pihole_match and pihole_match.get("ip") and is_valid_ipv4(pihole_match["ip"]):
            suggested_ip = pihole_match["ip"]
            if "pihole" not in (source or ""):
                source = f"pihole:{pihole_match.get('source') or 'pihole'}"
        elif is_valid_ipv4((server.hostname or "").strip()):
            suggested_ip = (server.hostname or "").strip()
            source = source or "hostname"
        elif saved_override and is_valid_ipv4(saved_override):
            suggested_ip = saved_override

    dns_name = saved_name or suggested_name
    ip_address = saved_ip or suggested_ip

    return {
        "dns_name": dns_name,
        "ip_address": ip_address,
        "dns_ip_override": saved_override,
        "dns_manage_a": bool(server.dns_manage_a)
        if saved_name
        else bool(dns_name and ip_address),
        "is_saved": bool(saved_name),
        "source": source or "empty",
        "pihole_match": pihole_match,
        "suggested_name": suggested_name,
        "suggested_ip": suggested_ip,
    }


def _is_already_present_error(err: str) -> bool:
    """Pi-hole rejects creates when the record already exists — treat as success."""
    e = (err or "").lower()
    return any(
        s in e
        for s in (
            "duplicate",
            "already exist",
            "already exists",
            "already present",
            "item already present",
            "uniqueness of items",
            "dnsmasq: duplicate",
        )
    )


def fanout_pihole_dns(
    session: Session,
    *,
    op: str,
    kind: str,
    ip: str = "",
    domain: str = "",
    target: str = "",
    scope: str = "all",
    source_id: int | None = None,
) -> list[dict[str, Any]]:
    """Apply DNS mutation across Pi-holes. op=add|delete, kind=host|cname.

    On **add**, a Pi-hole \"duplicate\" response is treated as **ok** (record already present).
    That lets adopt/import of existing fleet DNS succeed without recreating entries.
    """
    rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.enabled
    ]
    sc = (scope or "all").strip().lower()
    if sc in ("this", "here", "local", "self"):
        rows = [r for r in rows if r.id == source_id]
    elif sc in ("secondaries", "secondary", "others", "other"):
        rows = [r for r in rows if r.id != source_id]
    else:
        rows.sort(key=lambda r: (0 if reg.is_pihole_primary(r) else 1, r.id or 0))

    results: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "id": r.id,
            "name": r.name,
            "ok": False,
            "error": "",
            "already_present": False,
        }
        try:
            sess = ph.login(
                r.base_url,
                reg.pihole_password(r),
                tls_verify=reg.tls_verify(r),
            )
            try:
                if kind == "host":
                    if op == "add":
                        ph.add_dns_host(sess, ip, domain)
                    else:
                        ph.delete_dns_host(sess, ip, domain)
                else:
                    if op == "add":
                        ph.add_dns_cname(sess, domain, target)
                    else:
                        ph.delete_dns_cname(sess, domain, target)
                item["ok"] = True
            finally:
                ph.logout(sess)
        except Exception as e:
            err = str(e)[:200]
            if op == "add" and _is_already_present_error(err):
                item["ok"] = True
                item["already_present"] = True
                item["error"] = ""
                logger.info(
                    "pihole dns %s %s on %s already present — treated as ok",
                    op,
                    kind,
                    r.name,
                )
            else:
                item["error"] = err
                logger.warning("pihole dns %s %s on %s: %s", op, kind, r.name, e)
        results.append(item)
    return results


def _summarize_results(results: list[dict[str, Any]]) -> tuple[str, str]:
    if not results:
        return "error", "No enabled Pi-hole instances"
    ok = sum(1 for r in results if r.get("ok"))
    n = len(results)
    detail = "; ".join(
        f"{r.get('name')}:{'ok' if r.get('ok') else r.get('error') or 'fail'}"
        for r in results
    )
    if ok == n:
        return "ok", detail
    if ok == 0:
        return "error", detail
    return "partial", detail


def _assert_unique_dns_name(session: Session, dns_name: str, server_id: int | None) -> None:
    q = select(Server).where(Server.dns_name == dns_name)
    for row in session.exec(q).all():
        if server_id is None or row.id != server_id:
            raise DnsFabricError(
                f"DNS name {dns_name} already used by server {row.name}",
                "duplicate",
            )


def update_server_dns(
    session: Session,
    server: Server,
    *,
    dns_name: str | None,
    dns_manage_a: bool,
    dns_ip_override: str | None = None,
    user_id: int | None = None,
    sync_now: bool = False,
) -> dict[str, Any]:
    """Update host DNS identity fields; optionally sync A to Pi-holes."""
    name = normalize_fqdn(dns_name) if dns_name else ""
    if name and not is_valid_fqdn(name):
        raise DnsFabricError(f"Invalid DNS name: {dns_name}", "invalid_fqdn")
    if name:
        _assert_unique_dns_name(session, name, server.id)

    ip_over = (dns_ip_override or "").strip() or None
    if ip_over and not is_valid_ipv4(ip_over):
        raise DnsFabricError(f"Invalid IP override: {ip_over}", "invalid_ip")

    old_name = normalize_fqdn(server.dns_name)
    old_ip = host_ip_for_dns(server)
    old_manage = bool(server.dns_manage_a)

    server.dns_name = name or None
    server.dns_manage_a = bool(dns_manage_a) and bool(name)
    server.dns_ip_override = ip_over
    session.add(server)
    session.commit()
    session.refresh(server)

    # "Manage A" means PiHerder owns the record: keep Pi-holes in sync on save.
    # Explicit sync_now=True forces a push even when manage is off.
    results: list[dict[str, Any]] = []
    action = "saved"
    if not server.dns_manage_a and old_manage and old_name:
        # Unticked manage → remove previous A from Pi-holes
        results = remove_host_a(
            session, server, user_id=user_id, domain=old_name, ip=old_ip
        )
        action = "removed"
    elif name and server.dns_manage_a:
        # Manage on + name → always create/update A on all Pi-holes
        ip_now = host_ip_for_dns(server)
        if not ip_now:
            # Fields already committed above; surface a clear error for the UI
            raise DnsFabricError(
                "DNS name saved, but no IP for A record — set IP address and save again",
                "no_ip",
            )
        if old_manage and old_name and (old_name != name or old_ip != ip_now):
            if old_ip:
                fanout_pihole_dns(
                    session, op="delete", kind="host", ip=old_ip, domain=old_name
                )
        results = sync_host_a(session, server, user_id=user_id)
        action = "synced"
    elif name and sync_now and not server.dns_manage_a:
        # Force push without owning/manage flag
        results = sync_host_a(session, server, user_id=user_id)
        action = "synced"
    elif name and not server.dns_manage_a:
        action = "saved_no_manage"

    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=server.id,
            action="dns_host_update",
            status="success",
            details=json.dumps(
                {
                    "dns_name": server.dns_name,
                    "dns_manage_a": server.dns_manage_a,
                    "dns_ip_override": server.dns_ip_override,
                    "action": action,
                    "sync": results,
                }
            ),
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()
    return {
        "server_id": server.id,
        "dns_name": server.dns_name,
        "dns_manage_a": server.dns_manage_a,
        "action": action,
        "sync": results,
        "ip": host_ip_for_dns(server) if name else "",
    }


def sync_host_a(
    session: Session,
    server: Server,
    *,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    name = normalize_fqdn(server.dns_name)
    if not name:
        raise DnsFabricError("Server has no DNS name", "no_dns_name")
    ip = host_ip_for_dns(server)
    if not ip:
        raise DnsFabricError(
            "No IP for A record — set IP address or DNS IP override",
            "no_ip",
        )
    results = fanout_pihole_dns(
        session, op="add", kind="host", ip=ip, domain=name
    )
    status, detail = _summarize_results(results)
    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=server.id,
            action="dns_host_a_sync",
            status="success" if status == "ok" else ("partial" if status == "partial" else "failed"),
            details=json.dumps({"domain": name, "ip": ip, "results": results}),
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()
    if status == "error":
        raise DnsFabricError(f"Pi-hole A sync failed: {detail}", "sync_failed")
    return results


def remove_host_a(
    session: Session,
    server: Server,
    *,
    user_id: int | None = None,
    domain: str | None = None,
    ip: str | None = None,
) -> list[dict[str, Any]]:
    name = normalize_fqdn(domain or server.dns_name)
    addr = (ip or host_ip_for_dns(server) or "").strip()
    if not name or not addr:
        return []
    results = fanout_pihole_dns(
        session, op="delete", kind="host", ip=addr, domain=name
    )
    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=server.id,
            action="dns_host_a_remove",
            status="success",
            details=json.dumps({"domain": name, "ip": addr, "results": results}),
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()
    return results


def get_service_record(session: Session, record_id: int) -> ServiceDnsRecord | None:
    return session.get(ServiceDnsRecord, record_id)


def list_service_records(session: Session) -> list[ServiceDnsRecord]:
    return list(
        session.exec(select(ServiceDnsRecord).order_by(ServiceDnsRecord.fqdn)).all()
    )


def find_service_for_deployment(
    session: Session, deployment_id: int
) -> ServiceDnsRecord | None:
    return session.exec(
        select(ServiceDnsRecord).where(
            ServiceDnsRecord.stack_deployment_id == deployment_id
        )
    ).first()


def upsert_service_record(
    session: Session,
    *,
    fqdn: str,
    target_server_id: int,
    backend_server_id: int,
    stack_deployment_id: int | None = None,
    docker_project: str | None = None,
    label: str | None = None,
    managed_on_pihole: bool = True,
    via_proxy: bool | None = None,
    npm_hint: str | None = None,
    certificate_id: int | None = None,
    external_dns_status: str = "checklist",
    notes: str | None = None,
    record_id: int | None = None,
    user_id: int | None = None,
    sync_now: bool = True,
) -> tuple[ServiceDnsRecord, list[dict[str, Any]]]:
    name = normalize_fqdn(fqdn)
    if not is_valid_fqdn(name):
        raise DnsFabricError(f"Invalid service FQDN: {fqdn}", "invalid_fqdn")

    target = session.get(Server, target_server_id)
    backend = session.get(Server, backend_server_id)
    if not target or not backend:
        raise DnsFabricError("Target or backend server not found", "not_found")
    if not normalize_fqdn(target.dns_name):
        raise DnsFabricError(
            f"Target host {target.name} needs a DNS name first",
            "target_no_dns",
        )

    # uniqueness
    existing = session.exec(
        select(ServiceDnsRecord).where(ServiceDnsRecord.fqdn == name)
    ).first()
    if existing and record_id and existing.id != record_id:
        raise DnsFabricError(f"FQDN {name} already registered", "duplicate")
    if existing and not record_id:
        row = existing
    elif record_id:
        row = session.get(ServiceDnsRecord, record_id)
        if not row:
            raise DnsFabricError("Record not found", "not_found")
    else:
        row = ServiceDnsRecord(fqdn=name)

    old_target = ""
    old_fqdn = ""
    if getattr(row, "id", None) and row.target_server_id:
        old_srv = session.get(Server, row.target_server_id)
        old_target = normalize_fqdn(old_srv.dns_name) if old_srv else ""
        old_fqdn = normalize_fqdn(row.fqdn)

    proxy = via_proxy
    if proxy is None:
        proxy = int(target_server_id) != int(backend_server_id)

    host_identity = is_host_identity_name(name, backend) or is_host_identity_name(
        name, target
    )
    if host_identity:
        # Name is the host A record — target/backend must be that host; no CNAME
        target = backend
        target_server_id = int(backend.id)
        proxy = False

    row.fqdn = name
    row.record_type = "a" if host_identity else "cname"
    row.target_server_id = int(target_server_id)
    row.backend_server_id = int(backend_server_id)
    row.stack_deployment_id = stack_deployment_id
    row.docker_project = (docker_project or "").strip() or None
    row.label = (label or "").strip() or None
    row.managed_on_pihole = bool(managed_on_pihole)
    row.via_proxy = bool(proxy) and not host_identity
    row.npm_hint = (npm_hint or "").strip() or None
    row.certificate_id = certificate_id
    row.external_dns_status = (external_dns_status or "checklist").strip() or "checklist"
    row.notes = notes
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)

    results: list[dict[str, Any]] = []
    if sync_now and row.managed_on_pihole:
        if row.record_type in ("a", "host", "host_identity"):
            # Ensure host A exists; never create a CNAME for the same name
            results = sync_service_dns(session, row, user_id=user_id)
        else:
            if old_fqdn and old_target and (
                old_fqdn != name or old_target != normalize_fqdn(target.dns_name)
            ):
                fanout_pihole_dns(
                    session,
                    op="delete",
                    kind="cname",
                    domain=old_fqdn,
                    target=old_target,
                )
            results = sync_service_dns(session, row, user_id=user_id)
    else:
        session.add(
            make_audit_log(
                user_id=user_id,
                server_id=backend.id,
                action="dns_service_upsert",
                status="success",
                details=json.dumps(
                    {
                        "fqdn": name,
                        "target": target.dns_name,
                        "backend": backend.name,
                        "via_proxy": row.via_proxy,
                        "synced": False,
                    }
                ),
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()

    return row, results


def sync_service_dns(
    session: Session,
    row: ServiceDnsRecord,
    *,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Push mapping to Pi-hole: host A for identity names, else CNAME.

    Never creates a CNAME when FQDN equals the host A name (invalid / duplicate).
    """
    target = session.get(Server, row.target_server_id)
    backend = session.get(Server, row.backend_server_id) or target
    if not target or not normalize_fqdn(target.dns_name):
        raise DnsFabricError("Target host missing DNS name", "target_no_dns")
    domain = normalize_fqdn(row.fqdn)
    tname = normalize_fqdn(target.dns_name)
    host_identity = (row.record_type or "").lower() in (
        "a",
        "host",
        "host_identity",
    ) or is_host_identity_name(domain, backend) or is_host_identity_name(domain, target)

    if host_identity:
        row.record_type = "a"
        row.via_proxy = False
        ip = host_ip_for_dns(backend or target)
        if not ip:
            raise DnsFabricError(
                "Host identity needs an IP for the A record",
                "no_ip",
            )
        results = fanout_pihole_dns(
            session, op="add", kind="host", ip=ip, domain=domain
        )
        action = "dns_service_a_sync"
        detail_extra = {"ip": ip, "record_type": "a", "host_identity": True}
    else:
        row.record_type = "cname"
        results = fanout_pihole_dns(
            session, op="add", kind="cname", domain=domain, target=tname
        )
        action = "dns_service_cname_sync"
        detail_extra = {
            "target": tname,
            "via_proxy": row.via_proxy,
            "record_type": "cname",
        }

    status, detail = _summarize_results(results)
    row.last_synced_at = datetime.utcnow()
    row.last_sync_status = status
    row.last_sync_detail = detail[:2000]
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=row.backend_server_id,
            action=action,
            status="success" if status == "ok" else ("partial" if status == "partial" else "failed"),
            details=json.dumps({"fqdn": domain, "results": results, **detail_extra}),
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()
    if status == "error":
        raise DnsFabricError(f"Pi-hole DNS sync failed: {detail}", "sync_failed")
    return results


# Back-compat alias
def sync_service_cname(session: Session, row: ServiceDnsRecord, *, user_id: int | None = None):
    return sync_service_dns(session, row, user_id=user_id)


def delete_service_record(
    session: Session,
    row: ServiceDnsRecord,
    *,
    user_id: int | None = None,
    remove_from_pihole: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    target = session.get(Server, row.target_server_id)
    backend = session.get(Server, row.backend_server_id) or target
    tname = normalize_fqdn(target.dns_name) if target else ""
    domain = normalize_fqdn(row.fqdn)
    host_identity = (row.record_type or "").lower() in (
        "a",
        "host",
        "host_identity",
    ) or is_host_identity_name(domain, backend)
    if remove_from_pihole and domain:
        if host_identity:
            # Do not delete the host A record when removing the mapping —
            # host DNS is owned by Server.dns_manage_a
            results = []
        elif tname:
            results = fanout_pihole_dns(
                session, op="delete", kind="cname", domain=domain, target=tname
            )
    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=row.backend_server_id,
            action="dns_service_delete",
            status="success",
            details=json.dumps(
                {"fqdn": domain, "target": tname, "results": results}
            ),
            finished_at=datetime.utcnow(),
        )
    )
    session.delete(row)
    session.commit()
    return results


def certs_matching_fqdn(session: Session, fqdn: str) -> list[ManagedCertificate]:
    """Suggest managed certs whose domains cover fqdn (exact or wildcard)."""
    name = normalize_fqdn(fqdn)
    out: list[ManagedCertificate] = []
    for cert in session.exec(select(ManagedCertificate)).all():
        try:
            domains = json.loads(cert.domains_json or "[]")
        except Exception:
            domains = []
        if not isinstance(domains, list):
            continue
        for d in domains:
            dn = normalize_fqdn(str(d))
            if dn == name:
                out.append(cert)
                break
            if dn.startswith("*.") and (name.endswith(dn[1:]) or name == dn[2:]):
                out.append(cert)
                break
    return out


# Access path kinds (one mapping, layers optional):
#   host      — FQDN → host (bare metal / direct A or CNAME to host)
#   app       — FQDN → host → project → container (no NPM)
#   npm_host  — FQDN → NPM edge → host
#   npm_app   — FQDN → NPM edge → host → project → container
PATH_KINDS = ("host", "app", "npm_host", "npm_app")


def _find_docker_container(
    session: Session, server_id: int, docker_project: str | None
) -> Optional[str]:
    """Container name from Kuma service binding for project."""
    if not docker_project:
        return None
    from ..models import IntegrationBinding

    proj = docker_project.lower()
    for b in session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.server_id == server_id,
            IntegrationBinding.role == reg.ROLE_SERVICE,
        )
    ).all():
        bp = (b.docker_project or "").lower()
        if bp == proj or proj in bp or bp in proj:
            cont = (b.docker_container or "").strip()
            if cont:
                return cont
    return None


def _fqdn_match_tokens(fqdn: str | None, project: str | None = None) -> set[str]:
    tokens: set[str] = set()
    if fqdn:
        n = normalize_fqdn(fqdn)
        if n:
            tokens.add(n)
            tokens.add(n.split(".")[0])
    if project:
        p = project.lower().strip()
        if p:
            tokens.add(p)
            tokens.add(re.sub(r"[^a-z0-9]+", "", p))
    return {t for t in tokens if t and len(t) >= 2}


def resolve_app_layers(
    session: Session,
    server_id: int,
    *,
    fqdn: str | None = None,
    docker_project: str | None = None,
) -> dict[str, Optional[str]]:
    """Resolve compose project + container from fleet relationships.

    Sources (in order):
      1. Explicit docker_project
      2. Kuma ``service`` binds on this host (match FQDN label / project name)
      3. StackDeployment on this host matching FQDN tokens
      4. Single Kuma service bind if only one project on the host (weak)

    Returns ``{docker_project, docker_container, source}``.
    """
    from ..models import IntegrationBinding, StackDeployment

    tokens = _fqdn_match_tokens(fqdn, docker_project)
    project = (docker_project or "").strip() or None
    container: Optional[str] = None
    source = "explicit" if project else ""

    if project:
        container = _find_docker_container(session, server_id, project)
        return {
            "docker_project": project,
            "docker_container": container,
            "source": source or "explicit",
        }

    # Kuma service bindings — primary link for host-direct apps (Grafana, etc.)
    binds = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.server_id == server_id,
                IntegrationBinding.role == reg.ROLE_SERVICE,
            )
        ).all()
    )
    best: Optional[tuple[int, IntegrationBinding]] = None
    for b in binds:
        if not (b.docker_project or b.docker_container):
            continue  # host-only Kuma monitors (e.g. 3D Print) stay path=host
        score = 0
        bp = (b.docker_project or "").lower()
        lab = (b.external_label or "").lower()
        cont = (b.docker_container or "").lower()
        for t in tokens:
            tl = t.lower()
            if tl == bp or tl in bp or bp == tl:
                score += 20
            if tl in lab:
                score += 12
            if tl in cont:
                score += 8
        if score > 0 and (best is None or score > best[0]):
            best = (score, b)

    if best and best[0] >= 8:
        b = best[1]
        project = (b.docker_project or "").strip() or None
        container = (b.docker_container or "").strip() or None
        return {
            "docker_project": project,
            "docker_container": container,
            "source": "kuma",
        }

    # Template deployments on host
    for dep in session.exec(
        select(StackDeployment).where(StackDeployment.server_id == server_id)
    ).all():
        pname = (dep.project_name or "").lower()
        slug = (dep.template_slug or "").lower()
        if any(t.lower() in pname or t.lower() in slug or t.lower() == pname for t in tokens):
            project = dep.project_name
            container = _find_docker_container(session, server_id, project)
            return {
                "docker_project": project,
                "docker_container": container,
                "source": "deployment",
            }

    return {
        "docker_project": None,
        "docker_container": None,
        "source": "",
    }


def _find_npm_forward(
    session: Session, fqdn: str, docker_project: str | None, backend_server_id: int | None
) -> Optional[dict[str, Any]]:
    name = normalize_fqdn(fqdn)
    best = None
    best_score = -1
    for h in _npm_proxy_hosts_cached(session):
        domains = h.get("domain_names") or []
        score = 0
        if name and name in domains:
            score += 50
        if docker_project and (h.get("docker_project") or "").lower() == docker_project.lower():
            score += 20
        if backend_server_id and h.get("server_id") == backend_server_id:
            score += 10
        if score > best_score and score >= 20:
            best_score = score
            best = h
    return best


def is_host_identity_name(fqdn: str | None, server: Server | None) -> bool:
    """True when the service name *is* the host's LAN A name (CNAME impossible).

    Example: 3dprint.hacknow.info is both the host DNS A record and the app name.
    """
    if not server or not fqdn:
        return False
    host_dns = normalize_fqdn(server.dns_name)
    name = normalize_fqdn(fqdn)
    return bool(host_dns and name and host_dns == name)


def build_access_path(
    session: Session,
    *,
    fqdn: str,
    target_server_id: int | None,
    backend_server_id: int | None,
    via_proxy: bool = False,
    docker_project: str | None = None,
    docker_container: str | None = None,
    label: str | None = None,
    npm_hint: str | None = None,
    record_type: str | None = None,
) -> dict[str, Any]:
    """Entity relationship chain for one name (any subset of layers).

    Entities (optional except name when set)::

        name  →  [npm]  →  host  →  [service/project]  →  [container]

    Special case — **host identity**: FQDN equals host ``dns_name`` (A record).
    No CNAME hop; name and host are the same DNS entity (e.g. 3D Print).
    """
    servers = _servers_by_id(session)
    target = servers.get(int(target_server_id)) if target_server_id else None
    backend = servers.get(int(backend_server_id)) if backend_server_id else None
    if not backend and target:
        backend = target
    if not target and backend:
        target = backend

    name = normalize_fqdn(fqdn)
    project = (docker_project or "").strip() or None
    container = (docker_container or "").strip() or None
    app_source = "explicit" if project or container else ""
    rtype = (record_type or "").strip().lower()
    host_identity = rtype in ("a", "host", "host_identity") or is_host_identity_name(
        name, backend
    ) or is_host_identity_name(name, target)

    # Resolve missing app layers from Kuma / deployments (host-direct paths)
    if backend and (not project or not container) and not host_identity:
        layers = resolve_app_layers(
            session,
            int(backend.id),
            fqdn=name or label,
            docker_project=project,
        )
        if not project and layers.get("docker_project"):
            project = layers["docker_project"]
            app_source = layers.get("source") or "resolved"
        if not container and layers.get("docker_container"):
            container = layers["docker_container"]
            app_source = app_source or layers.get("source") or "resolved"
        elif project and not container:
            container = _find_docker_container(session, int(backend.id), project)
    elif backend and host_identity and (not project or not container):
        # Still attach Docker if present; host-only Kuma monitors stay host path
        layers = resolve_app_layers(
            session,
            int(backend.id),
            fqdn=name or label,
            docker_project=project,
        )
        if layers.get("docker_project"):
            project = layers["docker_project"]
            container = layers.get("docker_container")
            app_source = layers.get("source") or "resolved"

    npm_fwd = None if host_identity else _find_npm_forward(
        session, name, project, backend.id if backend else None
    )
    # NPM hop only when CNAME target is edge and (differs from backend OR npm publishes)
    edge_differs = (
        not host_identity
        and target is not None
        and backend is not None
        and target.id != backend.id
    )
    edge_is_proxy = False if host_identity else (bool(via_proxy) or edge_differs)
    if not host_identity:
        if npm_fwd and (edge_differs or via_proxy or (target and backend and target.id == backend.id and npm_fwd)):
            edge_is_proxy = True
        if not npm_fwd and not edge_differs and not via_proxy:
            edge_is_proxy = False
        if target and backend and target.id == backend.id and not npm_fwd:
            edge_is_proxy = False

    hops: list[dict[str, Any]] = []
    if host_identity:
        name_sub = "A · host identity"
    elif target and normalize_fqdn(target.dns_name) != name:
        name_sub = "CNAME"
    else:
        name_sub = "name"
    hops.append(
        {
            "kind": "name",
            "label": name or (label or "service"),
            "sub": name_sub,
            "href": None,
            "entity": "dns_name",
        }
    )

    if edge_is_proxy and target:
        hop_npm: dict[str, Any] = {
            "kind": "npm",
            "label": target.name,
            "sub": normalize_fqdn(target.dns_name)
            or (npm_fwd or {}).get("forward_host")
            or "NPM edge",
            "href": f"/servers/{target.id}",
            "server_id": target.id,
            "entity": "npm_edge",
        }
        if npm_fwd and npm_fwd.get("forward_host"):
            hop_npm["forward"] = (
                f"{npm_fwd.get('forward_host')}:{npm_fwd.get('forward_port')}"
            )
            hop_npm["sub"] = hop_npm.get("sub") or hop_npm["forward"]
        hops.append(hop_npm)

    if backend:
        ip = host_ip_for_dns(backend)
        hops.append(
            {
                "kind": "host",
                "label": backend.name,
                "sub": (normalize_fqdn(backend.dns_name) or backend.hostname or "")
                + (f" · {ip}" if ip else ""),
                "href": f"/servers/{backend.id}",
                "server_id": backend.id,
                "entity": "host",
            }
        )

    if project:
        hops.append(
            {
                "kind": "service",
                "label": project,
                "sub": "compose project",
                "href": f"/servers/{backend.id}/docker" if backend else None,
                "entity": "docker_project",
            }
        )
    if container:
        hops.append(
            {
                "kind": "container",
                "label": container,
                "sub": "container",
                "href": None,
                "entity": "docker_container",
            }
        )

    has_npm = any(h["kind"] == "npm" for h in hops)
    has_app = any(h["kind"] in ("service", "container") for h in hops)
    if host_identity and has_app:
        path_kind = "host_app"
        path_title = "A name = host → app"
    elif host_identity:
        path_kind = "host_identity"
        path_title = "A name = host"
    elif has_npm and has_app:
        path_kind = "npm_app"
        path_title = "name → NPM → host → app"
    elif has_npm:
        path_kind = "npm_host"
        path_title = "name → NPM → host"
    elif has_app:
        path_kind = "app"
        path_title = "name → host → app"
    else:
        path_kind = "host"
        path_title = "name → host"

    # Host identity: name and host are same DNS entity — collapse label noise
    if host_identity and backend:
        # Keep both hops for clarity but mark identity
        for h in hops:
            if h["kind"] == "host":
                h["sub"] = (h.get("sub") or "") + " · same as name"
                h["identity"] = True
            if h["kind"] == "name":
                h["identity"] = True

    chain = " → ".join(h["label"] for h in hops)
    return {
        "path_kind": path_kind,
        "path_title": path_title,
        "hops": hops,
        "chain": chain,
        "via_proxy": has_npm,
        "host_identity": host_identity,
        "record_type": "a" if host_identity else "cname",
        "docker_project": project,
        "docker_container": container,
        "app_source": app_source,
        "npm_forward": (
            f"{npm_fwd.get('forward_host')}:{npm_fwd.get('forward_port')}"
            if npm_fwd
            else None
        ),
        "npm_hint": npm_hint,
        "entities": [h.get("entity") or h["kind"] for h in hops],
    }


def build_access_path_for_record(
    session: Session, row: ServiceDnsRecord, *, persist_links: bool = False
) -> dict[str, Any]:
    path = build_access_path(
        session,
        fqdn=row.fqdn,
        target_server_id=row.target_server_id,
        backend_server_id=row.backend_server_id,
        via_proxy=bool(row.via_proxy),
        docker_project=row.docker_project,
        label=row.label,
        npm_hint=row.npm_hint,
        record_type=row.record_type,
    )
    # Persist discovered project so the relationship is stored, not only displayed
    if persist_links and path.get("docker_project") and not row.docker_project:
        row.docker_project = path["docker_project"]
        if not row.label:
            row.label = path["docker_project"]
        row.updated_at = datetime.utcnow()
        session.add(row)
        try:
            session.commit()
        except Exception:
            session.rollback()
    return path


def build_fabric_view(session: Session) -> dict[str, Any]:
    """Data for DNS page: hosts, service paths, path-chain mesh."""
    servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    records = list_service_records(session)
    server_by_id = {s.id: s for s in servers if s.id is not None}

    hosts: list[dict[str, Any]] = []
    for s in servers:
        ip = host_ip_for_dns(s)
        hosts.append(
            {
                "server_id": s.id,
                "name": s.name,
                "dns_name": normalize_fqdn(s.dns_name) or None,
                "ip": ip or None,
                "dns_manage_a": bool(s.dns_manage_a),
                "ssh_host": s.hostname,
                "href": f"/servers/{s.id}",
            }
        )

    services: list[dict[str, Any]] = []
    path_kind_counts = {k: 0 for k in PATH_KINDS}
    for r in records:
        target = server_by_id.get(r.target_server_id)
        backend = server_by_id.get(r.backend_server_id)
        # Re-resolve Kuma/deploy links for host-direct names missing project
        path = build_access_path_for_record(session, r, persist_links=True)
        path_kind_counts[path["path_kind"]] = path_kind_counts.get(path["path_kind"], 0) + 1
        cert_name = None
        if r.certificate_id:
            c = session.get(ManagedCertificate, r.certificate_id)
            cert_name = c.name if c else None
        if not cert_name:
            matches = certs_matching_fqdn(session, r.fqdn)
            if matches:
                cert_name = matches[0].name
        dep_href = (
            f"/templates/deployments/{r.stack_deployment_id}"
            if r.stack_deployment_id
            else None
        )
        services.append(
            {
                "id": r.id,
                "fqdn": r.fqdn,
                "label": r.label or r.docker_project or r.fqdn,
                "target_server_id": r.target_server_id,
                "target_name": target.name if target else "?",
                "target_dns": normalize_fqdn(target.dns_name) if target else None,
                "backend_server_id": r.backend_server_id,
                "backend_name": backend.name if backend else "?",
                "backend_dns": normalize_fqdn(backend.dns_name) if backend else None,
                "via_proxy": path["via_proxy"],
                "path_kind": path["path_kind"],
                "path_title": path["path_title"],
                "path_chain": path["chain"],
                "hops": path["hops"],
                "docker_project": path.get("docker_project") or r.docker_project,
                "docker_container": path.get("docker_container"),
                "npm_hint": r.npm_hint or path.get("npm_forward"),
                "managed_on_pihole": r.managed_on_pihole,
                "external_dns_status": r.external_dns_status,
                "last_sync_status": r.last_sync_status,
                "last_synced_at": r.last_synced_at.isoformat() + "Z"
                if r.last_synced_at
                else None,
                "cert_name": cert_name,
                "certificate_id": r.certificate_id,
                "stack_deployment_id": r.stack_deployment_id,
                "dep_href": dep_href,
                "backend_href": f"/servers/{r.backend_server_id}",
                "target_href": f"/servers/{r.target_server_id}",
            }
        )

    external_checklist = []
    for s in services:
        if s.get("external_dns_status") in ("checklist", "pending", ""):
            external_checklist.append(
                {
                    "type": "CNAME",
                    "name": s["fqdn"],
                    "target": s.get("target_dns") or s.get("target_name"),
                    "note": s.get("path_title") or "",
                }
            )

    mesh = _build_path_mesh(services)
    physical = _build_physical_view(hosts, services)
    logical = _build_logical_view(services)

    named_hosts = [h for h in hosts if h.get("dns_name")]
    unnamed = [h for h in hosts if not h.get("dns_name")]

    return {
        "hosts": hosts,
        "named_hosts": named_hosts,
        "unnamed_hosts": unnamed,
        "services": services,
        "external_checklist": external_checklist,
        "mesh": mesh,
        "physical": physical,
        "logical": logical,
        "path_kinds": [
            {
                "id": "host_identity",
                "title": "A name = host",
                "hint": "App name is the host A record — no CNAME (e.g. 3D Print)",
            },
            {
                "id": "app",
                "title": "CNAME → host → app",
                "hint": "CNAME to host + Docker (e.g. Grafana)",
            },
            {
                "id": "npm_app",
                "title": "CNAME → NPM → host → app",
                "hint": "Proxied to project/container",
            },
            {
                "id": "npm_host",
                "title": "CNAME → NPM → host",
                "hint": "Proxied to a host",
            },
        ],
        "stats": {
            "hosts_total": len(hosts),
            "hosts_named": len(named_hosts),
            "services": len(services),
            "via_proxy": sum(1 for s in services if s.get("via_proxy")),
            "checklist": len(external_checklist),
            "by_path": path_kind_counts,
        },
    }


def _build_physical_view(
    hosts: list[dict[str, Any]], services: list[dict[str, Any]]
) -> dict[str, Any]:
    """Physical topology: every host as a rack unit + apps that land on it."""
    by_id: dict[int, dict[str, Any]] = {}
    for h in hosts:
        sid = h.get("server_id")
        if sid is None:
            continue
        by_id[int(sid)] = {
            **h,
            "apps": [],
            "is_npm_edge": False,
            "ingress_count": 0,
            "app_count": 0,
        }

    for s in services:
        bid = s.get("backend_server_id")
        tid = s.get("target_server_id")
        if tid is not None and int(tid) in by_id and s.get("via_proxy"):
            by_id[int(tid)]["is_npm_edge"] = True
            by_id[int(tid)]["ingress_count"] += 1
        if bid is None or int(bid) not in by_id:
            continue
        app = {
            "fqdn": s.get("fqdn"),
            "path_kind": s.get("path_kind"),
            "path_title": s.get("path_title"),
            "via_npm": s.get("via_proxy"),
            "npm_edge": s.get("target_name") if s.get("via_proxy") else None,
            "project": s.get("docker_project"),
            "container": s.get("docker_container"),
            "label": s.get("label"),
            "href": s.get("dep_href") or s.get("backend_href"),
            "record_id": s.get("id"),
        }
        by_id[int(bid)]["apps"].append(app)
        by_id[int(bid)]["app_count"] += 1

    racks = sorted(
        by_id.values(),
        key=lambda r: (-r["app_count"], -int(r["is_npm_edge"]), (r.get("name") or "")),
    )
    return {
        "racks": racks,
        "npm_edges": [r for r in racks if r.get("is_npm_edge")],
        "empty_hosts": [r for r in racks if not r.get("apps") and r.get("dns_name")],
        "svg": _build_physical_mesh_svg(hosts, services),
    }


def _build_logical_view(services: list[dict[str, Any]]) -> dict[str, Any]:
    """Logical topology: URL → (optional NPM link) → app destination."""
    flows: list[dict[str, Any]] = []
    for s in services:
        hops = s.get("hops") or []
        npm_hop = next((h for h in hops if h.get("kind") == "npm"), None)
        host_hop = next((h for h in hops if h.get("kind") == "host"), None)
        svc_hop = next((h for h in hops if h.get("kind") == "service"), None)
        cont_hop = next((h for h in hops if h.get("kind") == "container"), None)
        dest_parts = []
        if host_hop:
            dest_parts.append(host_hop.get("label") or "")
        if svc_hop:
            dest_parts.append(svc_hop.get("label") or "")
        if cont_hop:
            dest_parts.append(cont_hop.get("label") or "")
        flows.append(
            {
                "id": s.get("id"),
                "url": s.get("fqdn"),
                "url_scheme": "https",
                "path_kind": s.get("path_kind"),
                "path_title": s.get("path_title"),
                "via_npm": bool(s.get("via_proxy") or npm_hop),
                "link_label": (
                    f"via NPM · {npm_hop.get('label')}"
                    if npm_hop
                    else (
                        "host identity (A)"
                        if s.get("path_kind") in ("host_identity", "host_app")
                        else "direct CNAME"
                    )
                ),
                "link_detail": (npm_hop or {}).get("forward")
                or (npm_hop or {}).get("sub")
                or s.get("npm_hint"),
                "npm_edge": (npm_hop or {}).get("label"),
                "dest_host": (host_hop or {}).get("label"),
                "dest_host_href": (host_hop or {}).get("href"),
                "dest_project": (svc_hop or {}).get("label"),
                "dest_container": (cont_hop or {}).get("label"),
                "dest_summary": " / ".join(p for p in dest_parts if p),
                "backend_server_id": s.get("backend_server_id"),
                "target_server_id": s.get("target_server_id"),
                "href": s.get("dep_href") or s.get("backend_href"),
                "synced": s.get("last_sync_status") == "ok",
            }
        )
    flows.sort(key=lambda f: (0 if f.get("via_npm") else 1, f.get("url") or ""))
    return {
        "flows": flows,
        "via_npm_count": sum(1 for f in flows if f.get("via_npm")),
        "direct_count": sum(1 for f in flows if not f.get("via_npm")),
        "svg": _build_logical_mesh_svg(flows),
    }


def _build_physical_mesh_svg(
    hosts: list[dict[str, Any]], services: list[dict[str, Any]]
) -> dict[str, Any]:
    """Full-fleet physical SVG: host nodes + app nodes + edges."""
    import math

    named = [h for h in hosts if h.get("dns_name") or h.get("server_id")]
    if not named:
        return {"width": 800, "height": 400, "nodes": [], "edges": [], "labels": []}

    n = len(named)
    # Hosts on a wide ellipse
    width = max(960, 120 * n)
    height = max(640, 80 * max(len(services), 4) + 200)
    cx, cy = width / 2, height * 0.42
    rx, ry = width * 0.38, height * 0.28

    host_nodes: list[dict[str, Any]] = []
    host_pos: dict[int, tuple[float, float]] = {}
    for i, h in enumerate(named):
        angle = (2 * math.pi * i / n) - math.pi / 2
        x = cx + rx * math.cos(angle)
        y = cy + ry * math.sin(angle)
        sid = int(h["server_id"])
        host_pos[sid] = (x, y)
        is_edge = any(
            s.get("via_proxy") and s.get("target_server_id") == sid for s in services
        )
        apps_here = sum(1 for s in services if s.get("backend_server_id") == sid)
        host_nodes.append(
            {
                "id": f"h{sid}",
                "kind": "host",
                "label": h.get("name") or f"#{sid}",
                "sub": h.get("dns_name") or h.get("ip") or "",
                "ip": h.get("ip") or "",
                "x": round(x, 1),
                "y": round(y, 1),
                "href": h.get("href"),
                "npm_edge": is_edge,
                "app_count": apps_here,
                "server_id": sid,
            }
        )

    # Apps as satellites outside their host
    app_nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    # Group apps by backend for radial offset
    by_backend: dict[int, list[dict[str, Any]]] = {}
    for s in services:
        bid = s.get("backend_server_id")
        if bid is None:
            continue
        by_backend.setdefault(int(bid), []).append(s)

    for bid, apps in by_backend.items():
        hx, hy = host_pos.get(bid, (cx, cy))
        # Direction away from center
        dx, dy = hx - cx, hy - cy
        dist = math.hypot(dx, dy) or 1
        ux, uy = dx / dist, dy / dist
        for j, s in enumerate(apps):
            # perpendicular spread
            px, py = -uy, ux
            spread = (j - (len(apps) - 1) / 2) * 36
            ax = hx + ux * 95 + px * spread
            ay = hy + uy * 95 + py * spread
            aid = f"a{s.get('id')}"
            app_nodes.append(
                {
                    "id": aid,
                    "kind": "app",
                    "label": (s.get("fqdn") or "")[:28],
                    "sub": (s.get("docker_container") or s.get("docker_project") or s.get("path_kind") or "")[:22],
                    "x": round(ax, 1),
                    "y": round(ay, 1),
                    "href": s.get("dep_href") or s.get("backend_href"),
                    "path_kind": s.get("path_kind"),
                    "via_npm": s.get("via_proxy"),
                }
            )
            # app → host
            edges.append(
                {
                    "x1": ax,
                    "y1": ay,
                    "x2": hx,
                    "y2": hy,
                    "kind": "land",
                    "dashed": False,
                }
            )
            # if via NPM, also line app → npm edge host
            tid = s.get("target_server_id")
            if s.get("via_proxy") and tid and int(tid) in host_pos and int(tid) != bid:
                tx, ty = host_pos[int(tid)]
                edges.append(
                    {
                        "x1": ax,
                        "y1": ay,
                        "x2": tx,
                        "y2": ty,
                        "kind": "npm",
                        "dashed": True,
                    }
                )

    return {
        "width": int(width),
        "height": int(height),
        "nodes": host_nodes + app_nodes,
        "edges": edges,
        "host_count": len(host_nodes),
        "app_count": len(app_nodes),
    }


def _build_logical_mesh_svg(flows: list[dict[str, Any]]) -> dict[str, Any]:
    """Full logical mesh: URLs left, NPM hub center, destinations right."""
    if not flows:
        return {"width": 900, "height": 400, "nodes": [], "edges": [], "hub": None}

    n = len(flows)
    row_h = 52
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

    for i, f in enumerate(flows):
        y = pad_top + i * row_h
        uid = f"u{f.get('id') or i}"
        did = f"d{f.get('id') or i}"
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
            }
        )
        if f.get("via_npm") and has_npm:
            edges.append(
                {
                    "x1": x_url + 90,
                    "y1": y,
                    "x2": x_hub - 50,
                    "y2": hub_y,
                    "kind": "to_npm",
                    "dashed": False,
                    "label": "",
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
                }
            )

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


def servers_with_dns_name(session: Session) -> list[Server]:
    rows = list(session.exec(select(Server).order_by(Server.name)).all())
    return [s for s in rows if normalize_fqdn(s.dns_name)]


def cleanup_dns_for_server(session: Session, server_id: int) -> int:
    """Delete ServiceDnsRecord rows referencing a server about to be removed."""
    rows = list(
        session.exec(
            select(ServiceDnsRecord).where(
                (ServiceDnsRecord.target_server_id == server_id)
                | (ServiceDnsRecord.backend_server_id == server_id)
            )
        ).all()
    )
    n = 0
    for r in rows:
        session.delete(r)
        n += 1
    return n
