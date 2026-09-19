# PiHerder v1.5.0 — job runtime (Move on the worker)

**Status:** **Freeze** 2026-09-18 — package **1.5.0**. Operator QA **signed**. **M-flag C** (kill switch stays **false**). Tag · Hub after merge to `main`.  
**Date opened:** 2026-09-07  
**Git branch:** `v1.5.0-dev` → `main` · tag `v1.5.0` (at freeze)  
**Package / image version:** **`1.5.0`** (freeze 2026-09-18). Hub tags after merge.  
**Theme:** **Job runtime** — run `service_migrate` on Celery so recycling **web** cannot kill a long copy; **N3** custom Reports layout (Should)  
**Baseline:** `v1.4.0` (tagged 2026-09-06)  
**Mode:** **Must → Should → Discover.** Must **M-worker**. Should **N3a** + **N3b** + **M-hb** + **Q**. **AC-fg is out.**  
**QA:** [QA_v1.5.0.md](QA_v1.5.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · [RELEASE_v1.4.0.md](RELEASE_v1.4.0.md) · [PLAN_v1.6.0.md](PLAN_v1.6.0.md) (**Active**) · [PLAN_v1.7.0.md](PLAN_v1.7.0.md) (candidate inbox) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) · wiki [Move a service](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Reports](../wiki/day-to-day/reports.md)

> **Freeze 2026-09-18.** Package **1.5.0**. Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false**. HAOS path-2 **plugin ships v1.6.0**. J-runtime **v1.7**.

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

**Now (2026-09-18 freeze):** Move on Celery. Reports layout + Move jobs card. Unit **~70.6%**. Operator QA **signed**. **M-flag C** (kill switch **false**). Package **1.5.0**. Discover parked v1.6 / J-runtime v1.7. [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md).

**Out of 1.5 product code:** **AC-fg**, **M-live**, ACME-in-herder, full NPM CRUD, Files token API, **N3c** widget picker, HA custom component **code** (discover only; ship **v1.6.0**).

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.5.0-dev`** |
| Production line | **`main` @ `v1.5.0`** after merge — hotfixes → **`v1.5.x`** |
| Git tag (freeze) | **`v1.5.0`** (RCs: `1.5.0-rc.N` if needed) |
| Image tags (freeze) | `1.5.0` · `1.5` · `latest` (multi-arch); keep `1.4` / `1.4.x` pins valid |
| In-scope streams | **M-worker** Must · **N3a** Should · **N3b** Should stretch · **M-hb** Should · **Q** · Discover catalog |
| Out-of-focus | **AC-fg** · **M-live** · ACME · full NPM CRUD · Files token API · **N3c** · HA-p2 **plugin code** · multi-tenant · Swarm/k8s |
| Mode | Worker Move · no half-built Celery migrate · Must → freeze; Should may slip |
| Coverage | **≥ 70%** unit; CI fail-under **70**; focused tests for enqueue, fail-on-worker-restart, dual-host lock. **1.x end goal 80%** is later trains, not this freeze |
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
| 2 | Broader “one job runtime”? | **J-runtime Discover written 2026-09-18.** All remaining exclusive types later, in one go. Host-down queue/retry. Park **v1.7**. Not this freeze. |
| 3 | Heartbeats | Reuse backup `_flush_job_progress` / `Job.details`. No new WS protocol |
| 4 | Dual-host lock on Celery | Extend exclusive + backup mutex so a Move holds **both** server ids. Spike if Redis lock is single-id only |
| 5 | **M-flag** | **C — stay false** at freeze 2026-09-18. Worker does not imply GA. Demo never. |
| 6 | **M-undo** | Discover **written 2026-09-13**. Fail-path only; stop dest then start source. Job → v1.6. No silent `finally`. **M-live** Out |
| 7 | **CSP-n** | Discover + inline-script count. **Written 2026-09-08 — not Should.** 71 scripts / 190 `on*`. Slice 1 → v1.6 |
| 8 | **Brand** | Discover **written 2026-09-18**. Wordmark + one accent; hide Catalog in nav. Brand-1 → v1.6. No theme engine. |
| 9 | **W-mux** | Discover **written 2026-09-13**. tmux then screen else PTY; opt-in per host. Mux → v1.6. Soft-park stays. |
| 10 | **AC-fg** | **Out of 1.5.** Park ≥1.6. No discover spike |
| 11 | **N3** | **Should = N3a** (pin/hide/reorder `/reports` cards, per-user). **N3b** Move card stretch. **N3c** Out |
| 12 | **HA-p2** | Discover **written 2026-09-10**. HACS on HA; Slice 1 fleet+hosts; 1b containers/services. **Plugin ship = v1.6.0** |
| 13 | Files token API / ACME / NPM CRUD | Out |
| 14 | Coverage | CI fail-under **70** |
| 15 | Version bump | `1.5.0` at freeze only |
| 16 | E2E | Wizard chrome. No live two-host in CI |

---

## 1b. Recommended delivery order

```text
Phase 0  Open train + docs lock              ← this commit 2026-09-07
Phase 1  M-worker enqueue on Celery          dual-host lock spike first
Phase 2  M-hb heartbeats + JobHold           fail-on-worker-restart honest
Phase 3  N3a Reports pin/hide/reorder        Should; must not block Must
Phase 4  Wiki + QA + Discover notes          wiki/QA pass 2026-09-07; Discover notes still open
Phase 5  Q freeze: tests ≥70% · version      coverage bar met; freeze = M-flag · 1.5.0 · tag · Hub
         bump · M-flag freeze question
