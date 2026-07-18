"""Uptime Kuma monitoring coverage audit (PLAN v0.6.0 H3 + dependency suggest).

1. **Path coverage** — Network fabric FQDNs vs Kuma service/SSH bindings  
2. **Dependency suggest** — Docker inventory containers (compose services) without a
   matching Kuma bind; optional infra mute; TCP monitor hints  

Coverage (paths):
  covered  — service-role binding matches this FQDN / project
  partial  — host SSH only, or weak label match
  none     — no useful Kuma binding
  n/a      — no Uptime Kuma integration

Dependencies:
  covered / none / muted (infra default or operator mute)
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import Integration, IntegrationBinding, Server, ServiceDnsRecord

# Default "expected unmonitored" roles (image / compose service / container name).
# Operators can add more via app setting kuma_coverage_mute_patterns (JSON list).
DEFAULT_INFRA_MUTE_PATTERNS: tuple[str, ...] = (
    "postgres",
    "postgresql",
    "timescaledb",
    "mysql",
    "mariadb",
    "mongo",
    "mongodb",
    "redis",
    "valkey",
    "keydb",
    "memcached",
    "rabbitmq",
    "nats",
    "kafka",
    "zookeeper",
    "elasticsearch",
    "opensearch",
    "meilisearch",
    "influxdb",
    "minio",
    "db",
    "database",
    "pgbouncer",
    "adminer",  # optional tooling; often not public-path monitored
)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _tokens(fqdn: str | None, project: str | None = None, label: str | None = None) -> set[str]:
    out: set[str] = set()
    for raw in (fqdn, project, label):
        if not raw:
            continue
        n = _norm(raw)
        if not n:
            continue
        out.add(n)
        if "." in n:
            out.add(n.split(".")[0])
        out.add(re.sub(r"[^a-z0-9]+", "", n))
    return {t for t in out if t and len(t) >= 2}


def _score_service_binding(
    b: IntegrationBinding,
    *,
    tokens: set[str],
    docker_project: str | None,
) -> int:
    """Higher = better match for this fabric service. 0 = no match."""
    bp = _norm(b.docker_project)
    cont = _norm(b.docker_container)
    lab = _norm(b.external_label) or _norm(b.external_id)
    proj = _norm(docker_project)
    score = 0

    # Explicit project match
    if proj and bp:
        if proj == bp or proj in bp or bp in proj:
            score += 40
        else:
            # Different project on same host — not a match for this service
            return 0

    if not bp and not cont:
        # Host-scoped service monitor (no docker) — covers host-identity / bare apps
        for t in tokens:
            if t in lab:
                score += 18
        if score == 0 and tokens:
            # Generic host HTTP monitor still counts as partial service coverage
            score += 10
        return score

    for t in tokens:
        if bp and (t == bp or t in bp or bp in t):
            score += 22
        if t in lab:
            score += 14
        if cont and t in cont:
            score += 8
    return score


def _binding_summary(b: IntegrationBinding) -> dict[str, Any]:
    return {
        "binding_id": b.id,
        "external_id": b.external_id,
        "label": b.external_label or b.external_id,
        "state": b.last_state or "unknown",
        "role": b.role,
        "docker_project": b.docker_project or "",
        "docker_container": b.docker_container or "",
        "server_id": b.server_id,
        "href": f"/integrations/{b.integration_id}",
    }


def kuma_integrations_enabled(session: Session) -> list[Integration]:
    from ..integrations import registry as reg

    rows = reg.list_integrations(session, type_filter=reg.TYPE_UPTIME_KUMA)
    return [i for i in rows if i.enabled]


def build_kuma_coverage_audit(session: Session) -> dict[str, Any]:
    """Audit fabric services + host SSH against Kuma bindings.

    Returns structure for Network hub pulse, gap list, and per-service badges.
    """
    from ..integrations import registry as reg

    kumas = kuma_integrations_enabled(session)
    if not kumas:
        return {
            "has_kuma": False,
            "summary": {
                "covered": 0,
                "partial": 0,
                "none": 0,
                "total_services": 0,
                "hosts_ssh_covered": 0,
                "hosts_total": 0,
            },
            "services": [],
            "gaps": [],
            "hosts": [],
            "by_service_id": {},
        }

    kuma_ids = {i.id for i in kumas if i.id is not None}
    all_binds = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id.in_(list(kuma_ids))
            )
        ).all()
    )
    svc_by_server: dict[int, list[IntegrationBinding]] = {}
    ssh_by_server: dict[int, list[IntegrationBinding]] = {}
    for b in all_binds:
        if b.role == reg.ROLE_SERVICE:
            svc_by_server.setdefault(b.server_id, []).append(b)
        elif b.role == reg.ROLE_SSH:
            ssh_by_server.setdefault(b.server_id, []).append(b)

    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    server_name = {s.id: s.name for s in servers if s.id is not None}

    hosts_out: list[dict[str, Any]] = []
    hosts_ssh = 0
    for s in servers:
        if s.id is None:
            continue
        ssh_binds = ssh_by_server.get(s.id) or []
        if ssh_binds:
            hosts_ssh += 1
        hosts_out.append(
            {
                "server_id": s.id,
                "name": s.name,
                "ssh_coverage": "covered" if ssh_binds else "none",
                "ssh_bindings": [_binding_summary(b) for b in ssh_binds],
                "service_binding_count": len(svc_by_server.get(s.id) or []),
                "href": f"/servers/{s.id}",
            }
        )

    records = list(
        session.exec(select(ServiceDnsRecord).order_by(ServiceDnsRecord.fqdn)).all()
    )
    services_out: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    covered = partial = none = 0

    for r in records:
        if r.id is None:
            continue
        backend_id = r.backend_server_id or r.target_server_id
        target_id = r.target_server_id
        project = (r.docker_project or "").strip() or None
        tokens = _tokens(r.fqdn, project, r.label)
        candidates: list[tuple[int, IntegrationBinding]] = []
        for sid in {backend_id, target_id}:
            if not sid:
                continue
            for b in svc_by_server.get(sid) or []:
                sc = _score_service_binding(b, tokens=tokens, docker_project=project)
                if sc > 0:
                    candidates.append((sc, b))
        candidates.sort(key=lambda x: -x[0])
        best = candidates[0] if candidates else None
        ssh_here = bool(ssh_by_server.get(backend_id or 0))

        if best and best[0] >= 18:
            status = "covered"
            covered += 1
            reason = "Matched Uptime Kuma service monitor"
            matched = [_binding_summary(best[1])]
        elif best and best[0] >= 8:
            status = "partial"
            partial += 1
            reason = "Weak Kuma match (label/host monitor) — confirm scope"
            matched = [_binding_summary(best[1])]
        elif ssh_here:
            status = "partial"
            partial += 1
            reason = "Host SSH monitored; no service monitor for this name/project"
            matched = [_binding_summary(b) for b in (ssh_by_server.get(backend_id) or [])[:2]]
        else:
            status = "none"
            none += 1
            reason = "No Uptime Kuma binding for this path"
            matched = []

        via_proxy = bool(getattr(r, "via_proxy", False))
        if not project:
            path_kind = "host_identity"
        elif via_proxy:
            path_kind = "npm_app"
        else:
            path_kind = "app"
        row = {
            "service_id": r.id,
            "fqdn": r.fqdn,
            "label": r.label or r.docker_project or r.fqdn,
            "docker_project": project or "",
            "backend_server_id": backend_id,
            "backend_name": server_name.get(backend_id, "?") if backend_id else "?",
            "coverage": status,
            "reason": reason,
            "bindings": matched,
            "score": best[0] if best else 0,
            "via_proxy": via_proxy,
            "path_kind": path_kind,
            "path_href": f"/dns#path-{r.id}",
            "logical_href": f"/dns/logical?focus={r.id}#map",
            "kuma_href": matched[0]["href"] if matched else (
                f"/integrations/{list(kuma_ids)[0]}" if kuma_ids else "/integrations"
            ),
        }
        services_out.append(row)
        by_id[r.id] = row

    gaps = [s for s in services_out if s["coverage"] in ("none", "partial")]
    # Sort gaps: none first, then partial
    gaps.sort(key=lambda s: (0 if s["coverage"] == "none" else 1, s.get("fqdn") or ""))

    # Annotate path rows for filters (public HTTPS edge vs host identity)
    for s in services_out:
        s["is_public_path"] = bool(
            s.get("via_proxy")
            or (s.get("path_kind") or "") in ("npm_app", "npm_host", "app")
            or (s.get("fqdn") and "." in str(s.get("fqdn")))
        )
        # host identity: name == host A, often weak partials
        s["is_host_identity"] = (s.get("path_kind") or "") in (
            "host",
            "host_identity",
        ) or not (s.get("docker_project") or "").strip()

    audit = {
        "has_kuma": True,
        "summary": {
            "covered": covered,
            "partial": partial,
            "none": none,
            "total_services": len(services_out),
            "hosts_ssh_covered": hosts_ssh,
            "hosts_total": len(hosts_out),
            "gap_count": len(gaps),
        },
        "services": services_out,
        "gaps": gaps,
        "hosts": hosts_out,
        "by_service_id": by_id,
        "kuma_count": len(kumas),
    }
    audit = enrich_gaps_with_bind_hints(session, audit)
    audit = attach_dependency_coverage(session, audit)
    return audit


def attach_coverage_to_fabric_services(
    services: list[dict[str, Any]], audit: dict[str, Any]
) -> None:
    """Mutate fabric service dicts with kuma_coverage fields for path cards."""
    by_id = audit.get("by_service_id") or {}
    for s in services:
        sid = s.get("id")
        info = by_id.get(sid) if sid is not None else None
        if not info:
            s["kuma_coverage"] = "n/a" if not audit.get("has_kuma") else "none"
            s["kuma_coverage_reason"] = ""
            s["kuma_bindings"] = []
            continue
        s["kuma_coverage"] = info.get("coverage") or "none"
        s["kuma_coverage_reason"] = info.get("reason") or ""
        s["kuma_bindings"] = info.get("bindings") or []
        s["kuma_href"] = info.get("kuma_href")


def _score_monitor_for_service(
    mon: dict[str, Any],
    *,
    tokens: set[str],
    fqdn: str | None,
) -> int:
    """Rank a cached Kuma monitor for binding to a fabric service path."""
    name = _norm(str(mon.get("name") or ""))
    url = _norm(str(mon.get("url") or mon.get("hostname") or ""))
    mtype = _norm(str(mon.get("type") or ""))
    score = 0
    # Prefer HTTP(s) style for app coverage
    if mtype in ("http", "https", "keyword", "json-query", "grpc-keyword", ""):
        score += 4
    if mtype in ("port", "tcp", "ping") and "ssh" in name:
        score -= 8  # unlikely for service path coverage
    fq = _norm(fqdn)
    if fq and fq in url:
        score += 50
    if fq and fq in name:
        score += 35
    for t in tokens:
        if len(t) < 3:
            continue
        if t in url:
            score += 18
        if t in name:
            score += 12
    return score


def suggest_monitors_for_service(
    session: Session,
    *,
    fqdn: str | None,
    label: str | None = None,
    docker_project: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Best-effort Kuma HTTP monitors to bind for this path (from poll cache)."""
    from ..integrations import registry as reg

    tokens = _tokens(fqdn, docker_project, label)
    out: list[dict[str, Any]] = []
    for integ in kuma_integrations_enabled(session):
        if integ.id is None:
            continue
        for m in reg.monitors_from_cache(integ):
            mid = str(m.get("id") or "").strip()
            if not mid:
                continue
            # Prefer service-like; keep others with lower score
            if m.get("is_ssh_like") and not m.get("is_service_like"):
                continue
            sc = _score_monitor_for_service(m, tokens=tokens, fqdn=fqdn)
            if sc < 8:
                continue
            name = str(m.get("name") or mid).strip()
            state = str(m.get("status") or m.get("state") or "").strip()
            url = str(m.get("url") or "").strip()
            label_s = name if not state else f"{name} · {state}"
            if url:
                label_s = f"{label_s} — {url[:48]}"
            out.append(
                {
                    "external_id": mid,
                    "name": name,
                    "label": label_s,
                    "score": sc,
                    "url": url,
                    "state": state,
                    "integration_id": integ.id,
                    "integration_name": integ.name,
                    "dashboard_id": str(m.get("dashboard_id") or "")
                    if m.get("dashboard_id")
                    else "",
                }
            )
    out.sort(key=lambda x: (-int(x.get("score") or 0), (x.get("name") or "").lower()))
    # Deduplicate by external_id keeping best score
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in out:
        eid = row["external_id"]
        if eid in seen:
            continue
        seen.add(eid)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped


