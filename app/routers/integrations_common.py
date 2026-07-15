"""Shared Integration hub helpers + router shell."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from ..models import User
from ..security.auth import role_at_least, ROLE_OPERATOR

logger = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])


def _audit(
    session: Session,
    user: User,
    action: str,
    *,
    server_id: Optional[int] = None,
    details: str = "",
    status: str = "success",
) -> None:
    try:
        from ..services.audit_write import make_audit_log

        session.add(
            make_audit_log(
                user_id=user.id,
                server_id=server_id,
                action=action,
                status=status,
                details=(details or "")[:2000],
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception as e:
        logger.debug("audit skip: %s", e)
        session.rollback()


def _redirect(path: str, *, fragment: str | None = None, **params) -> RedirectResponse:
    """303 redirect; optional URL fragment keeps scroll position on long pages."""
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    frag = (fragment or "").strip().lstrip("#")
    if frag:
        path = f"{path}#{frag}"
    return RedirectResponse(path, status_code=303)


def _can_mutate(user: User) -> bool:
    return role_at_least(user, ROLE_OPERATOR)
