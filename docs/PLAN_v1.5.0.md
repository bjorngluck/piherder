# PiHerder v1.5.0 — job runtime (Move on the worker)

**Status:** **Active** — train opened 2026-09-07  
**Date opened:** 2026-09-07  
**Git branch:** `v1.5.0-dev` → `main` · tag `v1.5.0` (at freeze)  
**Package / image version:** stays **`1.4.0` until freeze**  
**Theme:** **Job runtime** — run `service_migrate` on Celery so recycling **web** cannot kill a long copy; **N3** custom Reports layout (Should)  
**Baseline:** `v1.4.0` (tagged 2026-09-06)  
**Mode:** **Must → Should → Discover.** Must **M-worker**. Should **N3a** + **M-hb** + **Q**. **AC-fg is out.**  
**QA:** [QA_v1.5.0.md](QA_v1.5.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) · wiki [Move a service](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Reports](../wiki/day-to-day/reports.md)

> **Train open 2026-09-07.** Production stays **v1.4.x** on `main`. Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false** at train open. HAOS path-2 **plugin ships v1.6.0** (discover only here).

---

## 0. Intent

1.4 Move is one audited job, but it runs on FastAPI **`BackgroundTasks` on web**. Recreating **web** (herder `compose up`, deploy, crash) **fails** a running Move. Backups already run on Celery. Operators who enable Move should be able to recycle the UI process without aborting a multi-gigabyte copy.

Wanted:

1. `service_migrate` on the **Celery worker**  
2. Progress **heartbeats** so a hung copy is visible  
3. Recycle **web** → job **keeps running**; recycle **worker** → job **fails** honestly (same as backup)  
4. Dual-host exclusive lock still holds on **both** ids  
5. **N3a:** pin / hide / reorder existing `/reports` cards per user (Should; may slip the tag)

This is the migrate slice of 1.3’s parked “one job runtime.” It is **not** moving OS-patch / stack / template jobs off web unless Discover **J-runtime** is later promoted.

**Out of 1.5 product code:** **AC-fg**, **M-live**, ACME-in-herder, full NPM CRUD, Files token API, **N3c** widget picker, HA custom component **code** (discover only; ship **v1.6.0**).

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.5.0-dev`** |
| Production line | **`main` @ `v1.4.0`** — hotfixes → **`v1.4.x`**, port into `v1.5.0-dev` |
| Git tag (freeze) | **`v1.5.0`** (RCs: `1.5.0-rc.N` if needed) |
| Image tags (freeze) | `1.5.0` · `1.5` · `latest` (multi-arch); keep `1.4` / `1.4.x` pins valid |
| In-scope streams | **M-worker** Must · **N3a** Should · **M-hb** Should · **Q** · Discover catalog |
| Out-of-focus | **AC-fg** · **M-live** · ACME · full NPM CRUD · Files token API · **N3c** · HA-p2 **plugin code** · multi-tenant · Swarm/k8s |
| Mode | Worker Move · no half-built Celery migrate · Must → freeze; Should may slip |
| Coverage | **≥ 62%** unit; CI fail-under **62**; focused tests for enqueue, fail-on-worker-restart, dual-host lock |
| E2E | Wizard chrome still loads (no live two-host in CI) |
| Semver | Additive minor; no migrate pipeline behaviour change except **where it runs** |
| Version bump | `1.5.0` **at freeze only** |
| Kill switch | **`PIHERDER_SERVICE_MIGRATE=false`** at train open. Freeze question (**M-flag**). Worker does not imply default-on |

```text
main @ v1.4.0 (+ v1.4.x patches)
  └─ v1.5.0-dev → merge → main → tag v1.5.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | Do not start Out items while Must is open |
| Should may slip | **N3a** does not block the tag if M-worker is green |
| Prod critical bugs | **main** as **1.4.x** first, then port here |
| Demo never grows teeth | Real migrate off · real Files SFTP off |
| Residual Cap | **AC-fg** · ACME · M-live · N3c · Files token API stay out |
| Tag honesty | **v1.5.0 tags only with Move on the worker** |

---

## 1a. Kickoff leans (locked 2026-09-07)

