"""Shared dual-line ops-hero pulse helpers.

Routers build compact pulse dicts for ops-hero templates (Servers, Jobs, Audit,
Alerts, Catalog, Settings, Account, Users). Keep the shape consistent:

  health: 'ok' | 'warn' | 'hot' | 'mute'
  primary / primary_label: big orb number + caption
  bar: list of {n, cls, title} segments
  line1 / line2: list of {n, l, cls?} mini stats
  caption: footer hint
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def stat(n: Any, label: str, cls: str = "") -> Dict[str, Any]:
    return {"n": n, "l": label, "cls": cls or ""}


def bar_seg(n: Any, cls: str, title: str = "") -> Dict[str, Any]:
    # Floor at 0.001 so zero-width segments still participate in flex layout.
    try:
        val = float(n)
    except (TypeError, ValueError):
        val = 0.0
    return {"n": val if val > 0 else 0.001, "cls": cls, "title": title or ""}


def dual_line_pulse(
    *,
    health: str = "ok",
    primary: Any = "—",
    primary_label: str = "items",
    bar: Optional[List[Dict[str, Any]]] = None,
    line1: Optional[List[Dict[str, Any]]] = None,
    line2: Optional[List[Dict[str, Any]]] = None,
    caption: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    """Canonical ops-hero pulse payload."""
    out: Dict[str, Any] = {
        "health": health or "ok",
        "primary": primary,
        "primary_label": primary_label,
        "bar": list(bar or []),
        "line1": list(line1 or []),
        "line2": list(line2 or []),
        "caption": caption or "",
    }
    if extra:
        out.update(extra)
    return out


def users_pulse(
    users: Iterable[Any],
    sole_admin_ids: Optional[set] = None,
    *,
    role_admin: str = "admin",
    role_operator: str = "operator",
    role_viewer: str = "viewer",
) -> Dict[str, Any]:
    """Build Users admin hero pulse from a user list."""
    sole = sole_admin_ids or set()
    by_role = {role_admin: 0, role_operator: 0, role_viewer: 0}
    totp_on = 0
    total = 0
    for u in users:
        total += 1
        r = (getattr(u, "role", None) or role_admin or "").strip().lower() or role_admin
        # Map unknown roles into viewer bucket for pulse counts
        if r not in by_role:
            r = role_viewer if role_viewer in by_role else r
            if r not in by_role:
                by_role[r] = 0
        by_role[r] = by_role.get(r, 0) + 1
        if getattr(u, "totp_enabled", False):
            totp_on += 1
    n_admins = by_role.get(role_admin, 0)
    n_ops = by_role.get(role_operator, 0)
    n_view = by_role.get(role_viewer, 0)
    return dual_line_pulse(
        health="hot" if n_admins < 1 else "ok",
        primary=total,
        primary_label="users",
        bar=[
            bar_seg(n_admins, "ops-bar--ok", f"{n_admins} admin"),
            bar_seg(n_ops, "ops-bar--run", f"{n_ops} operator"),
            bar_seg(n_view, "ops-bar--mute", f"{n_view} viewer"),
        ],
        line1=[
            stat(n_admins, "admin", "text-accent"),
            stat(n_ops, "operator", "text-info"),
            stat(n_view, "viewer"),
            stat(totp_on, "2fa on", "text-accent" if totp_on else ""),
        ],
        line2=[
            stat(total, "total"),
            stat(len(sole), "sole adm", "text-warning" if sole else ""),
            stat(total - totp_on, "no 2fa", "text-warning" if totp_on < total else ""),
        ],
        caption="Roles · 2FA coverage",
    )


def catalog_health(*, err_n: int = 0, items: int = 0, warn_n: int = 0) -> str:
    """Standard catalog hero health from error/warn/item counts."""
    if err_n:
        return "hot"
    if warn_n:
        return "warn"
    if items:
        return "ok"
    return "mute"
