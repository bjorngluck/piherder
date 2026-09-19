# PiHerder v1.6.0 — HACS on HA + host console mux

**Status:** **Active** — train opened 2026-09-19 on `v1.6.0-dev`. Package stays **`1.5.0`** until freeze.  
**Date opened:** 2026-09-19  
**Git branch:** `v1.6.0-dev` → `main` · tag `v1.6.0` (at freeze)  
**Package / image version:** **`1.5.0`** until freeze. Hub tags after merge.  
**Theme:** **HACS fleet remote** (HA → PiHerder) + **host console mux** (Mux-1) + unit **≥ 75%**  
**Baseline:** `v1.5.0` (tagged 2026-09-18; Hub digest `sha256:98cf929a6577b84ca03c5f8145f7cf24021ed56176a986a314f3b3cb59145949`)  
**Mode:** **Must → Should → Discover.** Must **HA-p2 Slice 1** + **Mux-1** + **Q-80**. Should **Slice 1b** + **Docs-archive-0x** + **CSP-n Slice 1** + **Undo-1**. **Brand** and **AC-fg** are out (park **v1.7**).  
**QA:** [QA_v1.6.0.md](QA_v1.6.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.5.0.md](PLAN_v1.5.0.md) · [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md) · [PLAN_v1.7.0.md](PLAN_v1.7.0.md) (candidate inbox) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7 · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [API.md](API.md) · [SPEC.md](../SPEC.md) · wiki [HAOS hosts](../wiki/day-to-day/haos-hosts.md) · [API tokens](../wiki/operations/api-tokens.md) · [web SSH](../wiki/day-to-day/web-ssh-console.md) · [Move a service](../wiki/docker/service-migration.md)

> **Train open 2026-09-19.** Production stays **v1.5.0** on `main`. Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false**. Plugin is a **separate** HACS repo — not in this image.

---

## 0. Intent

1.5 Move runs on Celery. Operators who also run Home Assistant still glue YAML `rest` sensors to a token. Path 2 (HA **observes** the PiHerder fleet) was discovered in 1.5; this train **ships Slice 1**. Console still dies on herder web recycle (in-memory park); Mux-1 puts an opt-in `tmux`/`screen` session on the Pi.

Wanted:

1. A HACS integration **on HA**: config flow, coordinator, fleet sensors, per-host devices, Open in PiHerder  
2. Token **`read` only** for Slice 1; poll **DB snapshots**, never SSH the fleet every 30s  
3. Per-host console mux (tmux then screen else PTY) so Hide detaches and ✕ kills  
4. Unit coverage **≥ 75%** (CI fail-under **75**; 1.x ceiling remains 80%)

**Out of 1.6 product code:** **Brand-1/2**, **AC-fg**, **J-runtime**, HA Slice 3 (start/stop, webhooks, Move-from-HA), Brand-3, theme engine, default-on Move, plugin inside the PiHerder image.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.6.0-dev`** |
| Production line | **`main` @ `v1.5.0`** — hotfixes → **`v1.5.x`**, port here |
| Git tag (freeze) | **`v1.6.0`** (RCs: `1.6.0-rc.N` if needed) |
| Image tags (freeze) | `1.6.0` · `1.6` · `latest` (multi-arch); keep `1.5` / `1.5.x` pins valid |
| HACS repo | **New git repo** (`custom_components/piherder`). Do **not** vendor HA in this image. Name at Phase 1 (lean: `bjorngluck/piherder-ha`) |
| In-scope streams | **HA-p2 Slice 1** Must · **Mux-1** Must · **Q-80** Must · **Slice 1b** Should · **Docs-archive-0x** Should · **CSP-n Slice 1** Should · **Undo-1** Should |
| Out-of-focus | **Brand** · **AC-fg** · **J-runtime** · HA Slice 3 · Brand-3 · theme engine · M-flag C (stay false) · ACME · NPM CRUD · Files token API · N3c · M-live · multi-tenant · Swarm/k8s |
| Mode | Must → freeze; Should may slip; Discover only if Must is green |
| Coverage | **≥ 75%** unit; CI fail-under **75**; service tests first; no 100% target; do not chase router %. **1.x end goal 80%** is later trains |
| E2E | Wizard chrome still loads. No live HA / two-host / mux host in CI |
| Semver | Additive minor; new read APIs (1b) and optional `summary`; console opt-in mux |
| Version bump | `1.6.0` **at freeze only** |
| Kill switch | **`PIHERDER_SERVICE_MIGRATE=false`**. Demo never copies, never mux, never backup-from-HA |

```text
main @ v1.5.0 (+ v1.5.x patches)
  └─ v1.6.0-dev → merge → main → tag v1.6.0 → Hub
     + separate HACS repo (not this image)
