# PiHerder brand images

**Source master:** `source/PiHerder_Logo_T.png` (full-colour herder raspberry).

Regenerate all app/wiki sizes:

```bash
python3 scripts/generate_brand_assets.py
```

## App mark (nav / login / PWA)

| File | Theme | Notes |
|------|--------|--------|
| `piherder-mark.png` (+ `-64` / `-128`) | **Light** | Full-colour mark on transparent square |
| `piherder-mark-dark.png` (+ `-64` / `-128`) | **Dark** | Near-black body + circuit ink recolored light; red/green/brown kept |

UI swaps `src` via `img.ph-theme-logo` + `data-logo-light` / `data-logo-dark`.

## About / README / wiki hero

| File | Theme | Notes |
|------|--------|--------|
| `piherder-about.png` / `piherder-logo.png` | Light | Same mark (square) for About + README |
| `piherder-about-dark.png` / `piherder-logo-dark.png` | Dark | Dark-theme ink treatment |

Legacy `piherder-wordmark*.png` may remain for historical refs; primary UI uses **mark** assets above.

## Favicon & PWA

| File | Role |
|------|------|
| `../favicon.png` / `../favicon.ico` | Browser tab (face-cropped; **light plate** `#f8f9fa` — not transparent/black) |
| `../icons/icon-192.png`, `icon-512.png` | PWA `any` purpose (same light plate) |
| `../icons/icon-maskable-*.png` | PWA maskable (safe-zone padding on light plate) |
| `../icons/apple-touch-icon.png` | iOS home screen (light plate) |

Wiki copies live under `wiki/assets/` (including `favicon.png` for MkDocs).