```

Discover notes may parallel after Phase 1. **HA-p2 plugin code is v1.6.** **AC-fg is not this train.**

---

## 2. Stream **M-worker** — Move on Celery (Must)

**1.4 (historical):** `enqueue_service_migrate` → FastAPI `BackgroundTasks` → `_execute_service_migrate` on **web**. Startup fail-on-running-web-jobs marked a recycled Move **failed**. Wiki said do not recreate **web** mid-Move.

**Landed (1.5):** same pipeline, **Celery** process. Wiki current truth: recycle **web** is safe; recycle **worker** fails honestly.

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
| N3a1 | Layout chrome on `/reports` | Pin, hide, reorder history cards (1.3 five + **N3b** Move) |
| N3a2 | Per-user remember | Cookie or user setting. Default = 1.3 order + Move last, all visible. Reset-to-default exists |
| N3a3 | Viewer | Same chrome; still read-only data |
| N3a4 | Demo | Works on seeded Jobs |
| N3a5 | Wiki | [reports.md](../wiki/day-to-day/reports.md) |

**Stretch (does not block N3a):** **N3b** one extra built-in card — **Move jobs** (count / fail / last dest). **Landed 2026-09-08.** Cert expiry / nmap new-device stay later.

**Success (Should):** operator can hide LAN live, pin Backups first, reload, order sticks. Move jobs card shows count / fail / last dest from `service_migrate` Jobs. No Grafana iframes. Tag does **not** wait on N3 if M-worker is green.

---

## 4. Discover catalog

Written findings this train. No schema / plugin repo until a row is promoted.

### **M-undo** — Fail-path recover (not auto-rollback)

**Written 2026-09-13. Not 1.5 Should.** No `service_migrate_undo` job this freeze. Owning doc: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) Failure.

**Leans:** **Fail-path only** (do not reverse a green Move). After names have flipped: **stop dest, then start source.** Dest project dir and volumes **stay**. Never a silent `finally`.

**Today:**

- Pre-flip (`stop` / `copy` / `dest_up`): JobHold **Start source stack** (Docker Start all on source). Staging kept. Dest names unchanged.
- Worker restart during `dest_up`: dest **may** already be up — **no** Start source (would dual-run). Inspect dest.
- Post-flip (`cutover` / rebind / `validate`): dest up, names maybe flipped. **No auto-revert.** Start source would dual-run.
- Leftover `remove` after a **green** Move cannot be undone from PiHerder.

**Locks (if a job ships later):**

1. Named job **`service_migrate_undo`** — preview → confirm → audit. Dual-host exclusive + backup mutex. Kill switch = `PIHERDER_SERVICE_MIGRATE`. Demo never. Viewer 403.
2. Consumes a **failed** Move whose `failed_step` is `cutover` / rebind / `validate`. Pre-flip stays Start source.
3. Inverse cutover: NPM PUT `forward_host` back to source; direct CNAME → source `dns_name` + both `restartdns`.
4. Inverse rebind: maps / Kuma service / Grafana container chips / template deployment back to source. Cert: **re-enable source target**; do **not** delete dest clone.
5. **Never auto-wipe:** dest volumes, dest project tree, leftover `remove`, extra binds outside the jail, staging (kept until dismiss).
6. Green Move: operator who wants the stack back on A runs a **new Move** B→A. **M-live** / `dns_then_start` stay Out.
7. Token API never POSTs `service_migrate` or `service_migrate_undo`.

**1.6 candidate (not Must):**

| Slice | What |
|-------|------|
| **Undo-1** | JobHold CTA: revert names + stop dest + start source. Preview FQDNs / NPM / dest project. |
| **Undo-2** | dest_up worker-restart helper: inspect dest; optional stop-dest-then-start-source **without** DNS revert. |

Park Undo-1 on [PLAN_v1.6.0.md](PLAN_v1.6.0.md).

### **CSP-n** — CSP nonces

**Written 2026-09-08. Not 1.5 Should.** Catalog said promote only if the inline-script spike is small. It is not.

**Today:** CSP on (`PIHERDER_CSP=true`); no `unsafe-eval` (compiled Tailwind); `script-src` / `style-src` still `'self' 'unsafe-inline'`. Turnstile allowlist when keys are set. `/docs` `/redoc` only: jsDelivr + Google Fonts. Report-Only already exists (`PIHERDER_CSP_REPORT_ONLY`). Middleware stamps headers **after** the response — no per-request nonce. Jinja `env.globals` is process-wide, not request-scoped.

**Count (templates, 2026-09-08):**

| Kind | Count | Files |
|------|------:|------:|
| Inline executable `<script>` (no `src`) | **71** | 43 |
| `onclick` / `onchange` / `onsubmit` / `onerror` | **190** (148 / 30 / 11 / 1) | 40 |
| `<style>` blocks | 13 | 11 |
| `style="…"` attributes | 62 | 22 |
| Alpine (`x-data` / `@click`) | 64 hits | 3 (template edit / from-host / deploy) |
| `type=application/json` data blocks | 5 | 5 (not executed) |

**CSP3 trap:** `script-src 'nonce-…' 'unsafe-inline'` → modern browsers **drop** `'unsafe-inline'`. The 190 HTML event handlers die unless we rewrite them **or** split `script-src-attr 'unsafe-inline'`. Hashes are worse here (Jinja inside scripts).

**Slice 1 (v1.6 candidate):** script nonces, style stays unsafe-inline.

```
script-src 'self' 'nonce-{n}'
script-src-attr 'unsafe-inline'
style-src 'self' 'unsafe-inline'
```

Per-request nonce on `request.state` **before** `call_next`; stamp on the 71 inline tags; `'self'` covers `/static` src; JSON blocks stay un-nonced; OpenAPI paths keep today’s `'unsafe-inline'`; Turnstile host allowlist unchanged, login boot script gets nonce; Report-Only on demo first. Safari 15.4+ for `script-src-attr`.

**Slice 2 (later):** convert `on*` to `addEventListener` / HTMX, then drop `script-src-attr 'unsafe-inline'`. Real XSS win; large; easy to regress Docker/console modals.

**Out of 1.5:** do not change `headers.py` or templates. Do not flip Report-Only on production. Park Slice 1 on [PLAN_v1.6.0.md](PLAN_v1.6.0.md).

### **Brand** — Instance wordmark + accent (not a theme engine)

**Written 2026-09-18. Not 1.5 Should.** No Settings chrome this freeze.

**Leans:** **B1** = replaceable **wordmark** + **one accent** (official mark stays). **B2** = Settings hide **Catalog** in the nav (`/catalog` still works). **B3** = own-docs MkDocs skin **later**. Light/dark stay built-in. **No theme engine.** Do not pull B1 into 1.5 — freeze is QA + **M-flag**.

**Today:** `--color-primary: #e60012` · `--color-accent: #00a651` · header PNG pair · `ph_brand()` Pi+Herder · Catalog always in nav · PWA title “PiHerder”. Demo is official chrome.

