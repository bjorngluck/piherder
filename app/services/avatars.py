"""Avatar upload storage under DATA_ROOT/avatars/."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from ..config import settings

ALLOWED_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
# Magic bytes
_SIGS = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",  # refined below
}


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
    """Validate and store avatar. Returns relative path (avatars/{id}.ext)."""
    if len(data) > settings.AVATAR_MAX_BYTES:
        raise ValueError(f"Avatar too large (max {settings.AVATAR_MAX_BYTES // 1024} KB)")
    mime = detect_image_type(data)
    if not mime or mime not in ALLOWED_EXT:
        raise ValueError("Avatar must be JPEG, PNG, or WebP")
    ext = ALLOWED_EXT[mime]
    # Remove any previous avatar for this user
    delete_avatar_files(user_id)
    rel = f"avatars/{user_id}{ext}"
    dest = Path(settings.DATA_ROOT) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


def delete_avatar_files(user_id: int) -> None:
    d = avatar_dir()
    for p in d.glob(f"{user_id}.*"):
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
