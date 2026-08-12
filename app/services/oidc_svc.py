"""OIDC / SSO helpers (v1.2 Stream S).

Authorization code + PKCE via httpx + PyJWT (JWKS). No refresh tokens stored.
Identity key: (issuer, subject). Email is soft-match for auto-link only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from jwt import PyJWKClient
from sqlmodel import Session, select

from ..config import settings
from ..models import OidcIdentity, User
from ..security.auth import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_RANK,
    ROLE_VIEWER,
    VALID_ROLES,
    create_access_token,
    decode_token_payload,
    get_password_hash,
    normalize_role,
)
from ..security.encryption import decrypt_str, encrypt_str

logger = logging.getLogger(__name__)

STATE_COOKIE = "oidc_state"
STATE_MINUTES = 10
DISCOVERY_TTL_SEC = 300

# Process-local discovery cache: issuer -> (fetched_at, doc)
_discovery_cache: Dict[str, Tuple[float, dict]] = {}

# Unusable bcrypt hash so verify_password always fails (SSO-only accounts)
_UNUSABLE_PASSWORD_MARKER = "!"


class OidcConfigError(Exception):
    """OIDC is misconfigured or disabled."""


class OidcFlowError(Exception):
    """OIDC ceremony failed (user-safe message in str)."""


def normalize_issuer(issuer: str) -> str:
    raw = (issuer or "").strip()
    if not raw:
        return ""
    return raw.rstrip("/")


def public_redirect_uri() -> str:
    base = (settings.PIHERDER_PUBLIC_URL or "").strip().rstrip("/")
    if not base:
        # Last resort for local; Settings UI shows operator must set PUBLIC_URL
        host = (settings.PIHERDER_HOSTNAME or "localhost").strip()
        base = f"https://{host}" if host not in ("localhost", "127.0.0.1") else f"http://{host}:8000"
    return f"{base}/auth/oidc/callback"


def oidc_settings() -> dict:
    from . import app_settings as app_cfg

    return app_cfg.load_settings()


def oidc_enabled() -> bool:
    cfg = oidc_settings()
    return bool(cfg.get("oidc_enabled")) and bool(normalize_issuer(str(cfg.get("oidc_issuer") or "")))


def oidc_display_name() -> str:
    cfg = oidc_settings()
    name = (cfg.get("oidc_display_name") or "").strip()
    return name or "SSO"


def oidc_require_sso() -> bool:
    return bool(oidc_settings().get("oidc_require_sso"))


def get_client_secret() -> str:
    cfg = oidc_settings()
    enc = (cfg.get("oidc_client_secret_encrypted") or "").strip()
    if not enc:
        return ""
    try:
        return decrypt_str(enc)
    except Exception as e:
        logger.warning("OIDC client secret decrypt failed: %s", e)
        return ""


def set_client_secret_encrypted(plain: str) -> str:
    return encrypt_str(plain) if plain else ""


def password_login_allowed(user: User) -> bool:
    return bool(getattr(user, "password_login_enabled", True))


def set_unusable_password(user: User) -> None:
    """Disable password verify without nullable column churn."""
    user.hashed_password = get_password_hash(secrets.token_urlsafe(48))
    user.password_login_enabled = False


def enable_password(user: User, plain: str) -> None:
    user.hashed_password = get_password_hash(plain)
    user.password_login_enabled = True
    user.must_change_password = False


def count_links(session: Session, user_id: int) -> int:
    rows = session.exec(
        select(OidcIdentity.id).where(OidcIdentity.user_id == int(user_id))
    ).all()
    return len(rows)


def has_oidc_link(session: Session, user_id: int) -> bool:
    return count_links(session, user_id) > 0


def list_identities(session: Session, user_id: int) -> List[OidcIdentity]:
    return list(
        session.exec(
            select(OidcIdentity)
            .where(OidcIdentity.user_id == int(user_id))
            .order_by(OidcIdentity.linked_at.desc())
        ).all()
    )


def get_identity_by_iss_sub(
    session: Session, issuer: str, subject: str
) -> Optional[OidcIdentity]:
    iss = normalize_issuer(issuer)
    sub = (subject or "").strip()
    if not iss or not sub:
        return None
    return session.exec(
        select(OidcIdentity).where(
            OidcIdentity.issuer == iss,
            OidcIdentity.subject == sub,
        )
    ).first()


def get_identity_for_user_issuer(
    session: Session, user_id: int, issuer: str
) -> Optional[OidcIdentity]:
    iss = normalize_issuer(issuer)
    return session.exec(
        select(OidcIdentity).where(
            OidcIdentity.user_id == int(user_id),
            OidcIdentity.issuer == iss,
        )
    ).first()


def _role_map_from_cfg(cfg: dict) -> Dict[str, str]:
    raw = cfg.get("oidc_role_map") or "{}"
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except json.JSONDecodeError:
            data = {}
    out: Dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        key = str(k).strip()
        role = normalize_role(str(v))
        if key and role in VALID_ROLES:
            out[key] = role
    return out


def _claim_path_get(claims: dict, path: str) -> Any:
    """Support dotted paths and simple keys (e.g. groups, realm_access.roles)."""
    path = (path or "").strip()
    if not path or not isinstance(claims, dict):
        return None
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_group_values(claims: dict, role_claim: str) -> List[str]:
    raw = _claim_path_get(claims, role_claim or "groups")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, dict):
        # e.g. {"roles": ["admin"]} already unwrapped by path; else keys
        return [str(k).strip() for k in raw.keys() if str(k).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def map_role_from_claims(claims: dict, cfg: Optional[dict] = None) -> str:
    """Highest privilege among matched groups; else default role."""
    cfg = cfg or oidc_settings()
    role_claim = (cfg.get("oidc_role_claim") or "groups").strip() or "groups"
    role_map = _role_map_from_cfg(cfg)
    default = normalize_role(str(cfg.get("oidc_default_role") or ROLE_VIEWER))
    groups = extract_group_values(claims, role_claim)
    best = default
    best_rank = ROLE_RANK.get(best, 0)
    for g in groups:
        mapped = role_map.get(g)
        if not mapped:
            # case-insensitive group key match
            for mk, mv in role_map.items():
                if mk.lower() == g.lower():
                    mapped = mv
                    break
        if mapped and ROLE_RANK.get(mapped, 0) > best_rank:
            best = mapped
            best_rank = ROLE_RANK[mapped]
    return best


def pkce_pair() -> Tuple[str, str]:
    """Return (code_verifier, code_challenge S256)."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def mint_state_token(
    *,
    mode: str,
    code_verifier: str,
    nonce: str,
    user_id: Optional[int] = None,
) -> str:
    payload: Dict[str, Any] = {
        "oidc": True,
        "mode": mode if mode in ("login", "link") else "login",
        "cv": code_verifier,
        "nonce": nonce,
    }
    if user_id is not None:
        payload["uid"] = int(user_id)
    return create_access_token(payload, expires_delta=None)


