# PR: v1.6.0-dev → main

**Title:** `v1.6.0: HACS fleet, console mux, CSP nonce, fail-path undo`

**Base:** `main` · **Head:** `v1.6.0-dev` · **Tag:** `v1.6.0` (only after this PR is no longer a draft)

**State:** **Draft.** Package stays **`1.5.0`**. Operator QA is open ([QA_v1.6.0.md](QA_v1.6.0.md)). Do not merge, tag, or publish Hub from this draft.

---

## Summary

Sixth minor after production **v1.5.0**. Home Assistant can observe the fleet through a separate HACS integration. Web SSH can opt a Debian host into `tmux` or `screen`. Inline scripts use a per-request nonce. A Move that failed after names flipped can be undone. Unit coverage on `app` is **75.04%**.

Design: [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Maintainer ticks: [QA_v1.6.0.md](QA_v1.6.0.md). User-facing release notes are written at freeze (`docs/RELEASE_v1.6.0.md` does not exist yet).

| Stream | Highlights |
|--------|------------|
| **HA-p2 Slice 1** (Must) | HACS [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.4**. Token `read`. Fleet sensors, one host device, one **Visit**. Lovelace fleet card. Not in this image |
| **Slice 1b** (Should) | `GET /api/v1/inventory` and `/services`. Container, service, and disk sensors on the host device. No start/stop |
| **Mux-1** (Must) | Per-host **Console mux**. `tmux` then `screen`, else a plain PTY. Hide detaches; ✕ kills. Never apt-install. HAOS and demo never mux |
| **Q-80** (Must) | Compose **75.04%** (35893/47833). CI `--cov-fail-under=75` |
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

Walk [QA_v1.6.0.md](QA_v1.6.0.md). Boxes there stay empty until each row is run.

- [x] Unit suite ≥ **75%**; CI fail-under **75**
- [ ] Mux-1 on a host that already has tmux or screen
- [ ] HA-p2 Slice 1 + Slice 1b on plugin **0.2.4**
- [ ] CSP enforce locally; public demo stays Report-Only
- [ ] Undo-1 on a disposable pair, then turn the Move flag off again
- [ ] 1.5 regression (web recycle mid-Move, Reports, Sign in, reboot flag)
- [ ] Screenshot pack in [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v160--pack-status)
- [ ] `.venv-docs/bin/mkdocs build --strict` at freeze

## Out of scope

- Version bump to **1.6.0**, merge, tag, Hub, GitHub Release
- Enforcing CSP on the public demo
- Turning Move on by default
- HA backup / start-stop / Move-from-HA
- Mux leftover list in the UI
- Brand chrome and per-feature grants

## Merge checklist

- [ ] Operator QA signed (Mux-1 + HA-p2 at minimum)
- [ ] Version bump `app/version_info.py` + `pyproject.toml` → **1.6.0**
- [ ] `docs/RELEASE_v1.6.0.md` + wiki Home current-release row
- [ ] Merge `v1.6.0-dev` → `main`
- [ ] Tag **`v1.6.0`** · Hub `1.6.0` / `1.6` / `latest`
- [ ] Keep `1.5` / `1.5.0` pins valid
- [x] Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false**

## After merge

Hub publish per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md). GitHub Release body = `docs/RELEASE_v1.6.0.md` (workflow uses that file when the tag is pushed). The public demo already tracks `v1.6.0-dev`; after the tag, point that clone at `main` or the tag when asked.
