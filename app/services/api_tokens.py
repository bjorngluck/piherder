"""API token create / hash / verify for /api/v1 automation.

Admin-managed, instance-wide automation tokens (not personal PATs).

Scopes
------
Capability:
  read   — GET fleet / jobs / meta
  jobs   — POST job triggers
  edit   — PATCH server feature flags (and future config writes)

Feature allowlist (optional; if none listed, all features allowed):
  feature:backup — backup / retention jobs + toggle backup feature
  feature:os     — OS patch / OS update check + toggle os_patch
  feature:docker — container patch / container update check + toggle docker
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from datetime import datetime
from typing import Iterable, Optional

from sqlmodel import Session, select

from ..models import ApiToken, User

TOKEN_PREFIX = "ph_"
TOKEN_BYTES = 32

# Capability scopes
SCOPE_READ = "read"
SCOPE_JOBS = "jobs"
SCOPE_EDIT = "edit"
CAPABILITY_SCOPES = frozenset({SCOPE_READ, SCOPE_JOBS, SCOPE_EDIT})

# Feature scopes (restrict which server features a token may act on)
FEATURE_BACKUP = "feature:backup"
FEATURE_OS = "feature:os"
FEATURE_DOCKER = "feature:docker"
FEATURE_SCOPES = frozenset({FEATURE_BACKUP, FEATURE_OS, FEATURE_DOCKER})

VALID_SCOPES = CAPABILITY_SCOPES | FEATURE_SCOPES
DEFAULT_SCOPES = (SCOPE_READ, SCOPE_JOBS)

# Map job types → feature key used in feature:* scopes and server flags
JOB_FEATURE_KEY = {
    "backup": "backup",
    "retention": "backup",
    "os_patch": "os",
    "os_update_check": "os",
    "container_patch": "docker",
    "container_update_check": "docker",
}

FEATURE_SCOPE_BY_KEY = {
    "backup": FEATURE_BACKUP,
    "os": FEATURE_OS,
    "docker": FEATURE_DOCKER,
}

SCOPE_HELP = {
    SCOPE_READ: "List servers, jobs, and API meta (GET)",
    SCOPE_JOBS: "Trigger backup / patch / update-check jobs (POST)",
    SCOPE_EDIT: "Change server feature flags (PATCH)",
    FEATURE_BACKUP: "Limit jobs/edits to the Backups feature",
    FEATURE_OS: "Limit jobs/edits to the OS patch feature",
    FEATURE_DOCKER: "Limit jobs/edits to Docker / containers feature",
}


def normalize_scopes(scopes: Iterable[str] | str | None) -> list[str]:
    if scopes is None:
        raw = list(DEFAULT_SCOPES)
    elif isinstance(scopes, str):
        raw = [s.strip().lower() for s in scopes.replace(" ", "").split(",") if s.strip()]
    else:
        raw = [str(s).strip().lower() for s in scopes if str(s).strip()]
    out = sorted({s for s in raw if s in VALID_SCOPES})
    # Always require at least one capability scope; default read if only feature:* given
    caps = [s for s in out if s in CAPABILITY_SCOPES]
    if not caps:
        out = sorted(set(out) | {SCOPE_READ})
    return out


def scopes_csv(scopes: Iterable[str] | str | None) -> str:
    return ",".join(normalize_scopes(scopes))


def parse_scopes(csv: str | None) -> set[str]:
    return set(normalize_scopes(csv or ""))


def token_has_scope(token: ApiToken, scope: str) -> bool:
    return scope in parse_scopes(token.scopes)


def feature_keys_allowed(scopes: set[str] | Iterable[str]) -> Optional[set[str]]:
    """Return set of feature keys allowed, or None if unrestricted (no feature:* scopes)."""
    sc = set(scopes)
    features = {k for k, scope in FEATURE_SCOPE_BY_KEY.items() if scope in sc}
    if not features and not (sc & FEATURE_SCOPES):
        return None
    return features


def token_allows_feature(scopes: set[str] | Iterable[str], feature_key: str) -> bool:
    allowed = feature_keys_allowed(scopes)
    if allowed is None:
        return True
    return feature_key in allowed


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_plaintext_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def normalize_allowed_cidrs(value: Iterable[str] | str | None) -> list[str]:
    """Parse CIDRs / single IPs from list, comma/newline-separated string. Invalid dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        raw = [p.strip() for p in value.replace(";", "\n").replace(",", "\n").splitlines()]
    else:
        raw = [str(p).strip() for p in value]
    out: list[str] = []
    for item in raw:
        if not item:
            continue
        try:
            # Single IP → /32 or /128 network for uniform matching
            if "/" not in item:
                ip = ipaddress.ip_address(item)
                net = ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}", strict=False)
            else:
                net = ipaddress.ip_network(item, strict=False)
            out.append(str(net))
        except ValueError:
            continue
    # stable unique
    seen: set[str] = set()
    unique: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def allowed_cidrs_json(value: Iterable[str] | str | None) -> Optional[str]:
    cidrs = normalize_allowed_cidrs(value)
    if not cidrs:
        return None
    return json.dumps(cidrs)


