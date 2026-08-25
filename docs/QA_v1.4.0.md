# PiHerder v1.4.0 — operator QA / sign-off

**Branch:** `v1.4.0-dev` → `main` · tag **`v1.4.0`** (cut after merge)  
**Code freeze:** not yet — train opened 2026-08-25.  
**Package:** stays **`1.3.0`** until freeze bump.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · design: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · operator wiki: [service-migration.md](../wiki/docker/service-migration.md).

1.3 production sign-off stays [QA_v1.3.0.md](QA_v1.3.0.md) (historical).

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.4.0-dev`** (`docker compose build web && docker compose up -d`). Alembic **`042_compose_project_meta`**. About / footer stay **1.3.0** until freeze |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least **two** real SSH Docker hosts + one HAOS + one hardware-bound project (Frigate-class) |
| **Flags** | Lock needs no flag. Move wizard: `PIHERDER_SERVICE_MIGRATE=true` then recreate web. Demo never copies |

---

## M1 — Host lock (landed)

- [ ] Docker project ⋯ **Lock to this host…** (hardware / operator / infra + note)  
- [ ] Badge on project + runtime stack panel  
- [ ] **Unlock…** confirm; badge gone  
- [ ] Locked **Move** is disabled with the reason  
- [ ] HAOS never lock/unlock; never in dest list  
- [ ] Viewer POST 403  
- [ ] Audit `service_host_lock` / `service_host_unlock` (no PEM / `.env` bodies)

## M2 — Preflight (landed)

- [ ] Flag **off** → Move 404; lock still works  
- [ ] Flag **on** → ⋯ **Move to another host…** (unlocked project)  
- [ ] Dest picker: other Docker hosts only  
- [ ] Blocks: dest project name taken, port clash, dest without DNS name (direct CNAME), `via_proxy` unmatched NPM cache, busy backup on dest  
- [ ] Warnings: `/dev` mounts, Cloudflare checklist  
- [ ] Viewer 403; demo no wizard  
- [ ] Audit `service_migrate_preview`

## M3 / M5 — Copy + dest up (landed)

- [ ] Flag **on**, green preflight → confirm **Move service** → JobHold  
- [ ] Job `service_migrate`: stop source → herder rsync (`/backups/_migrate/{job_id}`) → dest `up -d`  
- [ ] Named volume data present on dest (Mountpoint rsync)  
- [ ] Source left **stopped**, files still on disk  
- [ ] Concurrent backup/stack/migrate on source **or dest** → 409  
- [ ] Viewer POST 403; demo does not copy

## M4 / M-npm — Name / proxy follow (landed)

- [ ] Direct row: CNAME target is dest `dns_name`; both Pi-holes `restartdns`  
- [ ] `via_proxy`: public CNAME still on NPM; proxy-host `forward_host` is dest IP/hostname  
- [ ] Unmatched NPM host still blocks preflight  
- [ ] Host-identity FQDN (same as host A) is not rewritten

## M6 / M7 — Validate + rebind (landed)

- [ ] Fabric cert: TLS probe uses SNI = service FQDN; mismatch fails the job (no auto-rollback)  
- [ ] Kuma service binding follows dest; `down` after poll fails the job  
- [ ] Maps / visual stacks / port notes / template deployment follow dest  
- [ ] CertificateTarget cloned onto dest (source row kept)

## M8 / M9 — Leftover + hardware ack (landed)

- [ ] Default leftover: source **stopped**, files still on disk  
- [ ] Optional leftover **`compose down`** (volumes kept)  
- [ ] `/dev` warning requires acknowledge checkbox (or lock instead)

## D-F — Demo simulated Files (landed)

- [ ] Public demo: Files opens on a seeded host (viewer)  
- [ ] Banner: simulated / no SFTP  
- [ ] Browse folders, open README, image preview  
- [ ] Upload / delete / zip refused  
- [ ] Lab `PIHERDER_HOST_FILES` unchanged (real SFTP)

## Must (later slices)

- [ ] 1.3 regression (policy, Files, console, Reports)

## M-rm — Source remove + volumes (landed, Should, default off)

- [ ] Leftover default still **stopped** (data on disk)  
- [ ] Optional **`compose down`** still keeps volumes  
- [ ] Optional **remove source** lists project path + named volumes in preflight  
- [ ] Extra danger confirm + checkbox required (`leftover_remove_ack`)  
- [ ] After green move: source project dir gone, copied named volumes `docker volume rm`  
- [ ] Destination project / volumes **not** deleted  
- [ ] Source cert targets disabled; dest clone kept  
- [ ] Extra absolute binds outside the project folder left on disk  

## Should

- [ ] Live two-host E2E of leftover **remove** on a disposable stack
