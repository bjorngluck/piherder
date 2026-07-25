"""Pure helpers for compose project file kinds, sidecars, and bind mounts.

Concern boundary (keep free of SSH / SQL / FastAPI):
  - kinds + sort → editors & template UI
  - relative bind discovery → host import + live file probe
  - desired→live fill → compose editor tabs

Callers: ``docker_versions``, ``service_templates.harden``, ``compose_editor``.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

# Short-form volume: source:target or source:target:mode
SHORT_MOUNT_RE = re.compile(
    r"""^([ \t]*)-\s*["']?([^:"'\s][^:"']*?):(/[^:"'\s][^:"']*)(?::([^"'\s]+))?["']?\s*$"""
)

# Relative project files that ship with the stack (not volume vars)
CONFIG_FILE_EXTS = frozenset(
    {
        "yml",
        "yaml",
        "json",
        "toml",
        "conf",
        "cfg",
        "ini",
        "txt",
        "properties",
        "xml",
        "env",
        "js",
        "ts",
        "lua",
        "hcl",
    }
)

COMPOSE_BASENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

OVERRIDE_BASENAMES = (
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "compose.override.yml",
    "compose.override.yaml",
)

_COMPOSE_BASE_SET = frozenset(b.lower() for b in COMPOSE_BASENAMES)
_OVERRIDE_BASE_SET = frozenset(b.lower() for b in OVERRIDE_BASENAMES)

# Sort order for multi-file editors and UI browsers
FILE_ROLE_ORDER = {
    "compose": 0,
    "override": 1,
    "env": 2,
    "config": 3,
    "dockerfile": 4,
    "secret": 5,
    "other": 6,
    "file": 6,  # UI alias for other
}

FILE_ROLE_LABELS = {
    "compose": "Compose",
    "override": "Override",
    "env": "Env",
    "config": "Config",
    "dockerfile": "Dockerfile",
    "secret": "Secret",
    "other": "File",
    "file": "File",
}


def looks_like_config_file(path: str) -> bool:
    """True when a relative bind source looks like a file (has known extension)."""
    p = (path or "").strip().lstrip("./")
    if not p or p in (".", "..") or ".." in p.split("/"):
        return False
    base = p.rsplit("/", 1)[-1]
    if "." not in base or base.startswith("."):
        # allow .env.something already handled elsewhere
        if base.startswith(".env"):
            return True
        return False
    ext = base.rsplit(".", 1)[-1].lower()
    return ext in CONFIG_FILE_EXTS


def classify_volume_source(source: str) -> Tuple[str, str]:
    """Return (mode, normalized_source) for a compose volume left-hand side."""
    src = (source or "").strip()
    if not src:
        return "named", src
    if src.startswith("/") and not src.startswith("//"):
        return "bind_absolute", src
    if src.startswith("./") or src.startswith("../"):
        cleaned = src
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        return "bind_relative", cleaned or src
    # bare relative path used as bind in many compose files (./ optional)
    if "/" in src or src in (".", ".."):
        return "bind_relative", src.lstrip("./")
    return "named", src


def discover_relative_config_files(compose: str) -> List[str]:
    """Relative bind-mount sources in compose that look like project config files.

    Example: ``./promtail-config.yaml:/etc/promtail/config.yml`` →
    ``promtail-config.yaml``. Single-level and one-subdir paths only (SFTP write limit).
    """
    found: List[str] = []
    seen: Set[str] = set()
    for line in (compose or "").splitlines():
        m = SHORT_MOUNT_RE.match(line)
        if not m:
            continue
        source = (m.group(2) or "").strip()
        mode, norm_src = classify_volume_source(source)
        if mode != "bind_relative":
            continue
        rel = (source or "").strip()
        if rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        if not looks_like_config_file(rel) and not looks_like_config_file(norm_src):
            continue
        parts = [p for p in rel.split("/") if p]
        if not parts or len(parts) > 2 or any(p in (".", "..") for p in parts):
            continue
        key = "/".join(parts)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def project_file_role(path: str) -> str:
    """Canonical kind for a project file path.

    Returns one of: compose, override, env, config, dockerfile, secret, other.
    """
    p = (path or "").replace("\\", "/").strip()
    if not p:
        return "other"
    base = p.rsplit("/", 1)[-1].lower()
    low = p.lower()
    if low.startswith("secrets/") or "/secrets/" in low:
        return "secret"
    if base in _OVERRIDE_BASE_SET or "override" in base:
        return "override"
    if base in _COMPOSE_BASE_SET:
        return "compose"
    # Compose sets: docker-compose.e2e.yml / compose.foo.yaml / anything *compose*.yml
    if base.endswith((".yml", ".yaml")) and (
        base.startswith("docker-compose.")
        or base.startswith("compose.")
        or "compose" in base
    ):
        return "compose"
    if base == "dockerfile" or base.startswith("dockerfile."):
        return "dockerfile"
    if base == ".env" or base.startswith(".env") or base.endswith(".env"):
        return "env"
    if looks_like_config_file(base) or looks_like_config_file(p):
        return "config"
    return "other"


def ui_file_kind(path: str) -> str:
    """Template/deployment browser kind (maps override→compose, other→file)."""
    role = project_file_role(path)
    if role == "override":
        return "compose"
    if role == "other":
        return "file"
    return role


def sort_project_paths(names: List[str], *, skip_meta: bool = True) -> List[str]:
    def key(n: str):
        role = project_file_role(n)
        return (FILE_ROLE_ORDER.get(role, 9), (n or "").lower())

    out = []
    for n in names or []:
        s = str(n)
        if skip_meta and (s == "__piherder__" or s.startswith("__")):
            continue
        out.append(s)
    return sorted(out, key=key)


def merge_desired_missing_into_live(
    live: Optional[Dict[str, str]],
    desired: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Fill tabs missing on the host from template desired-state files.

    Skips secrets/*, meta keys, and empty paths. Does not overwrite host content.
    """
    out: Dict[str, str] = dict(live or {})
    for path, body in (desired or {}).items():
        p = str(path or "").replace("\\", "/").strip()
        if not p or p.startswith("secrets/") or p.startswith("__"):
            continue
        if p in out:
            continue
        out[p] = body if isinstance(body, str) else ("" if body is None else str(body))
    return out
