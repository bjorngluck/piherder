# PR: v1.6.0-dev → main

**Title:** `v1.6.0: HACS fleet, console mux, CSP nonce, fail-path undo`

**Base:** `main` · **Head:** `v1.6.0-dev` · **Tag:** `v1.6.0` (only after this PR is no longer a draft)

**State:** **Ready.** Package **`1.6.0`**. Operator QA signed. Screenshot pack is in the wiki. Release notes: [RELEASE_v1.6.0.md](RELEASE_v1.6.0.md). Tag and Hub follow merge. `PIHERDER_SERVICE_MIGRATE` stays **false**.

---

## Summary

Sixth minor after production **v1.5.0**. Home Assistant can observe the fleet through a separate HACS integration. Web SSH can opt a Debian host into `tmux` or `screen`. Inline scripts use a per-request nonce. A Move that failed after names flipped can be undone. Last measured unit coverage on `app` is **75.01%** (36304/48400). CI fail-under is **75**.

Design: [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Maintainer ticks: [QA_v1.6.0.md](QA_v1.6.0.md). User-facing notes: [RELEASE_v1.6.0.md](RELEASE_v1.6.0.md).

| Stream | Highlights |
|--------|------------|
| **HA-p2 Slice 1** (Must) | HACS [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.4**. Token `read`. Fleet sensors, one host device, one **Visit**. Lovelace fleet card. Not in this image |
| **Slice 1b** (Should) | `GET /api/v1/inventory` and `/services`. Container, service, and disk sensors on the host device. No start/stop |
| **Mux-1** (Must) | Per-host **Console mux**. `tmux` then `screen`, else a plain PTY. Hide detaches; ✕ kills. Never apt-install. HAOS and demo never mux |
| **Q-80** (Must) | Last compose **75.01%** (36304/48400) after `_q38`. CI `--cov-fail-under=75` |
| **Docs-archive-0x** (Should) | v0 PLAN/RELEASE under `docs/archive/v0/`; stubs at the old paths |
| **CSP-n Slice 1** (Should) | `script-src` nonce. `script-src-attr 'unsafe-inline'` keeps `onclick`. Home enforces. Public demo is Report-Only |
| **Undo-1** (Should) | `service_migrate_undo` after cutover / rebind / validate. Preview then confirm. Dest `compose stop`, never `down -v`. Green Move has no Undo |
| **M-flag** | **`PIHERDER_SERVICE_MIGRATE` stays false** |

Discover (not in this PR’s scope): HA Slice 2, Undo-2, Mux-2. Parked on **v1.7:** Brand, AC-fg, J-runtime, HA Slice 3, plugin-in-image, default-on Move.

## Migrations (apply on deploy)

| Rev | What |
|-----|------|
| **043** | `server.console_mux_enabled` |
| **044** | Host-facts snapshot (pretty OS, hardware) |
| **045** | CPU / memory / disk columns |

Recreate **web** and **celery-worker** after pull. App code is not bind-mounted.

## Test plan

Walk [QA_v1.6.0.md](QA_v1.6.0.md). Stream rows are signed.

- [x] Unit suite ≥ **75%**; CI fail-under **75**
- [x] Mux-1 on a host that already has tmux or screen
- [x] HA-p2 Slice 1 + Slice 1b on plugin **0.2.4**
- [x] CSP enforce locally; public demo stays Report-Only
- [x] Undo-1 on a disposable pair, then turn the Move flag off again
- [x] 1.5 regression (web recycle mid-Move, Reports, Sign in, reboot flag)
- [x] Docs-archive-0x
- [x] Screenshot pack in [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v160--pack-status)
- [x] `.venv-docs/bin/mkdocs build --strict`

## Out of scope

- Enforcing CSP on the public demo
- Turning Move on by default
- HA backup / start-stop / Move-from-HA
- Mux leftover list in the UI
- Brand chrome and per-feature grants

## Merge checklist

- [x] Operator QA signed (every in-scope stream, 2026-09-24)
- [x] Version bump `app/version_info.py` + `pyproject.toml` → **1.6.0**
- [x] `docs/RELEASE_v1.6.0.md` + wiki Home current-release row
- [x] Merge `v1.6.0-dev` → `main`
- [x] Tag **`v1.6.0`** · Hub `1.6.0` / `1.6` / `latest` (`sha256:cdf88c70099f78830943e6529f05eff1b83bb5565e7b12f71ebf0877c7b018a8`)
- [x] Keep `1.5` / `1.5.0` pins valid
- [x] Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false**

## After merge

Hub publish per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md). GitHub Release body = `docs/RELEASE_v1.6.0.md` (workflow uses that file when the tag is pushed). The public demo already tracks `v1.6.0-dev`; after the tag, point that clone at `main` or the tag when asked.