def enrich_gaps_with_bind_hints(session: Session, audit: dict[str, Any]) -> dict[str, Any]:
    """Add monitor suggestions + bind form defaults to each gap row."""
    if not audit.get("has_kuma"):
        return audit
    try:
        kumas = kuma_integrations_enabled(session)
    except Exception:
        kumas = []
    default_integ = kumas[0].id if kumas else None
    for g in audit.get("gaps") or []:
        try:
            suggestions = suggest_monitors_for_service(
                session,
                fqdn=g.get("fqdn"),
                label=g.get("label"),
                docker_project=g.get("docker_project") or None,
                limit=8,
            )
        except Exception:
            suggestions = []
        g["suggestions"] = suggestions
        g["suggested_external_id"] = (
            suggestions[0]["external_id"] if suggestions else ""
        )
        integ_id = (
            suggestions[0].get("integration_id") if suggestions else default_integ
        )
        g["bind_integration_id"] = integ_id
        g["bind_action"] = (
            f"/integrations/{integ_id}/bindings" if integ_id else ""
        )
        # Advanced: open Kuma detail with prefilled query
        if integ_id:
            from urllib.parse import urlencode

            q = urlencode(
                {
                    "bind": "service",
                    "server_id": g.get("backend_server_id") or "",
                    "docker_project": g.get("docker_project") or "",
                    "suggest": g.get("fqdn") or "",
                    "next": "/dns/coverage#kuma-coverage",
                }
            )
            g["bind_advanced_href"] = f"/integrations/{integ_id}?{q}#kuma-services"
        else:
            g["bind_advanced_href"] = "/integrations"
    # Flat monitor list for gap dropdowns (first Kuma) when suggestions empty
    all_opts: list[dict[str, Any]] = []
    if default_integ:
        try:
            from ..integrations import registry as reg

            integ = session.get(Integration, default_integ)
            if integ:
                for m in reg.monitors_from_cache(integ):
                    if m.get("is_ssh_like") and not m.get("is_service_like"):
                        continue
                    mid = str(m.get("id") or "").strip()
                    if not mid:
                        continue
                    name = str(m.get("name") or mid).strip()
                    all_opts.append(
                        {
                            "external_id": mid,
                            "label": name,
                            "integration_id": default_integ,
                        }
                    )
                all_opts.sort(key=lambda x: (x.get("label") or "").lower())
        except Exception:
            all_opts = []
    audit["all_service_monitors"] = all_opts[:80]
    audit["default_integration_id"] = default_integ
    return audit


