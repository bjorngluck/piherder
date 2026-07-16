# PiHerder brand images

## App mark (nav / login)

**Source:** `light-logo.png` (black ink / dark lines).

| File | Theme | Notes |
|------|--------|--------|
| `piherder-mark.png` (+ `-64` / `-128`) | **Light** | Black body + black circuits |
| `piherder-mark-dark.png` (+ `-64` / `-128`) | **Dark** | Same art, black ink recolored to light — **no white box** |

UI swaps `src` via `img.ph-theme-logo` + `data-logo-light` / `data-logo-dark`.

## Wordmark (About / README / wiki)

| File | Theme | Notes |
|------|--------|--------|
| `piherder-about.png` | Light | Transparent wordmark (no plate) — soft blend on hero |
| `piherder-about-dark.png` | Dark | Black ink → light (same as menu mark treatment) |
| `piherder-logo.png` | Light | Alias of about light (README / wiki) |
| `piherder-logo-dark.png` | Dark | Alias of about dark |
| `piherder-wordmark*.png` | — | Older lockups / wide variants |

About page: `img.ph-theme-logo` swaps about / about-dark.

Wiki: same names under `wiki/assets/`.
