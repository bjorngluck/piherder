"""Operator-defined container order for stack panel + map expand."""
from __future__ import annotations

import json
from typing import Any

from .app_settings import load_settings, save_settings

_SETTING_KEY = "stack_container_order_json"


def order_key(server_id: int, project: str) -> str:
    return f"{int(server_id)}:{(project or '').strip().lower()}"


def load_all_orders() -> dict[str, list[str]]:
    raw = load_settings().get(_SETTING_KEY) or "{}"
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        if not isinstance(data, dict):
            return {}
        out: dict[str, list[str]] = {}
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [str(x).strip() for x in v if str(x).strip()]
        return out
    except Exception:
        return {}


def get_order(server_id: int, project: str) -> list[str]:
    return list(load_all_orders().get(order_key(server_id, project)) or [])


def set_order(server_id: int, project: str, names: list[str]) -> list[str]:
    """Persist ordered container/service names for this host project."""
    clean = []
    seen: set[str] = set()
    for n in names or []:
        s = str(n or "").strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        clean.append(s)
    all_orders = load_all_orders()
    key = order_key(server_id, project)
    if clean:
        all_orders[key] = clean
    else:
        all_orders.pop(key, None)
    save_settings({_SETTING_KEY: json.dumps(all_orders)})
    return clean


def apply_order(
    containers: list[dict[str, Any]],
    order: list[str],
    *,
    name_keys: tuple[str, ...] = ("compose_service", "name", "id"),
) -> list[dict[str, Any]]:
    """Sort containers by saved order; unknowns keep relative role-stable order at end."""
    if not containers:
        return containers
    if not order:
        return containers

    def cname(c: dict[str, Any]) -> str:
        for k in name_keys:
            v = (c.get(k) or "").strip()
            if v:
                return v
        return ""

    rank = {n.lower(): i for i, n in enumerate(order)}
    indexed = list(enumerate(containers))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple:
        i, c = item
        n = cname(c).lower()
        if n in rank:
            return (0, rank[n], i)
        return (1, i)

    indexed.sort(key=sort_key)
    out = []
    for i, c in indexed:
        row = dict(c)
        n = cname(c)
        row["order_index"] = rank.get(n.lower(), 1000 + i)
        row["custom_ordered"] = n.lower() in rank
        out.append(row)
    return out
