"""Shared list chrome: page size, cookie, free-text tokens, smart aliases.

v1.3 slice 3 (L). Used by Servers, Docker stack, discovery, Jobs/Audit clamp,
and GET /api/v1/servers. Not a search engine — substring + frozen aliases.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlencode

PER_PAGE_CHOICES: tuple[int, ...] = (10, 20, 50, 100)
PER_PAGE_DEFAULT = 20
COOKIE = "ph_per_page"
COOKIE_MAX_AGE = 60 * 60 * 24 * 400
API_LIMIT_DEFAULT = 100
API_LIMIT_MAX = 100

# Frozen — do not grow into Settings.
ALIASES: dict[str, tuple[str, ...]] = {
    "ha": ("homeassistant",),
    "hass": ("homeassistant",),
    "pihole": ("pihole",),
    "pi-hole": ("pihole",),
    "npm": ("nginx", "nginxproxymanager", "proxy"),
    "kuma": ("uptime",),
    "rpi": ("raspberry",),
    "raspi": ("raspberry",),
    "adguard": ("adguard",),
}

DOCKER_STATUSES = frozenset({"all", "running", "stopped", "missing", "updates"})

_SHORT_TOKEN = re.compile(r"^.{1,2}$")


def clamp_per_page(raw: Any = None, *, cookie: Any = None) -> int:
    """Nearest allowed page size. Query wins when present; else cookie; else 20."""
    if raw is not None and str(raw).strip() != "":
        return _nearest_choice(raw, PER_PAGE_DEFAULT)
    if cookie is not None and str(cookie).strip() != "":
        return _nearest_choice(cookie, PER_PAGE_DEFAULT)
    return PER_PAGE_DEFAULT


def _nearest_choice(raw: Any, default: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    if n in PER_PAGE_CHOICES:
        return n
    return min(PER_PAGE_CHOICES, key=lambda x: abs(x - n))


def parse_page(raw: Any) -> int:
    try:
        return max(1, int(raw or 1))
    except (TypeError, ValueError):
        return 1


def per_page_from_request(request: Any) -> int:
    qp = getattr(request, "query_params", None)
    cookies = getattr(request, "cookies", None) or {}
    raw = qp.get("per_page") if qp is not None else None
    return clamp_per_page(raw, cookie=cookies.get(COOKIE))


def page_from_request(request: Any) -> int:
    qp = getattr(request, "query_params", None)
    raw = qp.get("page") if qp is not None else None
    return parse_page(raw)


def attach_per_page_cookie(response: Any, per_page: int) -> Any:
    """Persist the operator's page-size choice (Lax, HttpOnly, path=/)."""
    try:
        response.set_cookie(
            key=COOKIE,
            value=str(int(per_page)),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/",
        )
    except Exception:
        pass
    return response


def tokens(q: Any) -> list[str]:
    if q is None:
        return []
    return [t for t in str(q).lower().split() if t]


def expand(token: str) -> list[str]:
    t = (token or "").strip().lower()
    if not t:
        return []
    out = [t]
    for extra in ALIASES.get(t, ()):
        if extra not in out:
            out.append(extra)
    return out


def haystack(*fields: Any) -> str:
    parts: list[str] = []
    for f in fields:
        if f is None:
            continue
        s = str(f).strip().lower()
        if s:
            parts.append(s)
    return " ".join(parts)


def _variant_hits(hay: str, variant: str) -> bool:
    if not variant:
        return False
    if _SHORT_TOKEN.match(variant):
        return re.search(rf"(^|[^a-z0-9]){re.escape(variant)}([^a-z0-9]|$)", hay) is not None
    return variant in hay


def matches(q: Any, *fields: Any) -> bool:
    """True when every token hits haystack or an alias expansion (AND)."""
    toks = tokens(q)
    if not toks:
        return True
    hay = haystack(*fields)
    for tok in toks:
        if not any(_variant_hits(hay, v) for v in expand(tok)):
            return False
    return True


