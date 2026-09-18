# PiHerder v1.7.0 — candidate (not opened)

**Status:** **Under consideration** — train **not** opened. Do not start product code here.  
**Date parked:** 2026-09-18  
**Git branch:** none yet (`v1.7.0-dev` at train open)  
**Production until then:** `main` stays **v1.4.x** until **v1.5.0** freezes, then **v1.5.x** / **v1.6.x**  
**Related:** [PLAN_v1.5.0.md](PLAN_v1.5.0.md) · [PLAN_v1.6.0.md](PLAN_v1.6.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)

Theme and Must/Should lock at **train open**. This file is only the parked inbox so 1.5/1.6 do not grow leftovers.

---

## Parked inbox (from 1.5)

| ID | Item | Notes |
|----|------|--------|
| **J-runtime** | Remaining web-process jobs → Celery | **1.5 Discover written.** **Jr-1:** all remaining exclusive types in **one go** (`os_patch`, `container_patch`, update-checks, `docker_stack_*`, templates) onto the **default** Celery queue. Host down (Kuma/SSH): **queue + retry** until SSH or max wait; exclusive slot held. Worker kill of a **running** apt/compose: **fail honest**. nmap stays `-Q nmap`. Not Must until this train locks. [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 J-runtime. |

**Not parked here:** 1.6 inbox (HA-p2 ship, AC-fg, Q-80, CSP-n Slice 1, M-undo, W-mux, Brand, 0.x archive) — see [PLAN_v1.6.0.md](PLAN_v1.6.0.md).

---

## Capture log

| Date | Note |
|------|------|
| 2026-09-18 | Candidate file created. Inbox: **J-runtime** (one job runtime for remaining exclusive types + host-down wait). Train not opened. |

*Open the train with a real Must/Should lock. Until then production is still [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) (then 1.5 / 1.6).*
