# PiHerder v1.7.0 — candidate (not opened)

**Status:** **Under consideration** — train **not** opened. Do not start product code here.  
**Date parked:** 2026-09-18 · **inbox grown:** 2026-09-19 (from v1.6 lock)  
**Git branch:** none yet (`v1.7.0-dev` at train open)  
**Production until then:** `main` is **v1.5.x** until **v1.6.0** freezes, then **v1.6.x**  
**Related:** [PLAN_v1.5.0.md](PLAN_v1.5.0.md) · [PLAN_v1.6.0.md](PLAN_v1.6.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)

Theme and Must/Should lock at **train open**. This file is only the parked inbox so 1.6 does not grow leftovers.

---

## Parked inbox

| ID | Item | Notes |
|----|------|--------|
| **J-runtime** | Remaining web-process jobs → Celery | **1.5 Discover written.** **Jr-1:** all remaining exclusive types in **one go** (`os_patch`, `container_patch`, update-checks, `docker_stack_*`, templates) onto the **default** Celery queue. Host down (Kuma/SSH): **queue + retry** until SSH or max wait; exclusive slot held. Worker kill of a **running** apt/compose: **fail honest**. nmap stays `-Q nmap`. Not Must until this train locks. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 J-runtime. |
| **AC-fg** | Fine-grained / per-host / per-feature grants | **Out of 1.5 and 1.6** (2026-09-19). Three global roles stay. Not multi-tenant SaaS. No discover spike yet. |
| **Brand-1/2** | Instance wordmark + one accent; hide Catalog in nav | **1.5 Discover written.** **Out of 1.6** (2026-09-19). Official mark + primary red stay. No theme engine, no logo upload. Demo ignored. Brand-3 (own-docs MkDocs skin) later. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 Brand. |

**Not parked here:** 1.6 Active streams (HA-p2 Slice 1/1b, Mux-1, Q-80, CSP-n Slice 1, Undo-1, Docs-archive-0x) — see [PLAN_v1.6.0.md](PLAN_v1.6.0.md).

---

## Capture log

| Date | Note |
|------|------|
| 2026-09-18 | Candidate file created. Inbox: **J-runtime** (one job runtime for remaining exclusive types + host-down wait). Train not opened. |
| 2026-09-19 | **v1.6 opened.** Inbox grew: **AC-fg** and **Brand-1/2** (both out of 1.6). |

*Open the train with a real Must/Should lock. Until then production is [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md) (then 1.6).*