**Locks (if Brand-1 ships later):**

1. Settings → General: instance name (empty = `ph_brand()`). One accent hex → `--color-accent` + `--accent-subtle-bg` only. **Do not** recolour primary red or the mark.
2. **No** header logo upload. Avatars / service logos stay those pipelines.
3. PWA title follows instance name when set. Optional env `PIHERDER_INSTANCE_NAME` / `PIHERDER_ACCENT` lock (blank = unlocked).
4. Hide Catalog: instance-wide nav only; do not 404 `/catalog`. Default **show**. Not auto-hide on first host.
5. **Demo** ignores name/accent/hide (always official PiHerder).

**1.6 candidate (not Must):**

| Slice | What |
|-------|------|
| **Brand-1** | Instance name + one accent; PWA title; demo ignored. |
| **Brand-2** | Hide Catalog in nav. |
| **Brand-3** | Own-docs MkDocs skin notes. |
| **Out** | Theme engine; replacing primary red; header logo upload; white-label; per-user skins. |

Park Brand-1/2 on [PLAN_v1.7.0.md](PLAN_v1.7.0.md) (out of 1.6, 2026-09-19).

### **M-flag** — Default-on migrate

**Freeze 2026-09-18: C — stay false.** Kill switch `PIHERDER_SERVICE_MIGRATE` remains default **off** at tag. Celery Move does **not** imply GA. Demo never. Leftover-remove stays extra-acked. Same opt-in pattern as console/Files.

