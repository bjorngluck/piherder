from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import hashlib
import secrets
import hmac
import io
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select
from ..models import User, TotpBackupCode, TrustedDevice
from ..database import get_session
from ..config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

PENDING_2FA_MINUTES = 10
BACKUP_CODE_COUNT = 10


def verify_password(plain: str, hashed: str) -> bool:
    # Truncate input to 72 bytes to be consistent with hashing (prevents library errors on long passwords).
    if isinstance(plain, str):
        plain = plain.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.verify(plain, hashed)


def get_password_hash(password: str) -> str:
    # bcrypt has a hard 72-byte limit on the password (from the bcrypt C lib / passlib).
    # Truncate here to prevent the library from ever raising "password cannot be longer than 72 bytes".
    # See: https://passlib.readthedocs.io/en/stable/lib/passlib.hash.bcrypt.html
    if isinstance(password, str):
        password = password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_pending_2fa_token(user_id: int) -> str:
    """Short-lived token after password OK, before TOTP. Does not grant full access."""
    return create_access_token(
        {"sub": str(user_id), "2fa_pending": True},
        expires_delta=timedelta(minutes=PENDING_2FA_MINUTES),
    )


def decode_token_payload(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def _extract_token(request: Request, token: Optional[str]) -> Optional[str]:
    auth_token = token
    if not auth_token:
        cookie = request.cookies.get("access_token")
        if cookie:
            if cookie.startswith("Bearer "):
                auth_token = cookie.split(" ", 1)[1]
            else:
                auth_token = cookie
    return auth_token


# RBAC roles (lowest → highest privilege)
ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
VALID_ROLES = frozenset({ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN})
ROLE_RANK = {ROLE_VIEWER: 1, ROLE_OPERATOR: 2, ROLE_ADMIN: 3}

# Viewers may mutate only self-service / soft paths (not fleet jobs)
_VIEWER_WRITE_PREFIXES = (
    "/auth/logout",
    "/auth/account",
    "/auth/2fa",
    "/auth/force-password",
    "/auth/force-2fa",
    "/auth/me/",
    "/notifications/",
    "/api/push",
)
# Admin-only management surfaces
_ADMIN_ONLY_PREFIXES = (
    "/auth/users",
)

# Paths allowed while must_change_password is set
_FORCE_PASSWORD_ALLOW = (
    "/auth/force-password",
    "/auth/logout",
    "/auth/login",
    "/static",
    "/favicon.ico",
    "/sw.js",
    "/manifest.webmanifest",
    "/health",
)
# Paths allowed while force_2fa policy and user has no 2FA yet
_FORCE_2FA_ALLOW = (
    "/auth/force-2fa",
    "/auth/account",
    "/auth/logout",
    "/auth/login",
    "/auth/me/",
    "/static",
    "/favicon.ico",
    "/health",
)


class OnboardingRedirect(Exception):
    """Raised from get_current_user to force password / 2FA onboarding."""

    def __init__(self, location: str):
        self.location = location
        super().__init__(location)


def force_2fa_required() -> bool:
    """Global policy: every user must enable TOTP before using the app."""
    try:
        from ..services.app_settings import force_2fa_enabled
        return force_2fa_enabled()
    except Exception:
        return False


def _path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") or path.startswith(p) for p in prefixes)


def post_login_path(user: User) -> str:
    """Where to send the browser after a successful login / 2FA."""
    if getattr(user, "must_change_password", False):
        return "/auth/force-password"
    if force_2fa_required() and not getattr(user, "totp_enabled", False):
        return "/auth/force-2fa"
    return "/"


def normalize_role(role: str | None) -> str:
    r = (role or ROLE_ADMIN).strip().lower()
    return r if r in VALID_ROLES else ROLE_ADMIN


def user_role(user: User) -> str:
    return normalize_role(getattr(user, "role", None))


def role_at_least(user: User, min_role: str) -> bool:
    return ROLE_RANK.get(user_role(user), 0) >= ROLE_RANK.get(min_role, 99)


def count_active_admins(session: Session) -> int:
    """How many active users currently have the admin role."""
    users = session.exec(select(User).where(User.is_active == True)).all()  # noqa: E712
    return sum(1 for u in users if user_role(u) == ROLE_ADMIN)


def is_sole_admin(session: Session, user: User) -> bool:
    """True if this user is the only active admin (must not demote/disable)."""
    if user_role(user) != ROLE_ADMIN:
        return False
    return count_active_admins(session) <= 1


def _viewer_write_allowed(path: str) -> bool:
    return any(path.startswith(p) for p in _VIEWER_WRITE_PREFIXES)


def _admin_only_path(path: str) -> bool:
    return any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES)


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Please log in to continue",
        headers={"WWW-Authenticate": "Bearer"},
    )

    auth_token = _extract_token(request, token)
    if not auth_token:
        raise credentials_exception

    try:
        payload = jwt.decode(auth_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # Pending 2FA tokens must not grant access
        if payload.get("2fa_pending"):
            raise credentials_exception
        user_id: Optional[int] = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exception

    path = request.url.path or ""

    # First-login password change gate
    if getattr(user, "must_change_password", False) and not _path_allowed(
        path, _FORCE_PASSWORD_ALLOW
    ):
        raise OnboardingRedirect("/auth/force-password")

    # Global force-2FA gate (after password is OK)
    if (
        not getattr(user, "must_change_password", False)
        and force_2fa_required()
        and not getattr(user, "totp_enabled", False)
        and not _path_allowed(path, _FORCE_2FA_ALLOW)
    ):
        raise OnboardingRedirect("/auth/force-2fa")

    # Enforce RBAC for mutating methods (GET stays open for all logged-in roles)
    method = (request.method or "GET").upper()
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        role = user_role(user)
        if _admin_only_path(path) and role != ROLE_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin role required",
            )
        if role == ROLE_VIEWER and not _viewer_write_allowed(path):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Read-only account — operator or admin required for this action",
            )
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Dependency for admin-only routes (GET included)."""
    if user_role(user) != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


def get_operator_user(user: User = Depends(get_current_user)) -> User:
    """Dependency requiring operator or admin (GET included)."""
    if not role_at_least(user, ROLE_OPERATOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator or admin role required",
        )
    return user


def get_optional_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session)
) -> Optional[User]:
    """Use when login is optional (e.g. public landing page)."""
    try:
        return get_current_user(request, token, session)
    except HTTPException:
        return None


def authenticate_user(session: Session, email: str, password: str) -> Optional[User]:
    statement = select(User).where(User.email == email)
    user = session.exec(statement).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


# --- TOTP / backup codes / trusted devices ---

def generate_totp_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def encrypt_totp_secret(plain: str) -> str:
    from .encryption import encrypt_str
    return encrypt_str(plain)


def decrypt_totp_secret(cipher: str) -> str:
    from .encryption import decrypt_str
    return decrypt_str(cipher)


def totp_provisioning_uri(secret: str, email: str) -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="PiHerder")


def totp_qr_svg(uri: str, box_size: int = 6, border: int = 2) -> str:
    """Return SVG markup for a TOTP QR (no Pillow required — pure qrcode SVG backend)."""
    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image()
    # SvgPathImage exposes .to_string() in recent qrcode; fall back to save()
    if hasattr(img, "to_string"):
        raw = img.to_string()
        return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def totp_qr_data_uri(uri: str) -> Optional[str]:
    """SVG as data URI for <img src=...>. Prefer inline SVG when possible."""
    try:
        import base64
        svg = totp_qr_svg(uri)
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return None


def verify_totp_code(secret: str, code: str) -> bool:
    import pyotp
    if not code:
        return False
    code = code.strip().replace(" ", "")
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> List[str]:
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def replace_backup_codes(session: Session, user_id: int, codes: List[str]) -> None:
    old = session.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == user_id)).all()
    for row in old:
        session.delete(row)
    for code in codes:
        session.add(TotpBackupCode(user_id=user_id, code_hash=hash_backup_code(code)))
    session.commit()


def consume_backup_code(session: Session, user_id: int, code: str) -> bool:
    h = hash_backup_code(code)
    row = session.exec(
        select(TotpBackupCode).where(
            TotpBackupCode.user_id == user_id,
            TotpBackupCode.code_hash == h,
            TotpBackupCode.used_at.is_(None),
        )
    ).first()
    if not row:
        return False
    row.used_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return True


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_trusted_device(
    session: Session,
    user_id: int,
    *,
    label: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
    days: Optional[int] = None,
) -> Tuple[str, TrustedDevice]:
    raw = secrets.token_urlsafe(32)
    days = days if days is not None else settings.TRUSTED_DEVICE_DAYS
    dev = TrustedDevice(
        user_id=user_id,
        token_hash=hash_device_token(raw),
        label=label or "Trusted device",
        user_agent=(user_agent or "")[:300] or None,
        ip=ip,
        expires_at=datetime.utcnow() + timedelta(days=days),
        last_used_at=datetime.utcnow(),
    )
    session.add(dev)
    session.commit()
    session.refresh(dev)
    return raw, dev


def find_valid_trusted_device(session: Session, user_id: int, raw_token: str) -> Optional[TrustedDevice]:
    if not raw_token:
        return None
    h = hash_device_token(raw_token)
    dev = session.exec(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user_id,
            TrustedDevice.token_hash == h,
        )
    ).first()
    if not dev:
        return None
    if dev.expires_at < datetime.utcnow():
        return None
    dev.last_used_at = datetime.utcnow()
    session.add(dev)
    session.commit()
    return dev


def revoke_trusted_device(session: Session, user_id: int, device_id: int) -> bool:
    dev = session.get(TrustedDevice, device_id)
    if not dev or dev.user_id != user_id:
        return False
    session.delete(dev)
    session.commit()
    return True


def revoke_all_trusted_devices(session: Session, user_id: int) -> int:
    rows = list(session.exec(select(TrustedDevice).where(TrustedDevice.user_id == user_id)).all())
    for d in rows:
        session.delete(d)
    if rows:
        session.commit()
    return len(rows)


def list_trusted_devices(session: Session, user_id: int) -> List[TrustedDevice]:
    return list(
        session.exec(
            select(TrustedDevice)
            .where(TrustedDevice.user_id == user_id)
            .order_by(TrustedDevice.created_at.desc())
        ).all()
    )


# Simple in-memory rate limit for auth endpoints (per process)
_auth_attempts: dict = {}


def rate_limit_auth(key: str, max_attempts: int = 20, window_seconds: int = 300) -> bool:
    """Return True if allowed, False if rate limited."""
    now = datetime.utcnow().timestamp()
    bucket = _auth_attempts.get(key, [])
    bucket = [t for t in bucket if now - t < window_seconds]
    if len(bucket) >= max_attempts:
        _auth_attempts[key] = bucket
        return False
    bucket.append(now)
    _auth_attempts[key] = bucket
    return True
