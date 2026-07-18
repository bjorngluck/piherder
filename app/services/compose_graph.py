"""Parse Docker Compose service dependency graph for inventory enrich (P1b).

Pure helpers — no SSH. Used by inventory L1 + stack panel suggestions.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def _norm_svc(name: str | None) -> str:
    return (name or "").strip()


def _deps_from_value(raw: Any) -> list[str]:
    """Normalize depends_on (list | dict | str) → service name list."""
    out: list[str] = []
    if raw is None:
        return out
    if isinstance(raw, str):
        n = _norm_svc(raw)
        return [n] if n else []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                n = _norm_svc(item)
                if n:
                    out.append(n)
            elif isinstance(item, dict):
                # unusual: [{service: condition}] — rare
                for k in item:
                    n = _norm_svc(str(k))
                    if n:
                        out.append(n)
        return out
    if isinstance(raw, dict):
        # Compose v2/v3 long form: { db: { condition: service_started }, redis: ... }
        for k in raw:
            n = _norm_svc(str(k))
            if n:
                out.append(n)
        return out
    return out


def extract_compose_graph(
    compose_doc: dict[str, Any] | None,
    *,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """Build a compact dependency graph from a parsed compose document.

    Returns::

        {
          "depends_on": {"web": ["db", "redis"], "worker": ["db"]},
          "service_names": ["web", "db", ...],
          "networks": ["default", "internal"],
          "links": {"legacy": ["other"]},   # optional compose links:
          "compose_sha": "abc…"             # if raw_text given
        }
    """
    doc = compose_doc if isinstance(compose_doc, dict) else {}
    services = doc.get("services") or {}
    if not isinstance(services, dict):
        services = {}

    depends_on: dict[str, list[str]] = {}
    links: dict[str, list[str]] = {}
    service_names: list[str] = []

    for name, cfg in services.items():
        svc = _norm_svc(str(name))
        if not svc:
            continue
        service_names.append(svc)
        if not isinstance(cfg, dict):
            continue
        deps = _deps_from_value(cfg.get("depends_on"))
        # Filter self-deps and empty
        deps = [d for d in deps if d and d != svc]
        if deps:
            # preserve order, unique
            seen: set[str] = set()
            uniq: list[str] = []
            for d in deps:
                if d not in seen:
                    seen.add(d)
                    uniq.append(d)
            depends_on[svc] = uniq

        raw_links = cfg.get("links")
        if raw_links:
            link_list: list[str] = []
            if isinstance(raw_links, list):
                for item in raw_links:
                    # "db" or "db:database"
                    s = _norm_svc(str(item).split(":")[0] if item else "")
                    if s and s != svc:
                        link_list.append(s)
            if link_list:
                links[svc] = link_list

    networks: list[str] = []
    nets = doc.get("networks")
    if isinstance(nets, dict):
        networks = [str(k) for k in nets.keys() if k]
    elif isinstance(nets, list):
        networks = [str(x) for x in nets if x]

    graph: dict[str, Any] = {
        "depends_on": depends_on,
        "service_names": service_names,
        "networks": networks[:20],
    }
    if links:
        graph["links"] = links
    if raw_text:
        graph["compose_sha"] = hashlib.sha256(
            raw_text.encode("utf-8", errors="replace")
        ).hexdigest()[:16]

    return graph


def edges_from_graph(
    graph: dict[str, Any] | None,
    *,
    source: str = "compose",
    confidence: int = 85,
) -> list[dict[str, Any]]:
    """Flatten depends_on (+ links) into edge dicts for UI / suggestions."""
    if not graph or not isinstance(graph, dict):
        return []
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(frm: str, to: str, kind: str, conf: int) -> None:
        key = (frm, to, kind)
        if not frm or not to or frm == to or key in seen:
            return
        seen.add(key)
        edges.append(
            {
                "from": frm,
                "to": to,
                "kind": kind,
                "source": source,
                "confidence": conf,
            }
        )

    for frm, deps in (graph.get("depends_on") or {}).items():
        if not isinstance(deps, list):
            continue
        for to in deps:
            _add(str(frm), str(to), "depends_on", confidence)

    for frm, deps in (graph.get("links") or {}).items():
        if not isinstance(deps, list):
            continue
        for to in deps:
            _add(str(frm), str(to), "links", max(50, confidence - 20))

    return edges


# Role-based soft heuristics when compose has no depends_on
_APPISH = re.compile(
    r"\b(web|app|api|frontend|backend|ui|server|caddy|nginx|traefik|proxy)\b",
    re.I,
)
_DATAISH = re.compile(
    r"\b(db|database|postgres|mysql|mariadb|mongo|redis|valkey|cache|memcached)\b",
    re.I,
)
_WORKERISH = re.compile(r"\b(worker|celery|beat|rq|sidekiq|consumer)\b", re.I)


def heuristic_edges_from_services(
    service_names: list[str],
    *,
    roles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Suggest low-confidence edges from service/role names when compose is silent."""
    roles = roles or {}
    names = [_norm_svc(s) for s in service_names if _norm_svc(s)]
    if len(names) < 2:
        return []

    def role_of(n: str) -> str:
        if n in roles and roles[n] not in ("", "other"):
            return roles[n]
        if _DATAISH.search(n):
            if re.search(r"redis|valkey|cache|memcached", n, re.I):
                return "cache"
            return "data"
        if _WORKERISH.search(n):
            return "queue"
        if re.search(r"\b(caddy|nginx|traefik|haproxy|cloudflared)\b", n, re.I):
            return "edge"
        if _APPISH.search(n):
            return "app"
        return "app"

    by_role: dict[str, list[str]] = {}
    for n in names:
        by_role.setdefault(role_of(n), []).append(n)

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(frm: str, to: str) -> None:
        if frm == to or (frm, to) in seen:
            return
        seen.add((frm, to))
        edges.append(
            {
                "from": frm,
                "to": to,
                "kind": "talks_to",
                "source": "heuristic",
                "confidence": 40,
            }
        )

    apps = by_role.get("app") or []
    workers = by_role.get("queue") or []
    data = by_role.get("data") or []
    cache = by_role.get("cache") or []
    edge = by_role.get("edge") or []

    # edge → app (caddy → web)
    for e in edge:
        for a in apps:
            add(e, a)
    # app → data/cache (web → db/redis)
    for a in apps:
        for d in data:
            add(a, d)
        for c in cache:
            add(a, c)
        # app → worker/queue (web → celery) — often missing from compose depends_on
        for w in workers:
            add(a, w)
    for w in workers:
        for d in data:
            add(w, d)
        for c in cache:
            add(w, c)

    return edges[:32]


def merge_edge_lists(
    *lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dedupe edges preferring higher confidence / compose over heuristic."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for lst in lists:
        for e in lst or []:
            frm = str(e.get("from") or "")
            to = str(e.get("to") or "")
            if not frm or not to:
                continue
            key = (frm, to)
            prev = best.get(key)
            conf = int(e.get("confidence") or 0)
            if not prev or conf > int(prev.get("confidence") or 0):
                best[key] = e
    out = list(best.values())
    out.sort(
        key=lambda x: (
            -int(x.get("confidence") or 0),
            str(x.get("from") or ""),
            str(x.get("to") or ""),
        )
    )
    return out