def page_slice(
    rows: Sequence[Any], page: int, per_page: int
) -> tuple[list[Any], int, int, int]:
    """Return (page_rows, total, total_pages, page). Page past end snaps to last."""
    total = len(rows)
    per = per_page if per_page > 0 else PER_PAGE_DEFAULT
    total_pages = max(1, (total + per - 1) // per) if total else 1
    page = min(max(1, int(page or 1)), total_pages)
    start = (page - 1) * per
    return list(rows[start : start + per]), total, total_pages, page


def pager(
    *,
    page: int,
    per_page: int,
    total: int,
    total_pages: int,
    q: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "page": page,
        "per_page": per_page,
        "per_page_choices": list(PER_PAGE_CHOICES),
        "total": total,
        "total_pages": total_pages,
        "q": q or "",
        "pager_query": query_string(
            {"q": q, "per_page": per_page, **(extra or {})}, omit=("page",)
        ),
    }


def query_string(params: Mapping[str, Any], *, omit: Iterable[str] = ()) -> str:
    skip = set(omit)
    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if key in skip or value is None:
            continue
        if isinstance(value, bool):
            if not value:
                continue
            items.append((key, "1"))
            continue
        s = str(value).strip()
        if s == "":
            continue
        if key in ("filter", "status", "view") and s == "all":
            continue
        items.append((key, s))
    return urlencode(items)


def clamp_api_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return API_LIMIT_DEFAULT
    if n < 1:
        return API_LIMIT_DEFAULT
    return min(API_LIMIT_MAX, n)


def parse_offset(raw: Any) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def server_search_fields(row: Any) -> tuple[Any, ...]:
    return (
        getattr(row, "name", None) or (row.get("name") if isinstance(row, dict) else None),
        getattr(row, "hostname", None)
        or (row.get("hostname") if isinstance(row, dict) else None),
        getattr(row, "ip_address", None)
        or (row.get("ip_address") if isinstance(row, dict) else None),
        getattr(row, "dns_name", None)
        or (row.get("dns_name") if isinstance(row, dict) else None),
        getattr(row, "ssh_username", None)
        or (row.get("ssh_username") if isinstance(row, dict) else None),
    )


def match_server(row: Any, q: Any) -> bool:
    return matches(q, *server_search_fields(row))


def _container_status(c: Mapping[str, Any]) -> str:
    if c.get("running"):
        return "running"
    if c.get("placeholder"):
        return "missing"
    return "stopped"


def _project_fields(proj: Mapping[str, Any]) -> list[Any]:
    fields: list[Any] = [proj.get("name"), proj.get("path")]
    for svc in proj.get("services") or []:
        if isinstance(svc, str):
            fields.append(svc)
        elif isinstance(svc, Mapping):
            fields.append(svc.get("name") or svc.get("service"))
    for c in proj.get("containers") or []:
        if not isinstance(c, Mapping):
            continue
        fields.extend(
            [
                c.get("name"),
                c.get("compose_service"),
                c.get("image"),
            ]
        )
    return fields


def project_matches(proj: Mapping[str, Any], q: Any, status: str) -> bool:
    if not matches(q, *_project_fields(proj)):
        return False
    st = (status or "all").strip().lower() or "all"
    if st not in DOCKER_STATUSES:
        st = "all"
    if st == "all":
        return True
    containers = [c for c in (proj.get("containers") or []) if isinstance(c, Mapping)]
    if st == "updates":
        if proj.get("has_pending_update"):
            return True
        return any(c.get("has_pending_update") for c in containers)
    return any(_container_status(c) == st for c in containers)


def filter_docker_stack(
    projects: Sequence[Mapping[str, Any]],
    orphans: Sequence[Mapping[str, Any]] | None,
    *,
    q: str = "",
    status: str = "all",
    page: int = 1,
    per_page: int = PER_PAGE_DEFAULT,
    force_project: str | None = None,
) -> dict[str, Any]:
    """Filter/page compose projects. Snapshot is already in memory."""
    st = (status or "all").strip().lower() or "all"
    if st not in DOCKER_STATUSES:
        st = "all"
    kept = [p for p in projects if isinstance(p, Mapping) and project_matches(p, q, st)]
    orphan_list = [c for c in (orphans or []) if isinstance(c, Mapping)]
    orphans_ok = False
    if orphan_list:
        fake = {
            "name": "orphans",
            "containers": orphan_list,
            "has_pending_update": any(c.get("has_pending_update") for c in orphan_list),
        }
        orphans_ok = project_matches(fake, q, st)

    forced = False
    want = (force_project or "").strip()
    if want:
        names = {str(p.get("name") or "") for p in kept}
        if want not in names:
            for p in projects:
                if isinstance(p, Mapping) and str(p.get("name") or "") == want:
                    kept = [p]
                    forced = True
                    break
        idx = next(
            (i for i, p in enumerate(kept) if str(p.get("name") or "") == want),
            None,
        )
        if idx is not None:
            per = per_page if per_page > 0 else PER_PAGE_DEFAULT
            page = idx // per + 1

    page_rows, total, total_pages, page = page_slice(kept, page, per_page)
    show_orphans = False
    if orphans_ok:
        if not kept:
            show_orphans = page == 1
        elif page == total_pages:
            show_orphans = True

    return {
        "projects": page_rows,
        "orphan_containers": orphan_list if show_orphans else [],
        "total": total,
        "total_pages": total_pages,
        "page": page,
        "per_page": per_page,
        "orphans_match": orphans_ok,
        "forced_project": forced,
        "filtered": bool(tokens(q)) or st != "all",
        "q": q or "",
        "status": st,
    }


def docker_params(request: Any) -> dict[str, Any]:
    qp = getattr(request, "query_params", None)
    get = qp.get if qp is not None else lambda _k, d="": d
    q = (get("q") or "").strip()
    status = (get("status") or "all").strip().lower() or "all"
    if status not in DOCKER_STATUSES:
        status = "all"
    return {
        "q": q,
        "status": status,
        "page": page_from_request(request),
        "per_page": per_page_from_request(request),
        "project": (get("project") or "").strip(),
    }