| # | Question | Decision |
|---|----------|----------|
| 1 | Theme / Must | **M-worker**. Recycle web must not fail a running Move |
| 2 | Broader “one job runtime”? | **J-runtime Discover.** Do not start OS-patch / stack / template Celery moves |
| 3 | Heartbeats | Reuse backup `_flush_job_progress` / `Job.details`. No new WS protocol |
| 4 | Dual-host lock on Celery | Extend exclusive + backup mutex so a Move holds **both** server ids. Spike if Redis lock is single-id only |
| 5 | **M-flag** | Stay **false** at train open. Freeze question. Prefer Settings+env over compose default `true` |
| 6 | **M-undo** | **Discover** (undo matrix). No silent `finally`. **M-live** Out |
| 7 | **CSP-n** | Discover + inline-script count. Promote to Should only if the spike is small (script nonces; style may stay unsafe-inline) |
| 8 | **Brand** | Discover. Pull **B1** only if Must green and we want it |
| 9 | **W-mux** | Discover (tmux vs screen + isolation). Low priority; do not start by default |
| 10 | **AC-fg** | **Out of 1.5.** Park ≥1.6. No discover spike |
| 11 | **N3** | **Should = N3a** (pin/hide/reorder `/reports` cards, per-user). **N3b** Move card stretch. **N3c** Out |
| 12 | **HA-p2** | Discover in 1.5 (shape, entities, auth, API gaps). **Plugin ship = v1.6.0** |
| 13 | Files token API / ACME / NPM CRUD | Out |
| 14 | Coverage | CI fail-under **62** |
| 15 | Version bump | `1.5.0` at freeze only |
| 16 | E2E | Wizard chrome. No live two-host in CI |

---

## 1b. Recommended delivery order

```text
Phase 0  Open train + docs lock              ← this commit 2026-09-07
Phase 1  M-worker enqueue on Celery          dual-host lock spike first
Phase 2  M-hb heartbeats + JobHold           fail-on-worker-restart honest
Phase 3  N3a Reports pin/hide/reorder        Should; must not block Must
Phase 4  Wiki + QA + Discover notes          HA-p2 / undo / CSP counts
Phase 5  Q freeze: tests ≥62% · version      1.5.0 · tag · Hub
         bump · M-flag freeze question
```

Discover notes may parallel after Phase 1. **HA-p2 plugin code is v1.6.** **AC-fg is not this train.**

---

## 2. Stream **M-worker** — Move on Celery (Must)

**Today (1.4):** `enqueue_service_migrate` → FastAPI `BackgroundTasks` → `_execute_service_migrate` on **web**. Startup fail-on-running-web-jobs marks a recycled Move **failed**. Staging is kept. Wiki: do not recreate **web** mid-Move.

**Wanted:** same pipeline, different process.

| ID | Item | Notes |
|----|------|--------|
| MW1 | Celery task | New task next to `backup_server` in `app/tasks.py`. Same pipeline functions. Do not fork `service_migrate/` |
| MW2 | Enqueue | `enqueue_service_migrate` goes to the worker. Web request only creates the Job + audit + lock, then `.delay()` |
| MW3 | Fail-on-restart | Worker recycle **fails** the job (honest, same as backup). Web recycle **does not** |
| MW4 | Dual-host lock | Exclusive with backup **and** stack-mutating jobs on **both** source and dest. Spike Redis per-server backup mutex vs two ids |
| MW5 | Progress | `_flush_job_progress` / details JSON heartbeats (**M-hb** Should). JobHold live log stays |
| MW6 | Recover CTA | Copy / dest-up fail still offers **Start source stack** |
| MW7 | Demo / RBAC | Viewer 403; demo never copies; flag still required to open the wizard |
| MW8 | Docs | Wiki Move + Jobs: job runs on **worker**. 1.4 RELEASE notes stay historical |

**Reuse, do not fork:** `app/tasks.py` backup task, `Job.details` progress, `server_job_lock`, `app/services/service_migrate/` pipeline.

**Success (Must):**

