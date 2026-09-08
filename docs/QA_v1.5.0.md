# PiHerder v1.5.0 — operator QA / sign-off

**Branch:** `v1.5.0-dev` → `main` · tag **`v1.5.0`** (cut after merge)  
**Code freeze:** *not yet*  
**Package:** stays **`1.4.0`** until freeze.  
**Operator QA:** **partial** — **N3a** signed 2026-09-07. **M-worker** live Job **#1314** (NPM-fronted Open WebUI) green. Recycle **web** / **worker** mid-copy **not** live-proved.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes: [Move a service](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Reports](../wiki/day-to-day/reports.md). Screenshot capture list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md).

Plan: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) · migrate design: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md).

1.4 production sign-off stays [QA_v1.4.0.md](QA_v1.4.0.md) (historical).

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.5.0-dev`** (`docker compose build web celery-worker && docker compose up -d`). About / footer still **1.4.0** until freeze |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least **two** real SSH Docker hosts + one HAOS (never a Move dest) |
| **Flags** | Move wizard: `PIHERDER_SERVICE_MIGRATE=true` then recreate **web**. Demo never copies. Lock needs no flag |

---

## M-worker — Move on Celery (Must)

- [ ] Flag **off** → Move 404; lock still works  
- [ ] Start a disposable Move; recreate **web** mid-copy → job still **running**; JobHold still streams  
- [ ] Recreate **celery-worker** mid-copy → job **failed** (honest); staging kept; **Start source stack** when fail is copy/dest-up  
- [x] Dual-host exclusive: backup or stack mutate on source **or** dest blocked while Move runs — Job **#1314** held backup mutex on both hosts (ids 5 then 6)  
- [ ] Viewer POST 403  
- [ ] Demo never copies  
- [x] Wiki no longer says “do not recycle web during a Move” as current 1.5 truth — [Move](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Multi-worker](../wiki/operations/multi-worker.md)  

**Live:** Job **#1314** (2026-09-07) — Celery worker, NPM-fronted Open WebUI RPI5-4→RPI5-3, `ai.hacknow.info` `forward_host` PUT, leftover remove, staging wiped. Did **not** prove recycle-web-mid-copy or recycle-worker-fail.

## N3a — Reports layout (Should; may slip)

- [x] `/reports`: pin / hide / reorder existing cards — operator signed 2026-09-07  
- [x] Reload: order sticks (per user) — cookie `ph_reports_layout`  
- [x] Reset to default  
- [x] Viewer can use layout chrome; data still read-only  
- [x] No Grafana iframes / widget picker  

## N3b — Move jobs card (Should stretch)

- [ ] `/reports` **Move jobs** card: runs / fail / last dest from finished `service_migrate` Jobs  
- [ ] Empty window copy when no Moves  
- [ ] Pin / hide / reorder includes Move (sixth card; old cookies append it)  
- [ ] Viewer can see the card (read-only)  

## 1.4 regression

- [ ] Host lock + HAOS refuse  
- [ ] Direct TLS Move still works (on worker)  
- [x] NPM-fronted Move still PUTs `forward_host` — Job **#1314**  
- [x] Leftover / optional source remove (disposable stack only) — Job **#1314**  
- [ ] Policy, Reports history data, Files, console  

## Freeze

- [x] Unit ≥ 70% (fail-under 70) — suite **~70.6%**; CI `--cov-fail-under=70`. **1.x end goal 80%** is later trains, not this freeze.  
- [x] `mkdocs build --strict` — green 2026-09-07 docs pass  
- [ ] **M-flag** freeze question recorded (default stays false unless decided otherwise)  
- [ ] Version bump `1.5.0` · tag · Hub  
