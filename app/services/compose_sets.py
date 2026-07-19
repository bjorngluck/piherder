"""Compose sets — multiple compose files under one project directory.

One Docker *project* (folder / compose project name) may contain:

- **Primary** ``docker-compose.yml`` / ``compose.yml``
- **Override** (Compose auto-merge) — not a set
- **Sets** ``docker-compose.<label>.yml`` / ``compose.<label>.yml`` — sub-views
  under the same project card (not a second stack)

Presentation-only view groups (fabric) remain orthogonal.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Primary basenames (Compose default project file)
PRIMARY_BASENAMES = frozenset(
    {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }
)

OVERRIDE_BASENAMES = frozenset(
    {
        "docker-compose.override.yml",
        "docker-compose.override.yaml",
        "compose.override.yml",
        "compose.override.yaml",
    }
)

# docker-compose.e2e.yml → e2e; compose.workers.yaml → workers
_SET_RE = re.compile(
    r"^(?:docker-)?compose\.(?P<label>[a-zA-Z0-9][a-zA-Z0-9._-]*)\.(?:ya?ml)$",
    re.IGNORECASE,
)


def is_override_filename(name: str) -> bool:
    n = (name or "").strip().lower()
    if n in {b.lower() for b in OVERRIDE_BASENAMES}:
        return True
    return "override" in n and n.endswith((".yml", ".yaml"))


def is_primary_filename(name: str) -> bool:
    return (name or "").strip().lower() in {b.lower() for b in PRIMARY_BASENAMES}


def is_compose_yaml(name: str) -> bool:
    n = (name or "").strip().lower()
    return n.endswith((".yml", ".yaml")) and not n.startswith(".")


def set_label_from_filename(name: str) -> Optional[str]:
    """Return set label for a non-primary compose file, or None if not a set."""
    base = (name or "").strip().split("/")[-1]
    if not base or is_primary_filename(base) or is_override_filename(base):
        return None
    m = _SET_RE.match(base)
    if not m:
        return None
    label = (m.group("label") or "").strip().lower()
    if not label or label == "override":
        return None
    return label


def classify_compose_filename(name: str) -> str:
    """primary | override | set | other."""
    base = (name or "").strip().split("/")[-1]
    if not base:
        return "other"
    if is_primary_filename(base):
        return "primary"
    if is_override_filename(base):
        return "override"
    if set_label_from_filename(base):
        return "set"
    if is_compose_yaml(base):
        return "other"
    return "other"


def short_set_key(filename: str, *, is_primary: bool = False) -> str:
    if is_primary:
        return "main"
    lab = set_label_from_filename(filename)
    return lab or "set"


def build_compose_sets(
    filenames: Iterable[str],
    *,
    services_by_file: Optional[dict[str, list[str]]] = None,
    primary_filename: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build ordered compose-set descriptors for a project directory.

    Always includes a **main** entry when a primary file exists (or is inferred).
    Extra set files become additional entries. Overrides are omitted (editor only).
    """
    services_by_file = services_by_file or {}
    names = []
    seen: set[str] = set()
    for raw in filenames or []:
        base = (raw or "").strip().split("/")[-1]
        if not base or base.lower() in seen:
            continue
        seen.add(base.lower())
        names.append(base)

    primary = primary_filename
    if primary:
        primary = primary.strip().split("/")[-1]
    if not primary:
        for cand in (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ):
            if any(n.lower() == cand for n in names):
                primary = next(n for n in names if n.lower() == cand)
                break

    sets: list[dict[str, Any]] = []
    if primary:
        svcs = list(services_by_file.get(primary) or [])
        sets.append(
            {
                "key": "main",
                "label": "main",
                "filename": primary,
                "is_primary": True,
                "services": svcs,
            }
        )

    extras: list[tuple[str, str]] = []
    for n in names:
        if primary and n.lower() == primary.lower():
            continue
        if classify_compose_filename(n) != "set":
            continue
        lab = set_label_from_filename(n) or n
        extras.append((lab, n))
    extras.sort(key=lambda x: x[0])

    for lab, n in extras:
        sets.append(
            {
                "key": lab,
                "label": lab,
                "filename": n,
                "is_primary": False,
                "services": list(services_by_file.get(n) or []),
            }
        )
    return sets


def service_to_set_key(
    compose_service: str,
    sets: list[dict[str, Any]],
) -> str:
    """Map a compose service name to a set key (prefer non-primary if in both)."""
    svc = (compose_service or "").strip()
    if not svc or not sets:
        return "main"
    # Non-primary first so split-out services win over accidental primary leftovers
    ordered = sorted(sets, key=lambda s: (bool(s.get("is_primary")), s.get("key") or ""))
    for s in ordered:
        svcs = s.get("services") or []
        if svc in svcs or svc.lower() in {str(x).lower() for x in svcs}:
            return str(s.get("key") or "main")
    return "main"


def annotate_containers_with_sets(
    containers: list[dict[str, Any]],
    sets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach ``compose_set`` key to each container dict (in place + return)."""
    for c in containers or []:
        if not isinstance(c, dict) or c.get("placeholder"):
            # placeholders still get a set from compose_service
            pass
        if not isinstance(c, dict):
            continue
        csvc = (c.get("compose_service") or "").strip()
        c["compose_set"] = service_to_set_key(csvc, sets)
    return containers


def list_extra_compose_filenames(filenames: Iterable[str]) -> list[str]:
    """Filenames that should be probed for live edit (primary + sets + overrides)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in filenames or []:
        base = (raw or "").strip().split("/")[-1]
        if not base:
            continue
        kind = classify_compose_filename(base)
        if kind not in ("primary", "override", "set"):
            continue
        low = base.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(base)
    return out
