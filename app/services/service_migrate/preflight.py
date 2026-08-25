"""Migrate preflight matrix (v1.4 M2). DB-first; SSH facts optional."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import Integration, IntegrationBinding, Job, Server, ServiceDnsRecord
from .. import docker_inventory as inventory_svc
from ..compose_project_files import classify_volume_source
from ..dns_fabric.ports import parse_published_ports
from ..haos import is_haos_server
from ..integrations.registry import ROLE_SERVICE, TYPE_NPM
from .facts import docker_base_abs
from .host_lock import compose_project_name, lock_state

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


def _busy_jobs(session: Session, server_id: int) -> list[Job]:
    sid = int(server_id or 0)
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
    return rows


def _dns_rows(session: Session, source_id: int, project: str) -> list[ServiceDnsRecord]:
    return list(
        session.exec(
            select(ServiceDnsRecord).where(
                ServiceDnsRecord.backend_server_id == source_id,
                ServiceDnsRecord.docker_project == project,
            )
        ).all()
    )


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


def _dataset_from_project(project_row: Optional[dict[str, Any]], docker_base: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    total = 0
    known = True
    if not project_row:
        return {"items": items, "bytes": None, "bytes_human": "unknown", "outside_jail": []}
    outside: list[str] = []
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
                mode, _ = classify_volume_source(src)
                items.append(
                    {
                        "source": src,
                        "kind": mode,
                        "bytes": int(b) if b is not None else None,
                    }
                )
                if mode == "bind_absolute" and jail != "/" and not src.startswith(jail) and src != docker_base:
                    if not src.startswith("/dev/"):
                        outside.append(src)
            continue
        for line in c.get("mounts_list") or []:
            src = _mount_source_from_line(str(line))
            if not src or src in seen:
                continue
            seen.add(src)
            vol = named_volume_id(source=src)
            if vol:
                known = False
                items.append({"source": src, "kind": "named", "volume": vol, "bytes": None})
                continue
            mode, _ = classify_volume_source(src)
            known = False
            items.append({"source": src, "kind": mode, "bytes": None})
            if mode == "bind_absolute" and jail != "/" and not src.startswith(jail) and src != docker_base:
                if not src.startswith("/dev/"):
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
    }


def _hardware_warn(project_row: Optional[dict[str, Any]]) -> Optional[str]:
    if not project_row:
        return None
    hits: list[str] = []
    for c in project_row.get("containers") or []:
        if not isinstance(c, dict):
            continue
        blobs: list[str] = []
        for m in c.get("mounts_detail") or []:
            if isinstance(m, dict):
                blobs.append(str(m.get("source") or ""))
        for line in c.get("mounts_list") or []:
            blobs.append(str(line))
        for b in blobs:
            if b.startswith("/dev/") or _DEV_HINT.search(b):
                hits.append(b.split(":")[0])
    if not hits:
        return None
    uniq = sorted(set(hits))[:6]
    return "Hardware-looking mounts: " + ", ".join(uniq)


def _published_ports(project_row: Optional[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not project_row:
        return out
    for c in project_row.get("containers") or []:
        if not isinstance(c, dict) or c.get("placeholder"):
            continue
        for p in parse_published_ports(c.get("ports_display"), c.get("ports")):
            host = str(p.get("host") or "")
            proto = str(p.get("proto") or "tcp")
            if host:
                out.append((host, proto))
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
) -> dict[str, Any]:
    name = compose_project_name(project)
    blocks: list[dict[str, Any]] = []
    warns: list[dict[str, Any]] = []

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
    if src_row is None:
        blocks.append(
            _item(
                "source_missing",
                "Project is not in the source Docker inventory (refresh Docker on the source host)",
            )
        )

    dest_names = _inventory_project_names(dest)
    if name in dest_names:
        blocks.append(
            _item(
                "dest_project_exists",
                f"Destination already has a compose project named {name}",
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

    src_base = docker_base_abs(source)
    dataset = _dataset_from_project(src_row, src_base)
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

    for path in dataset.get("outside_jail") or []:
        blocks.append(
            _item(
                "bind_outside_jail",
                f"Absolute bind outside docker base dir will not be copied silently: {path}",
                path=path,
            )
        )

    hw = _hardware_warn(src_row)
    if hw:
        warns.append(_item("devices", hw))

    src_ports = _published_ports(src_row)
    dest_ports = _dest_used_ports(dest)
    clashes = sorted({f"{h}/{p}" for h, p in src_ports if (h, p) in dest_ports})
    if clashes:
        blocks.append(
            _item(
                "port_clash",
                "Published port clash on destination: " + ", ".join(clashes[:12]),
                ports=clashes,
            )
        )

    for job in _busy_jobs(session, int(source.id or 0)):
        blocks.append(
            _item(
                "busy_source",
                f"Source host has an active {job.job_type} job #{job.id}",
                job_id=job.id,
            )
        )
        break
    for job in _busy_jobs(session, int(dest.id or 0)):
        blocks.append(
            _item(
                "busy_dest",
                f"Destination host has an active {job.job_type} job #{job.id}",
                job_id=job.id,
            )
        )
        break

    dns_rows = _dns_rows(session, int(source.id or 0), name)
    dns_out: list[dict[str, Any]] = []
    npm_hosts, npm_count = _npm_hosts_cached(session)
    dest_dns = (getattr(dest, "dns_name", None) or "").strip()
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
        "blocks": uniq_blocks,
        "warns": warns,
        "dataset": dataset,
        "dns": dns_out,
        "source": {
            "id": source.id,
            "name": source.name,
            "hostname": source.hostname,
            "dns_name": getattr(source, "dns_name", None),
            "arch": src_arch,
        },
        "dest": {
            "id": dest.id,
            "name": dest.name,
            "hostname": dest.hostname,
            "dns_name": dest_dns or None,
            "arch": dst_arch,
            "disk_free_bytes": dest_free,
            "disk_free_human": _human(dest_free) if dest_free is not None else "unknown",
        },
    }
