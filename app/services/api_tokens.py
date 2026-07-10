"""API token create / hash / verify for /api/v1 automation."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Iterable, Optional

from sqlmodel import Session, select

from ..models import ApiToken, User

TOKEN_PREFIX = "ph_"
TOKEN_BYTES = 32
VALID_SCOPES = frozenset({"read", "jobs"})
DEFAULT_SCOPES = ("read", "jobs")


def normalize_scopes(scopes: Iterable[str] | str | None) -> list[str]:
    if scopes is None:
        raw = list(DEFAULT_SCOPES)
    elif isinstance(scopes, str):
        raw = [s.strip().lower() for s in scopes.replace(" ", "").split(",") if s.strip()]
    else:
        raw = [str(s).strip().lower() for s in scopes if str(s).strip()]
    out = sorted({s for s in raw if s in VALID_SCOPES})
    return out or list(DEFAULT_SCOPES)


def scopes_csv(scopes: Iterable[str] | str | None) -> str:
    return ",".join(normalize_scopes(scopes))


def parse_scopes(csv: str | None) -> set[str]:
    return set(normalize_scopes(csv or ""))


def token_has_scope(token: ApiToken, scope: str) -> bool:
    return scope in parse_scopes(token.scopes)


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_plaintext_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def create_api_token(
    session: Session,
    *,
    name: str,
    created_by: User | None,
    scopes: Iterable[str] | str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiToken, str]:
    """Create token row; returns (row, plaintext once)."""
    plain = generate_plaintext_token()
    row = ApiToken(
        name=(name or "unnamed").strip()[:120] or "unnamed",
        token_prefix=plain[:12],
        token_hash=hash_token(plain),
        scopes=scopes_csv(scopes),
        created_by_user_id=created_by.id if created_by else None,
        expires_at=expires_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, plain


def list_api_tokens(session: Session, *, include_revoked: bool = False) -> list[ApiToken]:
    q = select(ApiToken).order_by(ApiToken.created_at.desc())
    rows = list(session.exec(q).all())
    if include_revoked:
        return rows
    return [r for r in rows if r.revoked_at is None]


def get_api_token(session: Session, token_id: int) -> Optional[ApiToken]:
    return session.get(ApiToken, token_id)


def revoke_api_token(session: Session, token: ApiToken) -> ApiToken:
    if token.revoked_at is None:
        token.revoked_at = datetime.utcnow()
        session.add(token)
        session.commit()
        session.refresh(token)
    return token


def delete_api_token(session: Session, token: ApiToken) -> None:
    session.delete(token)
    session.commit()


def verify_bearer_token(session: Session, plain: str) -> Optional[ApiToken]:
    """Return active ApiToken for plaintext bearer value, or None."""
    if not plain or not plain.startswith(TOKEN_PREFIX):
        return None
    h = hash_token(plain)
    row = session.exec(select(ApiToken).where(ApiToken.token_hash == h)).first()
    if not row or row.revoked_at is not None:
        return None
    if row.expires_at and row.expires_at < datetime.utcnow():
        return None
    row.last_used_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def token_public_dict(token: ApiToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "scopes": sorted(parse_scopes(token.scopes)),
        "created_by_user_id": token.created_by_user_id,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "active": token.revoked_at is None
        and (token.expires_at is None or token.expires_at >= datetime.utcnow()),
    }