def parse_allowed_cidrs(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return normalize_allowed_cidrs(data)
    except Exception:
        pass
    return normalize_allowed_cidrs(raw)


def client_ip_allowed(allowed_cidrs: list[str] | str | None, client_ip: str | None) -> bool:
    """Empty allowlist → any IP. Otherwise client must fall in a listed CIDR."""
    cidrs = (
        parse_allowed_cidrs(allowed_cidrs)
        if isinstance(allowed_cidrs, str) or allowed_cidrs is None
        else normalize_allowed_cidrs(allowed_cidrs)
    )
    if not cidrs:
        return True
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip.strip())
    except ValueError:
        return False
    for c in cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


def _normalize_ip_candidate(raw: str | None) -> str:
    """Strip brackets/ports so '1.2.3.4:5678' or '[::1]:80' become parseable IPs."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            return s[1:end]
    # IPv4 host:port (single colon)
    if s.count(":") == 1:
        host, port = s.rsplit(":", 1)
        if port.isdigit():
            return host
    return s


def extract_client_ip(headers: dict | None, peer_host: str | None) -> str:
    """Resolve client IP for API token allowlists.

    Preference (edge proxy should *set* these, not pass client spoofed values):
      1. X-Forwarded-For — first hop only
      2. X-Real-IP
      3. TCP peer (request.client.host)

    Caddy is configured to overwrite X-Forwarded-For / X-Real-IP with the true
    remote host so allowlists work behind the reverse proxy.
    """
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    xff = h.get("x-forwarded-for") or h.get("x-forwarded_for")
    if xff:
        return _normalize_ip_candidate(xff.split(",")[0])
    xri = h.get("x-real-ip") or h.get("x-real_ip")
    if xri:
        return _normalize_ip_candidate(xri)
    return _normalize_ip_candidate(peer_host)


def create_api_token(
    session: Session,
    *,
    name: str,
    created_by: User | None,
    scopes: Iterable[str] | str | None = None,
    allowed_cidrs: Iterable[str] | str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiToken, str]:
    """Create token row; returns (row, plaintext once)."""
    plain = generate_plaintext_token()
    row = ApiToken(
        name=(name or "unnamed").strip()[:120] or "unnamed",
        token_prefix=plain[:12],
        token_hash=hash_token(plain),
        scopes=scopes_csv(scopes),
        allowed_cidrs=allowed_cidrs_json(allowed_cidrs),
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


def update_api_token(
    session: Session,
    token: ApiToken,
    *,
    name: str | None = None,
    scopes: Iterable[str] | str | None = None,
    allowed_cidrs: Iterable[str] | str | None = None,
    update_cidrs: bool = False,
) -> ApiToken:
    """Update name, scopes, and/or IP allowlist. Does not change the secret.

    Pass update_cidrs=True to apply allowed_cidrs (including empty → clear allowlist).
    """
    if token.revoked_at is not None:
        raise ValueError("Cannot update a revoked token")
    if name is not None:
        token.name = (name or "unnamed").strip()[:120] or "unnamed"
    if scopes is not None:
        token.scopes = scopes_csv(scopes)
    if update_cidrs:
        token.allowed_cidrs = allowed_cidrs_json(allowed_cidrs)
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def rotate_api_token(session: Session, token: ApiToken) -> tuple[ApiToken, str]:
    """Issue a new secret for an active token; old secret stops working immediately.

    Returns (row, new_plaintext once). Name, scopes, and IP allowlist are preserved.
    """
    if token.revoked_at is not None:
        raise ValueError("Cannot rotate a revoked token")
    if token.expires_at and token.expires_at < datetime.utcnow():
        raise ValueError("Cannot rotate an expired token")
    plain = generate_plaintext_token()
    token.token_prefix = plain[:12]
    token.token_hash = hash_token(plain)
    # last_used stays historical; secret is brand new
    session.add(token)
    session.commit()
    session.refresh(token)
    return token, plain


def delete_api_token(session: Session, token: ApiToken) -> None:
    session.delete(token)
    session.commit()


def lookup_active_token(session: Session, plain: str) -> Optional[ApiToken]:
    """Find non-revoked, non-expired token by plaintext (no IP / last_used side effects)."""
    if not plain or not plain.startswith(TOKEN_PREFIX):
        return None
    h = hash_token(plain)
    row = session.exec(select(ApiToken).where(ApiToken.token_hash == h)).first()
    if not row or row.revoked_at is not None:
        return None
    if row.expires_at and row.expires_at < datetime.utcnow():
        return None
    return row


def touch_token_last_used(session: Session, token: ApiToken) -> ApiToken:
    token.last_used_at = datetime.utcnow()
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def verify_bearer_token(
    session: Session,
    plain: str,
    *,
    client_ip: str | None = None,
    check_ip: bool = True,
) -> Optional[ApiToken]:
    """Return active ApiToken for plaintext bearer value, or None.

    If check_ip and the token has an allowlist, client_ip must match.
    On success, updates last_used_at.
    """
    row = lookup_active_token(session, plain)
    if not row:
        return None
    if check_ip:
        cidrs = parse_allowed_cidrs(getattr(row, "allowed_cidrs", None))
        if cidrs and not client_ip_allowed(cidrs, client_ip):
            return None
    return touch_token_last_used(session, row)


def token_public_dict(token: ApiToken) -> dict:
    scopes = sorted(parse_scopes(token.scopes))
    cidrs = parse_allowed_cidrs(getattr(token, "allowed_cidrs", None))
    feat = feature_keys_allowed(set(scopes))
    return {
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "scopes": scopes,
        "features_restricted": feat is not None,
        "allowed_features": sorted(feat) if feat is not None else None,
        "allowed_cidrs": cidrs,
        "created_by_user_id": token.created_by_user_id,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "active": token.revoked_at is None
        and (token.expires_at is None or token.expires_at >= datetime.utcnow()),
    }


def api_meta_dict() -> dict:
    """Machine-readable API catalog for GET /api/v1 and docs."""
    return {
        "version": "v1",
        "auth": {
            "type": "bearer",
            "token_prefix": TOKEN_PREFIX,
            "header": "Authorization: Bearer ph_…",
            "management": "Admin-only; Settings → API tokens or /api/v1/tokens (session)",
        },
        "scopes": {
            name: SCOPE_HELP[name]
            for name in sorted(VALID_SCOPES)
        },
        "scope_groups": {
            "capability": sorted(CAPABILITY_SCOPES),
            "feature": sorted(FEATURE_SCOPES),
        },
        "feature_notes": (
            "If no feature:* scopes are on a token, all server features are allowed "
            "for jobs/edits (subject to capability scopes and server feature flags)."
        ),
        "ip_restriction": (
            "Optional allowed_cidrs on each token (IPv4/IPv6 or CIDR). Empty = any IP. "
            "Client IP from X-Forwarded-For (first), X-Real-IP, or TCP peer."
        ),
        "endpoints": [
            {"method": "GET", "path": "/api/v1", "scope": "read", "summary": "API meta / catalog"},
            {"method": "GET", "path": "/api/v1/health", "scope": "read", "summary": "Token health + scopes"},
            {"method": "GET", "path": "/api/v1/servers", "scope": "read", "summary": "List servers"},
            {"method": "GET", "path": "/api/v1/servers/{id}", "scope": "read", "summary": "Server detail"},
            {
                "method": "PATCH",
                "path": "/api/v1/servers/{id}/features",
                "scope": "edit",
                "summary": "Enable/disable backup, os_patch, docker features",
            },
            {"method": "GET", "path": "/api/v1/servers/{id}/jobs", "scope": "read", "summary": "Jobs for server"},
            {"method": "POST", "path": "/api/v1/servers/{id}/jobs", "scope": "jobs", "summary": "Trigger job"},
            {"method": "GET", "path": "/api/v1/jobs", "scope": "read", "summary": "List jobs"},
            {"method": "GET", "path": "/api/v1/jobs/{id}", "scope": "read", "summary": "Job detail"},
        ],
        "job_types": sorted(JOB_FEATURE_KEY.keys()),
        "docs": {
            "markdown": "/static is app assets; human API guide: docs/API.md in the repo",
            "openapi": "/openapi.json",
            "swagger_ui": "/docs",
        },
    }
