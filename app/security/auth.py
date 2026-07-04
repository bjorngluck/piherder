from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select
from ..models import User
from ..database import get_session
from ..config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


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

    # Try header first (for API), then cookie (for UI)
    auth_token = token
    if not auth_token:
        # Check cookie set by login form (raw token)
        cookie = request.cookies.get("access_token")
        if cookie:
            if cookie.startswith("Bearer "):
                auth_token = cookie.split(" ", 1)[1]
            else:
                auth_token = cookie

    if not auth_token:
        raise credentials_exception

    try:
        payload = jwt.decode(auth_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: Optional[int] = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exception
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
