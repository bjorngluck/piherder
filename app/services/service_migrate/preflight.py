"""Migrate preflight matrix (v1.4 M2). DB-first; SSH facts optional."""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from sqlmodel import Session, select

from ...models import Integration, IntegrationBinding, Job, Server, ServiceDnsRecord
from .. import docker_inventory as inventory_svc
from ..compose_project_files import classify_volume_source
from ..dns_fabric.ports import parse_published_ports
from ..dns_fabric.core import normalize_fqdn
from ..haos import is_haos_server
from ..integrations.registry import ROLE_PROXY_HOST, ROLE_SERVICE, TYPE_NPM
from .facts import docker_base_abs, probe_dest_occupancy
from .host_lock import compose_project_name, lock_state
from .overrides import (
    is_host_local_bind,
    is_truncated_host_path,
    mapped_host_port,
    normalize_dest_project,
    path_in_jail,
    suggest_dest_bind,
    validate_dest_bind_path,
    validate_port_map,
)

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DEV_HINT = re.compile(r"/dev/(apex|bus/usb|dri|kfd|nvidia)", re.I)
_VOL_DATA_RE = re.compile(r"^/var/lib/docker/volumes/([^/]+)/_data/?$")

STACK_BUSY_TYPES = frozenset(
    {
        "backup",
        "docker_stack_deploy",
        "docker_stack_stop",
        "docker_stack_start",
        "docker_stack_restart",
        "template_deploy",
        "template_redeploy",
        "service_migrate",
    }
)
DISK_MARGIN_MIN = 512 * 1024 * 1024  # 512 MiB
DISK_MARGIN_RATIO = 0.15


def named_volume_id(*, source: str = "", mtype: str = "", name: str = "") -> Optional[str]:
    """Docker named volume from inspect Type/Name or volume-store Source path."""
    n = (name or "").strip()
    t = (mtype or "").strip().lower()
    if t == "volume" and n and "/" not in n and ".." not in n:
        return n
    m = _VOL_DATA_RE.match((source or "").strip())
    if m:
        vol = m.group(1)
        if vol and "/" not in vol and ".." not in vol:
            return vol
    return None


def _mount_source_from_line(line: str) -> str:
    s = str(line).strip()
    if "→" in s:
        s = s.split("→", 1)[0].strip()
    if ":" in s:
        s = s.split(":", 1)[0].strip()
    return s


def _job_dest_server_id(job: Job) -> Optional[int]:
    try:
        data = json.loads(job.details or "{}") or {}
        v = data.get("dest_server_id")
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _item(cid: str, message: str, **extra: Any) -> dict[str, Any]:
    row = {"id": cid, "message": message}
    row.update(extra)
    return row


