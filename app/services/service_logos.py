"""Service logo storage + favicon discovery under DATA_ROOT/service_logos/."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

ALLOWED_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/svg+xml": ".svg",
    "image/gif": ".gif",
}
MAX_BYTES = min(getattr(settings, "AVATAR_MAX_BYTES", 2 * 1024 * 1024), 512 * 1024)
DISCOVER_TIMEOUT = 8.0

_ICON_LINK_RE = re.compile(
    r"""<link[^>]+rel=["'][^"']*(?:icon|apple-touch-icon)[^"']*["'][^>]*>""",
    re.I,
)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)


def logo_dir() -> Path:
    p = Path(settings.DATA_ROOT or "/data") / "service_logos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def detect_image_type(data: bytes, content_type: str = "") -> Optional[str]:
    if len(data) < 4:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"\x00\x00\x01\x00" or data[:4] == b"\x00\x00\x02\x00":
        return "image/x-icon"
    head = data[:200].lstrip().lower()
    if head.startswith(b"<svg") or b"<svg" in data[:500].lower():
        return "image/svg+xml"
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_EXT:
        return ct
    return None


def absolute_logo_path(rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    rel = rel.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        return None
    full = (Path(settings.DATA_ROOT or "/data") / rel).resolve()
    root = Path(settings.DATA_ROOT or "/data").resolve()
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
        ".ico": "image/x-icon",
        ".svg": "image/svg+xml",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")


def delete_logo_files(binding_id: int) -> None:
    d = logo_dir()
    for p in d.glob(f"{int(binding_id)}.*"):
        try:
            p.unlink()
        except OSError:
            pass


def save_logo_bytes(binding_id: int, data: bytes, content_type: str = "") -> str:
    """Validate and store logo. Returns relative path service_logos/{id}.ext."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"Logo too large (max {MAX_BYTES // 1024} KB)")
    mime = detect_image_type(data, content_type)
    if not mime or mime not in ALLOWED_EXT:
        raise ValueError("Logo must be JPEG, PNG, WebP, GIF, ICO, or SVG")
    ext = ALLOWED_EXT[mime]
    delete_logo_files(binding_id)
    rel = f"service_logos/{int(binding_id)}{ext}"
    dest = Path(settings.DATA_ROOT or "/data") / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


def logo_public_url(binding_id: int) -> str:
    return f"/services/logo/{int(binding_id)}"


def _fetch_bytes(client: httpx.Client, url: str) -> Optional[tuple[bytes, str]]:
    try:
        r = client.get(url)
        if r.status_code != 200:
            return None
        data = r.content or b""
        if not data or len(data) > MAX_BYTES:
            return None
        ct = r.headers.get("content-type") or ""
        if detect_image_type(data, ct):
            return data, ct
    except Exception:
        return None
    return None


def discover_logo_from_url(service_url: str) -> Optional[tuple[bytes, str]]:
    """Try to fetch a favicon / apple-touch-icon for the service URL."""
    url = (service_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
        origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None

    candidates: list[str] = []
    try:
        with httpx.Client(
            timeout=DISCOVER_TIMEOUT,
            follow_redirects=True,
            verify=True,
            headers={"User-Agent": "PiHerder/0.2 service-logo"},
        ) as client:
            # Parse HTML for icon links
            try:
                page = client.get(url)
                if page.status_code == 200 and page.text:
                    for m in _ICON_LINK_RE.finditer(page.text[:200_000]):
                        tag = m.group(0)
                        hm = _HREF_RE.search(tag)
                        if hm:
                            candidates.append(urljoin(str(page.url), hm.group(1)))
            except Exception:
                pass
            candidates.extend(
                [
                    urljoin(origin + "/", "apple-touch-icon.png"),
                    urljoin(origin + "/", "favicon.ico"),
                    urljoin(origin + "/", "favicon.png"),
                ]
            )
            seen = set()
            for cand in candidates:
                if cand in seen:
                    continue
                seen.add(cand)
                hit = _fetch_bytes(client, cand)
                if hit:
                    return hit
    except Exception as e:
        logger.debug("logo discover failed for %s: %s", url, e)
    return None


def try_discover_and_save(binding_id: int, service_url: str) -> Optional[str]:
    """Discover favicon and save. Returns relative path or None."""
    hit = discover_logo_from_url(service_url)
    if not hit:
        return None
    data, ct = hit
    try:
        return save_logo_bytes(binding_id, data, ct)
    except Exception as e:
        logger.debug("logo save failed: %s", e)
        return None
