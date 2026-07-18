"""Persist runtime dependency edges (topology P2 accept/dismiss + P3 manual)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..models import RuntimeEdge, Server


def _norm(s: str | None) -> str:
    return (s or "").strip()


def edge_key(
    *,
    from_server_id: int,
    from_project: str,
    from_container: str | None,
    to_server_id: int,
    to_project: str,
    to_container: str | None,
) -> tuple:
    return (
        int(from_server_id),
        _norm(from_project).lower(),
        _norm(from_container).lower(),
        int(to_server_id),
        _norm(to_project).lower(),
        _norm(to_container).lower(),
    )


def _row_key(row: RuntimeEdge) -> tuple:
    return edge_key(
        from_server_id=row.from_server_id,
        from_project=row.from_project,
        from_container=row.from_container,
        to_server_id=row.to_server_id,
        to_project=row.to_project,
        to_container=row.to_container,
    )


def find_edge(
    session: Session,
    *,
    from_server_id: int,
    from_project: str,
    from_container: str | None,
    to_server_id: int,
    to_project: str,
    to_container: str | None,
) -> RuntimeEdge | None:
    want = edge_key(
        from_server_id=from_server_id,
        from_project=from_project,
        from_container=from_container,
        to_server_id=to_server_id,
        to_project=to_project,
        to_container=to_container,
    )
    # Small table; filter by hosts then match in Python for nullable containers
    rows = session.exec(
        select(RuntimeEdge).where(
            RuntimeEdge.from_server_id == int(from_server_id),
            RuntimeEdge.to_server_id == int(to_server_id),
        )
    ).all()
    for r in rows:
        if _row_key(r) == want:
            return r
    return None


def list_edges_for_project(
    session: Session,
    *,
    server_id: int,
    project: str,
    include_dismissed: bool = False,
) -> list[RuntimeEdge]:
    """Edges that touch this host/project (either end)."""
    proj = _norm(project).lower()
    if not proj or not server_id:
        return []
    rows = session.exec(
        select(RuntimeEdge).where(
            (RuntimeEdge.from_server_id == int(server_id))
            | (RuntimeEdge.to_server_id == int(server_id))
        )
    ).all()
    out: list[RuntimeEdge] = []
    for r in rows:
        fp = _norm(r.from_project).lower()
        tp = _norm(r.to_project).lower()
        if int(r.from_server_id) == int(server_id) and fp == proj:
            out.append(r)
        elif int(r.to_server_id) == int(server_id) and tp == proj:
            out.append(r)
        elif fp == proj or tp == proj:
            # same project name on other host (shared services) — include if either end matches name
            if int(r.from_server_id) == int(server_id) or int(r.to_server_id) == int(
                server_id
            ):
                out.append(r)
    if not include_dismissed:
        out = [r for r in out if not r.dismissed_at]
    return out


def serialize_edge(
    row: RuntimeEdge,
    *,
    server_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    names = server_names or {}
    return {
        "id": row.id,
        "from_server_id": row.from_server_id,
        "from_server_name": names.get(row.from_server_id) or f"#{row.from_server_id}",
        "from_project": row.from_project,
        "from_container": row.from_container or "",
        "to_server_id": row.to_server_id,
        "to_server_name": names.get(row.to_server_id) or f"#{row.to_server_id}",
        "to_project": row.to_project,
        "to_container": row.to_container or "",
        "kind": row.kind or "depends_on",
        "source": row.source or "manual",
        "confidence": int(row.confidence or 0),
        "note": row.note or "",
        "dismissed": bool(row.dismissed_at),
        "same_host": int(row.from_server_id) == int(row.to_server_id),
        "same_project": _norm(row.from_project).lower() == _norm(row.to_project).lower()
        and int(row.from_server_id) == int(row.to_server_id),
    }


def accept_suggestion(
    session: Session,
    *,
    from_server_id: int,
    from_project: str,
    from_container: str | None,
    to_server_id: int,
    to_project: str,
    to_container: str | None,
    kind: str = "depends_on",
    confidence: int = 85,
    user_id: int | None = None,
    note: str | None = None,
) -> RuntimeEdge:
    """Promote a suggestion to accepted (or clear dismiss)."""
    existing = find_edge(
        session,
        from_server_id=from_server_id,
        from_project=from_project,
        from_container=from_container,
        to_server_id=to_server_id,
        to_project=to_project,
        to_container=to_container,
    )
    now = datetime.utcnow()
    if existing:
        existing.dismissed_at = None
        existing.source = "accepted"
        existing.kind = (kind or existing.kind or "depends_on")[:32]
        existing.confidence = int(confidence or existing.confidence or 85)
        if note is not None:
            existing.note = (note or "")[:500] or None
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    row = RuntimeEdge(
        from_server_id=int(from_server_id),
        from_project=_norm(from_project)[:200],
        from_container=(_norm(from_container)[:200] or None),
        to_server_id=int(to_server_id),
        to_project=_norm(to_project)[:200],
        to_container=(_norm(to_container)[:200] or None),
        kind=(kind or "depends_on")[:32],
        source="accepted",
        confidence=int(confidence or 85),
        note=(note or "")[:500] or None,
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def dismiss_suggestion(
    session: Session,
    *,
    from_server_id: int,
    from_project: str,
    from_container: str | None,
    to_server_id: int,
    to_project: str,
    to_container: str | None,
    user_id: int | None = None,
) -> RuntimeEdge:
    """Soft-dismiss a suggestion so it does not re-nag."""
    existing = find_edge(
        session,
        from_server_id=from_server_id,
        from_project=from_project,
        from_container=from_container,
        to_server_id=to_server_id,
        to_project=to_project,
        to_container=to_container,
    )
    now = datetime.utcnow()
    if existing:
        existing.dismissed_at = now
        existing.source = existing.source or "suggested"
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    row = RuntimeEdge(
        from_server_id=int(from_server_id),
        from_project=_norm(from_project)[:200],
        from_container=(_norm(from_container)[:200] or None),
        to_server_id=int(to_server_id),
        to_project=_norm(to_project)[:200],
        to_container=(_norm(to_container)[:200] or None),
        kind="depends_on",
        source="suggested",
        confidence=0,
        dismissed_at=now,
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_manual_edge(
    session: Session,
    *,
    from_server_id: int,
    from_project: str,
    from_container: str | None,
    to_server_id: int,
    to_project: str,
    to_container: str | None,
    kind: str = "talks_to",
    note: str | None = None,
    user_id: int | None = None,
) -> RuntimeEdge:
    existing = find_edge(
        session,
        from_server_id=from_server_id,
        from_project=from_project,
        from_container=from_container,
        to_server_id=to_server_id,
        to_project=to_project,
        to_container=to_container,
    )
    now = datetime.utcnow()
    if existing:
        existing.dismissed_at = None
        existing.source = "manual"
        existing.kind = (kind or "talks_to")[:32]
        existing.confidence = 100
        existing.note = (note or "")[:500] or None
        existing.updated_at = now
        if user_id and not existing.created_by_user_id:
            existing.created_by_user_id = user_id
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    row = RuntimeEdge(
        from_server_id=int(from_server_id),
        from_project=_norm(from_project)[:200],
        from_container=(_norm(from_container)[:200] or None),
        to_server_id=int(to_server_id),
        to_project=_norm(to_project)[:200],
        to_container=(_norm(to_container)[:200] or None),
        kind=(kind or "talks_to")[:32],
        source="manual",
        confidence=100,
        note=(note or "")[:500] or None,
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_edge(session: Session, edge_id: int) -> bool:
    row = session.get(RuntimeEdge, int(edge_id))
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


def undismiss_edge(session: Session, edge_id: int) -> RuntimeEdge | None:
    row = session.get(RuntimeEdge, int(edge_id))
    if not row:
        return None
    row.dismissed_at = None
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def server_name_map(session: Session) -> dict[int, str]:
    out: dict[int, str] = {}
    for s in session.exec(select(Server)).all():
        if s.id is not None:
            out[int(s.id)] = s.name or f"#{s.id}"
    return out


def partition_for_panel(
    session: Session,
    *,
    server_id: int,
    project: str,
    suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge inventory suggestions with persisted edges for the stack panel.

    Returns confirmed, suggested (filtered), dismissed counts.
    Suggestions are same-host compose/heuristic dicts with from/to service names.
    """
    names = server_name_map(session)
    stored = list_edges_for_project(
        session, server_id=server_id, project=project, include_dismissed=True
    )
    confirmed: list[dict[str, Any]] = []
    dismissed_keys: set[tuple] = set()
    active_keys: set[tuple] = set()
    seen_ids: set[int] = set()

    for r in stored:
        key = _row_key(r)
        if r.dismissed_at:
            dismissed_keys.add(key)
            continue
        if (r.source or "") not in ("accepted", "manual"):
            continue
        if r.id is not None and r.id in seen_ids:
            continue
        ser = serialize_edge(r, server_names=names)
        confirmed.append(ser)
        active_keys.add(key)
        if r.id is not None:
            seen_ids.add(int(r.id))

    open_suggestions: list[dict[str, Any]] = []
    for s in suggestions or []:
        frm = _norm(str(s.get("from") or ""))
        to = _norm(str(s.get("to") or ""))
        if not frm or not to:
            continue
        # Same-host suggestions default both ends to this server/project
        key = edge_key(
            from_server_id=server_id,
            from_project=project,
            from_container=frm,
            to_server_id=int(s.get("to_server_id") or server_id),
            to_project=_norm(str(s.get("to_project") or project)),
            to_container=to,
        )
        # also try both containers in same project
        key_same = edge_key(
            from_server_id=server_id,
            from_project=project,
            from_container=frm,
            to_server_id=server_id,
            to_project=project,
            to_container=to,
        )
        if key in dismissed_keys or key_same in dismissed_keys:
            continue
        if key in active_keys or key_same in active_keys:
            continue
        open_suggestions.append(
            {
                "from": frm,
                "to": to,
                "kind": s.get("kind") or "depends_on",
                "source": s.get("source") or "compose",
                "confidence": int(s.get("confidence") or 0),
                "from_server_id": server_id,
                "from_project": project,
                "to_server_id": int(s.get("to_server_id") or server_id),
                "to_project": _norm(str(s.get("to_project") or project)) or project,
                "cross_host": int(s.get("to_server_id") or server_id) != int(server_id),
            }
        )

    confirmed.sort(
        key=lambda x: (
            0 if x.get("source") == "manual" else 1,
            (x.get("from_container") or x.get("from_project") or "").lower(),
            (x.get("to_container") or x.get("to_project") or "").lower(),
        )
    )
    open_suggestions.sort(
        key=lambda x: (
            -int(x.get("confidence") or 0),
            x.get("from") or "",
            x.get("to") or "",
        )
    )

    return {
        "confirmed": confirmed,
        "suggested": open_suggestions,
        "dismissed_count": len(dismissed_keys),
    }