def _human(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    n = int(n)
    for unit, div in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _project_from_inventory(server: Server, name: str) -> Optional[dict[str, Any]]:
    inv = inventory_svc.parse_inventory(server) or {}
    for p in inv.get("projects") or []:
        if isinstance(p, dict) and (p.get("name") or "") == name:
            return p
    return None


def _inventory_project_names(server: Server) -> set[str]:
    inv = inventory_svc.parse_inventory(server) or {}
    names: set[str] = set()
    for p in inv.get("projects") or []:
        if isinstance(p, dict) and p.get("name"):
            names.add(str(p["name"]))
    return names


_LIVE_DEST_STATES = frozenset({"running", "restarting", "paused"})


def _dest_container_state(line: str) -> str:
    parts = str(line or "").strip().rsplit(" ", 1)
    return parts[-1].lower() if parts else ""


def _dest_running_containers(occ: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for line in occ.get("containers") or []:
        raw = str(line).strip()
        if raw and _dest_container_state(raw) in _LIVE_DEST_STATES:
            out.append(raw)
    return out


def _dest_leftover_container_names(occ: dict[str, Any]) -> list[str]:
    return [str(x) for x in (occ.get("containers") or []) if str(x).strip()]


def _dest_folder_nonempty(occ: dict[str, Any]) -> list[str]:
    return [str(x) for x in (occ.get("files") or []) if str(x).strip()]


def _live_dest_ports(occ: dict[str, Any]) -> set[tuple[str, str]]:
    used: set[tuple[str, str]] = set()
    for item in occ.get("ports") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            host = str(item[0]).strip()
            proto = str(item[1] or "tcp").strip() or "tcp"
        elif isinstance(item, str) and "/" in item:
            host, proto = item.rsplit("/", 1)
            host, proto = host.strip(), (proto.strip() or "tcp")
        else:
            continue
        if host:
            used.add((host, proto))
    return used


def eligible_destinations(session: Session, source: Server) -> list[Server]:
    sid = int(source.id or 0)
    rows = session.exec(
        select(Server).where(Server.id != sid).order_by(Server.name.asc())
    ).all()
    out: list[Server] = []
    for s in rows:
        if is_haos_server(s):
            continue
        if not getattr(s, "container_patch_enabled", False):
            continue
        out.append(s)
    return out


def _busy_jobs(
    session: Session, server_id: int, ignore_job_id: Optional[int] = None
) -> list[Job]:
    sid = int(server_id or 0)
    ignore = int(ignore_job_id) if ignore_job_id else None
    rows = list(
        session.exec(
            select(Job)
            .where(Job.server_id == sid)
            .where(Job.job_type.in_(list(STACK_BUSY_TYPES)))
            .where(Job.status.in_(["pending", "running"]))
        ).all()
    )
    others = session.exec(
        select(Job)
        .where(Job.job_type == "service_migrate")
        .where(Job.status.in_(["pending", "running"]))
        .where(Job.server_id != sid)
    ).all()
    for job in others:
        if _job_dest_server_id(job) == sid:
            rows.append(job)
    if ignore:
        rows = [j for j in rows if int(j.id or 0) != ignore]
    return rows


def _dns_rows(
    session: Session,
    source_id: int,
    project: str,
    dest_id: Optional[int] = None,
) -> list[ServiceDnsRecord]:
    """Fabric rows for this compose project on source, plus dest (retry after partial cutover)."""
    rows = list(
        session.exec(
            select(ServiceDnsRecord).where(
                ServiceDnsRecord.backend_server_id == source_id,
                ServiceDnsRecord.docker_project == project,
            )
        ).all()
    )
    seen = {int(r.id) for r in rows if r.id is not None}
    if dest_id and int(dest_id) != int(source_id):
        for rec in session.exec(
            select(ServiceDnsRecord).where(
                ServiceDnsRecord.backend_server_id == int(dest_id),
                ServiceDnsRecord.docker_project == project,
            )
        ).all():
            rid = int(rec.id) if rec.id is not None else None
            if rid is None or rid in seen:
                continue
            rows.append(rec)
            seen.add(rid)
    return rows


def npm_edge_fqdns(session: Session) -> set[str]:
    """Hostnames of enabled NPM integrations (the public edge alias)."""
    out: set[str] = set()
    for integ in session.exec(
        select(Integration).where(
            Integration.type == TYPE_NPM,
            Integration.enabled == True,  # noqa: E712
        )
    ).all():
        host = urlparse(integ.base_url or "").hostname
        name = normalize_fqdn(host)
        if name:
            out.add(name)
    return out


def npm_edge_server(session: Session) -> Optional[Server]:
    """Fleet host that runs the NPM edge (CNAME target for via_proxy rows)."""
    edges = npm_edge_fqdns(session)
    if not edges:
        return None
    for rec in session.exec(select(ServiceDnsRecord)).all():
        if normalize_fqdn(rec.fqdn) not in edges:
            continue
        sid = int(rec.backend_server_id or rec.target_server_id or 0)
        srv = session.get(Server, sid) if sid else None
        if srv:
            return srv
    for b in session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.role == ROLE_PROXY_HOST,
        )
    ).all():
        if normalize_fqdn(b.external_label) not in edges:
            continue
        srv = session.get(Server, int(b.server_id or 0))
        if srv:
            return srv
    return None


def is_npm_edge_project(
    session: Session,
    server_id: int,
    project: str,
    rows: Optional[list[ServiceDnsRecord]] = None,
) -> bool:
    """True when this compose project *is* the NPM reverse-proxy stack."""
    edges = npm_edge_fqdns(session)
    if not edges:
        return False
    name = compose_project_name(project)
    recs = rows if rows is not None else _dns_rows(session, int(server_id), name)
    for rec in recs:
        if normalize_fqdn(rec.fqdn) in edges:
            return True
    for b in session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.server_id == int(server_id),
            IntegrationBinding.docker_project == name,
            IntegrationBinding.role == ROLE_PROXY_HOST,
        )
    ).all():
        if normalize_fqdn(b.external_label) in edges:
            return True
    return False


def npm_edge_dependents(
    session: Session,
    *,
    source_id: int,
    dest_id: Optional[int] = None,
    skip_ids: Optional[set[int]] = None,
) -> list[ServiceDnsRecord]:
    """via_proxy rows whose public CNAME target is this NPM host (not the moved project)."""
    ids = [int(source_id)]
    if dest_id:
        ids.append(int(dest_id))
    skip = skip_ids or set()
    out: list[ServiceDnsRecord] = []
    for rec in session.exec(
        select(ServiceDnsRecord).where(
            ServiceDnsRecord.via_proxy == True,  # noqa: E712
            ServiceDnsRecord.target_server_id.in_(ids),
        )
    ).all():
        rid = int(rec.id) if rec.id is not None else None
        if rid is not None and rid in skip:
            continue
        out.append(rec)
    return out


def _npm_hosts_cached(session: Session) -> tuple[list[dict[str, Any]], int]:
    rows = session.exec(
        select(Integration).where(
            Integration.type == TYPE_NPM,
            Integration.enabled == True,  # noqa: E712
        )
    ).all()
    hosts: list[dict[str, Any]] = []
    for integ in rows:
        try:
            data = json.loads(integ.last_status_json or "{}")
        except Exception:
            data = {}
        for h in data.get("proxy_hosts") or []:
            if isinstance(h, dict):
                hosts.append(h)
    return hosts, len(rows)


