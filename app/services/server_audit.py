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
    api_token_id: int | None = None,
    api_token_name: str | None = None,
) -> AuditLog:
    payload: dict[str, Any] = dict(details or {})
    if message:
        payload["message"] = message
    if api_token_id is not None:
        payload["api_token_id"] = api_token_id
    if api_token_name:
        payload["api_token_name"] = api_token_name

    now = datetime.utcnow()
    audit = AuditLog(
        user_id=user_id,
        server_id=server_id,
        api_token_id=api_token_id,
        api_token_name=(api_token_name[:120] if api_token_name else None),
        action=action,
        status=status,
        details=json.dumps(payload) if payload else (message or ""),
        started_at=now,
        finished_at=now,
    )
    session.add(audit)
    return audit
