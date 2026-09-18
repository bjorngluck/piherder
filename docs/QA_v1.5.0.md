# PiHerder v1.5.0 — operator QA / sign-off

**Branch:** `v1.5.0-dev` → `main` · tag **`v1.5.0`** (cut after merge)  
**Code freeze:** **2026-09-18**  
**Package:** **`1.5.0`**  
**Operator QA:** **signed 2026-09-18** — **N3a** signed 2026-09-07. **M-worker** live Job **#1314** plus recycle web/worker. Remaining boxes signed with freeze.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes: [Move a service](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Reports](../wiki/day-to-day/reports.md). Screenshot capture list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md).

Plan: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) · migrate design: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md).

1.4 production sign-off stays [QA_v1.4.0.md](QA_v1.4.0.md) (historical).

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.5.0`** / `v1.5.0-dev` (`docker compose build web celery-worker && docker compose up -d`). About / footer **1.5.0** |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least **two** real SSH Docker hosts + one HAOS (never a Move dest) |
| **Flags** | Move wizard: `PIHERDER_SERVICE_MIGRATE=true` then recreate **web**. Demo never copies. Lock needs no flag |

---

## M-worker — Move on Celery (Must)

**Signed 2026-09-18.**

- [x] Flag **off** → Move 404; lock still works  
- [x] Start a disposable Move; recreate **web** mid-copy → job still **running**; JobHold still streams  
- [x] Recreate **celery-worker** mid-copy → job **failed** (honest); staging kept; **Start source stack** when fail is copy/dest-up  
- [x] Dual-host exclusive: backup or stack mutate on source **or** dest blocked while Move runs — Job **#1314** held backup mutex on both hosts (ids 5 then 6)  
- [x] Viewer POST 403  
- [x] Demo never copies  
- [x] Wiki no longer says “do not recycle web during a Move” as current 1.5 truth — [Move](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Multi-worker](../wiki/operations/multi-worker.md)  

**Live:** Job **#1314** (2026-09-07) — Celery worker, NPM-fronted Open WebUI RPI5-4→RPI5-3, `ai.hacknow.info` `forward_host` PUT, leftover remove, staging wiped. Recycle-web-mid-copy and recycle-worker-fail **signed 2026-09-18**.

## N3a — Reports layout (Should; may slip)

- [x] `/reports`: pin / hide / reorder existing cards — operator signed 2026-09-07  
- [x] Reload: order sticks (per user) — cookie `ph_reports_layout`  
- [x] Reset to default  
- [x] Viewer can use layout chrome; data still read-only  
- [x] No Grafana iframes / widget picker  

## N3b — Move jobs card (Should stretch)

**Signed 2026-09-18.**

- [x] `/reports` **Move jobs** card: runs / fail / last dest from finished `service_migrate` Jobs  
- [x] Empty window copy when no Moves  
- [x] Pin / hide / reorder includes Move (sixth card; old cookies append it)  
- [x] Viewer can see the card (read-only)  

## B-login-json — Expired session must Sign in (bug)

**Signed 2026-09-18.**

- [x] Leave a UI page open until the session cookie expires (or delete `access_token`), then click any nav link or refresh. **Sign in** — never a JSON page `{"detail":"Please log in to continue"}`.  
- [x] Same after an HTMX click (layout save, fragment refresh): full browser goes to Sign in, not a JSON swap in the page.  
- [x] `/api/v1` with a missing/expired Bearer still returns JSON 401 (not a login HTML page).

## B-reboot-i — Host reboot vs logind inhibitors (bug)

**Signed 2026-09-18.**

- [x] **Reboot now** on a host with a kernel/OS reboot pending actually restarts (including the herder host, and a Pi with a desktop seat / extra SSH).  
- [x] Confirm copy mentions SSH/desktop sessions are logged off.  
- [x] Does **not** use `systemctl reboot --force`. From a shell, `systemctl reboot -i` is the same as the UI path. Wiki [Updates — Reboot](../wiki/day-to-day/updates-and-patching.md#reboot).

## 1.4 regression

**Signed 2026-09-18.**

- [x] Host lock + HAOS refuse  
- [x] Direct TLS Move still works (on worker)  
- [x] NPM-fronted Move still PUTs `forward_host` — Job **#1314**  
- [x] Leftover / optional source remove (disposable stack only) — Job **#1314**  
- [x] Policy, Reports history data, Files, console  

## Freeze

- [x] Unit ≥ 70% (fail-under 70) — suite **~70.6%**; CI `--cov-fail-under=70`. **1.x end goal 80%** is later trains, not this freeze.  
- [x] `mkdocs build --strict` — re-run at freeze 2026-09-18  
- [x] **M-flag** **C** — default stays **false** at tag (worker does not imply GA)  
- [x] Version bump `1.5.0` (this freeze commit). Tag · Hub after merge to `main`  
