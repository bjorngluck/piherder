# PiHerder v1.4.0 — operator QA / sign-off

**Branch:** `v1.4.0-dev` → `main` · tag **`v1.4.0`** (cut after merge)  
**Code freeze:** **2026-09-04** — QA, screenshots, and bugfixes only.  
**Package:** stays **`1.3.0`** until version bump / tag.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes: [Move a service](../wiki/docker/service-migration.md) · [Host Files](../wiki/day-to-day/host-files.md) (demo canned tree) · [Journey Move](../wiki/getting-started/operator-scenarios.md#journey-move). Screenshot capture list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md) (**v1.4.0 pack**, not landed).

Plan: [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · design: [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md).

1.3 production sign-off stays [QA_v1.3.0.md](QA_v1.3.0.md) (historical).

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.4.0-dev`** (`docker compose build web && docker compose up -d`). Alembic **`042_compose_project_meta`**. About / footer stay **1.3.0** until version bump |
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
- [ ] Dest pick shows wait modal while facts/preflight run (not a silent hang)  
- [ ] Dest project name taken → set a new dest name, Recheck, Move enabled  
- [ ] After a failed move, dest folder emptied: Recheck must **not** block on leftover dest `created`/exited containers; Move removes them then dest up  
- [ ] Recheck dest uses live dest folder + containers + `ss` listen ports (not dest Docker cache)  
- [ ] Dest project tree owner matches dest docker root (`bjorn` for `/home/bjorn/docker`), not `root` / fleet SSH  
- [ ] Recheck + dest port change show wait modal  
- [ ] Job overlay stays with Succeeded/Failed until Close  
- [ ] Port clash → remap dest host port, Recheck, Move enabled  
- [ ] Absolute bind outside jail: full path visible; dest path default under dest docker base; Move not a no-op (wait overlay clears after Recheck)  
- [ ] Blocks: dest without DNS name (direct CNAME), `via_proxy` unmatched NPM cache, busy backup on dest  
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
- [ ] NPM **proxy-host binding** without a fabric DNS row still PUTs `forward_host`; optional **Adopt into fabric**  
- [ ] Unmatched NPM host still blocks preflight  
- [ ] Host-identity FQDN (same as host A) is not rewritten  
- [ ] Moving the NPM edge: public names stay CNAME to the NPM hostname; only that alias CNAME → dest; Pi-hole login uses LAN if the public URL is down; retry still syncs when fabric already shows dest

## M6 / M7 — Validate + rebind (landed)

- [ ] Fabric cert: TLS probe uses SNI = service FQDN; mismatch fails the job (no auto-rollback)  
- [ ] Kuma service binding follows dest; `down` after poll fails the job  
- [ ] Grafana **container** dashboard chips follow dest; host metrics/logs stay  
- [ ] Maps / visual stacks / port notes / template deployment follow dest  
- [ ] CertificateTarget cloned onto dest (source row kept)

## M8 / M9 — Leftover + hardware ack (landed)

- [ ] Default leftover: source **stopped**, files still on disk  
- [ ] Optional leftover **`compose down`** (volumes kept)  
- [ ] `/dev` warning requires acknowledge checkbox (or lock instead)
- [ ] Host network: dest port clash cannot be remapped; acknowledge checkbox
- [ ] Uptime Kuma (or any `docker.sock` bind): Move does **not** rsync the socket; dest uses dest’s sock

## D-F — Demo simulated Files (landed)

- [ ] Public demo: Files opens on a seeded host (viewer)  
- [ ] Banner: simulated / no SFTP  
- [ ] Browse folders, open README, image preview  
- [ ] Upload / delete / zip refused  
- [ ] Lab `PIHERDER_HOST_FILES` unchanged (real SFTP)

## M-rm — Source remove + volumes (landed, Should, default off)

- [ ] Leftover default still **stopped** (data on disk)  
- [ ] Optional **`compose down`** still keeps volumes  
- [ ] Optional **remove source** lists project path + named volumes in preflight  
- [ ] Extra danger confirm + checkbox required (`leftover_remove_ack`)  
- [ ] After green move: source project dir gone, copied named volumes `docker volume rm`  
- [ ] Destination project / volumes **not** deleted  
- [ ] Source cert targets disabled; dest clone kept  
- [ ] Extra absolute binds outside the project folder left on disk  

## Live queue (this fleet)

Code for 1–5 is on `v1.4.0-dev` (web runs migrate jobs). Tick when walked on real Pis. **Do not** pick Remove source on Open WebUI / NPM / n8n.

- [ ] **1 NPM-fronted (not the edge)** — job log has `NPM PUT … forward_host` (proxy-host binding is enough; no fabric row required). Public CNAME stays on `nginx.hacknow.info`. Retest of #959 (Open WebUI already on RPI5-4 — pick another proxied app).
- [ ] **2 Direct TLS** — Grafana / Authentik / `sso` class. CNAME target is dest `dns_name`; both Pi-holes `restartdns`.
- [ ] **3 Host lock** — Frigate (or hardware): Lock → Move disabled with reason → Unlock. HAOS never in dest list.
- [ ] **4 Leftover** — disposable stack only: one run **`compose down`** (volumes kept); one run **Remove source** (project dir + copied named volumes gone; dest untouched). Extra ack required.
- [ ] **5 Rebind / validate** — Kuma **service** bind + Grafana **container** dashboard chips + maps / stack panel follow dest; cert target cloned when a fabric cert exists; TLS probe SNI = service FQDN. Fail does not auto-rollback.
- [ ] **Adopt into fabric** (optional, default off) — NPM-only names appear on the DNS list as via_proxy; Pi-hole CNAMEs unchanged; no cert invented.
- [ ] **Start source stack** on JobHold after copy / dest-up fail (source left stopped).

## Screenshots (freeze pack — not landed)

Capture on **`v1.4.0-dev`** after rebuild (`docker compose build web && docker compose up -d web`). Flag **on** for Move shots. Light desktop. Do not photograph `.env` / PEM / NPM passwords. Full table: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v140--pack-status).

### New (no PNG yet)

- [ ] **P0** `docker-migrate-wizard.png` — dest picker (Docker hosts only; HAOS absent); wait modal OK to miss if the shot is the select  
- [ ] **P0** `docker-migrate-preflight.png` — Ready for copy; dest folder; leftover radios (stopped default); Move service  
- [ ] **P0** `docker-host-lock.png` — Lock to this host confirm (reason + note)  
- [ ] **P0** `docker-migrate-jobhold.png` — JobHold live log **Succeeded** or **Failed** until Close (`service_migrate`)  
- [ ] **P1** `docker-migrate-preflight-adopt.png` — NPM-only project: **Adopt into fabric** checkbox (default off)  
- [ ] **P1** `docker-migrate-jobhold-start-source.png` — copy or dest-up **Failed** + **Start source stack** (stage a disposable fail, or skip if you cannot)  
- [ ] **P1** `demo-files.png` — public demo Files canned tree + simulated banner (viewer OK)  

### Recapture (chrome changed)

- [ ] **P0** `docker-project-lifecycle.png` — project ⋯ includes **Lock to this host…** and **Move to another host…** (locked Move disabled is a plus)  
- [ ] **P1** `dns-logical.png` — Path map still shows **name → NPM → host → app** when the app shares the NPM host (n8n-class)  
- [ ] **P2** `jobs-page.png` — only if the type filter / a row shows `service_migrate`  
- [ ] **P2** `dns-stack-panel.png` — locked badge on a locked project, if in frame  

### Spot-check (1.3 pack still good)

- [ ] 1.3 Settings / Reports / Files / identity / certs / LAN / templates — recapture only if chrome drifted  
- [ ] `integrations-npm.png` / `integrations-grafana.png` — binding UI unchanged unless you want dest-follow in the caption  

After files land: wire `![…]` on [Move a service](../wiki/docker/service-migration.md) (and Path map / demo Files as needed) · `mkdocs build --strict` · commit PNGs + captions together.

## Freeze gates

- [ ] 1.3 regression (policy, Files, console, Reports)  
- [x] Unit coverage **≥ 62%** (CI fail-under **62**)  
- [ ] `mkdocs build --strict`  
- [ ] Screenshot pack **P0** landed (this section + wiki assets README)  
- [ ] Kill switch review (`PIHERDER_SERVICE_MIGRATE` default still **false** at tag unless GA-enough)  
- [ ] Version bump `1.4.0` · tag · Hub  

## Should (live)

- [ ] Live two-host E2E of leftover **remove** on a disposable stack  
- [ ] Live NPM-fronted move + direct TLS move on real Pis
