"""Dest project rename + published-port remap for service migrate."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from ..compose_project_files import COMPOSE_BASENAMES, OVERRIDE_BASENAMES
from .host_lock import HostLockError, compose_project_name

logger = logging.getLogger(__name__)

_DEST_PORT_KEY = re.compile(r"^dest_port_(\d{1,5})_(tcp|udp)$", re.I)
_BIND_SRC_KEY = re.compile(r"^bind_src_(\d+)$")
_DEST_BIND_KEY = re.compile(r"^dest_bind_(\d+)$")
_SKIP_BIND_KEY = re.compile(r"^skip_bind_(\d+)$")
_SHORT_BIND = re.compile(
    r"^(?P<pre>\s*-\s*)(?P<q>[\"']?)(?P<src>/[^:\"'\s]+)(?P<rest>:[^\n]*)$"
)
_SHORT_PORT = re.compile(
    r"^(?P<pre>\s*-\s*)(?P<q>[\"']?)"
    r"(?:(?P<ip>(?:\d{1,3}\.){3}\d{1,3}):)?"
    r"(?P<host>\d{1,5}):(?P<container>\d{1,5})"
    r"(?P<pr>/tcp|/udp)?"
    r"(?P<q2>[\"']?)(?P<sp>\s*)$",
    re.I,
)
_BARE_PORT = re.compile(
    r"^(?P<pre>\s*-\s*)(?P<q>[\"']?)"
    r"(?P<host>\d{1,5})(?P<pr>/tcp|/udp)?"
    r"(?P<q2>[\"']?)(?P<sp>\s*)$",
    re.I,
)
_PUBLISHED = re.compile(
    r"^(?P<pre>\s*published:\s*)(?P<q>[\"']?)(?P<host>\d{1,5})(?P<q2>[\"']?)(?P<sp>\s*)$",
    re.I,
)
_TOP_NAME = re.compile(r"^(name:\s*)([\"']?)([^\"'\n]+)([\"']?)\s*$")
_COMPOSE_PROJECT_ENV = re.compile(
    r"^(COMPOSE_PROJECT_NAME=)([\"']?)([^\"'\n]*)([\"']?)\s*$"
)

_COMPOSE_NAMES = frozenset(
    b.lower() for b in COMPOSE_BASENAMES + OVERRIDE_BASENAMES
)


def port_map_key(host: str | int, proto: str = "tcp") -> str:
    return f"{int(host)}/{(proto or 'tcp').lower()}"


def parse_port_map_from_mapping(mapping: Mapping[Any, Any] | None) -> dict[str, str]:
    """Read dest_port_{host}_{proto} fields from query or form."""
    out: dict[str, str] = {}
    if not mapping:
        return out
    items = mapping.multi_items() if hasattr(mapping, "multi_items") else mapping.items()
    for k, v in items:
        m = _DEST_PORT_KEY.match(str(k))
        if not m:
            continue
        val = str(v or "").strip()
        if not val:
            continue
        out[port_map_key(m.group(1), m.group(2))] = val
    return out


def validate_port_map(port_map: Mapping[str, str] | None) -> tuple[dict[str, str], list[str]]:
    clean: dict[str, str] = {}
    errors: list[str] = []
    seen_dest: dict[str, str] = {}
    for raw_key, raw_val in (port_map or {}).items():
        key = str(raw_key).strip().lower()
        val = str(raw_val).strip()
        if "/" not in key:
            errors.append(f"invalid port map key {raw_key}")
            continue
        host_s, proto = key.split("/", 1)
        if proto not in ("tcp", "udp"):
            errors.append(f"{key}: protocol must be tcp or udp")
            continue
        if not host_s.isdigit() or not (1 <= int(host_s) <= 65535):
            errors.append(f"{key}: source host port must be 1–65535")
            continue
        if not val.isdigit() or not (1 <= int(val) <= 65535):
            errors.append(f"{key}: destination host port must be 1–65535")
            continue
        dest_key = port_map_key(val, proto)
        prev = seen_dest.get(dest_key)
        src_key = port_map_key(host_s, proto)
        if prev and prev != src_key:
            errors.append(f"two published ports mapped to {dest_key}")
            continue
        seen_dest[dest_key] = src_key
        clean[src_key] = str(int(val))
    return clean, errors


def normalize_dest_project(
    source_project: str, dest_raw: Optional[str]
) -> tuple[str, Optional[str]]:
    """Return (dest_project, error). Empty dest_raw → source name."""
    raw = (dest_raw or "").strip()
    if not raw:
        return source_project, None
    try:
        return compose_project_name(raw), None
    except HostLockError as e:
        return source_project, e.message or "invalid destination project name"


def remap_named_volume(volume: str, source_project: str, dest_project: str) -> str:
    vol = (volume or "").strip()
    src = (source_project or "").strip()
    dest = (dest_project or "").strip()
    if not vol or not src or not dest or src == dest:
        return vol
    for sep in ("_", "-"):
        prefix = src + sep
        if vol.startswith(prefix):
            return dest + sep + vol[len(prefix) :]
    return vol


def is_truncated_host_path(path: str) -> bool:
    """True when docker ps (or UI) stored an ellipsis instead of a real path."""
    p = (path or "").strip()
    if not p:
        return False
    if "…" in p or "\u2026" in p:
        return True
    if "..." in p:
        return True
    return False


def path_in_jail(path: str, docker_base: str) -> bool:
    """True when path is the docker base or a child of it."""
    p = os.path.normpath((path or "").strip())
    base = os.path.normpath((docker_base or "").strip())
    if not p or not base or base == "/":
        return False
    return p == base or p.startswith(base.rstrip("/") + "/")


def suggest_dest_bind(
    source_path: str, src_base: str, dest_base: str, dest_project: str
) -> str:
    src = os.path.normpath((source_path or "").strip())
    src_b = os.path.normpath((src_base or "").strip()).rstrip("/")
    dest_b = os.path.normpath((dest_base or "").strip()).rstrip("/")
    dest_p = (dest_project or "bind").strip() or "bind"
    if src_b and (src == src_b or src.startswith(src_b + "/")):
        rel = src[len(src_b) :] or "/"
        return dest_b + rel
    leaf = os.path.basename(src.rstrip("/")) or "bind"
    return f"{dest_b}/{dest_p}/{leaf}"


def validate_dest_bind_path(path: str, dest_base: str) -> Optional[str]:
    p = (path or "").strip()
    if not p:
        return "destination bind path is required (or skip copy)"
    if not p.startswith("/") or ".." in p.split("/") or "\x00" in p:
        return "destination bind must be an absolute path without .."
    if os.path.normpath(p) in ("", "/"):
        return "refusing dest bind of /"
    if not path_in_jail(p, dest_base):
        return f"destination bind must stay under dest docker base {dest_base}"
    if os.path.normpath(p) == os.path.normpath((dest_base or "").strip()):
        return "destination bind cannot be the docker base dir itself"
    return None


def parse_bind_overrides_from_mapping(
    mapping: Mapping[Any, Any] | None,
) -> list[dict[str, Any]]:
    """Read bind_src_N / dest_bind_N / skip_bind_N from query or form."""
    if not mapping:
        return []
    items = mapping.multi_items() if hasattr(mapping, "multi_items") else mapping.items()
    srcs: dict[int, str] = {}
    dests: dict[int, str] = {}
    skips: set[int] = set()
    for k, v in items:
        ks = str(k)
        val = str(v or "").strip()
        m = _BIND_SRC_KEY.match(ks)
        if m:
            srcs[int(m.group(1))] = val
            continue
        m = _DEST_BIND_KEY.match(ks)
        if m:
            dests[int(m.group(1))] = val
            continue
        m = _SKIP_BIND_KEY.match(ks)
        if m and val.lower() in ("1", "true", "on", "yes"):
            skips.add(int(m.group(1)))
    out: list[dict[str, Any]] = []
    for idx in sorted(set(srcs) | set(dests) | skips):
        src = srcs.get(idx) or ""
        if not src:
            continue
        out.append(
            {
                "index": idx,
                "source": src,
                "dest": dests.get(idx) or "",
                "skip": idx in skips,
            }
        )
    return out


def bind_map_from_overrides(rows: list[dict[str, Any]]) -> dict[str, str]:
    """source path → dest path for binds that will be copied (not skipped)."""
    out: dict[str, str] = {}
    for row in rows or []:
        if row.get("skip"):
            continue
        src = str(row.get("source") or "").strip()
        dest = str(row.get("dest") or "").strip()
        if src and dest:
            out[src] = dest
    return out


def mapped_host_port(
    host: str | int, proto: str, port_map: Mapping[str, str] | None
) -> str:
    key = port_map_key(host, proto)
    if port_map and key in port_map:
        return str(port_map[key])
    return str(int(host))


def rewrite_port_spec(spec: Any, port_map: Mapping[str, str]) -> Any:
    if not port_map:
        return spec
    if isinstance(spec, int):
        key = port_map_key(spec, "tcp")
        if key in port_map:
            return int(port_map[key])
        return spec
    if isinstance(spec, dict):
        published = spec.get("published")
        proto = str(spec.get("protocol") or "tcp").lower()
        if published is None:
            return spec
        try:
            host = int(str(published).split("-", 1)[0])
        except (TypeError, ValueError):
            return spec
        key = port_map_key(host, proto)
        if key not in port_map:
            return spec
        out = dict(spec)
        if isinstance(published, int):
            out["published"] = int(port_map[key])
        else:
            out["published"] = str(port_map[key])
        return out
    text = str(spec).strip()
    m2 = re.match(
        r"^(?:(?P<ip>(?:\d{1,3}\.){3}\d{1,3}):)?"
        r"(?P<host>\d{1,5}):(?P<container>\d{1,5})"
        r"(?P<pr>/tcp|/udp)?$",
        text,
        re.I,
    )
    if m2:
        proto = (m2.group("pr") or "/tcp").lstrip("/").lower()
        key = port_map_key(m2.group("host"), proto)
        if key not in port_map:
            return spec
        ip = m2.group("ip")
        prefix = f"{ip}:" if ip else ""
        pr = m2.group("pr") or ""
        return f"{prefix}{port_map[key]}:{m2.group('container')}{pr}"
    m3 = re.match(r"^(?P<host>\d{1,5})(?P<pr>/tcp|/udp)?$", text, re.I)
    if m3:
        proto = (m3.group("pr") or "/tcp").lstrip("/").lower()
        key = port_map_key(m3.group("host"), proto)
        if key not in port_map:
            return spec
        return f"{port_map[key]}{m3.group('pr') or ''}"
    return spec


def _rewrite_ports_in_obj(data: Any, port_map: Mapping[str, str]) -> None:
    if not isinstance(data, dict):
        return
    services = data.get("services")
    if not isinstance(services, dict):
        return
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        ports = svc.get("ports")
        if not isinstance(ports, list):
            continue
        svc["ports"] = [rewrite_port_spec(p, port_map) for p in ports]


def _rewrite_volume_names_in_obj(data: Any, volume_renames: Mapping[str, str]) -> None:
    if not isinstance(data, dict) or not volume_renames:
        return
    vols = data.get("volumes")
    if not isinstance(vols, dict):
        return
    for _key, spec in vols.items():
        if not isinstance(spec, dict):
            continue
        nm = spec.get("name")
        if isinstance(nm, str) and nm in volume_renames:
            spec["name"] = volume_renames[nm]


def _rewrite_lines_ports(text: str, port_map: Mapping[str, str]) -> str:
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        nl = ""
        body = line
        if line.endswith("\n"):
            nl = "\n"
            body = line[:-1]
        if body.endswith("\r"):
            body = body[:-1]
            nl = "\r\n" if nl else "\r"
        m = _SHORT_PORT.match(body)
        if m:
            proto = (m.group("pr") or "/tcp").lstrip("/").lower()
            key = port_map_key(m.group("host"), proto)
            if key in port_map:
                ip = m.group("ip")
                ip_s = f"{ip}:" if ip else ""
                body = (
                    f"{m.group('pre')}{m.group('q')}{ip_s}{port_map[key]}:"
                    f"{m.group('container')}{m.group('pr') or ''}{m.group('q2')}"
                )
            out.append(body + nl)
            continue
        m = _BARE_PORT.match(body)
        if m:
            proto = (m.group("pr") or "/tcp").lstrip("/").lower()
            key = port_map_key(m.group("host"), proto)
            if key in port_map:
                body = (
                    f"{m.group('pre')}{m.group('q')}{port_map[key]}"
                    f"{m.group('pr') or ''}{m.group('q2')}"
                )
            out.append(body + nl)
            continue
        m = _PUBLISHED.match(body)
        if m:
            host = m.group("host")
            mapped = None
            for proto in ("tcp", "udp"):
                key = port_map_key(host, proto)
                if key in port_map:
                    mapped = port_map[key]
                    break
            if mapped:
                body = f"{m.group('pre')}{m.group('q')}{mapped}{m.group('q2')}"
            out.append(body + nl)
            continue
        out.append(body + nl)
    return "".join(out)


def _rewrite_bind_spec(spec: Any, bind_map: Mapping[str, str]) -> Any:
    if not bind_map:
        return spec
    if isinstance(spec, str):
        src = spec.split(":", 1)[0].strip()
        if src in bind_map:
            return bind_map[src] + spec[len(src) :]
        return spec
    if isinstance(spec, dict):
        src = spec.get("source") or spec.get("Source")
        if isinstance(src, str) and src in bind_map:
            out = dict(spec)
            if "source" in out:
                out["source"] = bind_map[src]
            if "Source" in out:
                out["Source"] = bind_map[src]
            return out
    return spec


def _rewrite_binds_in_obj(data: Any, bind_map: Mapping[str, str]) -> None:
    if not isinstance(data, dict) or not bind_map:
        return
    services = data.get("services")
    if not isinstance(services, dict):
        return
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        vols = svc.get("volumes")
        if isinstance(vols, list):
            svc["volumes"] = [_rewrite_bind_spec(v, bind_map) for v in vols]


def _rewrite_lines_binds(text: str, bind_map: Mapping[str, str]) -> str:
    if not bind_map:
        return text
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        nl = "\n" if line.endswith("\n") else ""
        body = line[:-1] if nl else line
        if body.endswith("\r"):
            body = body[:-1]
            nl = "\r\n" if nl else "\r"
        m = _SHORT_BIND.match(body)
        if m and m.group("src") in bind_map:
            body = f"{m.group('pre')}{m.group('q')}{bind_map[m.group('src')]}{m.group('rest')}"
        out.append(body + nl)
    return "".join(out)


def _rewrite_top_name(text: str, dest_project: str) -> str:
    lines = text.splitlines(keepends=True)
    found = False
    out: list[str] = []
    for line in lines:
        raw = line[:-1] if line.endswith("\n") else line
        m = _TOP_NAME.match(raw)
        if m and not found and not raw.startswith((" ", "\t")):
            out.append(f"{m.group(1)}{m.group(2)}{dest_project}{m.group(4)}\n" if line.endswith("\n") else f"{m.group(1)}{m.group(2)}{dest_project}{m.group(4)}")
            found = True
            continue
        out.append(line)
    return "".join(out)


def _rewrite_env_project(text: str, dest_project: str) -> str:
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        raw = line[:-1] if line.endswith("\n") else line
        m = _COMPOSE_PROJECT_ENV.match(raw)
        if m:
            rebuilt = f"{m.group(1)}{m.group(2)}{dest_project}{m.group(4)}"
            lines.append(rebuilt + ("\n" if line.endswith("\n") else ""))
        else:
            lines.append(line)
    return "".join(lines)


def apply_staging_overrides(
    staging: Path,
    *,
    dest_project: Optional[str] = None,
    source_project: Optional[str] = None,
    port_map: Optional[Mapping[str, str]] = None,
    volume_renames: Optional[Mapping[str, str]] = None,
    bind_map: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Rewrite compose / .env in the herder staging tree before push."""
    changed: list[str] = []
    ports = {k: v for k, v in (port_map or {}).items() if k.split("/", 1)[0] != str(v)}
    binds = {k: v for k, v in (bind_map or {}).items() if k and v and k != v}
    rename = bool(dest_project and source_project and dest_project != source_project)
    if not staging.is_dir():
        return {
            "files": changed,
            "ports": bool(ports),
            "rename": rename,
            "binds": bool(binds),
        }
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(staging).as_posix()
        if ".." in rel:
            continue
        base = path.name.lower()
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        orig = text
        if base in _COMPOSE_NAMES:
            if ports:
                text = _rewrite_lines_ports(text, ports)
            if binds:
                text = _rewrite_lines_binds(text, binds)
            if rename and dest_project:
                try:
                    data = yaml.safe_load(text)
                except Exception:
                    data = None
                if isinstance(data, dict):
                    if dest_project:
                        data["name"] = dest_project
                    if ports:
                        _rewrite_ports_in_obj(data, ports)
                    if binds:
                        _rewrite_binds_in_obj(data, binds)
                    if volume_renames:
                        _rewrite_volume_names_in_obj(data, volume_renames)
                    text = yaml.safe_dump(
                        data,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                else:
                    text = _rewrite_top_name(text, dest_project)
        elif base in (".env", "env") or base.startswith(".env"):
            if rename and dest_project:
                text = _rewrite_env_project(text, dest_project)
        if text != orig:
            path.write_text(text, encoding="utf-8")
            changed.append(rel)
            logger.info("[migrate-overrides] rewrote %s", rel)
    return {
        "files": changed,
        "ports": bool(ports),
        "rename": rename,
        "binds": bool(binds),
    }
