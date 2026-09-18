# PiHerder v1.6.0 — candidate (not opened)

**Status:** **Under consideration** — train **not** opened. Do not start product code here.  
**Date parked:** 2026-09-08  
**Git branch:** none yet (`v1.6.0-dev` at train open)  
**Production until then:** `main` stays **v1.4.x** until **v1.5.0** freezes, then **v1.5.x**  
**Related:** [PLAN_v1.5.0.md](PLAN_v1.5.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7

Theme and Must/Should lock at **train open**. This file is only the parked inbox so 1.5 does not grow leftovers.

---

## Parked inbox (from 1.5)

| ID | Item | Notes |
|----|------|--------|
| **HA-p2** | HA → PiHerder **HACS integration** (runs **on HA**) | **1.5 Discover written.** **Slice 1 Must:** config flow, coordinator, fleet sensors, per-host devices, Open in PiHerder, token `read`. Optional `GET /api/v1/summary`. **Slice 1b Should:** snapshot APIs (last docker inventory, fleet services, disk/OS facts) + container/service entities — still no start/stop. **Slice 2:** confirm + backup. Separate git repo; do not vendor in the PiHerder image. Details stay in PiHerder. [FEATURE_PLAN §7](FEATURE_PLAN_HOME_ASSISTANT.md). |
| **AC-fg** | Fine-grained / per-host / per-feature grants | Out of 1.5. Three global roles stay until this train. Not multi-tenant SaaS. |
| **Q-80** | Unit coverage step toward **1.x 80%** | 1.5 freeze stays **70%**. Typical step ~5pp on a quality-leaning minor. Service tests first. |
| **Docs-archive-0x** | Archive spent **0.x** `PLAN_*` / `RELEASE_*` | `docs/archive/v0/` + stubs at old paths (link, do not 404). Keep `FEATURE_PLAN_*`, ROADMAP, SPEC, ADMIN, and **1.0+** PLAN/RELEASE in `docs/`. Not a 1.5 freeze leftover. |
| **CSP-n Slice 1** | Script nonces; style stays `'unsafe-inline'` | 1.5 Discover: **not small**. 71 inline `<script>` · 190 `on*` handlers. CSP3: a nonce on `script-src` drops `'unsafe-inline'` unless `script-src-attr 'unsafe-inline'`. Per-request nonce before render; stamp 71 tags; OpenAPI paths keep `'unsafe-inline'`; Turnstile host allowlist + nonce the login boot script; Report-Only on demo first. Do **not** rewrite onclick this train. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 CSP-n. |
| **M-undo** | Named job `service_migrate_undo` (fail-path) | **1.5 Discover written.** **Undo-1:** JobHold CTA on failed Move (`cutover` / rebind / `validate`): revert DNS/NPM, stop dest, start source. Dest dir+volumes stay. Never silent `finally`, dest wipe, leftover-remove reverse, or reverse a green Move. **Undo-2:** dest_up worker-restart inspect helper. Not Must until this train locks. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 M-undo. |
| **W-mux** | Host `tmux` / `screen` for web console | **1.5 Discover written.** **Mux-1:** per-host opt-in; prefer tmux then screen else plain PTY; named session per user+host+tab; ✕ kills, Hide detaches; herder park stays. Never apt-install, never refuse console, never shared `piherder` session. **Mux-2:** reattach after web recycle; leftover list/kill. Not Must until this train locks. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 W-mux. |
| **Brand** | Instance wordmark + one accent; hide Catalog | **1.5 Discover written.** **Brand-1:** Settings instance name + one accent (`--color-accent` only); official mark + primary red stay; demo ignored. **Brand-2:** instance-wide hide Catalog in nav (`/catalog` still works). **Brand-3:** own-docs MkDocs skin later. No theme engine, no logo upload. Not Must until this train locks. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 Brand. |

**Not parked here:** remaining 1.5 Discover catalog (M-flag, J-runtime) — write those notes on the 1.5 train if we write them; promote later. **HA-p2 / M-undo / W-mux / Brand write-ups are done** (1.5); **ship** stays this inbox.

---

## Docs-archive-0x (shape, not started)

Spent pre-1.0 train records only:

- Move `docs/PLAN_v0.*.md` and `docs/RELEASE_v0.*.md` → `docs/archive/v0/`
- Leave a **stub** at the old path (title + “moved to archive” + link) so wiki / GitHub / old bookmarks do not 404
- Do **not** archive `FEATURE_PLAN_*`, `ROADMAP_ECOSYSTEM.md`, `SPEC.md`, `ADMIN.md`, or **v1.0+** PLAN/RELEASE
- Optional `docs/README.md` index of live vs archived

Do this **after** 1.5 is tagged, on `v1.6.0-dev`.

---

## Capture log

| Date | Note |
|------|------|
| 2026-09-08 | Candidate file created. Inbox: HA-p2 ship, AC-fg, coverage step toward 80%, **0.x PLAN/RELEASE archive**. Train not opened. |
| 2026-09-08 | **CSP-n Slice 1** parked (script nonces + `script-src-attr`; style stays unsafe-inline). Not 1.5. |
| 2026-09-10 | **HA-p2 Discover** landed on 1.5. This inbox: Slice 1 Must (HACS on HA, fleet+hosts), 1b snapshot entities, 2 backup action. No plugin on `v1.5.0-dev`. |
| 2026-09-13 | **M-undo Discover** landed on 1.5. This inbox: Undo-1 named job (fail-path, stop dest then start source). No undo code on `v1.5.0-dev`. |
| 2026-09-13 | **W-mux Discover** landed on 1.5. This inbox: Mux-1 per-host opt-in tmux/screen. No mux code on `v1.5.0-dev`. |
| 2026-09-18 | **Brand Discover** landed on 1.5. This inbox: Brand-1 wordmark + one accent; Brand-2 hide Catalog. No chrome code on `v1.5.0-dev`. |

*Open the train with a real Must/Should lock. Until then production is still [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) (then 1.5).*