1. Start a Move, recreate **web**, job still **running** and JobHold still streams.  
2. Recreate **worker** mid-copy → job **failed**, staging kept, **Start source stack** when the fail is copy/dest-up.  
3. Dual-host exclusive: backup or stack mutate on source **or** dest still blocked.  
4. Viewer 403; demo never copies.  
5. 1.5 docs no longer say “do not recycle web during a Move.”

---

## 3. Stream **N3** — Custom Reports layout (Should)

**Today (1.3 N2):** `/reports` is history Grafana never sees (backups, OS patches, LAN live, Docker, console; 7/30/90d). Home pulse stays status cards. Portlets were **rejected**.

**Must not become:** Grafana-in-herder, PromQL, iframes, SQL, a second TSDB, drag-and-drop **home** marketplace (**N3c**).

| ID | Item | Notes |
|----|------|--------|
| N3a1 | Layout chrome on `/reports` | Pin, hide, reorder the **existing** five history cards |
| N3a2 | Per-user remember | Cookie or user setting. Default = current 1.3 order, all visible. Reset-to-default exists |
| N3a3 | Viewer | Same chrome; still read-only data |
| N3a4 | Demo | Works on seeded Jobs |
| N3a5 | Wiki | [reports.md](../wiki/day-to-day/reports.md) |

**Stretch (does not block N3a):** **N3b** one extra built-in card — **Move jobs** (count / fail / last dest). Cert expiry / nmap new-device stay later.

**Success (Should):** operator can hide LAN live, pin Backups first, reload, order sticks. No Grafana iframes. Tag does **not** wait on N3 if M-worker is green.

---

## 4. Discover catalog

Written findings this train. No schema / plugin repo until a row is promoted.

### **M-undo** — Auto-rollback

Pre-flip fails already offer **Start source stack**. Post-flip (DNS/FTL, NPM PUT, TLS/Kuma, half-rebind) do **not** auto-revert. Two-host undo is its own design.

Write the undo matrix (which steps are reversible, preview vs automatic, dest stop vs leave up, what we **never** auto-wipe). If promoted later: named job `service_migrate_undo`, never a silent `finally`. **M-live** stays Out.

### **CSP-n** — CSP nonces

CSP is on; no `unsafe-eval`; `script-src` / `style-src` still `'unsafe-inline'`. Count inline `<script>` tags. If small: candidate Should “script nonces, style stays unsafe-inline.” Report-Only on demo first. Do not break Turnstile / OpenAPI UI.

### **Brand** — Branding

Built-in light/dark only. Discover slices: **B1** Settings logo + one accent; **B2** first-run hide Catalog; **B3** docs only. Pull **B1** only if Must is green. No theme engine.

### **M-flag** — Default-on migrate

Stay **false** at train open. Why 1.4 shipped off: first stop-source job, leftover remove in the same wizard, web-process kill on recycle, short live QA, same opt-in pattern as console/Files.

Freeze choices: **A** compose default `true` · **B** Settings toggle + env (prefer) · **C** stay false. Worker does **not** imply GA. Demo never. Leftover-remove stays extra-acked.

### **W-mux** — Host `tmux` / `screen`

Soft park is herder-side. Discover: tmux vs screen, isolation between operators, missing-binary fallback (plain PTY). Do not start unless Must is green. Non-goals: replacing soft-park, recording inside tmux, apt-installing tmux on every host.

### **HA-p2** — HAOS plugin (discover 1.5, **ship v1.6.0**)

Path 1 (v0.9) is PiHerder → HAOS over SSH. Path 2 is HA → PiHerder.

**1.5 write-up** (FEATURE_PLAN + this PLAN): integration first (HACS / `custom_components/piherder`), API token auth, draft entities (herder up, host down, last backup, OS updates, running Jobs, Move in progress), events on HA bus. No plugin repo on `v1.5.0-dev` except docs. Do not vendor HA inside the PiHerder image.

