"""Per-user Reports card layout (v1.5 N3a / N3b).

Pin / hide / reorder history cards. Remembered in cookie
``ph_reports_layout`` (not a DB row — the public demo shares one viewer).
Default = 1.3 order plus **Move jobs** last, all visible. At least one card stays visible.
"""
from __future__ import annotations

import json
from typing import Any

COOKIE = "ph_reports_layout"
COOKIE_MAX_AGE = 60 * 60 * 24 * 400

CARD_IDS: tuple[str, ...] = (
    "backups",
    "os_patch",
    "lan",
    "docker",
    "console",
    "move",
)

CARD_META: dict[str, dict[str, str]] = {
    "backups": {"title": "Backups", "testid": "reports-backups"},
    "os_patch": {"title": "OS patches", "testid": "reports-os-patch"},
    "lan": {"title": "LAN live", "testid": "reports-lan"},
    "docker": {"title": "Docker", "testid": "reports-docker"},
    "console": {"title": "Console", "testid": "reports-console"},
    "move": {"title": "Move jobs", "testid": "reports-move"},
}

_ACTIONS = frozenset({"pin", "unpin", "hide", "show", "up", "down", "reset"})


def default_layout() -> dict[str, list[str]]:
    return {
        "order": list(CARD_IDS),
        "hidden": [],
        "pinned": [],
    }


def normalize_layout(raw: Any) -> dict[str, list[str]]:
    """Clamp to known ids. Missing cards append in default order. Hidden ⊆ order."""
    base = default_layout()
    if not isinstance(raw, dict):
        return base
    seen: set[str] = set()
    order: list[str] = []
    for cid in list(raw.get("order") or []) + list(CARD_IDS):
        if cid in CARD_IDS and cid not in seen:
            order.append(cid)
            seen.add(cid)
    hidden_in = raw.get("hidden") or []
    pinned_in = raw.get("pinned") or []
    hidden = [c for c in order if c in hidden_in]
    if len(hidden) >= len(order):
        hidden = []
    pinned = [c for c in order if c in pinned_in and c not in hidden]
    return {"order": order, "hidden": hidden, "pinned": pinned}


def parse_cookie(raw: Any) -> dict[str, list[str]]:
    if raw is None or str(raw).strip() == "":
        return default_layout()
    try:
        data = json.loads(str(raw))
    except Exception:
        return default_layout()
    return normalize_layout(data)


def encode_cookie(layout: dict[str, list[str]]) -> str:
    n = normalize_layout(layout)
    return json.dumps(n, separators=(",", ":"), ensure_ascii=True)


def is_default(layout: dict[str, list[str]]) -> bool:
    n = normalize_layout(layout)
    return n == default_layout()


def visible_ids(layout: dict[str, list[str]]) -> list[str]:
    n = normalize_layout(layout)
    hidden = set(n["hidden"])
    return [c for c in n["order"] if c not in hidden]


def hidden_ids(layout: dict[str, list[str]]) -> list[str]:
    n = normalize_layout(layout)
    hidden = set(n["hidden"])
    return [c for c in n["order"] if c in hidden]


def apply_action(
    layout: dict[str, list[str]],
    action: str,
    card: str = "",
) -> dict[str, list[str]]:
    n = normalize_layout(layout)
    act = (action or "").strip().lower()
    cid = (card or "").strip()
    if act == "reset" or act not in _ACTIONS:
        if act == "reset":
            return default_layout()
        return n
    if cid not in CARD_IDS:
        return n

    order = list(n["order"])
    hidden = set(n["hidden"])
    pinned = set(n["pinned"])

    if act == "pin":
        hidden.discard(cid)
        pinned.add(cid)
        order = [cid] + [c for c in order if c != cid]
    elif act == "unpin":
        pinned.discard(cid)
    elif act == "hide":
        vis = [c for c in order if c not in hidden]
        if cid not in vis or len(vis) <= 1:
            return n
        hidden.add(cid)
        pinned.discard(cid)
    elif act == "show":
        hidden.discard(cid)
    elif act in ("up", "down"):
        vis = [c for c in order if c not in hidden]
        if cid not in vis:
            return n
        i = vis.index(cid)
        j = i - 1 if act == "up" else i + 1
        if j < 0 or j >= len(vis):
            return n
        vis[i], vis[j] = vis[j], vis[i]
        it = iter(vis)
        order = [next(it) if c not in hidden else c for c in order]

    return normalize_layout(
        {
            "order": order,
            "hidden": sorted(hidden),
            "pinned": [c for c in order if c in pinned],
        }
    )


def layout_from_request(request: Any) -> dict[str, list[str]]:
    cookies = getattr(request, "cookies", None) or {}
    return parse_cookie(cookies.get(COOKIE))


def attach_layout_cookie(response: Any, layout: dict[str, list[str]]) -> Any:
    try:
        n = normalize_layout(layout)
        if is_default(n):
            response.delete_cookie(COOKIE, path="/")
        else:
            response.set_cookie(
                key=COOKIE,
                value=encode_cookie(n),
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
            )
    except Exception:
        pass
    return response


def cards_for_template(layout: dict[str, list[str]]) -> dict[str, Any]:
    """Template context: visible/hidden rows + default flag."""
    n = normalize_layout(layout)
    vis = visible_ids(n)
    hid = hidden_ids(n)
    pinned = set(n["pinned"])

    def _row(cid: str) -> dict[str, Any]:
        meta = CARD_META[cid]
        return {
            "id": cid,
            "title": meta["title"],
            "testid": meta["testid"],
            "pinned": cid in pinned,
        }

    visible = [_row(c) for c in vis]
    for i, row in enumerate(visible):
        row["first"] = i == 0
        row["last"] = i == len(visible) - 1
    return {
        "visible": visible,
        "hidden": [_row(c) for c in hid],
        "is_default": is_default(n),
        "layout": n,
    }