```

| Rule | Practice |
|------|----------|
| Must → then freeze | Do not start Out items while Must is open |
| Should may slip | Slice 1b / CSP-n / Undo-1 / archive do **not** block the tag if Must is green |
| Prod critical bugs | **main** as **1.5.x** first, then port here |
| Demo never grows teeth | Real migrate off · real Files SFTP off · mux off · no HA mutating actions |
| Residual Cap | **Brand** · **AC-fg** · J-runtime · HA Slice 3 stay out |
| Tag honesty | **v1.6.0 tags only with Slice 1 + Mux-1 + fail-under 75** |

---

## 1a. Kickoff leans (locked 2026-09-19)

| # | Question | Decision |
|---|----------|----------|
| 1 | Theme / Must | **HA-p2 Slice 1**. HACS on HA: fleet + host devices. Separate repo. |
| 2 | Slice 1b snapshot entities | **Should**. New herder read APIs + HA container/service/disk entities. Still no start/stop. May slip. |
| 3 | Slice 2 backup-from-HA | **Discover**. First plugin tag is read-only. |
| 4 | `GET /api/v1/summary` | Optional on Slice 1 if cheap. Token `read`. |
| 5 | **W-mux Mux-1** | **Must**. Per-host opt-in; tmux then screen else PTY. Mux-2 Discover. |
| 6 | **Q-80** | **Must**. Fail-under **75** (typical ~5pp toward 80). |
| 7 | **Docs-archive-0x** | **Should**, Phase 0b. Stubs at old paths. |
| 8 | **CSP-n Slice 1** | **Should**. Nonce + `script-src-attr`; style stays unsafe-inline; Report-Only on demo first. Do not rewrite `onclick`. |
| 9 | **M-undo Undo-1** | **Should**. Fail-path named job after cutover/rebind/validate. Undo-2 Discover. Never reverse a green Move. |
| 10 | **Brand-1/2** | **Out of 1.6.** Park **v1.7**. No chrome code. No theme engine. |
| 11 | **AC-fg** | **Out of 1.6.** Park **v1.7**. Three global roles stay. No discover spike. |
| 12 | **J-runtime** | **Out.** Already [PLAN_v1.7.0.md](PLAN_v1.7.0.md). |
| 13 | HA Slice 3 / Move-from-HA / start-stop | **Out** |
| 14 | M-flag / default-on Move | **Stay false** (1.5 freeze C) |
| 15 | Files token API / ACME / NPM CRUD / N3c / M-live | Out |
| 16 | Version bump | `1.6.0` at freeze only |
| 17 | E2E | Wizard chrome. No live HA in CI. Plugin tests mock `/api/v1`. |

---

## 1b. Recommended delivery order

```text
Phase 0   Open train + docs lock              ← this commit 2026-09-19
Phase 0b  Docs-archive-0x (Should)            stubs; mkdocs --strict
Phase 1   HA-p2 Slice 1                       new HACS repo + wiki/API copy
Phase 2   Mux-1                               this repo; per-host opt-in
Phase 3   Q-80                                fail-under 75; may overlap 1–2
Phase 4   Slice 1b snapshot APIs + entities   Should; herder + plugin
Phase 5   CSP-n Slice 1                       Should; Report-Only demo first
Phase 6   Undo-1                              Should; named job, fail-path only
Phase 7   Wiki + QA_v1.6.0                    operator sign-off
Phase 8   Freeze                              1.6.0 bump · RELEASE · PR · tag · Hub — only when asked
```

Must before Should product. Discover (Slice 2, Undo-2, Mux-2) only if Must is green and you promote.

---

## 2. Stream **HA-p2** — HACS on HA (Slice 1 Must)

Owning design: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7. 1.5 Discover: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 HA-p2.

Path 1 (PiHerder **manages HAOS** over SSH) already shipped in 0.9. This stream is the **other arrow**: HA observes the fleet.

| ID | Item | Notes |
|----|------|--------|
| HA1 | New git repo | `custom_components/piherder`. Not inside this tree. Not in the image. Lean name `bjorngluck/piherder-ha` (confirm at Phase 1) |
| HA2 | Config flow | Base URL + `ph_…` token + TLS verify + poll interval |
| HA3 | Coordinator | `DataUpdateCoordinator` poll. Snapshot reads only — **never SSH** the fleet on the HA interval |
| HA4 | Fleet sensors | Herder up, host count, OS/container updates, reboot pending, running jobs, Move in progress |
| HA5 | Host devices | One HA device per PiHerder server; OS type, last seen, reboot pending, backup age |
| HA6 | Open in PiHerder | `{origin}/servers/{id}` (and job / Docker tab as needed) |
| HA7 | Auth | Slice 1 token **`read` only**. No OAuth, no session cookie, no CORS. IP allowlist = HA host |
| HA8 | Optional `summary` | `GET /api/v1/summary` if cheap — `{ ok, version, hosts, os_updates, container_updates, reboot_pending, jobs_running, move_running, last_backup_oldest_at }` |
| HA9 | Wiki + HACS readme | Operator install: HACS custom repo, token with `read`, allowlist. YAML `rest:` remains possible |
| HA10 | CI | Mock `/api/v1`. No live Home Assistant. `tests/test_haos.py` stays path 1 |

**Hard rules:** no start/stop from HA on the first plugin tag. Never Move, compose write, Files, console, decrypt keys, OS **apply** from HA. `service_migrate` stays off `POST /api/v1/…/jobs`. “Host down” is `last_seen` age, not a live SSH ping.

**Success (Must):**

1. Config flow accepts URL + token; coordinator polls without error.  
2. HA shows fleet sensors and one device per host.  
3. “Open in PiHerder” reaches the herder UI.  
4. Token without `read` fails closed. Viewer-class token is enough.  
5. Demo never. No plugin files in the PiHerder image.

### Slice 1b (Should)

Herder: last Docker **inventory**, **fleet services**, last **disk/OS facts** — same snapshots the UI already stores. HA: container (running/uptime/image), service up/down, host disk. Still no start/stop.

### Slice 2 (Discover)

Confirm + `piherder.backup`; token `jobs` + `feature:backup`. Optional OS **check** (not apply). Not this freeze unless promoted.

---

## 3. Stream **Mux-1** — host `tmux` / `screen` (Must)

Owning discover: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 W-mux · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md).

**Today:** Paramiko `invoke_shell`. Soft park is in-memory on **web** (`_held_sessions`). Dies on recreate **web**, herder crash, logout. Survives WS drop / Hide until idle/max/hold.

| ID | Item | Notes |
|----|------|--------|
| MX1 | Probe | tmux → screen → plain PTY + note. **Never** refuse the console. **Never** `apt install` |
| MX2 | Opt-in | Per-host checkbox, default **off**. Missing binary → PTY, do not fail open |
| MX3 | Session name | `ph-u{user}-s{server}-n{tab}`. Never a shared `piherder` session. No attach across privileged vs fleet |
| MX4 | Close vs Hide | ✕ **kills** the host session. Hide / app-switch **detaches**. Herder park **stays** |
| MX5 | Demo / HAOS | Demo **never** mux. Mux on HAOS is Out |
| MX6 | Docs | Wiki web SSH: leftover `ph-u*` on host remove; command audit inside tmux is best-effort |

**Mux-2 (Discover):** reattach after web recycle; leftover list/kill on SSH-access.

**Success (Must):**

1. Host with mux off → same PTY as 1.5.  
2. Host with mux on and tmux present → named session; Hide detaches; ✕ kills.  
3. Host with mux on and no binary → PTY + honest note.  
4. Recreate **web** while detached: session still on the Pi (Mux-1); herder park still works for non-mux.  
5. Viewer 403; demo never mux.

---

## 4. Stream **Q-80** — coverage step (Must)

1.5 freeze: suite **~70.6%** line on `app`; CI `--cov-fail-under=70`. 1.x ceiling **80%**. Typical step **~5pp**.

| ID | Item | Notes |
|----|------|--------|
| Q1 | CI | `.github/workflows/test.yml` fail-under **75**. Wiki [testing](../wiki/developers/testing.md) |
| Q2 | Tests | Service tests first (mux probe, HA summary/snapshots if pulled, undo preview). No live SSH/HA |
| Q3 | Freeze bar | Tag does **not** ship below 75 |

Do not chase router %. No 100% target.

---

## 5. Should streams (may slip)

### **Docs-archive-0x** (Phase 0b)

Spent pre-1.0 train records only:

- Move `docs/PLAN_v0.*.md` and `docs/RELEASE_v0.*.md` → `docs/archive/v0/`
- Leave a **stub** at the old path (title + “moved to archive” + link)
- Do **not** archive `FEATURE_PLAN_*`, `ROADMAP_ECOSYSTEM.md`, `SPEC.md`, `ADMIN.md`, or **v1.0+** PLAN/RELEASE
- Optional `docs/README.md` index of live vs archived
- `mkdocs build --strict` after

### **CSP-n Slice 1**

1.5 Discover: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 CSP-n. 71 inline `<script>` · 190 `on*` handlers.

- Per-request nonce **before** render (not process-wide `env.globals`)
- Stamp the 71 tags; `script-src` nonce drops `'unsafe-inline'` unless `script-src-attr 'unsafe-inline'`
- Style stays `'unsafe-inline'`. Do **not** rewrite `onclick` this train
- OpenAPI `/docs` `/redoc` keep `'unsafe-inline'`
- Turnstile host allowlist + nonce the login boot script
- **Report-Only on demo first** (`PIHERDER_CSP_REPORT_ONLY`) before enforce

### **Undo-1**

1.5 Discover: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 M-undo · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md).

Named job **`service_migrate_undo`**. JobHold CTA on a **failed** Move whose `failed_step` is `cutover` / rebind / `validate`: revert DNS/NPM, `restartdns`, revert control-plane rows, **stop dest**, **start source**. Dest dir + volumes **stay**. Kill switch = `PIHERDER_SERVICE_MIGRATE`. Demo never. Viewer 403. Token API never POSTs migrate or undo. Never reverse a **green** Move. Never silent `finally` / dest wipe / leftover-remove reverse. Pre-flip stays Start source stack.

**Undo-2 (Discover):** dest_up worker-restart inspect helper.

---

## 6. Discover catalog

Written findings. No schema / plugin mutating actions until a row is promoted.

| ID | Item | Notes |
|----|------|--------|
| **HA-p2 Slice 2** | Backup from HA | Confirm + `piherder.backup`; `jobs` + `feature:backup`. Optional OS check. |
| **Undo-2** | dest_up inspect | Worker-restart helper; optional stop-dest-then-start-source without DNS revert |
| **Mux-2** | Reattach | After web recycle; leftover list/kill |

---

## 7. Ship bar

| Priority | Item | Bar | Status |
|----------|------|-----|--------|
| **Must** | **HA-p2 Slice 1** | HACS config flow + fleet sensors + host devices + Open in PiHerder; token `read`; not in this image | **Open** |
| **Must** | **Mux-1** | Per-host opt-in tmux/screen; Hide detaches; ✕ kills; fallback PTY | **Open** |
| **Must** | **Q-80** | Unit ≥ **75%**; CI fail-under **75** | **Open** (1.5 was ~70.6% / 70) |
| **Should** | **Slice 1b** | Snapshot APIs + container/service/disk entities | **Open** |
| **Should** | **Docs-archive-0x** | `docs/archive/v0/` + stubs | **Open** |
| **Should** | **CSP-n Slice 1** | Script nonces; Report-Only demo first | **Open** |
| **Should** | **Undo-1** | Fail-path `service_migrate_undo` | **Open** |
| **Discover** | Slice 2 · Undo-2 · Mux-2 | Notes only unless promoted | Parked |
| **Out** | Brand · AC-fg · J-runtime · HA Slice 3 · M-flag C · plugin-in-image | Park Brand + AC-fg on **v1.7** | Locked 2026-09-19 |

---

## 8. Quality bar

| Gate | Target |
|------|--------|
| Unit | **≥ 75%** (fail-under **75**); mux probe · HA API mocks · undo preview if pulled |
| Tests | No live SSH / HA / two-host copy. Plugin CI mocks `/api/v1` |
| E2E | Wizard chrome (same as 1.5). No live mux host in CI |
| Docs | Wiki HA integration + web SSH mux + Move undo (if pulled); `mkdocs build --strict` at freeze |
| Security | Token `read` for Slice 1; IP allowlist; demo off; mux never apt-install; undo never dest wipe |

---

## 9. Out of scope (stay honest)

- **Brand-1/2** instance wordmark + accent + hide Catalog — **out 2026-09-19**. Park [PLAN_v1.7.0.md](PLAN_v1.7.0.md). No theme engine, no logo upload  
- **AC-fg** per-host / per-feature grants — **out 2026-09-19**. Three global roles stay. Park **v1.7**  
- **J-runtime** remaining exclusive jobs → Celery — already **v1.7**  
- HA Slice 3: start/stop, webhooks, alerts API, add-on, Lovelace card, Move-from-HA, Files from HA  
- Brand-3 own-docs MkDocs skin  
- Default-on Move (**M-flag C** stays false)  
- Plugin / add-on **inside** the PiHerder image  
- Mux on HAOS · auto-on when binary present · shared lab tmux  
- Reverse a **green** Move · dest `down -v`  
- ACME-in-herder · full NPM CRUD · Files token API · **N3c** · **M-live**  
- Multi-tenant SaaS · k8s/bare  

---

## 10. Capture log

| Date | Note |
|------|------|
| 2026-09-08 | Candidate file created. Inbox: HA-p2 ship, AC-fg, coverage step toward 80%, **0.x PLAN/RELEASE archive**. Train not opened. |
| 2026-09-08 | **CSP-n Slice 1** parked (script nonces + `script-src-attr`; style stays unsafe-inline). |
| 2026-09-10 | **HA-p2 Discover** landed on 1.5. Inbox: Slice 1 Must, 1b snapshot entities, 2 backup action. |
| 2026-09-13 | **M-undo Discover** landed on 1.5. Inbox: Undo-1 named job. |
| 2026-09-13 | **W-mux Discover** landed on 1.5. Inbox: Mux-1 per-host opt-in. |
| 2026-09-18 | **Brand Discover** landed on 1.5. Inbox: Brand-1/2. |
| 2026-09-18 | **J-runtime** Discover written on 1.5; parked on **[PLAN_v1.7.0.md](PLAN_v1.7.0.md)**. |
| 2026-09-18 | **v1.5.0 tagged.** Kill switch false. Hub `1.5.0` / `1.5` / `latest`. |
| 2026-09-19 | **Train opened** on `v1.6.0-dev`. Must **HA-p2 Slice 1** + **Mux-1** + **Q-80** (fail-under **75**). Should **Slice 1b** + **Docs-archive-0x** + **CSP-n Slice 1** + **Undo-1**. Discover Slice 2 / Undo-2 / Mux-2. **Brand** and **AC-fg** out → **v1.7**. Package stays `1.5.0` until freeze. `main` patchable as **v1.5.x**. |

---

## 11. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.6.0-dev`** + lock Must/Should | **This commit** 2026-09-19 |
| 2 | **Docs-archive-0x** (Should, Phase 0b) | Open |
| 3 | Confirm HACS repo name · create public MIT repo | Phase 1 — not this commit |
| 4 | Slice 1: config flow + coordinator + fleet + host devices | Open |
| 5 | **Mux-1** per-host opt-in | Open |
| 6 | **Q-80** raise fail-under **70 → 75** | Open |
| 7 | Slice 1b / CSP-n / Undo-1 as capacity after Must | Open |
| 8 | Wiki + QA · freeze · `1.6.0` · tag · Hub | When asked |

---

*Production remains [RELEASE_v1.5.0.md](RELEASE_v1.5.0.md) until this train freezes.*