(Rejected: **A** compose default `true`. **B** Settings toggle + env remains the shape if a later train turns Move on.)

### **W-mux** — Host `tmux` / `screen`

**Written 2026-09-13. Not 1.5 Should.** No host mux this freeze. Prior Cap sketch: [PLAN_v1.3.0.md](PLAN_v1.3.0.md) Stream W-mux.

**Leans:** Prefer **tmux**, then **screen**, else **plain PTY**. Default **off**, **opt-in per host**. Explicit **✕** kills the host session; Hide / app-switch **detaches**. Herder soft-park **stays**.

**Today:** Paramiko `invoke_shell`. Soft park is in-memory on **web** (`_held_sessions`). Survives WS drop / Hide / app-switch until idle, max, or park hold. **Dies** on recreate **web**, herder crash, logout, explicit close. Operators may type `tmux`/`screen` themselves; PiHerder does not start or reattach them.

**Gap a mux would fill:** durability **on the Pi** across herder web recycle. Daily tablet UX is already park. Mux is still low priority (binary detect, attach races, leftover processes, multi-operator isolation).

**Locks (if Mux-1 ships later):**

1. Probe tmux → screen → plain PTY + note. **Never** refuse the console. **Never** `apt install` on the host.
2. Per-host checkbox, default off. Missing binary on an opted-in host → plain PTY, do not fail open.
3. Session name per user + host + shell tab (`ph-u{user}-s{server}-n{tab}`). **Never** a shared `piherder` session. No attach across privileged vs fleet.
4. Do **not** replace herder park. Demo never mux. Command audit inside tmux stays best-effort / worse.
5. Host remove: leftover `ph-u*` sessions may remain until reboot or kill as that Unix user — document, do not silently `kill-server` for every user.

**1.6 candidate (not Must):**

| Slice | What |
|-------|------|
| **Mux-1** | Per-host opt-in; tmux then screen; detach vs kill as above; fallback PTY. |
| **Mux-2** | Reattach after web recycle; leftover session list/kill on SSH-access. |
| **Out** | Auto-on when binary present; replacing park; apt-install; shared lab tmux; mux on HAOS. |

Park Mux-1 on [PLAN_v1.6.0.md](PLAN_v1.6.0.md).

### **HA-p2** — HA → PiHerder integration (discover 1.5, **ship v1.6.0**)

**Written 2026-09-10. Not 1.5 Should.** No plugin repo, no `custom_components/` on `v1.5.0-dev`, no vendoring HA in the PiHerder image. Owning doc: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7.

