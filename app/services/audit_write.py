"""Single entry point for creating AuditLog rows (always attach client IP when known)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..models import AuditLog
from .request_ip import get_request_client_ip


def resolve_client_ip(
    explicit: Optional[str] = None,
    *,
    fallback: Optional[str] = None,
) -> Optional[str]:
    """Prefer explicit IP, then request context, then fallback (e.g. job.details)."""
    for candidate in (explicit, get_request_client_ip(), fallback):
        if candidate is None:
            continue
        s = str(candidate).strip()
        if s:
            return s[:64]
    return None


def make_audit_log(
    *,
    action: str,
    status: str = "success",
    user_id: Optional[int] = None,
    server_id: Optional[int] = None,
    api_token_id: Optional[int] = None,
    api_token_name: Optional[str] = None,
    details: Optional[str] = None,
    output_snippet: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    client_ip: Optional[str] = None,
    **extra: Any,
) -> AuditLog:
    """Build an AuditLog with client_ip filled from request context when omitted.

    Use this for all *new* operator/system events. Herder restore may construct
    AuditLog directly from archived rows (which already include client_ip).
    """
    ip = resolve_client_ip(client_ip)
    # Drop unknown kwargs that SQLModel would reject (forward-compat)
    now = datetime.utcnow()
    return AuditLog(
        user_id=user_id,
        server_id=server_id,
        api_token_id=api_token_id,
        api_token_name=(str(api_token_name)[:120] if api_token_name else None),
        action=action,
        status=status,
        details=details,
        output_snippet=output_snippet,
        started_at=started_at if started_at is not None else now,
        finished_at=finished_at,
        client_ip=ip,
    )
