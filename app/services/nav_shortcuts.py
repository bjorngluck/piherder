"""Favourites (J) + cross-host feature jump (K) helpers.

Pin kinds (allowlisted — no free-form URLs):
  * server_feature — host Overview / Docker / Backups / Services
  * app_page       — Hosts map, Path map, Certificates, Jobs, Templates
  * integration    — a Catalog integration detail (Pi-hole, LAN Discovery, …)
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from ..models import Integration, Server, UserFavourite

KIND_SERVER_FEATURE = "server_feature"
KIND_APP_PAGE = "app_page"
KIND_INTEGRATION = "integration"
KINDS = frozenset({KIND_SERVER_FEATURE, KIND_APP_PAGE, KIND_INTEGRATION})

# Host feature key → path suffix (overview = host root)
FEATURE_META: dict[str, dict[str, str]] = {
    "overview": {"label": "Overview", "suffix": ""},
    "backups": {"label": "Backups", "suffix": "/backups"},
    "docker": {"label": "Docker", "suffix": "/docker"},
    "services": {"label": "Services", "suffix": "/services"},
}

# App-level pages (stable keys only).
# Hosts/Path maps MUST include #map so fabric-mesh opens the SVG (list-first otherwise).
APP_PAGE_META: dict[str, dict[str, str]] = {
    "hosts_map": {"label": "Hosts map", "href": "/dns/physical#map"},
    "path_map": {"label": "Path map", "href": "/dns/logical#map"},
    "certificates": {"label": "Certificates", "href": "/certificates"},
    "jobs": {"label": "Jobs", "href": "/jobs"},
    "templates": {"label": "Templates", "href": "/templates"},
    "fleet_services": {"label": "Fleet services", "href": "/services"},
}

# Integration type → short group/chip label
INTEGRATION_TYPE_LABELS: dict[str, str] = {
    "nmap": "LAN Discovery",
    "pihole": "Pi-hole",
    "npm": "NPM",
    "uptime_kuma": "Uptime Kuma",
    "grafana": "Grafana",
}

MAX_FAVOURITES = 24


def normalize_feature(feature: str | None) -> str:
    f = (feature or "").strip().lower()
    if f in FEATURE_META:
        return f
    raise ValueError(f"unknown host feature {feature!r}")


def normalize_app_page(page: str | None) -> str:
    p = (page or "").strip().lower()
    if p in APP_PAGE_META:
        return p
    raise ValueError(f"unknown app page {page!r}")


def feature_href(server_id: int, feature: str) -> str:
    f = normalize_feature(feature)
    suffix = FEATURE_META[f]["suffix"]
    return f"/servers/{int(server_id)}{suffix}"


def feature_label(feature: str) -> str:
    try:
        return FEATURE_META[normalize_feature(feature)]["label"]
    except ValueError:
        return (feature or "Feature").strip() or "Feature"


def app_page_href(page: str) -> str:
    p = normalize_app_page(page)
    return APP_PAGE_META[p]["href"]


def app_page_label(page: str) -> str:
    try:
        return APP_PAGE_META[normalize_app_page(page)]["label"]
    except ValueError:
        return (page or "Page").strip() or "Page"


def integration_type_label(itype: str | None) -> str:
    t = (itype or "").strip().lower()
    return INTEGRATION_TYPE_LABELS.get(t, t.replace("_", " ").title() or "Integration")


def server_has_feature(server: Server, feature: str | None) -> bool:
    """Whether a fleet host offers this surface for cross-host jump."""
    f = (feature or "").strip().lower()
    if not f or f == "overview":
        return True
    if f == "docker":
        return bool(getattr(server, "container_patch_enabled", False))
    if f == "backups":
        return bool(getattr(server, "backup_enabled", False))
    if f == "services":
        # Host services page is always available; useful when Docker is off (HAOS).
        return True
    return True


def fleet_server_choices(
    session: Session,
    *,
    feature: str | None = None,
) -> list[dict[str, Any]]:
    """Fleet hosts for switcher. When *feature* is set, only hosts with that flag."""
    rows = list(
        session.exec(select(Server).order_by(Server.sort_order, Server.name)).all()
    )
    out: list[dict[str, Any]] = []
    for s in rows:
        if s.id is None:
            continue
        if feature and not server_has_feature(s, feature):
            continue
        out.append(
            {
                "id": int(s.id),
                "name": s.name or f"#{s.id}",
                "backup_enabled": bool(getattr(s, "backup_enabled", False)),
                "container_patch_enabled": bool(
                    getattr(s, "container_patch_enabled", False)
                ),
            }
        )
    return out


def _count_user_pins(session: Session, user_id: int) -> int:
    return len(
        list(
            session.exec(
                select(UserFavourite).where(UserFavourite.user_id == user_id)
            ).all()
        )
    )


def find_server_favourite(
    session: Session,
    user_id: int,
    *,
    server_id: int,
    feature: str,
) -> Optional[UserFavourite]:
    f = normalize_feature(feature)
    return session.exec(
        select(UserFavourite).where(
            UserFavourite.user_id == user_id,
            UserFavourite.kind == KIND_SERVER_FEATURE,
            UserFavourite.server_id == server_id,
            UserFavourite.feature == f,
        )
    ).first()


# Back-compat alias
find_favourite = find_server_favourite


def find_app_page_favourite(
    session: Session, user_id: int, *, page: str
) -> Optional[UserFavourite]:
    p = normalize_app_page(page)
    return session.exec(
        select(UserFavourite).where(
            UserFavourite.user_id == user_id,
            UserFavourite.kind == KIND_APP_PAGE,
            UserFavourite.feature == p,
        )
    ).first()


def find_integration_favourite(
    session: Session, user_id: int, *, integration_id: int
) -> Optional[UserFavourite]:
    key = str(int(integration_id))
    return session.exec(
        select(UserFavourite).where(
            UserFavourite.user_id == user_id,
            UserFavourite.kind == KIND_INTEGRATION,
            UserFavourite.feature == key,
        )
    ).first()


def list_favourites(session: Session, user_id: int) -> list[dict[str, Any]]:
    """Flat list for JSON; client groups by host / App / Integrations."""
    rows = list(
        session.exec(
            select(UserFavourite)
            .where(UserFavourite.user_id == user_id)
            .order_by(UserFavourite.sort_order, UserFavourite.id)
        ).all()
    )
    if not rows:
        return []

    server_ids = {
        int(r.server_id)
        for r in rows
        if r.kind == KIND_SERVER_FEATURE and r.server_id
    }
    names: dict[int, str] = {}
    if server_ids:
        for s in session.exec(
            select(Server).where(Server.id.in_(list(server_ids)))  # type: ignore[attr-defined]
        ).all():
            if s.id is not None:
                names[int(s.id)] = s.name or f"#{s.id}"

    integ_ids: set[int] = set()
    for r in rows:
        if r.kind == KIND_INTEGRATION and (r.feature or "").isdigit():
            integ_ids.add(int(r.feature))
    integ_map: dict[int, Integration] = {}
    if integ_ids:
        for row in session.exec(
            select(Integration).where(Integration.id.in_(list(integ_ids)))  # type: ignore[attr-defined]
        ).all():
            if row.id is not None:
                integ_map[int(row.id)] = row

    out: list[dict[str, Any]] = []
    for r in rows:
        kind = (r.kind or KIND_SERVER_FEATURE).strip() or KIND_SERVER_FEATURE

        if kind == KIND_SERVER_FEATURE:
            if not r.server_id:
                continue
            sid = int(r.server_id)
            sname = names.get(sid)
            if not sname:
                continue
            flab = feature_label(r.feature)
            out.append(
                {
                    "id": r.id,
                    "kind": kind,
                    "group": "host",
                    "group_key": f"host:{sid}",
                    "group_label": sname,
                    "server_id": sid,
                    "server_name": sname,
                    "feature": r.feature,
                    "feature_label": flab,
                    "label": (r.label or "").strip() or f"{sname} · {flab}",
                    "href": feature_href(sid, r.feature),
                }
            )
            continue

        if kind == KIND_APP_PAGE:
            try:
                page = normalize_app_page(r.feature)
            except ValueError:
                continue
            plab = app_page_label(page)
            out.append(
                {
                    "id": r.id,
                    "kind": kind,
                    "group": "app",
                    "group_key": "app",
                    "group_label": "App",
                    "server_id": None,
                    "server_name": None,
                    "feature": page,
                    "feature_label": plab,
                    "label": (r.label or "").strip() or plab,
                    "href": app_page_href(page),
                }
            )
            continue

        if kind == KIND_INTEGRATION:
            if not (r.feature or "").isdigit():
                continue
            iid = int(r.feature)
            integ = integ_map.get(iid)
            if not integ:
                continue
            tlab = integration_type_label(integ.type)
            name = (integ.name or tlab).strip() or tlab
            out.append(
                {
                    "id": r.id,
                    "kind": kind,
                    "group": "integration",
                    "group_key": "integration",
                    "group_label": "Integrations",
                    "server_id": None,
                    "server_name": None,
                    "integration_id": iid,
                    "feature": str(iid),
                    "feature_label": name,
                    "type_label": tlab,
                    "label": (r.label or "").strip() or name,
                    "href": f"/integrations/{iid}",
                }
            )
            continue

    return out


def add_server_favourite(
    session: Session,
    user_id: int,
    *,
    server_id: int,
    feature: str,
    label: str | None = None,
) -> UserFavourite:
    f = normalize_feature(feature)
    existing = find_server_favourite(
        session, user_id, server_id=server_id, feature=f
    )
    if existing:
        return existing
    if _count_user_pins(session, user_id) >= MAX_FAVOURITES:
        raise ValueError(f"At most {MAX_FAVOURITES} favourites")
    server = session.get(Server, server_id)
    if not server:
        raise ValueError("Server not found")
    row = UserFavourite(
        user_id=user_id,
        kind=KIND_SERVER_FEATURE,
        server_id=server_id,
        feature=f,
        label=(label or "").strip()[:128] or None,
        sort_order=_count_user_pins(session, user_id),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# Back-compat
add_favourite = add_server_favourite


def add_app_page_favourite(
    session: Session,
    user_id: int,
    *,
    page: str,
    label: str | None = None,
) -> UserFavourite:
    p = normalize_app_page(page)
    existing = find_app_page_favourite(session, user_id, page=p)
    if existing:
        return existing
    if _count_user_pins(session, user_id) >= MAX_FAVOURITES:
        raise ValueError(f"At most {MAX_FAVOURITES} favourites")
    row = UserFavourite(
        user_id=user_id,
        kind=KIND_APP_PAGE,
        server_id=None,
        feature=p,
        label=(label or "").strip()[:128] or None,
        sort_order=_count_user_pins(session, user_id),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def add_integration_favourite(
    session: Session,
    user_id: int,
    *,
    integration_id: int,
    label: str | None = None,
) -> UserFavourite:
    integ = session.get(Integration, int(integration_id))
    if not integ:
        raise ValueError("Integration not found")
    existing = find_integration_favourite(
        session, user_id, integration_id=int(integration_id)
    )
    if existing:
        return existing
    if _count_user_pins(session, user_id) >= MAX_FAVOURITES:
        raise ValueError(f"At most {MAX_FAVOURITES} favourites")
    tlab = integration_type_label(integ.type)
    auto = (integ.name or tlab).strip()[:128] or tlab
    row = UserFavourite(
        user_id=user_id,
        kind=KIND_INTEGRATION,
        server_id=None,
        feature=str(int(integration_id)),
        label=(label or "").strip()[:128] or auto,
        sort_order=_count_user_pins(session, user_id),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def remove_favourite(session: Session, user_id: int, favourite_id: int) -> bool:
    row = session.get(UserFavourite, favourite_id)
    if not row or row.user_id != user_id:
        return False
    session.delete(row)
    session.commit()
    return True


def toggle_server_favourite(
    session: Session,
    user_id: int,
    *,
    server_id: int,
    feature: str,
) -> dict[str, Any]:
    f = normalize_feature(feature)
    existing = find_server_favourite(
        session, user_id, server_id=server_id, feature=f
    )
    if existing:
        rid = int(existing.id)  # type: ignore[arg-type]
        session.delete(existing)
        session.commit()
        return {"pinned": False, "id": rid, "kind": KIND_SERVER_FEATURE}
    row = add_server_favourite(
        session, user_id, server_id=server_id, feature=f
    )
    return {"pinned": True, "id": row.id, "kind": KIND_SERVER_FEATURE}


# Back-compat name used by host_feature_nav
toggle_favourite = toggle_server_favourite


def toggle_app_page_favourite(
    session: Session, user_id: int, *, page: str
) -> dict[str, Any]:
    p = normalize_app_page(page)
    existing = find_app_page_favourite(session, user_id, page=p)
    if existing:
        rid = int(existing.id)  # type: ignore[arg-type]
        session.delete(existing)
        session.commit()
        return {"pinned": False, "id": rid, "kind": KIND_APP_PAGE, "page": p}
    row = add_app_page_favourite(session, user_id, page=p)
    return {"pinned": True, "id": row.id, "kind": KIND_APP_PAGE, "page": p}


def toggle_integration_favourite(
    session: Session, user_id: int, *, integration_id: int
) -> dict[str, Any]:
    existing = find_integration_favourite(
        session, user_id, integration_id=int(integration_id)
    )
    if existing:
        rid = int(existing.id)  # type: ignore[arg-type]
        session.delete(existing)
        session.commit()
        return {
            "pinned": False,
            "id": rid,
            "kind": KIND_INTEGRATION,
            "integration_id": int(integration_id),
        }
    row = add_integration_favourite(
        session, user_id, integration_id=int(integration_id)
    )
    return {
        "pinned": True,
        "id": row.id,
        "kind": KIND_INTEGRATION,
        "integration_id": int(integration_id),
    }


def host_feature_context(
    session: Session,
    user_id: int | None,
    server: Server | dict[str, Any],
    feature: str,
) -> dict[str, Any]:
    """Template context for pin button + cross-host jump."""
    f = normalize_feature(feature)
    if isinstance(server, dict):
        sid = int(server.get("id") or 0)
        sname = server.get("name") or f"#{sid}"
    else:
        sid = int(server.id or 0)
        sname = server.name or f"#{sid}"
    # Jump targets: only hosts that have this feature enabled (+ always include self)
    fleet = fleet_server_choices(session, feature=f)
    if sid and not any(h["id"] == sid for h in fleet):
        # Current host still listed even if flag off (operator already here)
        fleet = [{"id": sid, "name": sname}] + fleet
    jump_others = [h for h in fleet if h["id"] != sid]
    pinned = False
    fav_id = None
    if user_id and sid:
        fav = find_server_favourite(
            session, int(user_id), server_id=sid, feature=f
        )
        if fav:
            pinned = True
            fav_id = fav.id
    return {
        "host_feature": f,
        "host_feature_label": feature_label(f),
        "host_feature_href": feature_href(sid, f) if sid else "",
        "fleet_servers": fleet,
        "fleet_jump_count": len(jump_others),
        "can_host_jump": len(jump_others) > 0,
        "is_favourite": pinned,
        "favourite_id": fav_id,
        "host_server_id": sid,
        "host_server_name": sname,
    }


def app_page_pin_context(
    session: Session, user_id: int | None, page: str
) -> dict[str, Any]:
    p = normalize_app_page(page)
    pinned = False
    fav_id = None
    if user_id:
        fav = find_app_page_favourite(session, int(user_id), page=p)
        if fav:
            pinned = True
            fav_id = fav.id
    return {
        "pin_kind": KIND_APP_PAGE,
        "pin_page": p,
        "pin_label": app_page_label(p),
        "pin_href": app_page_href(p),
        "is_favourite": pinned,
        "favourite_id": fav_id,
    }


def integration_pin_context(
    session: Session, user_id: int | None, integration: Integration
) -> dict[str, Any]:
    iid = int(integration.id or 0)
    pinned = False
    fav_id = None
    if user_id and iid:
        fav = find_integration_favourite(
            session, int(user_id), integration_id=iid
        )
        if fav:
            pinned = True
            fav_id = fav.id
    tlab = integration_type_label(integration.type)
    name = (integration.name or tlab).strip() or tlab
    return {
        "pin_kind": KIND_INTEGRATION,
        "pin_integration_id": iid,
        "pin_label": name,
        "pin_type_label": tlab,
        "pin_href": f"/integrations/{iid}" if iid else "",
        "is_favourite": pinned,
        "favourite_id": fav_id,
    }


def summarize_user_agent(ua: str | None) -> str:
    """Short device type for trusted-device list (AB)."""
    s = (ua or "").strip()
    if not s:
        return "Unknown browser"
    low = s.lower()
    browser = "Browser"
    if "edg/" in low or "edgios" in low:
        browser = "Edge"
    elif "chrome/" in low and "chromium" not in low:
        browser = "Chrome"
    elif "firefox/" in low or "fxios" in low:
        browser = "Firefox"
    elif "safari/" in low and "chrome" not in low:
        browser = "Safari"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    os_name = "device"
    if "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "android" in low:
        os_name = "Android"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "windows" in low:
        os_name = "Windows"
    elif "cros" in low:
        os_name = "ChromeOS"
    elif "linux" in low:
        os_name = "Linux"
    return f"{browser} on {os_name}"


def trusted_device_public(dev: Any) -> dict[str, Any]:
    """Enriched view dict for Account trusted-device list (AB)."""
    ua = getattr(dev, "user_agent", None)
    label = (getattr(dev, "label", None) or "").strip()
    auto = summarize_user_agent(ua)
    if not label or label == "Trusted device":
        display = auto
    else:
        display = label
    return {
        "id": getattr(dev, "id", None),
        "label": label or None,
        "display_name": display,
        "device_type": auto,
        "user_agent": ua,
        "ip": getattr(dev, "ip", None),
        "created_at": getattr(dev, "created_at", None),
        "last_used_at": getattr(dev, "last_used_at", None),
        "expires_at": getattr(dev, "expires_at", None),
    }