def _match_npm(hosts: list[dict[str, Any]], fqdn: str) -> Optional[dict[str, Any]]:
    want = (fqdn or "").strip().lower().rstrip(".")
    if not want:
        return None
    for h in hosts:
        domains = h.get("domain_names") or h.get("domains") or []
        if isinstance(domains, str):
            domains = [domains]
        for d in domains:
            if str(d).strip().lower().rstrip(".") == want:
                return h
    return None


def _match_npm_id(hosts: list[dict[str, Any]], host_id: str) -> Optional[dict[str, Any]]:
    hid = str(host_id or "").strip()
    if not hid:
        return None
    for h in hosts:
        if str(h.get("id") or "").strip() == hid:
            return h
    return None


def npm_proxy_hosts_for_project(
    session: Session, project: str
) -> list[dict[str, Any]]:
    """NPM proxy hosts bound to this compose project (fabric row not required).

    Operators often bind ``ai.hacknow.info`` on NPM without a ServiceDnsRecord.
    Move still has to PUT ``forward_host``.
    """
    name = compose_project_name(project)
    hosts, _n = _npm_hosts_cached(session)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    binds = session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.role == ROLE_PROXY_HOST,
            IntegrationBinding.docker_project == name,
        )
    ).all()
    for b in binds:
        label = (b.external_label or "").strip()
        hid = str(b.external_id or "").strip()
        match = _match_npm(hosts, label) if label else None
        if not match and hid:
            match = _match_npm_id(hosts, hid)
        domains: list[str] = []
        if match:
            raw = match.get("domain_names") or match.get("domains") or []
            if isinstance(raw, str):
                raw = [raw]
            domains = [str(d).strip() for d in raw if str(d).strip()]
        if label and not domains:
            domains = [label]
        npm_id = str((match or {}).get("id") or hid or "")
        for fqdn in domains:
            key = fqdn.lower().rstrip(".")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "fqdn": fqdn,
                    "npm_id": npm_id or None,
                    "forward_host": (match or {}).get("forward_host"),
                    "forward_port": (match or {}).get("forward_port"),
                    "binding_id": b.id,
                    "binding_server_id": b.server_id,
                }
            )
    return out


