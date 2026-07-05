"""Append-only audit events for server admin actions."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Session

from ..models import AuditLog


def record_server_audit(
    session: Session,
    *,
    server_id: int | None,
    user_id: int | None,
    action: str,
    status: str = "success",
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    payload: dict[str, Any] = dict(details or {})
    if message:
        payload["message"] = message

    now = datetime.utcnow()
    audit = AuditLog(
        user_id=user_id,
        server_id=server_id,
        action=action,
        status=status,
        details=json.dumps(payload) if payload else (message or ""),
        started_at=now,
        finished_at=now,
    )
    session.add(audit)
    return audit