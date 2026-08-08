"""WebAuthn / passkeys — registration and authentication (v1.2 Stream I).

Passkeys are a **second factor** after password (coexist with TOTP + backup codes).
Passwordless-only is deferred. RP ID / origin come from PIHERDER_HOSTNAME and
PIHERDER_PUBLIC_URL (HTTPS required in real browsers for non-localhost).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from sqlmodel import Session, select

from ..config import settings
from ..models import User, WebAuthnCredential
from ..security.auth import create_access_token, decode_token_payload

logger = logging.getLogger(__name__)

CHALLENGE_COOKIE_REG = "wa_reg_chal"
CHALLENGE_COOKIE_AUTH = "wa_auth_chal"
CHALLENGE_MINUTES = 5

# Max passkeys per user (UI + abuse bound)
MAX_CREDENTIALS_PER_USER = 10


class WebAuthnConfigError(Exception):
    """RP / origin cannot be resolved for WebAuthn ceremonies."""


class WebAuthnVerifyError(Exception):
    """Registration or authentication ceremony failed verification."""


def totp_active(user: User) -> bool:
    return bool(getattr(user, "totp_enabled", False) and getattr(user, "totp_secret_encrypted", None))


def count_passkeys(session: Session, user_id: int) -> int:
    rows = session.exec(
        select(WebAuthnCredential.id).where(WebAuthnCredential.user_id == int(user_id))
    ).all()
    return len(rows)


def has_passkeys(session: Session, user_id: int) -> bool:
    return count_passkeys(session, user_id) > 0


def user_has_2fa(session: Session, user: User) -> bool:
    """True if user can complete a 2FA step-up (TOTP and/or at least one passkey)."""
    if totp_active(user):
        return True
    if not getattr(user, "id", None):
        return False
    return has_passkeys(session, int(user.id))


def user_requires_2fa_stepup(session: Session, user: User) -> bool:
    """After password OK, send to /auth/2fa when any second factor is enrolled."""
    return user_has_2fa(session, user)


def list_credentials(session: Session, user_id: int) -> List[WebAuthnCredential]:
    return list(
        session.exec(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == int(user_id))
            .order_by(WebAuthnCredential.created_at.desc())
        ).all()
    )


def get_credential(session: Session, cred_id: int, user_id: int) -> Optional[WebAuthnCredential]:
    row = session.get(WebAuthnCredential, int(cred_id))
    if not row or int(row.user_id) != int(user_id):
        return None
    return row


def delete_credential(session: Session, cred: WebAuthnCredential) -> None:
    session.delete(cred)
    session.commit()


def delete_all_credentials(session: Session, user_id: int) -> int:
    rows = list_credentials(session, user_id)
    n = 0
    for row in rows:
        session.delete(row)
        n += 1
    if n:
        session.commit()
    return n


def rename_credential(session: Session, cred: WebAuthnCredential, nickname: str) -> None:
    nick = (nickname or "").strip()[:128] or None
    cred.nickname = nick
    session.add(cred)
    session.commit()


def resolve_rp_id() -> str:
    """Relying Party ID — hostname only (no port, no scheme)."""
    host = (settings.PIHERDER_HOSTNAME or "").strip().lower()
    if host:
        # strip accidental scheme / path
        if "://" in host:
            host = urlparse(host).hostname or host
        host = host.split("/")[0].split(":")[0]
        if host:
            return host
    pub = (settings.PIHERDER_PUBLIC_URL or "").strip()
    if pub:
        parsed = urlparse(pub if "://" in pub else f"https://{pub}")
        if parsed.hostname:
            return parsed.hostname.lower()
    # Dev / test fallback — browsers only allow localhost without HTTPS
    return "localhost"


def resolve_expected_origin() -> str:
    """Expected origin for WebAuthn (scheme://host[:port])."""
    pub = (settings.PIHERDER_PUBLIC_URL or "").strip().rstrip("/")
    if pub:
        if "://" not in pub:
            pub = f"https://{pub}"
        parsed = urlparse(pub)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    host = resolve_rp_id()
    if host in ("localhost", "127.0.0.1"):
        return f"http://{host}:8000"
    return f"https://{host}"


def resolve_rp_name() -> str:
    return "PiHerder"


def user_handle_for(user: User) -> bytes:
    """Stable opaque user handle (WebAuthn user.id)."""
    return f"ph-user-{int(user.id)}".encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    from webauthn.helpers import bytes_to_base64url

    return bytes_to_base64url(raw)


def _b64url_decode(raw: str) -> bytes:
    from webauthn.helpers import base64url_to_bytes

    return base64url_to_bytes(raw)


def _transports_json(transports: Optional[Sequence[Any]]) -> Optional[str]:
    if not transports:
        return None
    vals = []
    for t in transports:
        if t is None:
            continue
        vals.append(t.value if hasattr(t, "value") else str(t))
    return json.dumps(vals) if vals else None


def _transports_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    return None


def mint_challenge_token(*, kind: str, user_id: int, challenge_b64: str) -> str:
    """Short-lived JWT holding the ceremony challenge (no full session grant)."""
    return create_access_token(
        {
            "wa_chal": True,
            "wa_kind": kind,
            "sub": str(user_id),
            "chal": challenge_b64,
        },
        expires_delta=timedelta(minutes=CHALLENGE_MINUTES),
    )


def read_challenge_token(token: Optional[str], *, kind: str, user_id: int) -> Optional[bytes]:
    if not token:
        return None
    payload = decode_token_payload(token)
    if not payload or not payload.get("wa_chal"):
        return None
    if payload.get("wa_kind") != kind:
        return None
    try:
        if int(payload.get("sub")) != int(user_id):
            return None
    except (TypeError, ValueError):
        return None
    chal = payload.get("chal")
    if not chal or not isinstance(chal, str):
        return None
    try:
        return _b64url_decode(chal)
    except Exception:
        return None


def registration_options_json(session: Session, user: User) -> Tuple[str, str]:
    """Return (options_json, challenge_cookie_value)."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    if count_passkeys(session, int(user.id)) >= MAX_CREDENTIALS_PER_USER:
        raise WebAuthnConfigError(
            f"Maximum of {MAX_CREDENTIALS_PER_USER} passkeys per account"
        )

    existing = list_credentials(session, int(user.id))
    exclude = []
    for c in existing:
        try:
            exclude.append(
                {
                    "id": _b64url_decode(c.credential_id),
                    "type": "public-key",
                    "transports": _transports_list(c.transports) or [],
                }
            )
        except Exception:
            continue

    # Prefer exclude_credentials as PublicKeyCredentialDescriptor objects when possible
    try:
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor

        exclude_desc = [
            PublicKeyCredentialDescriptor(
                id=e["id"],
                transports=e.get("transports") or None,
            )
            for e in exclude
        ]
    except Exception:
        exclude_desc = exclude  # type: ignore[assignment]

    options = generate_registration_options(
        rp_id=resolve_rp_id(),
        rp_name=resolve_rp_name(),
        user_id=user_handle_for(user),
        user_name=user.email,
        user_display_name=(user.display_name or user.email or f"user-{user.id}")[:64],
        exclude_credentials=exclude_desc or None,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    chal_b64 = _b64url_encode(options.challenge)
    token = mint_challenge_token(kind="reg", user_id=int(user.id), challenge_b64=chal_b64)
    return options_to_json(options), token


def verify_registration(
    session: Session,
    user: User,
    credential: Dict[str, Any],
    challenge_token: Optional[str],
    *,
    nickname: Optional[str] = None,
) -> WebAuthnCredential:
    from webauthn import verify_registration_response
    from webauthn.helpers.exceptions import InvalidRegistrationResponse

    expected_challenge = read_challenge_token(
        challenge_token, kind="reg", user_id=int(user.id)
    )
    if not expected_challenge:
        raise WebAuthnVerifyError("Registration challenge expired or missing — try again")

    if count_passkeys(session, int(user.id)) >= MAX_CREDENTIALS_PER_USER:
        raise WebAuthnConfigError(
            f"Maximum of {MAX_CREDENTIALS_PER_USER} passkeys per account"
        )

    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=resolve_rp_id(),
            expected_origin=resolve_expected_origin(),
            require_user_verification=False,
        )
    except InvalidRegistrationResponse as e:
        logger.info("WebAuthn registration failed user=%s: %s", user.id, e)
        raise WebAuthnVerifyError(str(e) or "Invalid registration response") from e
    except Exception as e:
        logger.warning("WebAuthn registration error user=%s: %s", user.id, e)
        raise WebAuthnVerifyError("Could not verify passkey registration") from e

    cred_id_b64 = _b64url_encode(verification.credential_id)
    # Reject duplicates
    dup = session.exec(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == cred_id_b64)
    ).first()
    if dup:
        raise WebAuthnVerifyError("This passkey is already registered")

    nick = (nickname or "").strip()[:128] or None
    if not nick:
        nick = f"Passkey {datetime.utcnow().strftime('%Y-%m-%d')}"

    aaguid = None
    try:
        if verification.aaguid:
            aaguid = str(verification.aaguid)
    except Exception:
        aaguid = None

    row = WebAuthnCredential(
        user_id=int(user.id),
        credential_id=cred_id_b64,
        public_key=_b64url_encode(verification.credential_public_key),
        sign_count=int(verification.sign_count or 0),
        transports=_transports_json(getattr(verification, "credential_device_type", None) and None),
        nickname=nick,
        aaguid=aaguid,
        backup_eligible=bool(getattr(verification, "credential_backup_eligible", False)),
        backup_state=bool(getattr(verification, "credential_backup_state", False)),
        created_at=datetime.utcnow(),
    )
    # transports may be on the client response, not verification
    try:
        resp_trans = None
        if isinstance(credential, dict):
            resp_trans = credential.get("response", {}).get("transports") or credential.get(
                "transports"
            )
        if resp_trans:
            row.transports = json.dumps([str(t) for t in resp_trans])[:256]
    except Exception:
        pass

    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def authentication_options_json(
    session: Session, user: User
) -> Tuple[str, str]:
    """Return (options_json, challenge_cookie_value) for login step-up."""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        UserVerificationRequirement,
    )

    creds = list_credentials(session, int(user.id))
    if not creds:
        raise WebAuthnConfigError("No passkeys registered for this account")

    allow = []
    for c in creds:
        try:
            allow.append(
                PublicKeyCredentialDescriptor(
                    id=_b64url_decode(c.credential_id),
                    transports=_transports_list(c.transports) or None,
                )
            )
        except Exception:
            continue
    if not allow:
        raise WebAuthnConfigError("No usable passkeys for this account")

    options = generate_authentication_options(
        rp_id=resolve_rp_id(),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    chal_b64 = _b64url_encode(options.challenge)
    token = mint_challenge_token(kind="auth", user_id=int(user.id), challenge_b64=chal_b64)
    return options_to_json(options), token


def verify_authentication(
    session: Session,
    user: User,
    credential: Dict[str, Any],
    challenge_token: Optional[str],
) -> WebAuthnCredential:
    from webauthn import verify_authentication_response
    from webauthn.helpers.exceptions import InvalidAuthenticationResponse

    expected_challenge = read_challenge_token(
        challenge_token, kind="auth", user_id=int(user.id)
    )
    if not expected_challenge:
        raise WebAuthnVerifyError("Passkey challenge expired or missing — try again")

    # Identify which credential was used
    raw_id = credential.get("rawId") or credential.get("id")
    if not raw_id:
        raise WebAuthnVerifyError("Missing credential id")
    try:
        if isinstance(raw_id, str) and not raw_id.startswith("http"):
            # already base64url from browser JSON
            cred_id_b64 = raw_id.replace("+", "-").replace("/", "_").rstrip("=")
            # browser may send standard base64url already
            try:
                _b64url_decode(raw_id)
                cred_id_b64 = raw_id
            except Exception:
                cred_id_b64 = _b64url_encode(bytes(raw_id, "utf-8"))
        else:
            cred_id_b64 = _b64url_encode(raw_id)  # type: ignore[arg-type]
    except Exception:
        cred_id_b64 = str(raw_id)

    # Prefer lookup by decoding id field properly
    row: Optional[WebAuthnCredential] = None
    id_field = credential.get("id") or credential.get("rawId")
    if isinstance(id_field, str):
        row = session.exec(
            select(WebAuthnCredential).where(
                WebAuthnCredential.credential_id == id_field,
                WebAuthnCredential.user_id == int(user.id),
            )
        ).first()
        if not row:
            # try normalize padding
            for cand in (id_field, id_field.rstrip("="), cred_id_b64):
                row = session.exec(
                    select(WebAuthnCredential).where(
                        WebAuthnCredential.credential_id == cand,
                        WebAuthnCredential.user_id == int(user.id),
                    )
                ).first()
                if row:
                    break

    if not row:
        # Fall back: try all user credentials matching decoded bytes
        try:
            want = _b64url_decode(str(id_field))
            for c in list_credentials(session, int(user.id)):
                if _b64url_decode(c.credential_id) == want:
                    row = c
                    break
        except Exception:
            pass

    if not row:
        raise WebAuthnVerifyError("Unknown passkey for this account")

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=resolve_rp_id(),
            expected_origin=resolve_expected_origin(),
            credential_public_key=_b64url_decode(row.public_key),
            credential_current_sign_count=int(row.sign_count or 0),
            require_user_verification=False,
        )
    except InvalidAuthenticationResponse as e:
        logger.info("WebAuthn auth failed user=%s: %s", user.id, e)
        raise WebAuthnVerifyError(str(e) or "Invalid passkey assertion") from e
    except Exception as e:
        logger.warning("WebAuthn auth error user=%s: %s", user.id, e)
        raise WebAuthnVerifyError("Could not verify passkey") from e

    new_count = int(verification.new_sign_count or 0)
    # Clone/sign-count: accept equal when authenticator reports 0 (some platform keys)
    if new_count and new_count < int(row.sign_count or 0):
        raise WebAuthnVerifyError("Passkey sign count went backwards — possible clone")
    row.sign_count = max(new_count, int(row.sign_count or 0))
    row.last_used_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def credential_public_dict(row: WebAuthnCredential) -> Dict[str, Any]:
    return {
        "id": row.id,
        "nickname": row.nickname or "Passkey",
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() + "Z" if row.last_used_at else None,
        "aaguid": row.aaguid,
        "backup_eligible": bool(row.backup_eligible),
        "backup_state": bool(row.backup_state),
    }