**Product:** a **Home Assistant custom integration that runs on HA**. Fleet remote: dashboard, host/container/service entities, a few confirmed actions. Compose / Move / Files / console stay in **PiHerder** (deep link). Path 1 (PiHerder **manages HAOS** over SSH) is a different arrow — do not mix.

**Today (API, no plugin):**

- Token `read`: `GET /health`, `/servers` (`os_type`, `last_backup_at`, `os_updates_count`, `container_updates_count`, `reboot_pending`, `last_seen`), `/jobs?active_only=true`.
- Token `jobs`: POST backup / OS / container. **`service_migrate` is not an API job type** — keep it that way.
- No `GET /api/v1/summary`. No docker **list**, fleet **services**, or **disk facts** on the API. No notifications. No herder→HA webhook.
- CORS off is correct (HA Core is server-side). Token **IP allowlist** = HA host (HAOS ≈ LAN IP; container HA may be a bridge IP).

**Locks:**

1. **HACS** `custom_components/piherder` in a **new repo**. Config flow: URL + `ph_…` + TLS + poll interval. Coordinator poll.
2. Slice 1 token = **`read` only**. No OAuth, no add-on until a bridge is needed.
3. HA poll reads **DB snapshots only** — never SSH the fleet every 30s.
4. Hosts = HA **devices** from day one. Containers/services = entities in **1b** once snapshot APIs exist.
5. **No** start/stop from HA on the first plugin tag. Never Move / Files / console / OS apply from HA.
6. Events: poll-diff `piherder_job_completed`. Webhooks / `piherder_alert` later.
7. Optional 1.6 `GET /api/v1/summary` for the cheap heartbeat.

**1.6 slices (lock at that train open):**

| Slice | What | Bar |
|-------|------|-----|
| **1 (Must)** | Repo + config flow + fleet sensors + per-host devices + Open in PiHerder. Optional `summary`. | HA dashboard of the fleet without YAML |
| **1b (Should)** | Herder: last docker inventory, fleet services, last disk/OS facts. HA: container (running/uptime/image), service up/down, host disk. Still no start/stop. | Discovered items as entities |
| **2 (Should)** | Confirm + Backup this host (`jobs` + `feature:backup`). Optional OS check. | |
| **3 (Out)** | Start/stop, webhooks, alerts API, custom card, Move-from-HA, Files | |

**1.6:** ship Slice 1 in the separate repo; pull 1b/2 if the train wants them. See FEATURE_PLAN §7.

### **J-runtime** — Rest of web-process jobs

**Written 2026-09-18. Not 1.5 Should.** Do not move OS-patch / stack / template jobs this freeze.

**Leans:** Later Celery move is **all remaining exclusive jobs in one go**. If the **host is down** (Kuma / SSH not connecting), **queue and retry** — do not fail immediately. Worker kill of a **running** apt/compose stays **fail honest** (cannot resume mid-flight).

**Today — Celery:** `backup`, `service_migrate`, `nmap_*`, stale-data cleanup. Web recycle does not fail them.

**Today — web** (`BackgroundTasks` / thread pools; recycle web → startup `cleanup_orphan_web_jobs` fails the row):

| Family | Types |
|--------|-------|
| Patch | `os_patch`, `container_patch` |
| Checks | `os_update_check`, `container_update_check`, `docker_stack_check` |
| Stack mutate | `docker_stack_deploy` / `_stop` / `_start` / `_restart` / `_down` / `_remove` (shared lane) |
| Templates | `template_deploy`, `template_redeploy`, `template_drift_check` |
| Other | `herder_backup`, `retention` (not exclusive) |

**Locks (if Jr-1 ships later):**

1. One slice onto the **default Celery queue**. nmap stays `-Q nmap`. Keep DB exclusive + stack-mutating lane. Do not steal Move’s dual-host backup mutex for single-host apt.
2. Host unreachable: job stays **pending**, backoff until SSH works or **max wait**. Kuma/last_seen may **signal**; SSH probe is source of truth. Exclusive slot held. Not “fire a stored compose on wake.”
3. Worker recycle of **running** mutate: fail honest. Pending wait-for-host **redelivers**.
4. Demo never live-runs.

