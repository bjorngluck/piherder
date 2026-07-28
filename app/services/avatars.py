"""Avatar upload storage under DATA_ROOT/avatars/."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from ..config import settings

ALLOWED_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
# All extensions we may have written (incl. legacy .jpeg)
_ALL_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def avatar_dir() -> Path:
    p = Path(settings.DATA_ROOT) / "avatars"
    p.mkdir(parents=True, exist_ok=True)
    return p


def detect_image_type(data: bytes) -> Optional[str]:
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_avatar(user_id: int, data: bytes) -> str:
    """Validate and store avatar. Returns relative path (avatars/{id}.ext).

    Writes via temp + rename so a failed upload cannot leave a half-written
    file. Only this user's files are removed (exact ``{id}.ext`` names).
    """
    if len(data) > settings.AVATAR_MAX_BYTES:
        raise ValueError(f"Avatar too large (max {settings.AVATAR_MAX_BYTES // 1024} KB)")
    mime = detect_image_type(data)
    if not mime or mime not in ALLOWED_EXT:
        raise ValueError("Avatar must be JPEG, PNG, or WebP")
    ext = ALLOWED_EXT[mime]
    uid = int(user_id)
    rel = f"avatars/{uid}{ext}"
    dest = Path(settings.DATA_ROOT) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Atomic-ish write: temp in same dir then replace
    fd, tmp_name = tempfile.mkstemp(prefix=f".avatar-{uid}-", suffix=ext, dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    # Remove other formats for this user only (e.g. old .png after .jpg upload)
    for other_ext in _ALL_EXTS:
        if other_ext == ext:
            continue
        other = dest.parent / f"{uid}{other_ext}"
        if other.is_file():
            try:
                other.unlink()
            except OSError:
                pass
    return rel


def delete_avatar_files(user_id: int) -> None:
    """Remove avatar files for exactly this user id (never ``1`` when id is ``10``)."""
    d = avatar_dir()
    uid = int(user_id)
    for ext in _ALL_EXTS:
        p = d / f"{uid}{ext}"
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


def absolute_avatar_path(rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    # Prevent path traversal
    rel = rel.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        return None
    full = (Path(settings.DATA_ROOT) / rel).resolve()
    root = Path(settings.DATA_ROOT).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        return None
    if not full.is_file():
        return None
    return full


def content_type_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


def user_has_avatar(user) -> bool:
    """True when DB path is set *and* the file exists under DATA_ROOT."""
    return absolute_avatar_path(getattr(user, "avatar_path", None)) is not None


def avatar_img_url(user) -> str:
    """Cache-busted, user-scoped URL so browsers never reuse another account's image.

    Same path ``/auth/me/avatar`` is session-scoped, but without a query string
    HTTP caches / bfcache often show the previous user's photo after switch.
    """
    if not user_has_avatar(user):
        return ""
    uid = int(user.id) if getattr(user, "id", None) is not None else 0
    v = 0
    updated = getattr(user, "updated_at", None)
    if updated is not None:
        try:
            v = int(updated.timestamp())
        except (AttributeError, OSError, ValueError, TypeError):
            v = 0
    if not v and getattr(user, "avatar_path", None):
        # Stable-ish bust from path string when updated_at missing
        v = abs(hash(user.avatar_path)) % 1_000_000_000
    return f"/auth/me/avatar?u={uid}&v={v}"
