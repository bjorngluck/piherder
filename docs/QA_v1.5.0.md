# PiHerder v1.5.0 — operator QA / sign-off

**Branch:** `v1.5.0-dev` → `main` · tag **`v1.5.0`** (cut after merge)  
**Code freeze:** *not yet*  
**Package:** stays **`1.4.0`** until freeze.  
**Operator QA:** *not started.*

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
- [ ] Dual-host exclusive: backup or stack mutate on source **or** dest blocked while Move runs  
- [ ] Viewer POST 403  
- [ ] Demo never copies  
- [ ] Wiki no longer says “do not recycle web during a Move” as current 1.5 truth  

## N3a — Reports layout (Should; may slip)

- [ ] `/reports`: pin / hide / reorder existing cards  
- [ ] Reload: order sticks (per user)  
- [ ] Reset to default  
- [ ] Viewer can use layout chrome; data still read-only  
- [ ] No Grafana iframes / widget picker  

## 1.4 regression

- [ ] Host lock + HAOS refuse  
- [ ] Direct TLS Move still works (on worker)  
- [ ] NPM-fronted Move still PUTs `forward_host`  
- [ ] Leftover / optional source remove (disposable stack only)  
- [ ] Policy, Reports history data, Files, console  

## Freeze

- [ ] Unit ≥ 62% (fail-under 62)  
- [ ] `mkdocs build --strict`  
- [ ] **M-flag** freeze question recorded (default stays false unless decided otherwise)  
- [ ] Version bump `1.5.0` · tag · Hub  
