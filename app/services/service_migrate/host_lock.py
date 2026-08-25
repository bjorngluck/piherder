"""Per-compose-project host lock (v1.4 M1).

HAOS is an implicit lock (no row). Operator lock is first-class on ComposeProjectMeta.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from ...config import settings
from ...models import ComposeProjectMeta, Server
from ..haos import is_haos_server

LOCK_REASONS = frozenset({"operator", "hardware", "infra"})
REASON_LABELS = {
    "operator": "Operator",
    "hardware": "Hardware",
    "infra": "Infrastructure",
    "haos": "HAOS",
}

_MAX_PROJECT = 128
_MAX_NOTE = 255


class HostLockError(Exception):
    """Operator-facing lock failure."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = message


def migrate_enabled() -> bool:
    return bool(getattr(settings, "PIHERDER_SERVICE_MIGRATE", False))


def compose_project_name(raw: Optional[str]) -> str:
    """Compose project identity (never a filesystem path)."""
    name = (raw or "").strip()
    if (
        not name
        or name.startswith("/")
        or ".." in name
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or any(c in name for c in ("\n", "\r", ";", "|", "&", "`"))
    ):
        raise HostLockError("invalid compose project name", 400)
    if len(name) > _MAX_PROJECT:
        raise HostLockError("compose project name is too long", 400)
    return name


def _empty_state() -> dict[str, Any]:
    return {
        "locked": False,
        "implicit": False,
        "reason": None,
        "reason_label": None,
        "note": None,
        "locked_at": None,
        "locked_by_user_id": None,
    }


def _haos_state() -> dict[str, Any]:
    return {
        "locked": True,
        "implicit": True,
        "reason": "haos",
        "reason_label": REASON_LABELS["haos"],
        "note": "HAOS appliances cannot be a migrate source or destination",
        "locked_at": None,
        "locked_by_user_id": None,
    }


def _from_row(row: ComposeProjectMeta) -> dict[str, Any]:
    if not row.host_locked:
        return _empty_state()
    reason = (row.lock_reason or "operator").strip().lower()
    if reason not in REASON_LABELS:
        reason = "operator"
    return {
        "locked": True,
        "implicit": False,
        "reason": reason,
        "reason_label": REASON_LABELS.get(reason, "Operator"),
        "note": (row.lock_note or "").strip() or None,
        "locked_at": row.locked_at,
        "locked_by_user_id": row.locked_by_user_id,
    }


def lock_state(
    session: Session, server: Server, project: str
) -> dict[str, Any]:
    if is_haos_server(server):
        return _haos_state()
    name = compose_project_name(project)
    sid = int(server.id or 0)
    row = session.exec(
        select(ComposeProjectMeta).where(
            ComposeProjectMeta.server_id == sid,
            ComposeProjectMeta.compose_project == name,
        )
    ).first()
    if not row:
        return _empty_state()
    return _from_row(row)


def assert_unlocked(session: Session, server: Server, project: str) -> None:
    st = lock_state(session, server, project)
    if not st.get("locked"):
        return
    label = st.get("reason_label") or "this host"
    note = st.get("note")
    msg = f"Locked to {label}"
    if note:
        msg = f"{msg}: {note}"
    raise HostLockError(msg, 403)


def _get_or_create(
    session: Session, server_id: int, project: str
) -> ComposeProjectMeta:
    row = session.exec(
        select(ComposeProjectMeta).where(
            ComposeProjectMeta.server_id == server_id,
            ComposeProjectMeta.compose_project == project,
        )
    ).first()
    if row:
        return row
    row = ComposeProjectMeta(
        server_id=server_id,
        compose_project=project,
        host_locked=False,
    )
    session.add(row)
    session.flush()
    return row


def set_host_lock(
    session: Session,
    server: Server,
    project: str,
    *,
    reason: str,
    note: Optional[str] = None,
    user_id: Optional[int] = None,
) -> ComposeProjectMeta:
    if is_haos_server(server):
        raise HostLockError(
            "HAOS hosts cannot be a migrate source or destination", 403
        )
    name = compose_project_name(project)
    r = (reason or "").strip().lower()
    if r not in LOCK_REASONS:
        raise HostLockError("lock reason must be operator, hardware, or infra", 400)
    note_s = (note or "").strip()
    if len(note_s) > _MAX_NOTE:
        raise HostLockError("lock note is too long", 400)
    sid = int(server.id or 0)
    if sid <= 0:
        raise HostLockError("server required", 400)
    row = _get_or_create(session, sid, name)
    now = datetime.utcnow()
    row.host_locked = True
    row.lock_reason = r
    row.lock_note = note_s or None
    row.locked_at = now
    row.locked_by_user_id = user_id
    row.updated_at = now
    session.add(row)
    return row


def unlock_host(
    session: Session,
    server: Server,
    project: str,
) -> ComposeProjectMeta:
    if is_haos_server(server):
        raise HostLockError(
            "HAOS hosts cannot be a migrate source or destination", 403
        )
    name = compose_project_name(project)
    sid = int(server.id or 0)
    row = session.exec(
        select(ComposeProjectMeta).where(
            ComposeProjectMeta.server_id == sid,
            ComposeProjectMeta.compose_project == name,
        )
    ).first()
    if not row or not row.host_locked:
        raise HostLockError("project is not locked", 400)
    now = datetime.utcnow()
    row.host_locked = False
    row.lock_reason = None
    row.lock_note = None
    row.locked_at = None
    row.locked_by_user_id = None
    row.updated_at = now
    session.add(row)
    return row


def annotate_projects(
    session: Session,
    server: Server,
    projects: Iterable[dict],
) -> None:
    """Attach ``host_lock`` dict onto each inventory project (in place)."""
    rows = list(projects or [])
    if is_haos_server(server):
        haos = _haos_state()
        for p in rows:
            if isinstance(p, dict):
                p["host_lock"] = dict(haos)
        return
    sid = int(server.id or 0)
    names: list[str] = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        try:
            names.append(compose_project_name(str(p.get("name") or "")))
        except HostLockError:
            p["host_lock"] = _empty_state()
    by_name: dict[str, ComposeProjectMeta] = {}
    if names:
        found = session.exec(
            select(ComposeProjectMeta).where(
                ComposeProjectMeta.server_id == sid,
                ComposeProjectMeta.compose_project.in_(names),
            )
        ).all()
        for row in found:
            by_name[row.compose_project] = row
    for p in rows:
        if not isinstance(p, dict) or "host_lock" in p:
            continue
        raw = str(p.get("name") or "")
        try:
            key = compose_project_name(raw)
        except HostLockError:
            p["host_lock"] = _empty_state()
            continue
        row = by_name.get(key)
        p["host_lock"] = _from_row(row) if row else _empty_state()
