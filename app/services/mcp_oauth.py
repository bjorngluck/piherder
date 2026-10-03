"""OAuth 2.1 sign-in for hosted POST /mcp.

An agent that does not have a pasted ``ph_`` token can register, send the
admin through a browser consent, and call ``/mcp`` with the issued access
token. That token is a normal API token (scopes, expiry, revoke). Its
plaintext starts with ``ph_oa_`` so a rejected pasted ``ph_`` token can
stay on the old challenge.
A rejected ``ph_`` token does not advertise this flow, so a configured
Bearer header stays in place.

The public demo does not complete registration, consent, or token exchange.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import Request
from sqlmodel import Session, select

from ..models import ApiToken, McpOAuthClient, McpOAuthCode, McpOAuthRefresh, User
from ..security.auth import ROLE_ADMIN, create_access_token, decode_token_payload, user_role
from . import api_tokens as tok
from .password_reset import configured_public_origin

ACCESS_PREFIX = "ph_oa_"
REFRESH_PREFIX = "mcr_"
SCOPES = (tok.SCOPE_READ, tok.SCOPE_JOBS, tok.SCOPE_EDIT, tok.SCOPE_FILES)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
CONTINUE_COOKIE = "ph_mcp_oauth_go"
CONSENT_COOKIE = "ph_mcp_oauth_consent"
MAX_CLIENTS = 200
_BLOCKED_SCHEMES = frozenset(
    {"javascript", "data", "file", "vbscript", "blob", "about", ""}
)


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        self.error = error
        self.description = description
        self.status = status
        super().__init__(description)


def public_origin(request: Request) -> str:
    """Issuer origin. Prefer PIHERDER_PUBLIC_URL, then the request host."""
    configured = configured_public_origin()
    if configured:
        return configured
    scheme = request.url.scheme or "http"
    netloc = request.url.netloc
    if not netloc:
        return ""
    return f"{scheme}://{netloc}"


def resource_url(request: Request) -> str:
    origin = public_origin(request)
    return f"{origin}/mcp" if origin else ""


def resource_metadata_url(request: Request) -> str:
    origin = public_origin(request)
    if not origin:
        return ""
    return f"{origin}/.well-known/oauth-protected-resource"


def www_authenticate(request: Request, authorization: str | None) -> str:
    """401 challenge. A bad ``ph_`` token omits the discovery URL."""
    raw = (authorization or "").strip()
    token = ""
    if raw.lower().startswith("bearer "):
        token = raw.split(" ", 1)[1].strip()
    elif raw.startswith(tok.TOKEN_PREFIX):
        token = raw
    if token.startswith(tok.TOKEN_PREFIX) and not token.startswith(ACCESS_PREFIX):
        return "Bearer"
    meta = resource_metadata_url(request)
    if not meta:
        return "Bearer"
    if token.startswith(ACCESS_PREFIX):
        return f'Bearer error="invalid_token", resource_metadata="{meta}"'
    return f'Bearer resource_metadata="{meta}"'


def protected_resource_metadata(request: Request) -> dict[str, Any]:
    origin = public_origin(request)
    return {
        "resource": f"{origin}/mcp",
        "authorization_servers": [origin] if origin else [],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata(request: Request) -> dict[str, Any]:
    origin = public_origin(request)
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/mcp/oauth/authorize",
        "token_endpoint": f"{origin}/mcp/oauth/token",
        "registration_endpoint": f"{origin}/mcp/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SCOPES),
    }


def parse_oauth_scopes(raw: str | None) -> list[str]:
    """Capability scopes only. Empty becomes read. Read is always included."""
    text = (raw or "").replace(",", " ")
    out: list[str] = []
    for part in text.split():
        name = part.strip().lower()
        if name in tok.CAPABILITY_SCOPES and name not in out:
            out.append(name)
    if tok.SCOPE_READ not in out:
        out.insert(0, tok.SCOPE_READ)
    return out


def redirect_uri_allowed(uri: str) -> bool:
    """Loopback http, https, or a desktop app scheme. Not http to another host."""
    parsed = urlparse((uri or "").strip())
    scheme = (parsed.scheme or "").lower()
    if scheme in _BLOCKED_SCHEMES:
        return False
    host = (parsed.hostname or "").lower()
    if scheme == "http":
        return host in {"127.0.0.1", "localhost", "::1"}
    if scheme == "https":
        return bool(host)
    # cursor://, vscode://, and similar. Require a host or a path.
    return bool(parsed.netloc or parsed.path)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _demo() -> bool:
    from .demo import demo_mode

    return bool(demo_mode())


def pkce_s256(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _client_redirects(row: McpOAuthClient) -> list[str]:
    try:
        raw = json.loads(row.redirect_uris_json or "[]")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def register_client(
    session: Session,
    *,
    client_name: str,
    redirect_uris: list[str],
) -> McpOAuthClient:
    if _demo():
        raise OAuthError("access_denied", "The public demo does not sign in agents.", 403)
    uris = []
    for item in redirect_uris:
        uri = str(item or "").strip()
        if not uri:
            continue
        if not redirect_uri_allowed(uri):
            raise OAuthError(
                "invalid_redirect_uri",
                "Redirect URI must be https, loopback http, or an app scheme.",
            )
        if uri not in uris:
            uris.append(uri)
    if not uris:
        raise OAuthError("invalid_client_metadata", "redirect_uris is required.")
    if len(uris) > 8:
        raise OAuthError("invalid_client_metadata", "At most 8 redirect URIs.")
    existing = session.exec(select(McpOAuthClient).where(McpOAuthClient.revoked_at == None)).all()  # noqa: E711
    if len(list(existing)) >= MAX_CLIENTS:
        raise OAuthError("invalid_client_metadata", "Too many MCP OAuth clients.", 429)
    row = McpOAuthClient(
        client_id=secrets.token_urlsafe(24),
        client_name=(client_name or "MCP agent").strip()[:120] or "MCP agent",
        redirect_uris_json=json.dumps(uris),
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_client(session: Session, client_id: str) -> McpOAuthClient | None:
    cid = (client_id or "").strip()
    if not cid:
        return None
    row = session.exec(select(McpOAuthClient).where(McpOAuthClient.client_id == cid)).first()
    if not row or row.revoked_at is not None:
        return None
    return row


def resource_matches(request: Request, resource: str) -> bool:
    got = (resource or "").strip().rstrip("/")
    if not got:
        return True
    candidates = set()
    origin = public_origin(request)
    if origin:
        candidates.add(f"{origin}/mcp")
    if request.url.netloc:
        candidates.add(f"{request.url.scheme}://{request.url.netloc}/mcp")
    return got in candidates


def consent_token(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scopes: list[str],
    state: str,
    resource: str,
) -> str:
    return create_access_token(
        {
            "mcp_oauth_consent": True,
            "cid": client_id,
            "redir": redirect_uri,
            "cc": code_challenge,
            "scopes": scopes,
            "state": state,
            "resource": resource,
        },
        expires_delta=timedelta(minutes=10),
    )


def read_consent(token: str) -> dict[str, Any] | None:
    payload = decode_token_payload(token or "")
    if not payload or not payload.get("mcp_oauth_consent"):
        return None
    if not payload.get("cid") or not payload.get("redir") or not payload.get("cc"):
        return None
    return payload


def issue_code(
    session: Session,
    *,
    client_id: str,
    user_id: int,
    redirect_uri: str,
    code_challenge: str,
    scopes: list[str],
    resource: str,
) -> str:
    if _demo():
        raise OAuthError("access_denied", "The public demo does not sign in agents.", 403)
    plain = secrets.token_urlsafe(32)
    row = McpOAuthCode(
        code_hash=_hash(plain),
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        scopes=" ".join(scopes),
        resource=resource or "",
        expires_at=datetime.utcnow() + CODE_TTL,
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return plain


def _burn(session: Session, row: McpOAuthCode) -> None:
    row.used_at = datetime.utcnow()
    session.add(row)
    session.commit()


def _token_name(client_name: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (client_name or "").lower())
    slug = "-".join(part for part in slug.split("-") if part)[:40] or "agent"
    return f"mcp-oauth-{slug}"[:120]


def _issue_grant(
    session: Session,
    *,
    user: User,
    client: McpOAuthClient,
    scopes: list[str],
) -> dict[str, Any]:
    from .demo import raise_if_demo

    raise_if_demo("api_token")
    plain = ACCESS_PREFIX + secrets.token_urlsafe(tok.TOKEN_BYTES)
    now = datetime.utcnow()
    api = ApiToken(
        name=_token_name(client.client_name),
        token_prefix=plain[:12],
        token_hash=tok.hash_token(plain),
        scopes=tok.scopes_csv(scopes),
        allowed_cidrs=None,
        created_by_user_id=user.id,
        expires_at=now + ACCESS_TTL,
    )
    session.add(api)
    session.commit()
    session.refresh(api)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    session.add(
        McpOAuthRefresh(
            token_hash=_hash(refresh),
            api_token_id=int(api.id),
            client_id=client.client_id,
            scopes=" ".join(scopes),
            expires_at=now + REFRESH_TTL,
            created_at=now,
        )
    )
    session.commit()
    return _token_body(plain, refresh, scopes)


def _token_body(access: str, refresh: str, scopes: list[str]) -> dict[str, Any]:
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": " ".join(scopes),
    }


def exchange_code(
    session: Session,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    if _demo():
        raise OAuthError("access_denied", "The public demo does not sign in agents.", 403)
    row = session.exec(
        select(McpOAuthCode).where(McpOAuthCode.code_hash == _hash(code or ""))
    ).first()
    if not row or row.used_at is not None or row.expires_at < datetime.utcnow():
        raise OAuthError("invalid_grant", "Authorization code is invalid or expired.")
    if row.client_id != (client_id or "").strip() or row.redirect_uri != (redirect_uri or "").strip():
        _burn(session, row)
        raise OAuthError("invalid_grant", "Authorization code does not match this client.")
    verifier = (code_verifier or "").strip()
    if len(verifier) < 43 or len(verifier) > 128 or pkce_s256(verifier) != row.code_challenge:
        _burn(session, row)
        raise OAuthError("invalid_grant", "PKCE verification failed.")
    client = get_client(session, row.client_id)
    user = session.get(User, row.user_id)
    if not client or not user or not user.is_active or user_role(user) != ROLE_ADMIN:
        _burn(session, row)
        raise OAuthError("invalid_grant", "This sign-in is no longer valid.")
    scopes = parse_oauth_scopes(row.scopes)
    _burn(session, row)
    return _issue_grant(session, user=user, client=client, scopes=scopes)


def refresh_grant(session: Session, *, refresh_token: str, client_id: str) -> dict[str, Any]:
    if _demo():
        raise OAuthError("access_denied", "The public demo does not sign in agents.", 403)
    row = session.exec(
        select(McpOAuthRefresh).where(McpOAuthRefresh.token_hash == _hash(refresh_token or ""))
    ).first()
    now = datetime.utcnow()
    if not row or row.revoked_at is not None or row.expires_at < now:
        raise OAuthError("invalid_grant", "Refresh token is invalid or expired.")
    if client_id and row.client_id != client_id.strip():
        raise OAuthError("invalid_grant", "Refresh token does not match this client.")
    api = session.get(ApiToken, row.api_token_id)
    if not api or api.revoked_at is not None:
        row.revoked_at = now
        session.add(row)
        session.commit()
        raise OAuthError("invalid_grant", "The API token for this sign-in was revoked.")
    client = get_client(session, row.client_id)
    if not client:
        raise OAuthError("invalid_grant", "The MCP client was revoked.")
    scopes = parse_oauth_scopes(row.scopes)
    plain = ACCESS_PREFIX + secrets.token_urlsafe(tok.TOKEN_BYTES)
    api.token_prefix = plain[:12]
    api.token_hash = tok.hash_token(plain)
    api.expires_at = now + ACCESS_TTL
    api.scopes = tok.scopes_csv(scopes)
    session.add(api)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    row.token_hash = _hash(refresh)
    session.add(row)
    session.commit()
    return _token_body(plain, refresh, scopes)


def continue_ticket(url: str) -> str:
    return create_access_token(
        {"mcp_oauth_go": True, "url": url},
        expires_delta=timedelta(minutes=2),
    )


def read_continue(token: str) -> str:
    payload = decode_token_payload(token or "")
    if not payload or not payload.get("mcp_oauth_go"):
        raise OAuthError("invalid_request", "Sign-in continuation expired.", 400)
    url = str(payload.get("url") or "")
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else url.split("?", 1)[0]
    if parsed.scheme in {"http", "https"}:
        check = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    else:
        check = base
    if not redirect_uri_allowed(check):
        raise OAuthError("invalid_request", "Redirect target is not allowed.", 400)
    return url


def with_query(uri: str, params: dict[str, str]) -> str:
    clean = {k: v for k, v in params.items() if v}
    if not clean:
        return uri
    sep = "&" if "?" in uri else "?"
    return uri + sep + urlencode(clean)


def is_admin(user: User | None) -> bool:
    return bool(user and user.is_active and user_role(user) == ROLE_ADMIN)