**1.6:** ship the integration. See [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7.

### **J-runtime** — Rest of web-process jobs

Inventory only: OS/container patch, stack check/deploy/lifecycle, templates still die on web recycle. Do not move them in 1.5 unless Must is green and we explicitly pull.

---

## 5. Ship bar

| Priority | Item | Bar |
|----------|------|-----|
| **Must** | **M-worker** | Move runs on Celery; web recycle does not fail it; worker recycle does; dual-host lock; JobHold |
| **Should** | **N3a** | Pin/hide/reorder `/reports` cards per user |
| **Should** | **M-hb** | Heartbeats / stall visible |
| **Should** | **Q** | Tests; wiki truth; coverage ≥ 62% |
| **Discover** | M-undo · CSP-n · Brand · M-flag · W-mux · HA-p2 · J-runtime | Notes only |
| **Out** | **AC-fg** · M-live · ACME · NPM CRUD · Files token API · N3c · HA-p2 **code** | Park AC-fg + HA plugin on **v1.6.0** |

---

## 6. Quality bar

| Gate | Target |
|------|--------|
| Unit | **≥ 62%** (fail-under **62**); enqueue on Celery · fail-on-worker-restart · dual-host exclusive · N3a persist |
| Tests | Extend `tests/test_service_migrate.py` (or sibling); no live SSH; mock Celery |
| E2E | Wizard chrome + lock disabled CTA (same as 1.4). Reports layout smoke if N3a lands |
| Docs | Wiki Move + Jobs + Reports; `mkdocs build --strict` at freeze |
| Security | Dual-host lock, staging wipe, path jail, audit without secret bodies, demo off |

---

## 7. Out of scope (stay honest)

- **AC-fg** fine-grained / per-host / per-feature grants — **pushed out 2026-09-07**. Three global roles stay. ≥1.6  
- **M-live** zero-downtime / rsync-while-running  
- Silent auto-rollback `finally` (Discover **M-undo** is a named job if ever promoted)  
- ACME-in-herder · full NPM proxy CRUD · Files token API  
- **N3c** widget picker / Grafana-in-herder  
- HA custom component **implementation** (Discover here; **v1.6.0** ship)  
- Moving OS-patch / stack / template jobs to Celery unless **J-runtime** is promoted  
- Multi-tenant SaaS · k8s/bare · branding theme engine · forcing `tmux` onto fleet hosts  

---

## 8. Capture log

| Date | Note |
|------|------|
| 2026-09-06 | 1.4 PR review parked **M-worker** (Celery Move + heartbeats). 1.4 stays web `BackgroundTasks` |
| 2026-09-06 | **v1.4.0 tagged.** Kill switch false. Hub `1.4.0` / `1.4` / `latest` |
| 2026-09-07 | **Train opened** on `v1.5.0-dev`. Must **M-worker**. Should **N3a** + **M-hb** + **Q**. Discover: M-undo, CSP-n, Brand, M-flag, W-mux, HA-p2, J-runtime. **AC-fg out** (park ≥1.6). HA-p2 plugin **v1.6.0**. Package stays `1.4.0` until freeze. `main` patchable as **v1.4.x** |
| 2026-09-07 | **M-worker landed:** `app.tasks.service_migrate` on the backup worker queue. Dual-host **backup** Redis mutex (lower id first). `cleanup_orphan_web_jobs` skips Move. Worker redelivery of a `running` Move **fails** (staging kept, Start source stack). Unit tests inline via `PYTEST_CURRENT_TEST`. |

---

## 9. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Delete spent `v1.4.0-dev` · open **`v1.5.0-dev`** + lock Must/Should | **Done** 2026-09-07 |
| 2 | Spike dual-host Celery lock vs per-server backup mutex | **Done** — reuse `backup` mutex on both ids |
| 3 | Land **MW1–MW4** enqueue + fail-on-worker-restart | **Done** 2026-09-07 |
| 4 | **M-hb** heartbeats + JobHold | **Done** — reuse `_flush_job_progress` on the worker (JobHold polls DB) |
| 5 | Wiki Move + Jobs (worker truth) | **Done** 2026-09-07 |
| 6 | **N3a** Reports layout (Should; after M-worker moving) | |
| 7 | Discover notes: undo matrix, CSP count, HA-p2 entities | Parallel after 3 |
| 8 | Freeze · **M-flag** question · version `1.5.0` · tag · Hub | |

---

*Production remains [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) until this train freezes.*
