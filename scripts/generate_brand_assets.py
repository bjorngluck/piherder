#!/usr/bin/env python3
"""Generate PiHerder favicon, light/dark marks, and PWA icons from the master logo.

Usage (from repo root):
  python3 scripts/generate_brand_assets.py
  python3 scripts/generate_brand_assets.py /path/to/PiHerder_Logo_T.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "app/static/images/source/PiHerder_Logo_T.png"
# Fallback: workspace upload at repo root
FALLBACK_SRC = ROOT / "PiHerder_Logo_T.png"
IMG = ROOT / "app/static/images"
STATIC = ROOT / "app/static"
ICONS = STATIC / "icons"
WIKI = ROOT / "wiki/assets"
SRC_DIR = IMG / "source"

DARK_INK = (236, 238, 242)
DARK_INK_DIM = (200, 204, 212)


def load_cropped(path: Path, pad_frac: float = 0.04) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    bbox = im.getbbox()
    if not bbox:
        raise SystemExit(f"empty image: {path}")
    im = im.crop(bbox)
    w, h = im.size
    pad = int(max(w, h) * pad_frac)
    canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    canvas.paste(im, (pad, pad), im)
    return canvas


def to_square(im: Image.Image, size: int, margin_frac: float = 0.08) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    max_inner = int(size * (1 - 2 * margin_frac))
    w, h = im.size
    scale = min(max_inner / w, max_inner / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2), resized)
    return canvas


def face_crop(im: Image.Image, bottom_keep: float = 0.78) -> Image.Image:
    """Drop lower circuit fan for tiny favicons."""
    w, h = im.size
    return im.crop((0, 0, w, max(1, int(h * bottom_keep))))


def is_dark_ink(r: int, g: int, b: int, a: int) -> bool:
    if a < 16:
        return False
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    mx, mn = max(r, g, b), min(r, g, b)
    sat = (mx - mn) / mx if mx else 0.0
    if lum <= 48 and sat <= 0.35:
        return True
    if lum <= 22:
        return True
    return False


def make_dark(im: Image.Image) -> Image.Image:
    px = im.load()
    out = im.copy()
    opx = out.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0 or not is_dark_ink(r, g, b, a):
                continue
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            ink = DARK_INK if lum < 18 else DARK_INK_DIM
            opx[x, y] = (*ink, a)
    return out


def save(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", optimize=True)
    print(f"  {path.relative_to(ROOT)}  {im.size[0]}x{im.size[1]}  {path.stat().st_size}B")


def maskable(
    im_square: Image.Image,
    size: int,
    bg=(14, 16, 20, 255),
    content_frac: float = 0.72,
) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), bg)
    max_inner = int(size * content_frac)
    bbox = im_square.getbbox() or (0, 0, *im_square.size)
    cropped = im_square.crop(bbox)
    cw, ch = cropped.size
    scale = min(max_inner / cw, max_inner / ch)
    nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
    resized = cropped.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2), resized)
    return canvas


def favicon_ico(base: Image.Image, path: Path) -> None:
    sizes = [(16, 16), (32, 32), (48, 48)]
    imgs = [base.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    imgs[-1].save(path, format="ICO", sizes=sizes, append_images=imgs[:-1])
    print(f"  {path.relative_to(ROOT)}  ico  {path.stat().st_size}B")


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if src is None:
        src = DEFAULT_SRC if DEFAULT_SRC.is_file() else FALLBACK_SRC
    if not src.is_file():
        raise SystemExit(f"Source logo not found: {src}")

    print("Source:", src)
    base = load_cropped(src)
    light, dark = base, make_dark(base)

    max_side = 1024
    w, h = light.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1:
        light_full = light.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        dark_full = dark.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    else:
        light_full, dark_full = light, dark

    SRC_DIR.mkdir(parents=True, exist_ok=True)
    save(light_full, SRC_DIR / "PiHerder_Logo_T.png")

    mark_l = to_square(light, 1024, 0.06)
    mark_d = to_square(dark, 1024, 0.06)

    for path, im in {
        IMG / "piherder-mark.png": mark_l,
        IMG / "piherder-mark-dark.png": mark_d,
        IMG / "piherder-mark-128.png": to_square(light, 128, 0.08),
        IMG / "piherder-mark-dark-128.png": to_square(dark, 128, 0.08),
        IMG / "piherder-mark-64.png": to_square(light, 64, 0.08),
        IMG / "piherder-mark-dark-64.png": to_square(dark, 64, 0.08),
        IMG / "piherder-logo-header.png": to_square(light, 128, 0.08),
        IMG / "piherder-logo-small.png": to_square(light, 64, 0.08),
        IMG / "piherder-about.png": mark_l,
        IMG / "piherder-about-dark.png": mark_d,
        IMG / "piherder-logo.png": mark_l,
        IMG / "piherder-logo-dark.png": mark_d,
    }.items():
        save(im, path)

    face = face_crop(light)
    fav32 = to_square(face, 32, 0.03)
    fav48 = to_square(face, 48, 0.03)
    save(fav32, STATIC / "favicon.png")
    favicon_ico(fav48, STATIC / "favicon.ico")

    save(to_square(light, 192, 0.08), ICONS / "icon-192.png")
    save(to_square(light, 512, 0.06), ICONS / "icon-512.png")
    save(maskable(mark_l, 192), ICONS / "icon-maskable-192.png")
    save(maskable(mark_l, 512), ICONS / "icon-maskable-512.png")
    save(
        maskable(mark_l, 180, bg=(248, 249, 250, 255), content_frac=0.82),
        ICONS / "apple-touch-icon.png",
    )

    for name in (
        "piherder-mark.png",
        "piherder-mark-dark.png",
        "piherder-mark-128.png",
        "piherder-mark-dark-128.png",
        "piherder-mark-64.png",
        "piherder-mark-dark-64.png",
        "piherder-about.png",
        "piherder-about-dark.png",
        "piherder-logo.png",
        "piherder-logo-dark.png",
        "piherder-logo-header.png",
        "piherder-logo-small.png",
    ):
        save(Image.open(IMG / name), WIKI / name)
    save(fav32, WIKI / "favicon.png")
    print("Done.")


if __name__ == "__main__":
    main()