**1.7 candidate (not Must):** **Jr-1** exclusive types → Celery + wait-for-host. **Jr-2** Kuma/last_seen + Settings max wait. Park on [PLAN_v1.7.0.md](PLAN_v1.7.0.md) — **not** 1.6.

---

## 5. Ship bar

| Priority | Item | Bar | Status |
|----------|------|-----|--------|
| **Must** | **M-worker** | Move runs on Celery; web recycle does not fail it; worker recycle does; dual-host lock; JobHold | **Done** — Job #1314 + recycle web/worker **signed** 2026-09-18 |
| **Should** | **N3a** | Pin/hide/reorder `/reports` cards per user | **Done** — operator signed 2026-09-07 |
| **Should** | **N3b** | Move jobs card: count / fail / last dest | **Done** — operator signed 2026-09-18 |
| **Should** | **M-hb** | Heartbeats / stall visible | **Done** — reuse `_flush_job_progress` / JobHold DB poll |
| **Should** | **Q** | Tests; wiki truth; coverage ≥ 70% | **Bar met** (~70.6%; fail-under **70**) |
| **Discover** | M-undo · CSP-n · Brand · M-flag · W-mux · HA-p2 · J-runtime | Notes only | **All Discover write-ups done.** HA-p2 / Mux-1 / Undo-1 / CSP-n → [v1.6 Active](PLAN_v1.6.0.md). **J-runtime · Brand · AC-fg → v1.7**. |
| **Out** | **AC-fg** · M-live · ACME · NPM CRUD · Files token API · N3c · HA-p2 **code** | HA plugin ships **v1.6**; AC-fg parked **v1.7** | 1.6 opened 2026-09-19 |

---

## 6. Quality bar

| Gate | Target |
|------|--------|
| Unit | **≥ 70%** (fail-under **70**); enqueue on Celery · fail-on-worker-restart · dual-host exclusive · N3a persist |
| Tests | Extend `tests/test_service_migrate.py` (or sibling); no live SSH; mock Celery |
| E2E | Wizard chrome + lock disabled CTA (same as 1.4). Reports layout is unit-tested (`test_report_layout.py` + HTTP) |
| Docs | Wiki Move + Jobs + Reports; `mkdocs build --strict` at freeze |
| Security | Dual-host lock, staging wipe, path jail, audit without secret bodies, demo off |

---

## 7. Out of scope (stay honest)

- **AC-fg** fine-grained / per-host / per-feature grants — **pushed out 2026-09-07**; **out of 1.6** (2026-09-19). Three global roles stay. Park **v1.7**  
- **M-live** zero-downtime / rsync-while-running  
- Silent auto-rollback `finally` (Discover **M-undo** is a named job if ever promoted)  
- ACME-in-herder · full NPM proxy CRUD · Files token API  
- **N3c** widget picker / Grafana-in-herder  
- HA custom component **implementation** (Discover here; **v1.6.0** ship)  
- **80% unit coverage** — 1.x end goal; later trains. This freeze stays **70%**  
- **Archive 0.x PLAN/RELEASE** — parked [PLAN_v1.6.0.md](PLAN_v1.6.0.md) **Docs-archive-0x** (stubs + `docs/archive/v0/`)
- Moving OS-patch / stack / template jobs to Celery unless **J-runtime** is promoted  
- Multi-tenant SaaS · k8s/bare · branding theme engine · forcing `tmux` onto fleet hosts  

---

## 7a. Bugs this train

