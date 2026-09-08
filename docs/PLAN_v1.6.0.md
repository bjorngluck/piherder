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
| **HA-p2** | HAOS path 2 **plugin** (HA → PiHerder) | Discover write-up may land in 1.5; **ship** the integration here. No plugin repo on `v1.5.0-dev`. |
| **AC-fg** | Fine-grained / per-host / per-feature grants | Out of 1.5. Three global roles stay until this train. Not multi-tenant SaaS. |
| **Q-80** | Unit coverage step toward **1.x 80%** | 1.5 freeze stays **70%**. Typical step ~5pp on a quality-leaning minor. Service tests first. |
| **Docs-archive-0x** | Archive spent **0.x** `PLAN_*` / `RELEASE_*` | `docs/archive/v0/` + stubs at old paths (link, do not 404). Keep `FEATURE_PLAN_*`, ROADMAP, SPEC, ADMIN, and **1.0+** PLAN/RELEASE in `docs/`. Not a 1.5 freeze leftover. |

**Not parked here:** 1.5 Discover catalog (M-undo, CSP-n, Brand, M-flag, W-mux, J-runtime) — write those notes on the 1.5 train if we write them; promote later.

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

*Open the train with a real Must/Should lock. Until then production is still [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) (then 1.5).*