# ── Dependency discovery (inventory containers vs Kuma binds) ─────────────


def _mute_patterns(session: Session | None = None) -> list[str]:
    patterns = list(DEFAULT_INFRA_MUTE_PATTERNS)
    if session is None:
        return patterns
    try:
        from ..app_settings import load_settings

        raw = load_settings().get("kuma_coverage_mute_patterns") or ""
        if isinstance(raw, list):
            extra = [str(x).strip().lower() for x in raw if str(x).strip()]
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    extra = [str(x).strip().lower() for x in parsed if str(x).strip()]
                else:
                    extra = [p.strip().lower() for p in raw.split(",") if p.strip()]
            except Exception:
                extra = [p.strip().lower() for p in raw.split(",") if p.strip()]
        else:
            extra = []
        for p in extra:
            if p and p not in patterns:
                patterns.append(p)
    except Exception:
        pass
    return patterns


def _is_infra_role(
    *,
    name: str,
    image: str,
    compose_service: str,
    patterns: list[str],
) -> bool:
    blob = " ".join(
        [
            _norm(name),
            _norm(image),
            _norm(compose_service),
            # image repo last segment
            _norm(image.split("/")[-1].split(":")[0] if image else ""),
        ]
    )
    for p in patterns:
        if p and p in blob:
            return True
    return False


