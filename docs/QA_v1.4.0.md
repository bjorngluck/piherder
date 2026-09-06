# PiHerder v1.4.0 — operator QA / sign-off

**Branch:** `v1.4.0-dev` → `main` · tag **`v1.4.0`** (cut after merge)  
**Code freeze:** **2026-09-04** — QA, screenshots, and bugfixes only.  
**Package:** **`1.4.0`**.  
**Operator QA:** **complete 2026-09-06**. Screenshot pack **landed**. Kill switch stays **false**. Remaining: merge, tag, Hub.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes: [Move a service](../wiki/docker/service-migration.md) · [Host Files](../wiki/day-to-day/host-files.md) (demo canned tree) · [Journey Move](../wiki/getting-started/operator-scenarios.md#journey-move). Screenshot capture list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md) (**v1.4.0 pack landed**).

Plan: [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · design: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md).

1.3 production sign-off stays [QA_v1.3.0.md](QA_v1.3.0.md) (historical).

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.4.0`** (`docker compose build web && docker compose up -d`). Alembic **`042_compose_project_meta`**. About / footer **1.4.0** |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least **two** real SSH Docker hosts + one HAOS + one hardware-bound project (Frigate-class) |
| **Flags** | Lock needs no flag. Move wizard: `PIHERDER_SERVICE_MIGRATE=true` then recreate web. Demo never copies |

---

## M1 — Host lock (landed)

- [x] Docker project ⋯ **Lock to this host…** (hardware / operator / infra + note)  
- [x] Badge on project + runtime stack panel  
- [x] **Unlock…** confirm; badge gone  
- [x] Locked **Move** is disabled with the reason  
- [x] HAOS never lock/unlock; never in dest list  
- [x] Viewer POST 403  
- [x] Audit `service_host_lock` / `service_host_unlock` (no PEM / `.env` bodies)

## M2 — Preflight (landed)

- [x] Flag **off** → Move 404; lock still works  
- [x] Flag **on** → ⋯ **Move to another host…** (unlocked project)  
- [x] Dest picker: other Docker hosts only  
- [x] Dest pick shows wait modal while facts/preflight run (not a silent hang)  
- [x] Dest project name taken → set a new dest name, Recheck, Move enabled  
- [x] After a failed move, dest folder emptied: Recheck must **not** block on leftover dest `created`/exited containers; Move removes them then dest up  
- [x] Recheck dest uses live dest folder + containers + `ss` listen ports (not dest Docker cache)  
- [x] Dest project tree owner matches dest docker root (`bjorn` for `/home/bjorn/docker`), not `root` / fleet SSH  
- [x] Recheck + dest port change show wait modal  
- [x] Job overlay stays with Succeeded/Failed until Close  
- [x] Port clash → remap dest host port, Recheck, Move enabled  
- [x] Absolute bind outside jail: full path visible; dest path default under dest docker base; Move not a no-op (wait overlay clears after Recheck)  
- [x] Blocks: dest without DNS name (direct CNAME), `via_proxy` unmatched NPM cache, busy backup on dest  
- [x] Warnings: `/dev` mounts, Cloudflare checklist  
- [x] Viewer 403; demo no wizard  
- [x] Audit `service_migrate_preview`

## M3 / M5 — Copy + dest up (landed)

- [x] Flag **on**, green preflight → confirm **Move service** → JobHold  
- [x] Job `service_migrate`: stop source → herder rsync (`/backups/_migrate/{job_id}`) → dest `up -d`  
- [x] Do **not** recreate **web** while a Move is running (job is web `BackgroundTasks`; restart fails it)  
- [x] Named volume data present on dest (Mountpoint rsync)  
- [x] Source left **stopped**, files still on disk  
- [x] Concurrent backup/stack/migrate on source **or dest** → 409  
- [x] Viewer POST 403; demo does not copy

## M4 / M-npm — Name / proxy follow (landed)

- [x] Direct row: CNAME target is dest `dns_name`; both Pi-holes `restartdns`  
- [x] `via_proxy`: public CNAME still on NPM; proxy-host `forward_host` is dest IP/hostname  
- [x] NPM **proxy-host binding** without a fabric DNS row still PUTs `forward_host`; optional **Adopt into fabric**  
- [x] Unmatched NPM host still blocks preflight  
- [x] Host-identity FQDN (same as host A) is not rewritten  
- [x] Moving the NPM edge: public names stay CNAME to the NPM hostname; only that alias CNAME → dest; Pi-hole login uses LAN if the public URL is down; retry still syncs when fabric already shows dest

## M6 / M7 — Validate + rebind (landed)

- [x] Fabric cert: TLS probe uses SNI = service FQDN; mismatch fails the job (no auto-rollback)  
- [x] Kuma service binding follows dest; `down` after poll fails the job  
- [x] Grafana **container** dashboard chips follow dest; host metrics/logs stay  
- [x] Maps / visual stacks / port notes / template deployment follow dest  
- [x] CertificateTarget cloned onto dest (source row kept)

## M8 / M9 — Leftover + hardware ack (landed)

- [x] Default leftover: source **stopped**, files still on disk  
- [x] Optional leftover **`compose down`** (volumes kept)  
- [x] `/dev` warning requires acknowledge checkbox (or lock instead)
- [x] Host network: dest port clash cannot be remapped; acknowledge checkbox
- [x] Uptime Kuma (or any `docker.sock` bind): Move does **not** rsync the socket; dest uses dest’s sock

## D-F — Demo simulated Files (landed)

- [x] Public demo: Files opens on a seeded host (viewer)  
- [x] Banner: simulated / no SFTP  
- [x] Browse folders, open README, image preview  
- [x] Upload / delete / zip refused  
- [x] Lab `PIHERDER_HOST_FILES` unchanged (real SFTP)

## M-rm — Source remove + volumes (landed, Should, default off)

- [x] Leftover default still **stopped** (data on disk)  
- [x] Optional **`compose down`** still keeps volumes  
- [x] Optional **remove source** lists project path + named volumes in preflight  
- [x] Extra danger confirm + checkbox required (`leftover_remove_ack`)  
- [x] After green move: source project dir gone, copied named volumes `docker volume rm`  
- [x] Destination project / volumes **not** deleted  
- [x] Source cert targets disabled; dest clone kept  
- [x] Extra absolute binds outside the project folder left on disk  

## Live queue (this fleet)

Code for 1–5 is on `v1.4.0-dev` (web runs migrate jobs). Tick when walked on real Pis. **Do not** pick Remove source on Open WebUI / NPM / n8n.

- [x] **1 NPM-fronted (not the edge)** — job log has `NPM PUT … forward_host` (proxy-host binding is enough; no fabric row required). Public CNAME stays on `nginx.hacknow.info`. Retest of #959 (Open WebUI already on RPI5-4 — pick another proxied app).
- [x] **2 Direct TLS** — Grafana / Authentik / `sso` class. CNAME target is dest `dns_name`; both Pi-holes `restartdns`.
- [x] **3 Host lock** — Frigate (or hardware): Lock → Move disabled with reason → Unlock. HAOS never in dest list.
- [x] **4 Leftover** — disposable stack only: one run **`compose down`** (volumes kept); one run **Remove source** (project dir + copied named volumes gone; dest untouched). Extra ack required.
- [x] **5 Rebind / validate** — Kuma **service** bind + Grafana **container** dashboard chips + maps / stack panel follow dest; cert target cloned when a fabric cert exists; TLS probe SNI = service FQDN. Fail does not auto-rollback.
- [x] **Adopt into fabric** (optional, default off) — NPM-only names appear on the DNS list as via_proxy; Pi-hole CNAMEs unchanged; no cert invented.
- [x] **Start source stack** on JobHold after copy / dest-up fail (source left stopped).

## Screenshots (freeze pack — landed 2026-09-06)

Wired into [Move a service](../wiki/docker/service-migration.md), [Docker](../wiki/docker/overview.md), [Host Files](../wiki/day-to-day/host-files.md), [Public demo](../wiki/operations/demo-site.md), [Network maps](../wiki/integrations/dns-fabric.md). Full table: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v140--pack-status).

### New

- [x] **P0** `docker-migrate-wizard.png` — dest picker  
- [x] **P0** `docker-migrate-preflight.png` — Ready for copy; leftover **stopped**; Move service  
- [x] **P0** `docker-host-lock.png` — locked project ⋯ **Unlock…**; **Move** disabled  
- [x] **P0** `docker-migrate-jobhold.png` — JobHold live log until Close  
- [x] **P1** `docker-migrate-preflight-adopt.png` — **Adopt into fabric** (default off)  
- [x] **P1** `docker-migrate-jobhold-start-source.png` — **Start source stack**  
- [x] **P1** `demo-files.png` — public demo Files canned tree  

### Recapture

- [x] **P0** `docker-project-lifecycle.png` — ⋯ Lock + Move; Frigate **Locked · Hardware**  
- [x] **P1** `dns-logical.png` — 1.3 PNG kept (NPM hub already visible)  
- [x] **P2** `jobs-page.png` — skipped (optional)  
- [x] **P2** `dns-stack-panel.png` — **Locked · Hardware** on hailo-frigate-standalone  

### Spot-check (1.3 pack still good)

- [x] 1.3 Settings / Reports / Files / identity / certs / LAN / templates  
- [x] `integrations-npm.png` / `integrations-grafana.png`  

### Freeze bugfix (2026-09-06)

- [x] Move wizard **gap** between From card and preflight — HTMX wait indicator used `opacity:0` and still occupied a card. **Fixed** (`display: none` until request). Rebuild **web** to see it. Optional recapture of `docker-migrate-wizard.png`.

## Freeze gates

- [x] 1.3 regression (policy, Files, console, Reports)  
- [x] Unit coverage **≥ 62%** (CI fail-under **62**)  
- [x] Screenshot pack **P0/P1** landed + wiki `![…]`  
- [x] `mkdocs build --strict`  
- [x] Kill switch review — leave `PIHERDER_SERVICE_MIGRATE` **false** at tag (opt-in)  
- [x] Version bump `1.4.0`  
- [ ] Tag · Hub  

## Should (live)

- [x] Live two-host E2E of leftover **remove** on a disposable stack  
- [x] Live NPM-fronted move + direct TLS move on real Pis