def _dataset_from_project(project_row: Optional[dict[str, Any]], docker_base: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    total = 0
    known = True
    if not project_row:
        return {
            "items": items,
            "bytes": None,
            "bytes_human": "unknown",
            "outside_jail": [],
            "truncated": [],
        }
    outside: list[str] = []
    truncated: list[str] = []
    jail = (docker_base or "").rstrip("/") + "/"
    seen: set[str] = set()
    for c in project_row.get("containers") or []:
        if not isinstance(c, dict) or c.get("placeholder"):
            continue
        detail = c.get("mounts_detail")
        if isinstance(detail, list) and detail:
            for m in detail:
                if not isinstance(m, dict):
                    continue
                src = str(m.get("source") or m.get("name") or "").strip()
                if not src or src in seen:
                    continue
                if is_truncated_host_path(src):
                    truncated.append(src)
                    seen.add(src)
                    continue
                seen.add(src)
                b = m.get("size_bytes")
                if b is None:
                    known = False
                else:
                    total += int(b)
                vol = named_volume_id(
                    source=src,
                    mtype=str(m.get("type") or ""),
                    name=str(m.get("name") or ""),
                )
                if vol:
                    items.append(
                        {
                            "source": src,
                            "kind": "named",
                            "volume": vol,
                            "bytes": int(b) if b is not None else None,
                        }
                    )
                    continue
                mode = _bind_item_kind(src)
                items.append(
                    {
                        "source": src,
                        "kind": mode,
                        "bytes": int(b) if b is not None else None,
                    }
                )
                if _is_outside_jail_bind(src, mode, jail, docker_base):
                    outside.append(src)
            continue
        for line in c.get("mounts_list") or []:
            src = _mount_source_from_line(str(line))
            if not src or src in seen:
                continue
            if is_truncated_host_path(src):
                truncated.append(src)
                seen.add(src)
                continue
            seen.add(src)
            vol = named_volume_id(source=src)
            if vol:
                known = False
                items.append({"source": src, "kind": "named", "volume": vol, "bytes": None})
                continue
            mode = _bind_item_kind(src)
            known = False
            items.append({"source": src, "kind": mode, "bytes": None})
            if _is_outside_jail_bind(src, mode, jail, docker_base):
                outside.append(src)
        tb = c.get("mounts_total_bytes")
        if tb:
            # already counted via detail; if no detail, use total once
            if not detail:
                total += int(tb)
                known = True
    bytes_out = total if (items and known) else (total if total else None)
    if items and not known:
        bytes_out = total if total else None
    return {
        "items": items[:80],
        "bytes": bytes_out,
        "bytes_human": _human(bytes_out),
        "outside_jail": outside,
        "truncated": truncated,
    }


def _bind_item_kind(src: str) -> str:
    if is_host_local_bind(src):
        return "bind_host_local"
    mode, _ = classify_volume_source(src)
    return mode


def _is_outside_jail_bind(src: str, mode: str, jail: str, docker_base: str) -> bool:
    if mode not in ("bind_absolute", "bind_host_local"):
        return False
    if jail == "/" or src.startswith(jail) or src == docker_base:
        return False
    if src.startswith("/dev/"):
        return False
    return True


def _container_host_network(c: dict[str, Any]) -> bool:
    mode = str(c.get("network_mode") or "").strip().lower()
    if mode == "host":
        return True
    nets = c.get("networks") or []
    if isinstance(nets, str):
        nets = [nets]
    for n in nets:
        if str(n).strip().lower() == "host":
            return True
    return False


def _parse_port_token(raw: Any) -> Optional[tuple[str, str]]:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    proto = "tcp"
    if "/" in s:
        left, right = s.rsplit("/", 1)
        s, proto = left.strip(), (right.strip() or "tcp")
    if not s.isdigit():
        return None
    return (s, proto)


def _exposed_ports(c: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in c.get("exposed_ports") or []:
        parsed = _parse_port_token(raw)
        if parsed:
            out.append(parsed)
    return out


def _project_uses_host_network(project_row: Optional[dict[str, Any]]) -> bool:
    if not project_row:
        return False
    return any(
        isinstance(c, dict) and not c.get("placeholder") and _container_host_network(c)
        for c in (project_row.get("containers") or [])
    )


def _host_network_ports(project_row: Optional[dict[str, Any]]) -> set[tuple[str, str]]:
    used: set[tuple[str, str]] = set()
    if not project_row:
        return used
    for c in project_row.get("containers") or []:
        if not isinstance(c, dict) or c.get("placeholder") or not _container_host_network(c):
            continue
        for p in parse_published_ports(c.get("ports_display"), c.get("ports")):
            host = str(p.get("host") or "").strip()
            proto = str(p.get("proto") or "tcp")
            if host:
                used.add((host, proto))
        used.update(_exposed_ports(c))
    return used


def _hardware_warn(project_row: Optional[dict[str, Any]]) -> Optional[str]:
    if not project_row:
        return None
    hits: list[str] = []
    host_net = False
    privileged = False
    for c in project_row.get("containers") or []:
        if not isinstance(c, dict):
            continue
        if _container_host_network(c):
            host_net = True
        if c.get("privileged"):
            privileged = True
        blobs: list[str] = []
        for m in c.get("mounts_detail") or []:
            if isinstance(m, dict):
                blobs.append(str(m.get("source") or ""))
        for line in c.get("mounts_list") or []:
            blobs.append(str(line))
        for b in blobs:
            if b.startswith("/dev/") or _DEV_HINT.search(b):
                hits.append(b.split(":")[0])
    parts: list[str] = []
    if hits:
        uniq = sorted(set(hits))[:6]
        parts.append("Hardware-looking mounts: " + ", ".join(uniq))
    if host_net:
        parts.append(
            "uses host network (the process binds dest host ports; "
            "published-port remap does not apply)"
        )
    if privileged:
        parts.append("privileged")
    if not parts:
        return None
    return "; ".join(parts)


def _published_ports(project_row: Optional[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not project_row:
        return out
    for c in project_row.get("containers") or []:
        if not isinstance(c, dict) or c.get("placeholder"):
            continue
        for p in parse_published_ports(c.get("ports_display"), c.get("ports")):
            host = str(p.get("host") or "")
            proto = str(p.get("proto") or "tcp")
            key = (host, proto)
            if host and key not in seen:
                seen.add(key)
                out.append(key)
        if _container_host_network(c):
            for key in _exposed_ports(c):
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


def _dest_used_ports(dest: Server) -> set[tuple[str, str]]:
    inv = inventory_svc.parse_inventory(dest) or {}
    used: set[tuple[str, str]] = set()
    for p in inv.get("projects") or []:
        if not isinstance(p, dict):
            continue
        for c in p.get("containers") or []:
            if not isinstance(c, dict) or c.get("placeholder"):
                continue
            for pr in parse_published_ports(c.get("ports_display"), c.get("ports")):
                host = str(pr.get("host") or "")
                proto = str(pr.get("proto") or "tcp")
                if host:
                    used.add((host, proto))
    for c in inv.get("orphan_containers") or []:
        if not isinstance(c, dict):
            continue
        for pr in parse_published_ports(c.get("ports_display"), c.get("ports")):
            host = str(pr.get("host") or "")
            proto = str(pr.get("proto") or "tcp")
            if host:
                used.add((host, proto))
    return used


def _kuma_ip_warn(session: Session, source_id: int, project: str) -> Optional[str]:
    binds = session.exec(
        select(IntegrationBinding).where(
            IntegrationBinding.server_id == source_id,
            IntegrationBinding.role == ROLE_SERVICE,
            IntegrationBinding.docker_project == project,
        )
    ).all()
    ip_hits: list[str] = []
    for b in binds:
        blob = " ".join(
            str(x or "")
            for x in (b.external_label, b.external_id, b.last_message, b.external_meta_json)
        )
        if _IPV4.search(blob):
            ip_hits.append(b.external_label or b.external_id)
    if not ip_hits:
        return None
    return (
        "Kuma binding looks IP-based ("
        + ", ".join(ip_hits[:4])
        + ") — monitor hostname will not be rewritten"
    )


def run_preflight(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    source_facts: Optional[dict[str, Any]] = None,
    dest_facts: Optional[dict[str, Any]] = None,
    herder_free: Optional[int] = None,
    dest_project: Optional[str] = None,
    port_map: Optional[dict[str, str]] = None,
    bind_overrides: Optional[list[dict[str, Any]]] = None,
    ignore_job_id: Optional[int] = None,
    live_inspect: bool = False,
    inspect_fn=None,
    dest_occupy_fn=None,
) -> dict[str, Any]:
    name = compose_project_name(project)
    dest_name, dest_name_err = normalize_dest_project(name, dest_project)
    blocks: list[dict[str, Any]] = []
    warns: list[dict[str, Any]] = []
    clean_map, map_errors = validate_port_map(port_map)
    if dest_name_err:
        blocks.append(
            _item(
                "dest_project_invalid",
                f"Destination project name is invalid: {dest_name_err}",
            )
        )
    for err in map_errors:
        blocks.append(_item("port_map_invalid", err))

    if is_haos_server(source):
        blocks.append(_item("haos_source", "Source host is HAOS — never a migrate source"))
    if is_haos_server(dest):
        blocks.append(_item("haos_dest", "Destination host is HAOS — never a migrate dest"))
    if source.id is not None and dest.id is not None and int(source.id) == int(dest.id):
        blocks.append(_item("same_host", "Destination must be a different host"))
    if not getattr(dest, "container_patch_enabled", False):
        blocks.append(_item("dest_docker_off", "Destination does not have Docker / containers enabled"))

    lock = lock_state(session, source, name)
    if lock.get("locked"):
        label = lock.get("reason_label") or "this host"
        note = lock.get("note")
        msg = f"Source project is locked ({label})"
        if note:
            msg = f"{msg}: {note}"
        blocks.append(_item("host_lock", msg, reason=lock.get("reason")))

    src_row = _project_from_inventory(source, name)
    project_files: list[str] = []
    if src_row is not None and live_inspect:
        try:
            from .facts import inspect_project_mounts, list_project_tree

            filled = (inspect_fn or inspect_project_mounts)(source, src_row)
            if isinstance(filled, dict):
                src_row = filled
            src_base_early = str(
                (source_facts or {}).get("docker_base") or docker_base_abs(source)
            )
            project_files = list_project_tree(
                source, f"{src_base_early.rstrip('/')}/{name}"
            )
        except Exception:
            pass
    if src_row is None:
        blocks.append(
            _item(
                "source_missing",
                "Project is not in the source Docker inventory (refresh Docker on the source host)",
            )
        )

    dest_names = _inventory_project_names(dest)
    dst_base = str((dest_facts or {}).get("docker_base") or docker_base_abs(dest))
    dst_proj = f"{dst_base.rstrip('/')}/{dest_name}"
    dest_live: Optional[dict[str, Any]] = None
    dest_ports: Optional[set[tuple[str, str]]] = None
    if live_inspect:
        try:
            dest_live = (dest_occupy_fn or probe_dest_occupancy)(dest, dest_name, dst_proj)
        except Exception:
            dest_live = {"error": "probe failed"}
        if not isinstance(dest_live, dict):
            dest_live = {"error": "probe failed"}
        if dest_live.get("error"):
            blocks.append(
                _item(
                    "dest_live_failed",
                    "Could not SSH destination to see what is actually there "
                    f"({dest_live.get('error')}). Recheck when dest is reachable — "
                    "cached dest Docker inventory is not used.",
                )
            )
            dest_ports = set()
        else:
            dest_ports = _live_dest_ports(dest_live)
            if dest_live.get("listen_ports"):
                dest_ports |= _live_dest_ports({"ports": dest_live.get("listen_ports")})
            running = _dest_running_containers(dest_live)
            files = _dest_folder_nonempty(dest_live)
            ghosts = _dest_leftover_container_names(dest_live)
            if running:
                blocks.append(
                    _item(
                        "dest_project_exists",
                        f"Destination already has running containers for {dest_name}: "
                        + ", ".join(running[:8])
                        + " — pick a different dest name or stop that stack on dest",
                        dest_project=dest_name,
                    )
                )
            elif files or dest_live.get("nonempty"):
                sample = ", ".join(files[:8]) if files else "files"
                warns.append(
                    _item(
                        "dest_folder_overwrite",
                        f"Destination folder {dst_proj} is not empty ({sample}). "
                        "Move will overwrite it (rsync --delete). "
                        "Empty it first only if you do not want that.",
                        dest_project=dest_name,
                    )
                )
            if ghosts and not running:
                dest_ports -= _live_dest_ports(
                    {"ports": dest_live.get("project_ports") or []}
                )
                warns.append(
                    _item(
                        "dest_leftover_containers",
                        "Destination has leftover compose containers from a previous "
                        f"attempt ({', '.join(ghosts[:8])}). Move will remove them "
                        "before dest up — you do not need to docker rm on dest.",
                        dest_project=dest_name,
                    )
                )
    elif dest_name in dest_names:
        blocks.append(
            _item(
                "dest_project_exists",
                f"Destination already has a compose project named {dest_name} — "
                "pick a different dest name, or remove leftovers on dest then Recheck",
                dest_project=dest_name,
            )
        )

    src_arch = (source_facts or {}).get("arch")
    dst_arch = (dest_facts or {}).get("arch")
    if src_arch and dst_arch and str(src_arch) != str(dst_arch):
        blocks.append(
            _item(
                "arch_mismatch",
                f"Architecture mismatch: source {src_arch} vs dest {dst_arch}",
            )
        )
    elif not dst_arch:
        warns.append(_item("dest_arch_unknown", "Could not read destination arch (uname -m)"))
    elif not src_arch:
        warns.append(_item("source_arch_unknown", "Could not read source arch (uname -m)"))

    if dest_facts and dest_facts.get("docker_base_writable") is False:
        blocks.append(
            _item(
                "dest_not_writable",
                "Destination docker base dir is not writable by the fleet SSH user",
            )
        )
    elif dest_facts is not None and dest_facts.get("docker_base_writable") is None:
        warns.append(_item("dest_writable_unknown", "Could not test destination docker base writability"))

    src_base = str((source_facts or {}).get("docker_base") or docker_base_abs(source))
    dataset = _dataset_from_project(src_row, src_base)
    for path in dataset.get("truncated") or []:
        blocks.append(
            _item(
                "bind_truncated",
                "Inventory mount path is truncated (docker ps ellipsis) — "
                f"not a real directory, will not rsync: {path}. "
                "Refresh Docker on the source host so inspect fills full paths, then Recheck.",
                path=path,
            )
        )
    payload = dataset.get("bytes")
    margin = (
        max(int(payload * DISK_MARGIN_RATIO), DISK_MARGIN_MIN) if payload else DISK_MARGIN_MIN
    )
    dest_free = (dest_facts or {}).get("disk_free_bytes")
    if payload and dest_free is not None and int(dest_free) < int(payload) + margin:
        blocks.append(
            _item(
                "dest_disk",
                f"Destination free space {_human(dest_free)} is below payload {_human(payload)} + margin {_human(margin)}",
            )
        )
    elif dest_free is None and dest_facts is not None:
        warns.append(_item("dest_disk_unknown", "Could not read destination free disk"))

    if herder_free is not None and payload and int(herder_free) < int(payload) + margin:
        blocks.append(
            _item(
                "herder_disk",
                f"Herder staging disk {_human(herder_free)} is below payload {_human(payload)} + margin",
            )
        )

    by_src = {
        str(r.get("source") or ""): r
        for r in (bind_overrides or [])
        if isinstance(r, dict) and r.get("source")
    }
    bind_rows: list[dict[str, Any]] = []
    unresolved_binds: list[str] = []
    skipped_binds: list[str] = []
    bind_map: dict[str, str] = {}
    host_local_binds: list[str] = []
    for i, path in enumerate(dataset.get("outside_jail") or []):
        ov = by_src.get(path) or {}
        host_local = is_host_local_bind(path)
        dest_default = suggest_dest_bind(path, src_base, dst_base, dest_name)
        dest_path = str(ov.get("dest") or dest_default).strip()
        skip = True if host_local else bool(ov.get("skip"))
        if host_local:
            dest_path = path
            dest_default = path
        err = None if skip else validate_dest_bind_path(dest_path, dst_base)
        row = {
            "index": i,
            "source": path,
            "dest": dest_path,
            "dest_default": dest_default,
            "skip": skip,
            "ok": skip or err is None,
            "error": err,
            "host_local": host_local,
        }
        bind_rows.append(row)
        if host_local:
            host_local_binds.append(path)
            skipped_binds.append(path)
            warns.append(
                _item(
                    "bind_host_local",
                    f"Host socket/device {path} — not copied. Dest will bind "
                    "the dest host's own path (Uptime Kuma docker.sock, /dev, …).",
                    path=path,
                )
            )
        elif skip:
            skipped_binds.append(path)
            warns.append(
                _item(
                    "bind_skipped",
                    f"Will not copy bind {path} — dest compose still needs a dest path or the same host path on dest",
                    path=path,
                )
            )
        elif err:
            unresolved_binds.append(path)
            blocks.append(
                _item(
                    "bind_outside_jail",
                    f"Absolute bind outside source docker base ({src_base}): {path} — set a dest path under {dst_base} or skip copy",
                    path=path,
                )
            )
        else:
            bind_map[path] = dest_path
            if not path_in_jail(dest_path, dst_base):
                warns.append(
                    _item(
                        "bind_dest_outside_project",
                        f"Dest bind {dest_path} is outside dest docker base — "
                        "prefer dest project folder unless you overrode it.",
                        path=dest_path,
                    )
                )
    skipped_data = [p for p in skipped_binds if p not in host_local_binds]
    if skipped_data:
        warns.append(
            _item(
                "bind_skip_ack",
                "One or more absolute binds will not be copied. Dest may miss that data.",
            )
        )

    hw = _hardware_warn(src_row)
    if hw:
        warns.append(_item("devices", hw))

    src_ports = _published_ports(src_row)
    host_network = _project_uses_host_network(src_row)
    host_net_ports = _host_network_ports(src_row)
    if dest_ports is None:
        dest_ports = _dest_used_ports(dest)
    port_rows: list[dict[str, Any]] = []
    seen_port: set[tuple[str, str]] = set()
    clashes: list[str] = []
    host_net_clashes: list[str] = []
    remapped_host_net: list[str] = []
    for host, proto in src_ports:
        key = (str(host), str(proto or "tcp"))
        if key in seen_port:
            continue
        seen_port.add(key)
        dest_host = mapped_host_port(host, proto, clean_map)
        used = (dest_host, proto) in dest_ports
        on_host_net = key in host_net_ports
        row = {
            "host": str(host),
            "proto": str(proto or "tcp"),
            "dest_host": dest_host,
            "field": f"dest_port_{host}_{proto}",
            "clash": used,
            "host_network": on_host_net,
        }
        port_rows.append(row)
        if used:
            clashes.append(f"{dest_host}/{proto}")
            if on_host_net:
                host_net_clashes.append(f"{dest_host}/{proto}")
        if on_host_net and dest_host != str(host):
            remapped_host_net.append(f"{host}/{proto}→{dest_host}")
    if remapped_host_net:
        blocks.append(
            _item(
                "host_network_remap",
                "Host-network ports cannot be remapped (the process still binds "
                "the source host port on dest): "
                + ", ".join(remapped_host_net[:12])
                + " — leave dest ports as source, stop the dest listener, "
                "or change compose off host network first",
                ports=remapped_host_net,
            )
        )
    if clashes:
        extra = ""
        if host_net_clashes:
            extra = (
                " Host network: remap does not apply — dest must have "
                + ", ".join(host_net_clashes[:8])
                + " free (stop that dest listener or pick another dest)."
            )
        blocks.append(
            _item(
                "port_clash",
                "Published port clash on destination: "
                + ", ".join(clashes[:12])
                + " — that port is already bound on dest (live listen / docker). "
                + (
                    extra.strip()
                    if extra
                    else "Remap the dest host port below"
                ),
                ports=clashes,
            )
        )

    for job in _busy_jobs(session, int(source.id or 0), ignore_job_id):
        blocks.append(
            _item(
                "busy_source",
                f"Source host has an active {job.job_type} job #{job.id}",
                job_id=job.id,
            )
        )
        break
    for job in _busy_jobs(session, int(dest.id or 0), ignore_job_id):
        blocks.append(
            _item(
                "busy_dest",
                f"Destination host has an active {job.job_type} job #{job.id}",
                job_id=job.id,
            )
        )
        break

    dns_rows = _dns_rows(
        session, int(source.id or 0), name, dest_id=int(dest.id or 0)
    )
    dns_out: list[dict[str, Any]] = []
    npm_hosts, npm_count = _npm_hosts_cached(session)
    dest_dns = (getattr(dest, "dns_name", None) or "").strip()
    edge_names = npm_edge_fqdns(session)
    moving_edge = is_npm_edge_project(
        session, int(source.id or 0), name, rows=dns_rows
    )
    for rec in dns_rows:
        via = bool(rec.via_proxy)
        entry = {
            "fqdn": rec.fqdn,
            "via_proxy": via,
            "external_dns_status": rec.external_dns_status,
        }
        if via:
            entry["action"] = "npm"
            if npm_count == 0:
                blocks.append(
                    _item(
                        "npm_missing",
                        f"{rec.fqdn} is via NPM but no enabled NPM integration is configured",
                        fqdn=rec.fqdn,
                    )
                )
            else:
                match = _match_npm(npm_hosts, rec.fqdn)
                if not match:
                    blocks.append(
                        _item(
                            "npm_unmatched",
                            f"No NPM proxy host matches {rec.fqdn} (poll NPM if the list is stale)",
                            fqdn=rec.fqdn,
                        )
                    )
                else:
                    entry["npm_id"] = match.get("id")
                    entry["forward_host"] = match.get("forward_host")
        else:
            entry["action"] = "cname"
            if not dest_dns:
                blocks.append(
                    _item(
                        "dest_dns_name",
                        "Destination has no DNS name — cannot retarget a direct CNAME",
                    )
                )
        if rec.external_dns_status not in ("done", "none"):
            warns.append(
                _item(
                    "external_dns",
                    f"{rec.fqdn} still has an external DNS checklist (Cloudflare / other)",
                    fqdn=rec.fqdn,
                )
            )
        dns_out.append(entry)

    dest_fwd = (
        (getattr(dest, "ip_address", None) or "").strip()
        or (getattr(dest, "hostname", None) or "").strip()
        or dest_dns
    )
    seen_dns = {
        (d.get("fqdn") or "").strip().lower().rstrip(".") for d in dns_out
    }
    for phost in npm_proxy_hosts_for_project(session, name):
        fqdn = phost.get("fqdn") or ""
        key = fqdn.strip().lower().rstrip(".")
        if not key or key in seen_dns:
            continue
        seen_dns.add(key)
        entry = {
            "fqdn": fqdn,
            "via_proxy": True,
            "action": "npm",
            "from_binding": True,
            "npm_id": phost.get("npm_id"),
            "forward_host": phost.get("forward_host"),
            "external_dns_status": None,
        }
        if npm_count == 0:
            blocks.append(
                _item(
                    "npm_missing",
                    f"{fqdn} is an NPM proxy host for this project but no enabled NPM integration is configured",
                    fqdn=fqdn,
                )
            )
        elif not phost.get("npm_id") and phost.get("forward_host") is None:
            blocks.append(
                _item(
                    "npm_unmatched",
                    f"No NPM proxy host matches {fqdn} (poll NPM if the list is stale)",
                    fqdn=fqdn,
                )
            )
        elif not dest_fwd:
            blocks.append(
                _item(
                    "dest_forward_host",
                    "Destination has no IP/hostname — cannot retarget NPM forward_host",
                    fqdn=fqdn,
                )
            )
        else:
            entry["npm_id"] = phost.get("npm_id")
            entry["forward_host"] = phost.get("forward_host")
        warns.append(
            _item(
                "npm_binding",
                f"{fqdn} is proxied via NPM for this project (no fabric DNS row) — Move will PUT forward_host to dest",
                fqdn=fqdn,
            )
        )
        dns_out.append(entry)

    npm_edge_deps: list[dict[str, Any]] = []
    if moving_edge:
        skip = {int(r.id) for r in dns_rows if r.id is not None}
        dep_rows = npm_edge_dependents(
            session,
            source_id=int(source.id or 0),
            dest_id=int(dest.id or 0),
            skip_ids=skip,
        )
        alias = ", ".join(sorted(edge_names)) or "the NPM hostname"
        npm_edge_deps = [
            {"fqdn": rec.fqdn, "action": "keep_cname_on_edge"} for rec in dep_rows
        ]
        n = len(npm_edge_deps)
        warns.append(
            _item(
                "npm_edge",
                f"This stack is the NPM edge ({alias}). "
                + (
                    f"{n} public name(s) CNAME to that alias and stay; "
                    if n
                    else "Public names CNAME to that alias and stay; "
                )
                + "only the edge CNAME moves to dest. "
                "Pi-hole admin URLs that go through this proxy are reached on LAN during cutover.",
                fqdn=alias,
                dependents=n,
            )
        )

    kuma = _kuma_ip_warn(session, int(source.id or 0), name)
    if kuma:
        warns.append(_item("kuma_ip", kuma))

    # de-dupe dest_dns_name if many rows
    seen_ids: set[str] = set()
    uniq_blocks: list[dict[str, Any]] = []
    for b in blocks:
        key = f"{b['id']}:{b.get('fqdn') or b.get('path') or b.get('message')}"
        if key in seen_ids:
            continue
        seen_ids.add(key)
        uniq_blocks.append(b)

    return {
        "ok": len(uniq_blocks) == 0,
        "can_copy": False,
        "project": name,
        "dest_project": dest_name,
        "dest_project_input": (dest_project or "").strip() or dest_name,
        "port_map": clean_map,
        "ports": port_rows,
        "binds": bind_rows,
        "bind_map": bind_map,
        "skip_binds": skipped_binds,
        "host_network": host_network,
        "blocks": uniq_blocks,
        "warns": warns,
        "dataset": dataset,
        "project_files": project_files,
        "dns": dns_out,
        "npm_edge": moving_edge,
        "npm_edge_dependents": npm_edge_deps,
        "source": {
            "id": source.id,
            "name": source.name,
            "hostname": source.hostname,
            "dns_name": getattr(source, "dns_name", None),
            "arch": src_arch,
            "docker_base": src_base,
            "project_path": f"{src_base.rstrip('/')}/{name}",
        },
        "leftover_remove": {
            "project_path": f"{src_base.rstrip('/')}/{name}",
            "named_volumes": sorted(
                v
                for v in (
                    {
                        (str(it.get("volume") or "").strip()
                         or named_volume_id(source=str(it.get("source") or ""))
                         or "")
                        for it in (dataset.get("items") or [])
                        if isinstance(it, dict) and it.get("kind") == "named"
                    }
                    - {""}
                )
                if "/" not in v and ".." not in v
            ),
        },
        "dest": {
            "id": dest.id,
            "name": dest.name,
            "hostname": dest.hostname,
            "dns_name": dest_dns or None,
            "arch": dst_arch,
            "disk_free_bytes": dest_free,
            "disk_free_human": _human(dest_free) if dest_free is not None else "unknown",
            "project": dest_name,
            "project_path": f"{dst_base.rstrip('/')}/{dest_name}",
            "docker_base": dst_base,
        },
    }
