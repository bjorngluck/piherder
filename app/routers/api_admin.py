"""Read-only admin API. Bearer scope ``admin``. No writes and no MCP tools.

GET /api/v1/admin/users, /tokens, and /audit. Responses omit password hashes,
TOTP secrets, token secrets, token hashes, and token prefixes. When read audit
is on, each GET writes one api_read row, at most one per token and path every
5 minutes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlmodel import Session, select

from ..database import get_session
from ..models import ApiToken, AuditLog, User
from ..services import api_tokens as tok_svc
from .api_v1 import ApiAuth, get_api_auth

router = APIRouter()

_PAGE_DEFAULT = 50
_PAGE_MAX = 100
_TEXT_CAP = 500
_READ_DEDUPE = timedelta(minutes=5)

def _page(limit: int, offset: int) -> tuple[int, int]:
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = _PAGE_DEFAULT
    if lim < 1:
        lim = _PAGE_DEFAULT
    lim = min(_PAGE_MAX, lim)
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    return lim, off


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    try:
        text = dt.isoformat()
    except Exception:
        return None
    if text.endswith("Z") or (len(text) >= 6 and text[-6] in "+-" and text[-3] == ":"):
        return text
    return text + "Z"


def _cap(text: str | None) -> str | None:
    if text is None:
        return None
    return text[:_TEXT_CAP]


def _require_admin(auth: ApiAuth) -> None:
    auth.require(tok_svc.SCOPE_ADMIN)


def _audit_admin_get(session: Session, auth: ApiAuth, request: Request) -> None:
    """One read row per token and path every 5 minutes, when the flag is on."""
    from ..services.api_read_audit import (
        ACTION,
        public_detail,
        reads_enabled,
        record_api_read,
    )

    if not reads_enabled():
        return
    what = public_detail(f"{request.method} {request.url.path}")
    cutoff = datetime.utcnow() - _READ_DEDUPE
    recent = session.exec(
        select(AuditLog).where(
            AuditLog.action == ACTION,
            AuditLog.api_token_id == auth.token.id,
            AuditLog.details == what,
            AuditLog.started_at >= cutoff,
        )
    ).first()
    if recent is not None:
        return
    record_api_read(
        session,
        token_id=auth.token.id,
        token_name=auth.token.name,
        user_id=auth.user_id,
        client_ip=auth.client_ip,
        what=f"{request.method} {request.url.path}",
    )


def user_admin_dict(user: User) -> dict[str, Any]:
    """Public user row. No password, TOTP secret, or session version."""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "totp_enabled": bool(user.totp_enabled),
        "must_change_password": bool(user.must_change_password),
        "password_login_enabled": bool(user.password_login_enabled),
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
        "last_login_at": _iso(user.last_login_at),
    }


def token_admin_dict(token: ApiToken) -> dict[str, Any]:
    """Public token row. No secret, hash, or prefix."""
    scopes = sorted(tok_svc.parse_scopes(token.scopes))
    return {
        "id": token.id,
        "name": token.name,
        "scopes": scopes,
        "allowed_cidrs": tok_svc.parse_allowed_cidrs(getattr(token, "allowed_cidrs", None)),
        "created_by_user_id": token.created_by_user_id,
        "created_at": _iso(token.created_at),
        "last_used_at": _iso(token.last_used_at),
        "revoked_at": _iso(token.revoked_at),
        "expires_at": _iso(token.expires_at),
        "active": token.revoked_at is None
        and (token.expires_at is None or token.expires_at >= datetime.utcnow()),
    }


def audit_admin_dict(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "server_id": row.server_id,
        "api_token_id": row.api_token_id,
        "api_token_name": row.api_token_name,
        "action": row.action,
        "status": row.status,
        "details": _cap(row.details),
        "output_snippet": _cap(row.output_snippet),
        "client_ip": row.client_ip,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _missing(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


@router.get("/admin/users", summary="List users (admin scope)")
def list_admin_users(
    request: Request,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
    limit: int = _PAGE_DEFAULT,
    offset: int = 0,
):
    _require_admin(auth)
    _audit_admin_get(session, auth, request)
    lim, off = _page(limit, offset)
    total = int(session.exec(select(func.count()).select_from(User)).one() or 0)
    rows = list(session.exec(select(User).order_by(User.id).offset(off).limit(lim)).all())
    return {"users": [user_admin_dict(u) for u in rows], "total": total, "limit": lim, "offset": off}


@router.get("/admin/users/{user_id}", summary="One user (admin scope)")
def get_admin_user_row(
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    _require_admin(auth)
    _audit_admin_get(session, auth, request)
    row = session.get(User, user_id)
    if not row:
        raise _missing("User")
    return user_admin_dict(row)


@router.get("/admin/tokens", summary="List API tokens (admin scope)")
def list_admin_tokens(
    request: Request,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
    limit: int = _PAGE_DEFAULT,
    offset: int = 0,
):
    _require_admin(auth)
    _audit_admin_get(session, auth, request)
    lim, off = _page(limit, offset)
    total = int(session.exec(select(func.count()).select_from(ApiToken)).one() or 0)
    rows = list(
        session.exec(select(ApiToken).order_by(ApiToken.id).offset(off).limit(lim)).all()
    )
    return {
        "tokens": [token_admin_dict(t) for t in rows],
        "total": total,
        "limit": lim,
        "offset": off,
    }


@router.get("/admin/tokens/{token_id}", summary="One API token (admin scope)")
def get_admin_token_row(
    token_id: int,
    request: Request,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    _require_admin(auth)
    _audit_admin_get(session, auth, request)
    row = session.get(ApiToken, token_id)
    if not row:
        raise _missing("Token")
    return token_admin_dict(row)


@router.get("/admin/audit", summary="Audit log (admin scope)")
def list_admin_audit(
    request: Request,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
    limit: int = _PAGE_DEFAULT,
    offset: int = 0,
):
    _require_admin(auth)
    _audit_admin_get(session, auth, request)
    lim, off = _page(limit, offset)
    total = int(session.exec(select(func.count()).select_from(AuditLog)).one() or 0)
    rows = list(
        session.exec(
            select(AuditLog).order_by(AuditLog.id.desc()).offset(off).limit(lim)
        ).all()
    )
    return {
        "audit": [audit_admin_dict(r) for r in rows],
        "total": total,
        "limit": lim,
        "offset": off,
    }


@router.get("/admin/audit/{audit_id}", summary="One audit row (admin scope)")
def get_admin_audit_row(
    audit_id: int,
    request: Request,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    _require_admin(auth)
    _audit_admin_get(session, auth, request)
    row = session.get(AuditLog, audit_id)
    if not row:
        raise _missing("Audit row")
    return audit_admin_dict(row)