def _parse_host_ports(ports_display: str | None, ports: Any = None) -> list[str]:
    """Extract host-side port numbers from inventory fields."""
    found: list[str] = []
    text = ports_display or ""
    if isinstance(ports, list):
        for p in ports:
            text += " " + str(p)
    elif isinstance(ports, str):
        text += " " + ports
    for m in re.finditer(r"(?:^|[\s,;])(\d{2,5})(?::\d+)?(?:/tcp|/udp)?", text):
        port = m.group(1)
        if port not in found and port not in ("22",):  # skip bare SSH noise
            found.append(port)
    # Also "0.0.0.0:5432->5432/tcp"
    for m in re.finditer(r":(\d{2,5})->", text):
        port = m.group(1)
        if port not in found:
            found.append(port)
    return found[:8]


def _container_bound(
    binds: list[IntegrationBinding],
    *,
    project: str,
    container: str,
    compose_service: str,
) -> IntegrationBinding | None:
    proj = _norm(project)
    cont = _norm(container)
    svc = _norm(compose_service)
    for b in binds:
        bp = _norm(b.docker_project)
        if not bp or not (bp == proj or proj in bp or bp in proj):
            continue
        bc = _norm(b.docker_container)
        if not bc:
            # project-level bind covers all containers in project
            return b
        if bc == cont or bc == svc or cont in bc or svc in bc:
            return b
    return None