| ID | Bug | Fix |
|----|-----|-----|
| **B-reboot-i** | Host **Reboot now** used plain `reboot` / `systemctl reboot`. systemd logind **inhibits** while PiHerder SSH, a GUI seat (`gnome-session`), or another tty is logged in — CLI: *Operation inhibited… retry after logging out… `systemctl reboot -i`*. The command was backgrounded, so the UI could show success while the host stayed up (kernel update pending). | **Landed 2026-09-08** (`ad070db`): `systemctl reboot --ignore-inhibitors` (clean shutdown, not `--force`). Confirm copy notes SSH/desktop sessions are logged off. Wiki [Updates — Reboot](../wiki/day-to-day/updates-and-patching.md#reboot). Tests `tests/test_host_reboot.py`. Still allow Reboot now after kernel/OS pending. Operator QA **in progress**. |
| **B-login-json** | Expired / missing session painted FastAPI JSON `{"detail":"Please log in to continue"}` on any UI page (full navigation and HTMX swaps). | **Landed 2026-09-13:** HTML navigations **303** `/auth/login`; HTMX **`HX-Redirect`**; `/api/v1` stays JSON 401. Client fallback `session-login-redirect.js`. Tests `tests/test_login_redirect.py`. Operator QA **in progress**. |

---

## 8. Capture log

| Date | Note |
|------|------|
| 2026-09-06 | 1.4 PR review parked **M-worker** (Celery Move + heartbeats). 1.4 stays web `BackgroundTasks` |
| 2026-09-06 | **v1.4.0 tagged.** Kill switch false. Hub `1.4.0` / `1.4` / `latest` |
| 2026-09-07 | **Train opened** on `v1.5.0-dev`. Must **M-worker**. Should **N3a** + **M-hb** + **Q**. Discover: M-undo, CSP-n, Brand, M-flag, W-mux, HA-p2, J-runtime. **AC-fg out** (park ≥1.6). HA-p2 plugin **v1.6.0**. Package stays `1.4.0` until freeze. `main` patchable as **v1.4.x** |
| 2026-09-07 | **M-worker landed:** `app.tasks.service_migrate` on the backup worker queue. Dual-host **backup** Redis mutex (lower id first). `cleanup_orphan_web_jobs` skips Move. Worker redelivery of a `running` Move **fails** (staging kept, Start source stack). Unit tests inline via `PYTEST_CURRENT_TEST`. |
| 2026-09-07 | **N3a landed:** `/reports` pin / hide / reorder; cookie `ph_reports_layout`; Reset layout; viewer and demo POSTs allowed. Not Grafana. |
| 2026-09-07 | **Q coverage:** suite **~65%** line (`app`); CI fail-under raised **62 → 65**. Pack `tests/test_coverage_v15_pure.py` (job runners, backup progress/profiles, herder restore, OS-patch stream, onboarding scripts). |
| 2026-09-08 | **Q2 coverage:** suite **~70.6%** line (`app`); CI fail-under raised **65 → 70**. Packs `tests/test_coverage_v15_q2.py` (template apply/redeploy/drift, job enqueue, OIDC, SSH onboarding, docker write/validate, host Files docker, DNS plan) + `tests/test_coverage_v15_q2b.py` (WebAuthn, alert policy, registry bindings, Kuma coverage, harden/editor, backup Celery, TOTP QR). |
| 2026-09-08 | **1.x coverage end goal locked at 80%.** v1.5 freeze stays **70**. Later 1.x minors step the fail-under (~5pp) until 80. Service tests first; no 100% target. |
| 2026-09-08 | **0.x PLAN/RELEASE archive** pushed to **v1.6** ([PLAN_v1.6.0.md](PLAN_v1.6.0.md) Docs-archive-0x). Not this freeze. |
| 2026-09-08 | **N3b landed:** `/reports` **Move jobs** card — count / fail / last dest from finished `service_migrate` Jobs. Cookie layout treats `move` as a sixth card (appended on old cookies). |
| 2026-09-08 | **CSP-n Discover written.** 71 inline scripts · 190 `on*` handlers. Not 1.5 Should (CSP3 nonce drops `'unsafe-inline'`). Slice 1 (nonce + `script-src-attr`) parked on [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Style stays unsafe-inline. |
| 2026-09-08 | **Bug B-reboot-i:** Reboot now ignored systemd inhibitors (SSH/GUI/tty). Now `systemctl reboot --ignore-inhibitors`. |
| 2026-09-10 | **Bug B-login-json:** expired session showed JSON `detail` instead of Sign in. HTML → 303 login; HTMX → `HX-Redirect`; API JSON unchanged. Code committed 2026-09-13. |
| 2026-09-10 | **HA-p2 Discover written.** HACS integration on HA; Slice 1 fleet+host devices; 1b container/service entities from snapshots; details open PiHerder. Plugin ship v1.6. No code on this branch. |
| 2026-09-13 | **Operator QA in progress:** leftover recycle **web** / **worker** mid-Move; remaining boxes N3b · B-login-json · B-reboot-i · leftover M-worker · 1.4 regression. Boxes stay open until signed. |
| 2026-09-13 | **M-undo Discover written.** Fail-path only; stop dest then start source; never silent `finally` / dest wipe. Job `service_migrate_undo` parked on [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Not 1.5 Should. |
| 2026-09-13 | **W-mux Discover written.** tmux then screen else PTY; opt-in per host; ✕ kills session, Hide detaches; herder park stays. Mux-1 parked on [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Not 1.5 Should. |
| 2026-09-18 | **Brand Discover written.** Wordmark + one accent; hide Catalog in nav; official mark + primary red stay. Brand-1/2 parked on [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Not 1.5 Should. |
| 2026-09-18 | **J-runtime Discover written.** Remaining exclusive jobs still web. Later: all of them on Celery in one go; host-down queue/retry; running mutate fail-honest. Jr-1 parked on [PLAN_v1.7.0.md](PLAN_v1.7.0.md) (**not** 1.6). Not 1.5 Should. |
| 2026-09-18 | **Freeze.** Operator QA signed. **M-flag C** (kill switch stays false). Package **1.5.0**. [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md). |
| 2026-09-19 | **v1.6.0 train opened** on `v1.6.0-dev` — [PLAN_v1.6.0.md](PLAN_v1.6.0.md). Must HA-p2 Slice 1 + Mux-1 + Q-80. Brand + AC-fg slipped to [PLAN_v1.7.0.md](PLAN_v1.7.0.md). |
| 2026-09-07 | **Docs pass:** wiki Move / Jobs / Reports / multi-worker / architecture / upgrades 1.4→1.5 / troubleshooting; ADMIN migrate+Celery; README / SPEC / ROADMAP / QA aligned. 1.4 RELEASE stays historical (web `BackgroundTasks`). |

---

## 9. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Delete spent `v1.4.0-dev` · open **`v1.5.0-dev`** + lock Must/Should | **Done** 2026-09-07 |
| 2 | Spike dual-host Celery lock vs per-server backup mutex | **Done** — reuse `backup` mutex on both ids |
| 3 | Land **MW1–MW4** enqueue + fail-on-worker-restart | **Done** 2026-09-07 |
| 4 | **M-hb** heartbeats + JobHold | **Done** — reuse `_flush_job_progress` on the worker (JobHold polls DB) |
| 5 | Wiki Move + Jobs (worker truth) | **Done** 2026-09-07; docs pass widened 2026-09-07 |
| 6 | **N3a** Reports layout (Should; after M-worker moving) | **Done** 2026-09-07 — pin/hide/↑↓, cookie `ph_reports_layout`; operator signed |
| 6b | **N3b** Move jobs Reports card | **Done** 2026-09-08 — count / fail / last dest |
| 6c | **B-reboot-i** systemd inhibit on host reboot | **Done** 2026-09-08 — `--ignore-inhibitors` |
| 6d | **B-login-json** expired session JSON on UI pages | **Done** 2026-09-13 — 303 / `HX-Redirect` / fetch fallback. Operator QA **in progress** |
| 7 | Discover notes: undo matrix, CSP count, HA-p2 entities | **Done** 2026-09-18 — CSP-n · HA-p2 · M-undo · W-mux · Brand · J-runtime. **M-flag** stays freeze. |
| 8 | **Q** raise unit coverage **62 → 70** | **Done** 2026-09-07 (65%) · **Q2 2026-09-08 (70%)** — `test_coverage_v15_q2.py` + `test_coverage_v15_q2b.py`; CI fail-under **70** |
| 9 | Leftover live QA: recycle **web** mid-Move; recycle **worker** mid-Move | **Done** 2026-09-18 |
| 9b | Remaining operator boxes: N3b · B-login-json · B-reboot-i · leftover M-worker · 1.4 regression | **Done** 2026-09-18 |
| 10 | Freeze · **M-flag** question · version `1.5.0` · tag · Hub | **Freeze** 2026-09-18 — **M-flag C** · package **1.5.0**. Tag · Hub after merge |

---

*Production is [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md). Next train: [PLAN_v1.6.0.md](PLAN_v1.6.0.md).*
