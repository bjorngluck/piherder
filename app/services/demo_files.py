"""Demo-only simulated Host Files (v1.4 D-F).

Canned in-process tree. No SFTP, Paramiko, or host disk.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .host_files import FilesError, human_size, is_image_name, image_media_type, parse_rel

JAIL = "/home/demo/docker"

_MTIME = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

_COMPOSE = (
    "services:\n"
    "  app:\n"
    "    image: grafana/grafana:latest\n"
    "    ports:\n"
    "      - \"3000:3000\"\n"
    "    volumes:\n"
    "      - ./data:/var/lib/grafana\n"
)

_README = (
    "PiHerder demo Files\n"
    "===================\n\n"
    "This tree is simulated. There is no SFTP and no real host disk.\n"
    "Browse folders, open README.md, preview logo.svg.\n"
    "Upload, edit, zip, and delete are disabled in the public demo.\n"
)

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
    '<rect width="64" height="64" fill="#1a3a4a"/>'
    '<text x="32" y="38" text-anchor="middle" fill="#7dd3c0" '
    'font-size="14" font-family="sans-serif">PH</text></svg>'
)

# rel -> node
_TREE: dict[str, dict[str, Any]] = {
    "": {"kind": "dir", "kids": ["README.md", "grafana", "authentik", "logo.svg"]},
    "README.md": {"kind": "file", "text": _README},
    "grafana": {"kind": "dir", "kids": ["docker-compose.yml"]},
    "grafana/docker-compose.yml": {"kind": "file", "text": _COMPOSE},
    "authentik": {"kind": "dir", "kids": ["docker-compose.yml"]},
    "authentik/docker-compose.yml": {
        "kind": "file",
        "text": _COMPOSE.replace("grafana/grafana:latest", "authentik/server:latest"),
    },
    "logo.svg": {"kind": "file", "text": _SVG},
}


def _norm(rel: Optional[str]) -> str:
    parts = parse_rel(rel or "")
    if any(p == ".." for p in (rel or "").replace("\\", "/").split("/")):
        # parse_rel already drops ..
        pass
    return "/".join(parts)


def _node(rel: Optional[str]) -> dict[str, Any]:
    key = _norm(rel)
    node = _TREE.get(key)
    if not node:
        raise FilesError("not_found", "Not found in demo tree")
    return node


def _entry(name: str, rel: str, node: dict[str, Any]) -> dict[str, Any]:
    text = node.get("text") or ""
    raw = text.encode("utf-8") if isinstance(text, str) else b""
    kind = node.get("kind") or "file"
    size = len(raw) if kind == "file" else None
    return {
        "name": name,
        "rel": rel,
        "kind": kind,
        "size": size,
        "size_h": human_size(size) if size is not None else "",
        "mtime": _MTIME,
        "secretish": False,
        "escaped": False,
        "mode": 0o755 if kind == "dir" else 0o644,
        "mode_h": "0755" if kind == "dir" else "0644",
        "uid": 1000,
        "gid": 1000,
        "owner": "demo",
        "group": "demo",
        "owner_h": "demo",
    }


def list_dir(server: Any, rel: Optional[str] = "", **_k: Any) -> dict[str, Any]:
    key = _norm(rel)
    node = _node(key)
    if node.get("kind") != "dir":
        raise FilesError("is_file", "Not a directory")
    entries = []
    for name in node.get("kids") or []:
        child_rel = f"{key}/{name}" if key else name
        entries.append(_entry(name, child_rel, _node(child_rel)))
    crumbs = []
    acc: list[str] = []
    for seg in parse_rel(key):
        acc.append(seg)
        crumbs.append({"name": seg, "rel": "/".join(acc)})
    return {
        "jail": JAIL,
        "rel": key,
        "abs": f"{JAIL}/{key}".rstrip("/") if key else JAIL,
        "truncated": False,
        "entries": entries,
        "crumbs": crumbs,
    }


def _file_bytes(rel: str) -> bytes:
    node = _node(rel)
    if node.get("kind") != "file":
        raise FilesError("is_dir", "Not a file")
    return (node.get("text") or "").encode("utf-8")


def read_text(server: Any, rel: str, **_k: Any) -> dict[str, Any]:
    raw = _file_bytes(rel)
    name = _norm(rel).rsplit("/", 1)[-1]
    text = raw.decode("utf-8")
    return {
        "rel": _norm(rel),
        "name": name,
        "text": text,
        "bytes": len(raw),
        "encoding": "utf-8",
    }


def peek_file(server: Any, rel: str, **_k: Any) -> dict[str, Any]:
    raw = _file_bytes(rel)
    name = _norm(rel).rsplit("/", 1)[-1]
    sample = raw[:4096]
    hx = " ".join(f"{b:02x}" for b in sample)
    ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in sample)
    return {
        "rel": _norm(rel),
        "name": name,
        "size": len(raw),
        "size_h": human_size(len(raw)),
        "is_image": is_image_name(name),
        "is_text": True,
        "media_type": image_media_type(name) if is_image_name(name) else "text/plain",
        "hex": hx,
        "ascii": ascii_,
        "secretish": False,
    }


def stat_file(server: Any, rel: str, **_k: Any) -> dict[str, Any]:
    node = _node(rel)
    name = _norm(rel).rsplit("/", 1)[-1] or "."
    return _entry(name, _norm(rel), node)


def iter_file(server: Any, rel: str, **_k: Any) -> Iterator[bytes]:
    yield _file_bytes(rel)


def iter_preview(server: Any, rel: str, **_k: Any) -> Iterator[bytes]:
    yield _file_bytes(rel)


def search(server: Any, q: str, *, rel: str = "", **_k: Any) -> dict[str, Any]:
    needle = (q or "").strip().lower()
    listing = list_dir(server, rel)
    if not needle:
        return listing
    hits = []
    for key, node in _TREE.items():
        if needle in key.lower() or needle in (node.get("text") or "").lower():
            name = key.rsplit("/", 1)[-1] or key or "."
            if key:
                hits.append(_entry(name, key, node))
    listing["entries"] = hits[:80]
    listing["search"] = True
    listing["query"] = q
    return listing


def list_docker_volumes(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
    return []


def list_docker_containers(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
    return []


def list_container_mounts(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
    return []