def _score_tcp_monitor(mon: dict[str, Any], *, ports: list[str], name_tokens: set[str]) -> int:
    mtype = _norm(str(mon.get("type") or ""))
    name = _norm(str(mon.get("name") or ""))
    host = _norm(str(mon.get("hostname") or mon.get("url") or ""))
    mport = str(mon.get("port") or "").strip()
    score = 0
    if mtype in ("port", "tcp", "postgres", "mysql", "mongodb", "redis", "sqlserver"):
        score += 10
    elif mtype in ("http", "https", "keyword"):
        score -= 5  # less ideal for pure DB deps
    for p in ports:
        if mport == p or f":{p}" in host or p in name:
            score += 25
    for t in name_tokens:
        if len(t) >= 3 and t in name:
            score += 8
    return score


def suggest_tcp_monitors_for_dep(
    session: Session,
    *,
    ports: list[str],
    name: str,
    image: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Suggest Kuma TCP/DB-style monitors for a dependency container."""
    from ..integrations import registry as reg

    tokens = _tokens(None, name, image.split("/")[-1].split(":")[0] if image else None)
    out: list[dict[str, Any]] = []
    for integ in kuma_integrations_enabled(session):
        if integ.id is None:
            continue
        for m in reg.monitors_from_cache(integ):
            mid = str(m.get("id") or "").strip()
            if not mid:
                continue
            sc = _score_tcp_monitor(m, ports=ports, name_tokens=tokens)
            if sc < 12:
                continue
            label = str(m.get("name") or mid)
            mtype = str(m.get("type") or "")
            if mtype:
                label = f"{label} ({mtype})"
            if m.get("port"):
                label = f"{label} :{m.get('port')}"
            out.append(
                {
                    "external_id": mid,
                    "label": label,
                    "score": sc,
                    "integration_id": integ.id,
                    "type": mtype,
                    "port": str(m.get("port") or ""),
                }
            )
    out.sort(key=lambda x: (-int(x.get("score") or 0), x.get("label") or ""))
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in out:
        if row["external_id"] in seen:
            continue
        seen.add(row["external_id"])
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped


def attach_dependency_coverage(session: Session, audit: dict[str, Any]) -> dict[str, Any]:
    """Scan Docker inventory for compose containers; flag unmonitored deps."""
    empty_deps = {
        "items": [],
        "gaps": [],
        "muted": [],
        "summary": {
            "total": 0,
            "covered": 0,
            "none": 0,
            "muted": 0,
            "infra_hidden": 0,
        },
    }
    if not audit.get("has_kuma"):
        audit["dependencies"] = empty_deps
        return audit

    try:
        return _attach_dependency_coverage_inner(session, audit)
    except Exception:
        logger = __import__("logging").getLogger(__name__)
        logger.exception("dependency coverage audit failed")
        audit["dependencies"] = empty_deps
        return audit


def _attach_dependency_coverage_inner(
    session: Session, audit: dict[str, Any]
) -> dict[str, Any]:
    from .. import docker_inventory as inv_svc
    from ..integrations import registry as reg

    patterns = _mute_patterns(session)
    # Explicit mute keys: "server_id:project:container"
    explicit_mute: set[str] = set()
    try:
        from ..app_settings import load_settings

        raw = load_settings().get("kuma_coverage_mute_keys") or ""
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    explicit_mute = {str(x).strip() for x in parsed if str(x).strip()}
            except Exception:
                pass
    except Exception:
        pass

    show_infra = True
    try:
        from ..app_settings import load_settings

        # default hide infra from "needs attention" (still listed under muted)
        v = load_settings().get("kuma_coverage_show_infra")
        if v in (True, "1", "true", "on", "yes"):
            show_infra = True
        elif v in (False, "0", "false", "off", "no"):
            show_infra = False
        else:
            show_infra = False  # default: don't nag on postgres/redis
    except Exception:
        show_infra = False

    kumas = kuma_integrations_enabled(session)
    kuma_ids = {i.id for i in kumas if i.id is not None}
    all_binds = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id.in_(list(kuma_ids)),
                IntegrationBinding.role == reg.ROLE_SERVICE,
            )
        ).all()
    )
    binds_by_server: dict[int, list[IntegrationBinding]] = {}
    for b in all_binds:
        sid = getattr(b, "server_id", None)
        if sid is None:
            continue
        binds_by_server.setdefault(int(sid), []).append(b)

    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    items: list[dict[str, Any]] = []
    default_integ = kumas[0].id if kumas else None

    for srv in servers:
        if srv.id is None or not getattr(srv, "container_patch_enabled", True):
            # still scan if inventory exists
            pass
        inv = inv_svc.parse_inventory(srv) or {}
        projects = inv.get("projects") or []
        if not isinstance(projects, list):
            continue
        sbinds = binds_by_server.get(srv.id) or []
        for p in projects:
            if not isinstance(p, dict):
                continue
            pname = (p.get("name") or "").strip()
            if not pname:
                continue
            for c in p.get("containers") or []:
                if not isinstance(c, dict) or c.get("placeholder"):
                    continue
                cname = (c.get("name") or c.get("compose_service") or "").strip()
                if not cname:
                    continue
                csvc = (c.get("compose_service") or "").strip()
                image = (c.get("image") or "").strip()
                ports = _parse_host_ports(
                    c.get("ports_display") or "", c.get("ports")
                )
                mute_key = f"{srv.id}:{pname}:{cname}"
                is_infra = _is_infra_role(
                    name=cname,
                    image=image,
                    compose_service=csvc,
                    patterns=patterns,
                )
                explicitly_muted = mute_key in explicit_mute
                bound = _container_bound(
                    sbinds, project=pname, container=cname, compose_service=csvc
                )
                if bound:
                    status = "covered"
                elif explicitly_muted or (is_infra and not show_infra):
                    status = "muted"
                else:
                    status = "none"

                tcp_suggestions: list[dict[str, Any]] = []
                if status == "none":
                    try:
                        tcp_suggestions = suggest_tcp_monitors_for_dep(
                            session,
                            ports=ports,
                            name=cname,
                            image=image,
                            limit=5,
                        )
                    except Exception:
                        tcp_suggestions = []

                integ_id = (
                    tcp_suggestions[0]["integration_id"]
                    if tcp_suggestions
                    else default_integ
                )
                bind_action = (
                    f"/integrations/{integ_id}/bindings" if integ_id else ""
                )
                row = {
                    "key": mute_key,
                    "server_id": srv.id,
                    "server_name": srv.name,
                    "project": pname,
                    "container": cname,
                    "compose_service": csvc or cname,
                    "image": image,
                    "running": bool(c.get("running")),
                    "ports": ports,
                    "ports_display": (c.get("ports_display") or "")[:80],
                    "is_infra": is_infra,
                    "status": status,
                    "binding": _binding_summary(bound) if bound else None,
                    "tcp_suggestions": tcp_suggestions,
                    "suggested_external_id": (
                        tcp_suggestions[0]["external_id"] if tcp_suggestions else ""
                    ),
                    "bind_action": bind_action,
                    "bind_integration_id": integ_id,
                    "docker_href": f"/servers/{srv.id}/docker",
                    "reason": (
                        "Matched Kuma service bind"
                        if status == "covered"
                        else (
                            "Infra role (postgres/redis/…) — muted by default"
                            if status == "muted" and is_infra
                            else (
                                "Muted by operator"
                                if status == "muted"
                                else (
                                    "No Kuma bind for this container — "
                                    + (
                                        f"published port(s) {', '.join(ports)}; "
                                        "create TCP/Postgres monitor in Kuma if reachable"
                                        if ports
                                        else "no host port published (Kuma needs network path)"
                                    )
                                )
                            )
                        )
                    ),
                }
                items.append(row)

    gaps = [i for i in items if i["status"] == "none"]
    muted = [i for i in items if i["status"] == "muted"]
    covered_n = sum(1 for i in items if i["status"] == "covered")
    gaps.sort(
        key=lambda x: (
            0 if x.get("ports") else 1,
            (x.get("server_name") or "").lower(),
            (x.get("project") or "").lower(),
        )
    )

    audit["dependencies"] = {
        "items": items,
        "gaps": gaps,
        "muted": muted,
        "show_infra": show_infra,
        "mute_patterns": patterns,
        "summary": {
            "total": len(items),
            "covered": covered_n,
            "none": len(gaps),
            "muted": len(muted),
            "infra_patterns": len(patterns),
        },
    }
    # Surface on main summary
    sm = audit.setdefault("summary", {})
    sm["dep_total"] = len(items)
    sm["dep_gaps"] = len(gaps)
    sm["dep_muted"] = len(muted)
    sm["dep_covered"] = covered_n
    return audit


def filter_path_gaps(
    gaps: list[dict[str, Any]],
    *,
    mode: str = "all",
) -> list[dict[str, Any]]:
    """Filter path coverage gaps for UI.

    mode:
      all     — none + partial
      none    — hard gaps only
      public  — prefer app/npm paths; drop pure host-identity partials
      strict  — none only + public-ish
    """
    mode = (mode or "all").strip().lower()
    out = list(gaps)
    if mode == "none":
        out = [g for g in out if g.get("coverage") == "none"]
    elif mode == "public":
        out = [
            g
            for g in out
            if g.get("coverage") == "none"
            or (
                g.get("coverage") == "partial"
                and not g.get("is_host_identity")
            )
        ]
    elif mode == "strict":
        out = [
            g
            for g in out
            if g.get("coverage") == "none"
            and (
                g.get("is_public_path")
                or (g.get("docker_project") or "").strip()
            )
        ]
    return out