def parse_state_token(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    payload = decode_token_payload(raw)
    if not payload or not payload.get("oidc"):
        return None
    return payload


def fetch_discovery(issuer: str) -> dict:
    iss = normalize_issuer(issuer)
    if not iss:
        raise OidcConfigError("OIDC issuer is not set")
    now = time.time()
    cached = _discovery_cache.get(iss)
    if cached and (now - cached[0]) < DISCOVERY_TTL_SEC:
        return cached[1]
    url = f"{iss}/.well-known/openid-configuration"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            doc = r.json()
    except Exception as e:
        logger.warning("OIDC discovery failed for %s: %s", iss, e)
        raise OidcConfigError("Could not reach identity provider discovery URL") from e
    if not isinstance(doc, dict) or not doc.get("authorization_endpoint"):
        raise OidcConfigError("Invalid OIDC discovery document")
    _discovery_cache[iss] = (now, doc)
    return doc


def clear_discovery_cache() -> None:
    _discovery_cache.clear()


def build_authorize_url(
    *,
    mode: str = "login",
    user_id: Optional[int] = None,
    prompt: Optional[str] = None,
) -> Tuple[str, str]:
    """Return (authorize_url, state_cookie_value)."""
    if not oidc_enabled():
        raise OidcConfigError("SSO is not enabled")
    cfg = oidc_settings()
    issuer = normalize_issuer(str(cfg.get("oidc_issuer") or ""))
    client_id = (cfg.get("oidc_client_id") or "").strip()
    if not client_id:
        raise OidcConfigError("OIDC client id is not set")
    secret = get_client_secret()
    if not secret:
        raise OidcConfigError("OIDC client secret is not set")

    doc = fetch_discovery(issuer)
    auth_ep = doc["authorization_endpoint"]
    scopes = (cfg.get("oidc_scopes") or "openid email profile").strip()
    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(24)
    state = mint_state_token(
        mode=mode, code_verifier=verifier, nonce=nonce, user_id=user_id
    )
    # Put a short random state param; real payload lives in cookie (avoids long URLs)
    state_param = secrets.token_urlsafe(16)
    # Embed state_param into cookie token for correlation
    state_cookie = create_access_token(
        {
            "oidc": True,
            "mode": mode if mode in ("login", "link") else "login",
            "cv": verifier,
            "nonce": nonce,
            "sp": state_param,
            **({"uid": int(user_id)} if user_id is not None else {}),
        },
        expires_delta=None,
    )
    # Override exp via create_access_token default ACCESS_TOKEN — ok for 7d but better short.
    # Re-mint with short life:
    from datetime import timedelta

    state_cookie = create_access_token(
        {
            "oidc": True,
            "mode": mode if mode in ("login", "link") else "login",
            "cv": verifier,
            "nonce": nonce,
            "sp": state_param,
            **({"uid": int(user_id)} if user_id is not None else {}),
        },
        expires_delta=timedelta(minutes=STATE_MINUTES),
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": public_redirect_uri(),
        "scope": scopes,
        "state": state_param,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if prompt:
        params["prompt"] = prompt
    elif mode == "link":
        params["prompt"] = "login"

    url = f"{auth_ep}?{urlencode(params)}"
    return url, state_cookie


def exchange_code(code: str, code_verifier: str) -> dict:
    """Token endpoint exchange; returns token response JSON."""
    cfg = oidc_settings()
    issuer = normalize_issuer(str(cfg.get("oidc_issuer") or ""))
    client_id = (cfg.get("oidc_client_id") or "").strip()
    secret = get_client_secret()
    doc = fetch_discovery(issuer)
    token_ep = doc.get("token_endpoint")
    if not token_ep:
        raise OidcConfigError("IdP has no token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": public_redirect_uri(),
        "client_id": client_id,
        "client_secret": secret,
        "code_verifier": code_verifier,
    }
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            r = client.post(
                token_ep,
                data=data,
                headers={"Accept": "application/json"},
            )
            if r.status_code >= 400:
                logger.warning("OIDC token error %s: %s", r.status_code, r.text[:300])
                raise OidcFlowError("Identity provider rejected the login")
            return r.json()
    except OidcFlowError:
        raise
    except Exception as e:
        logger.warning("OIDC token exchange failed: %s", e)
        raise OidcFlowError("Could not complete sign-in with identity provider") from e


def claims_from_tokens(token_response: dict, expected_nonce: Optional[str]) -> dict:
    """Validate id_token when present; else userinfo."""
    cfg = oidc_settings()
    issuer = normalize_issuer(str(cfg.get("oidc_issuer") or ""))
    client_id = (cfg.get("oidc_client_id") or "").strip()
    id_token = token_response.get("id_token")
    access_token = token_response.get("access_token")

    claims: dict = {}
    if id_token:
        try:
            claims = _decode_id_token(id_token, issuer=issuer, client_id=client_id)
        except Exception as e:
            logger.warning("id_token validation failed: %s", e)
            raise OidcFlowError("Invalid identity token from provider") from e
        if expected_nonce and claims.get("nonce") and claims.get("nonce") != expected_nonce:
            raise OidcFlowError("Login state mismatch (nonce)")
    elif access_token:
        claims = _fetch_userinfo(issuer, access_token)
    else:
        raise OidcFlowError("Identity provider returned no identity")

    if not claims.get("sub"):
        raise OidcFlowError("Identity provider did not return a subject")
    return claims


def _decode_id_token(id_token: str, *, issuer: str, client_id: str) -> dict:
    doc = fetch_discovery(issuer)
    jwks_uri = doc.get("jwks_uri")
    if not jwks_uri:
        raise OidcConfigError("IdP has no jwks_uri")
    jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    algs = doc.get("id_token_signing_alg_values_supported") or ["RS256"]
    if isinstance(algs, str):
        algs = [algs]
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=list(algs),
        audience=client_id,
        issuer=issuer,
        options={"require": ["exp", "iat", "sub"]},
    )


def _fetch_userinfo(issuer: str, access_token: str) -> dict:
    doc = fetch_discovery(issuer)
    ep = doc.get("userinfo_endpoint")
    if not ep:
        raise OidcFlowError("No userinfo endpoint and no id_token")
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        r = client.get(
            ep,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        if r.status_code >= 400:
            raise OidcFlowError("Could not load user profile from identity provider")
        data = r.json()
        if not isinstance(data, dict):
            raise OidcFlowError("Invalid userinfo response")
        return data


def email_from_claims(claims: dict) -> Optional[str]:
    email = (claims.get("email") or "").strip().lower()
    return email or None


def email_verified_ok(claims: dict, cfg: Optional[dict] = None) -> bool:
    cfg = cfg or oidc_settings()
    if not cfg.get("oidc_require_email_verified", True):
        return True
    if "email_verified" not in claims:
        # Missing claim is not verified. Operators whose IdP omits the field
        # can turn off oidc_require_email_verified.
        return False
    return bool(claims.get("email_verified"))


def domain_allowed(email: Optional[str], cfg: Optional[dict] = None) -> bool:
    cfg = cfg or oidc_settings()
    raw = (cfg.get("oidc_allowed_email_domains") or "").strip()
    if not raw:
        return True
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    allowed = [d.strip().lower().lstrip("@") for d in raw.replace(";", ",").split(",") if d.strip()]
    return domain in allowed


def create_link(
    session: Session,
    user: User,
    *,
    issuer: str,
    subject: str,
    claims: dict,
) -> OidcIdentity:
    iss = normalize_issuer(issuer)
    sub = (subject or "").strip()
    existing = get_identity_by_iss_sub(session, iss, sub)
    if existing:
        if int(existing.user_id) != int(user.id):
            raise OidcFlowError("This SSO identity is already linked to another account")
        existing.claims_json = _safe_claims_json(claims)
        existing.last_login_at = datetime.utcnow()
        session.add(existing)
        return existing
    other = get_identity_for_user_issuer(session, int(user.id), iss)
    if other:
        raise OidcFlowError("This account is already linked to SSO for this provider")
    row = OidcIdentity(
        user_id=int(user.id),
        issuer=iss,
        subject=sub,
        email_at_link=email_from_claims(claims),
        display_name_at_link=(claims.get("name") or claims.get("preferred_username") or None),
        claims_json=_safe_claims_json(claims),
        linked_at=datetime.utcnow(),
        last_login_at=datetime.utcnow(),
    )
    if row.display_name_at_link:
        row.display_name_at_link = str(row.display_name_at_link)[:256]
    session.add(row)
    session.flush()
    return row


def _safe_claims_json(claims: dict) -> str:
    keep = {
        k: claims.get(k)
        for k in (
            "sub",
            "email",
            "email_verified",
            "name",
            "preferred_username",
            "groups",
            "roles",
        )
        if k in claims
    }
    # Also keep configured role claim path leaf
    return json.dumps(keep, default=str)[:8000]


def find_user_for_login(
    session: Session, claims: dict, cfg: Optional[dict] = None
) -> Tuple[User, str, Optional[OidcIdentity]]:
    """
    Resolve user for SSO login.

    Returns (user, link_reason, identity_or_none_before_link).
    link_reason: existing | email_match | jit
    """
    cfg = cfg or oidc_settings()
    issuer = normalize_issuer(str(cfg.get("oidc_issuer") or ""))
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise OidcFlowError("Missing subject from identity provider")

    ident = get_identity_by_iss_sub(session, issuer, sub)
    if ident:
        user = session.get(User, ident.user_id)
        if not user or not user.is_active:
            raise OidcFlowError("Account is disabled")
        return user, "existing", ident

    email = email_from_claims(claims)
    if not domain_allowed(email, cfg):
        raise OidcFlowError("Email domain is not allowed for SSO")

    auto = cfg.get("oidc_auto_link_by_email", True)
    if auto and email and email_verified_ok(claims, cfg):
        matches = list(
            session.exec(select(User).where(User.email == email, User.is_active == True)).all()  # noqa: E712
        )
        if len(matches) > 1:
            raise OidcFlowError("Multiple accounts share this email; contact an admin")
        if len(matches) == 1:
            u = matches[0]
            # Already linked to different sub under this issuer?
            if get_identity_for_user_issuer(session, int(u.id), issuer):
                raise OidcFlowError("This account is already linked to a different SSO identity")
            return u, "email_match", None

    # JIT provision
    if not email:
        raise OidcFlowError("Identity provider did not return an email address")
    if not email_verified_ok(claims, cfg):
        raise OidcFlowError("Email address is not verified at the identity provider")

    taken = session.exec(select(User).where(User.email == email)).first()
    if taken:
        raise OidcFlowError(
            "An account with this email already exists. Sign in locally and link SSO from Account."
        )

    role = map_role_from_claims(claims, cfg)
    display = (claims.get("name") or claims.get("preferred_username") or None)
    if display:
        display = str(display).strip()[:120] or None
    user = User(
        email=email,
        hashed_password=get_password_hash(secrets.token_urlsafe(48)),
        password_login_enabled=False,
        is_active=True,
        role=role,
        display_name=display,
        must_change_password=False,
    )
    session.add(user)
    session.flush()
    return user, "jit", None


def maybe_sync_role(session: Session, user: User, claims: dict, cfg: Optional[dict] = None) -> bool:
    cfg = cfg or oidc_settings()
    if not cfg.get("oidc_sync_roles_on_login", True):
        return False
    new_role = map_role_from_claims(claims, cfg)
    old = normalize_role(getattr(user, "role", None))
    if new_role == old:
        return False
    # Protect sole admin from demotion via missing groups
    if old == ROLE_ADMIN and new_role != ROLE_ADMIN:
        from ..security.auth import is_sole_admin

        if is_sole_admin(session, user):
            logger.info("Skipping OIDC role demotion for sole admin user_id=%s", user.id)
            return False
    user.role = new_role
    session.add(user)
    return True


def identity_public_dict(row: OidcIdentity) -> dict:
    host = ""
    try:
        host = urlparse(row.issuer).netloc or row.issuer
    except Exception:
        host = row.issuer
    return {
        "id": row.id,
        "issuer": row.issuer,
        "issuer_host": host,
        "subject": row.subject,
        "email_at_link": row.email_at_link,
        "display_name_at_link": row.display_name_at_link,
        "linked_at": row.linked_at.isoformat() + "Z" if row.linked_at else None,
        "last_login_at": row.last_login_at.isoformat() + "Z" if row.last_login_at else None,
    }


def verify_stepup_2fa(
    session: Session,
    user: User,
    *,
    password: Optional[str] = None,
    totp_code: Optional[str] = None,
    request=None,
) -> Tuple[bool, str]:
    """
    For sensitive Account SSO actions when 2FA is enrolled.

    Returns (ok, error_code).
    - If user has 2FA: require valid TOTP/backup **or** a recent passkey Account step-up cookie.
    - If no 2FA enrolled and password login enabled: require current password.
    - SSO-only without 2FA: allow mutation (already in session).
    """
    from . import webauthn_svc as wa_svc
    from ..security.auth import (
        account_stepup_active,
        verify_password,
        verify_totp_code,
        decrypt_totp_secret,
        consume_backup_code,
    )

    has_2fa = wa_svc.user_has_2fa(session, user)
    if has_2fa:
        if request is not None and account_stepup_active(request, user):
            return True, ""
        code = (totp_code or "").strip().replace(" ", "")
        if not code:
            if wa_svc.has_passkeys(session, int(user.id)) and not wa_svc.totp_active(user):
                return False, "use_passkey"
            return False, "2fa_required"
        # TOTP
        if getattr(user, "totp_enabled", False) and user.totp_secret_encrypted:
            try:
                secret = decrypt_totp_secret(user.totp_secret_encrypted)
                if verify_totp_code(secret, code):
                    return True, ""
            except Exception:
                pass
        if consume_backup_code(session, user.id, code):
            return True, ""
        return False, "2fa_bad_code"

    # No 2FA enrolled: require password if password login is enabled
    if password_login_allowed(user):
        if not password or not verify_password(password, user.hashed_password):
            return False, "password_required"
        return True, ""

    # SSO-only without 2FA: allow mutation (already in session) — link/unlink edge
    return True, ""
