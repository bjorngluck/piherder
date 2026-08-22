# PiHerder v1.4.0 — service migration

**Status:** **Planning / backlog** — capture while **v1.3.0** runs on `v1.3.0-dev`  
**Date opened:** 2026-08-17  
**Git branch (when train opens):** `v1.4.0-dev` (not opened yet)  
**Package / image version (at tag):** `1.4.0`  
**Theme:** **Service migration** — move a Docker Compose project host→host with dataset copy, DNS retarget, resolver flush, TLS / Kuma validate, and **host lock**  
**Baseline:** `v1.3.0` (when tagged)  
**Mode:** Planning only — do **not** start implementation on `v1.3.0-dev`  
**Related:** [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · [PLAN_v1.3.0.md](PLAN_v1.3.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [SPEC.md](../SPEC.md) · wiki [Docker](../wiki/docker/overview.md) · [DNS fabric](../wiki/integrations/dns-fabric.md) · [Backups](../wiki/day-to-day/backups.md) · [HAOS](../wiki/day-to-day/haos-hosts.md)

> **Not the active train.** v1.3 (active on `v1.3.0-dev`) is operator policy, scale UX, insights, and confined host files. This document parks **service migration** so that train stays focused. Promote Stream **M** when the 1.4 train opens.

---

## 0. Intent

Operators already stop/start a stack on one host, rsync that host’s trees to the herder, retarget a CNAME in the DNS fabric, restart Pi-hole FTL, deploy a vault cert, and bind Kuma. They **cannot** do those as **one audited cutover** to another host.

Wanted pipeline (operator, 2026-08-17):

1. Shut down the container / compose project on the **source** host  
2. Back up the current dataset and **copy** it to the **destination** (named volumes, bind data, project folder, config)  
3. Apply the CNAME update — remove CNAME → old host, re-add CNAME → new host  
4. Restart **both** Pi-hole DNS resolvers / flush cache  
5. Start the service on the new host  
6. Validate TLS (vault / `openssl` probe) and Uptime Kuma (and related bindings)  
7. **Host lock** some services so they cannot move:
   - **HAOS** is unique — nothing on an HAOS appliance is a migrate source or dest  
   - Hardware-bound stacks (e.g. **Frigate on rpi5-4** + Coral / TPU) — lock at **service / project** level  

This is the parked H2.5 / SPEC item **“Service migrate host→host; destructive service remove”** — named **service migration**. Destructive wipe stays a **later** sibling, not the 1.4 Must.

**Parked here (do not start on 1.2 / 1.3):** live zero-downtime cutover, NPM proxy-host rewrite, auto hardware detection as the only lock, cross-arch image rebuild.

**Under consideration (from 1.3 Files, not Must for 1.4):** richer **API Files** — token zip / edit / chmod / recursive delete / privileged identity. 1.3 keeps fleet list/get/put/mkdir/rename/empty-delete in the browser-adjacent API; extra verbs stay UI + 2FA. Promote only if operators actually automate Files.

---

## 1. Decision lock (planning defaults — revisit at train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.4.0-dev`** when 1.3 is on `main` |
| Production line until then | **`main` @ 1.3.x** (then 1.2.x until 1.3 tags); this plan does not block 1.2 or 1.3 |
| Theme streams (seed) | **M** service migration (discover + phased ship). Other 1.4 residuals only if 1.3 freeze parks them here |
| Cutover style (thin slice) | **Stop-first** — consistent dataset, planned downtime. Running-copy + final rsync is a later Cap |
| Copy transport | **Herder as staging** under the existing backup root (`_migrate/{job_id}/`) — reuse rsync/SSH; not a new host-to-host trust mesh |
| DNS path (thin slice) | **Direct / host-identity** fabric rows (`via_proxy=false`). NPM-in-front stays a checklist until NPM write exists |
| Host lock | **HAOS host** refuse + **per-project** operator lock + reason. Implicit `devices:` scan is Should, not the only gate |
| Source after success | Leave **stopped + data intact** in the thin slice. Optional source `compose down` / delete is a later confirm |
| Semver | Additive minor; new job type + optional `ComposeProjectMeta` table |
| Out of focus for seed | Live migrate · Swarm/k8s · HA add-ons · cross-arch rebuild · automatic NPM proxy CRUD · dual-control approve |

```text
main @ v1.3.0 (+ v1.3.x)
  └─ v1.4.0-dev → merge → main → tag v1.4.0 → Hub
```

---

## 2. Stream **M** — Service migration

**Living design:** [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md)

### 2.1 What exists today (discovery)

PiHerder already has the **pieces**. Nothing orchestrates them across two hosts.

| Layer | Primitive | Limit vs migrate |
|-------|-----------|------------------|
| Stop / start | `compose_action` + Jobs `docker_stack_stop` / `_start` / `_restart` / `_deploy` (`up -d`) | One host; exclusive **per host** |
| Project files | SFTP compose / `.env` / one-level sidecars; template `files_json` + desired state | Text; not trees or named volumes |
| Bind / volume inventory | `compose_project_files.classify_volume_source`; Docker inspect + `du` on mount Sources (incl. `/var/lib/docker/volumes/…/_data`) | Read-only sizes; no copy |
| Backup | Celery rsync **source host → herder** `/backups`; default sources include `~/docker` and `/var/lib/docker/volumes/` | Whole-host trees, not one project; Redis mutex **per source host** |
| Restore | `restore_backup_source` reverse rsync **herder → same server** | No dest remapping; no other host |
| DNS fabric | `upsert_service_record` already **deletes old CNAME** when target FQDN changes, then `fanout_pihole_dns` add | Row still points at one backend; no job wrapper; no FTL restart |
| Pi-hole cache | `run_action(..., "restartdns")` + UI on each integration | Manual per instance; migrate must **fan out both** |
| TLS | Vault + `CertificateTarget` (server-scoped) + `verify_tls_endpoint_fingerprint` (`openssl s_client` + SNI) | Target stays on old `server_id` until rewritten + redeployed |
| Kuma | `IntegrationBinding` (`server_id` + `docker_project` [+ container]); metrics poll; stack-down alerts | **Read** metrics. HTTP-on-FQDN keeps working after DNS; **IP/host** monitors are a checklist (no Kuma write API) |
| Topology | `RuntimeEdge`, `ContainerAnnotation`, `VisualServiceStack`, `PortAnnotation`, `StackDeployment` | All keyed `server_id` + project — must **rebind**, not orphan |
| HAOS | `Server.os_type=haos`; Docker feature off on pure appliances | Not a compose box; refuse migrate |
| Host lock | **None** | New |
| Cross-host copy | **None** | New (stage on herder) |
| Two-host job | `Job.server_id` is singular; exclusive types are per-server | New type + **lock both** hosts |

Reuse, do **not** fork: `compose_action` / `resolve_compose_project_path` · backup rsync + path policy · `upsert_service_record` / `fanout_pihole_dns` · Pi-hole `restartdns` · cert deploy + TLS verify · inventory refresh · JobHold live log · audit `preview → confirm` · dest-card chrome.

**Does not depend on 1.3 Stream F.** 1.3 Files is a confined **in-browser manager** (flag off; API still fleet list/get/put + limited mkdir/rename). Migration is bulk rsync. F is a useful operator peek, not a prerequisite.

### 2.2 Wanted

One **operator+** wizard + one **Job** (`service_migrate`) that runs the agreed steps with a live log, preview of payload (paths + bytes + dest free space), and a hard stop on **host lock** / HAOS / preflight fail.

| ID | Item | Notes |
|----|------|--------|
| M0 | **Discovery** (this capture) | Inventory above; lock model; copy transport; DNS/NPM split; validate set |
| M1 | **Host lock** | Per-project `host_locked` + reason (hardware / operator / implied). HAOS host = implicit lock. UI lock/unlock on Docker project + stack panel. Locked project: hide/disable **Move…**, 403 + audit if posted |
| M2 | **Preflight** | Dest Docker on, not HAOS; dest `docker_base_dir` writable; arch match; published-port clash; dest disk ≥ payload + margin; dest project name free; source not locked; no active backup/stack job on either host; fabric row `via_proxy` → block thin slice or force checklist |
| M3 | **Copy** | Stop source → rsync project dir + classified binds + named volumes (create volume on dest, copy `_data` via docker-user or `docker run` helper) via herder staging → dest paths remapped to dest `docker_base_dir` |
| M4 | **DNS** | For each `ServiceDnsRecord` with `docker_project` + `backend_server_id=source`: set backend (and target if not via-NPM) to dest; reuse upsert delete-old + add-new; then **`restartdns` on every Pi-hole integration** |
| M5 | **Start dest** | `docker compose up -d` on dest project path; refresh inventory both hosts |
| M6 | **Validate** | TLS probe when a cert / `verify_url` exists; Kuma poll + rebind `IntegrationBinding.server_id`; rebind topology / `StackDeployment` / cert targets (redeploy PEMs on dest). Fail job ≠ auto rollback start on source (operator **Start on source** CTA) |
| M7 | **Control-plane rebind** | Move or clone rows listed in the feature plan so maps / Services / certs / Kuma coverage follow the dest host |
| M8 | **Source leftover (Cap)** | After green validate: leave stopped (default) · optional `compose down` (keep volumes) · later destructive wipe (M-rm, not Must) |

**Product shape (thin slice):**

```text
Docker → project ⋯ → Move to another host…
┌─────────────────────────────────────────────────────┐
│  Move  frigate  ?  — LOCKED (Coral TPU on rpi5-4)   │
│  Move  grafana                                        │
│    From: rpi5-6    To: [ rpi5-3 ▾ ]                   │
│    Dataset: ./ + grafana-data (1.2 GiB)               │
│    DNS: grafana.hacknow.info  CNAME → rpi5-3…         │
│    After: leave source stopped (data kept)            │
│    [ Cancel ]  [ Preview ]  [ Move service ]          │
└─────────────────────────────────────────────────────┘
        → JobHold (steps 1–6) → Jobs / Audit
```

### 2.3 Discovery exit criteria

Written decisions on:

1. Staging = herder `_migrate/` vs dest-pulls-source over SSH (lean **herder stage**)  
2. Thin slice = **stop-first** + **direct TLS / host-identity** only  
3. Named-volume copy = docker-group rsync vs `docker run` tar helper (spike at train open)  
4. Lock UX = project ⋯ + optional compose `devices:` warning  
5. M8 leftover = keep source data (Must) vs down/wipe (Cap)

Reject at freeze: live migrate, “just rsync the whole `/var/lib/docker`”, auto-moving HAOS, silent NPM backend rewrite.

### 2.4 Security / ops notes

- operator+ only; viewer 403; demo: simulated job or disabled.  
- Preview lists **every** path and byte estimate; confirm is a danger modal (downtime).  
- Staging dir is job-scoped, mode 700, wiped in `finally` (keep on failure until operator dismisses).  
- Path policy: no `..`, refuse absolute binds **outside** source `docker_base_dir` unless explicitly allow-listed in the preview.  
- Dual host lock: do not interleave backup / stack mutate / a second migrate on source **or** dest.  
- Audit: `service_migrate_preview` / `_start` / `_step` / `_done` / `_fail` with source, dest, project, bytes, fqdns — never PEM / `.env` bodies.  
- Rollback: dest failed start → DNS **not** flipped yet if we **start dest before DNS**? Operator order is DNS then start (clients may hit dest before listen). Feature plan locks **copy → start dest health → DNS+FTL → validate** as the safer default, with the operator-stated order as an explicit option. See feature plan § pipeline order.

### 2.5 Non-goals (M) / defer past this minor

| Defer | Why |
|-------|-----|
| Zero-downtime / rsync-while-running | Consistency + compose file locks; Cap after stop-first is trusted |
| NPM proxy host rewrite | No NPM write API yet ([SPEC](../SPEC.md) residual) |
| HAOS / Supervisor add-ons | Not compose; unique appliance |
| Cross-arch (`arm64` → `amd64`) image rebuild | Preflight refuse; operator rebuilds |
| Destructive source wipe + unused volume prune | Sibling **M-rm**; easy to regret |
| Swarm / k8s | Out of product topology |
| Auto-detect every hardware bind as lock | Heuristic `devices:` warning only |
| 1.3 Files as the copy engine | Wrong size/trust model |
| Live DB logical replication (Postgres in-stack) | Stop-first file copy only |

---

## 3. Ship bar (draft — finalise at train open)

| Priority | Item | Bar |
|----------|------|-----|
| **Must** | **M1** lock + HAOS refuse · **M2** preflight · **M3** stop + copy project/binds/named vols · **M4** fabric CNAME + **both** Pi-hole `restartdns` · **M5** dest up · **M6** TLS and/or Kuma when those rows exist · **M7** rebind deployment / DNS / bindings / annotations | One unlocked compose project moves; locked/HAOS cannot; downtime accepted |
| **Should** | Compose `devices:` / privileged warning · cert target clone + redeploy on dest · dest published-port clash · source leftover `compose down` (keep volumes) | Hardware near-misses and TLS-on-dest without a manual cert job |
| **Discover / Cap** | Running-copy + final sync · via-NPM checklist wizard · dest-pull transport · destructive **M-rm** · auto rollback (revert DNS + start source) | Promote only if Must green |

Success criteria (draft):

1. Operator can lock Frigate on rpi5-4 with reason “Coral TPU”; **Move** is refused.  
2. HAOS host never appears as migrate source or destination.  
3. Unlocked stack (e.g. Grafana, Authentik) stop → copy → DNS → both Pi-holes restart → dest up → padlock / Kuma still green.  
4. Control-plane rows follow the dest host (maps, Services, stack panel).  
5. Source data still on disk after success unless a later Cap is used.  
6. Viewer cannot start a migrate; demo does not move real stacks.

---

## 4. Quality bar (draft)

| Gate | Target |
|------|--------|
| Unit | Hold ≥ 55%; preflight matrix · lock enforce · path remap · CNAME old/new · HAOS refuse |
| Tests | `tests/test_service_migrate.py` (new) — no live SSH; mock compose/rsync/Pi-hole |
| E2E | Wizard chrome + lock disabled CTA (no live two-host in CI) |
| Docs | FEATURE_PLAN + wiki Docker “Move a service” + DNS fabric + HAOS note; `mkdocs build --strict` at freeze |
| Security | Dual-host lock, staging wipe, path jail, audit without secret bodies, demo off |

---

## 5. Dependencies

| Deliverable | Why 1.4 needs it |
|-------------|------------------|
| 1.2 DNS fabric upsert + delete-old CNAME | **M4** |
| 1.2 cert verify (`openssl s_client` + SNI) | **M6** |
| 1.x stack lifecycle Jobs + exclusivity | **M3** / **M5** stop/start; extend exclusive set |
| 1.x backup rsync + path policy | **M3** staging |
| 1.x Pi-hole `restartdns` | **M4** fan-out |
| 1.x Kuma bindings + inventory-down alerts | **M6** / **M7** |
| 1.3 **F** (optional) | Operator inspect dest tree; **not** required to code M |
| 1.3 **W-id** (optional) | Named-volume `_data` may need privileged identity; spike may stay on docker-group + helper container |

1.2 / 1.3 bugs found during this capture stay on those trains.

---

## 6. Out of scope (stay honest)

- Implementing any of **M** on `v1.2.0-dev` or `v1.3.0-dev`  
- Replacing backups, templates, or Files  
- Moving the PiHerder stack itself, or the only Pi-hole, as a happy-path demo  
- Multi-tenant / two-person approve  
- Cloudflare / external DNS (still checklist)  

---

## 7. Capture log

| Date | Note |
|------|------|
| 2026-08-17 | Opened from operator request during 1.2 QA / Authentik TLS work. Feature name **service migration**. Pipeline + HAOS / Frigate host-lock captured. H2.5 + SPEC item promoted to this train. Discovery of compose / rsync / fabric / restartdns / TLS / Kuma primitives recorded. **Not** a 1.3 add. |
| 2026-08-20 | **Under consideration:** 1.3 Files API expansions (zip/edit/chmod/recursive delete/privileged tokens). Thin Docker volume browse + `docker cp` into the jail shipped in 1.3 UI, not as the migrate copy engine. |

---

## 8. Immediate next steps (when ready)

| # | Step |
|---|------|
| 1 | Finish **v1.3.0** freeze / tag · then open this train |
| 2 | Open **`v1.4.0-dev`** + lock Must/Should from this seed |
| 3 | Spike named-volume copy (docker-group rsync vs helper container) on two lab Pis |
| 4 | Land **M1** lock model + UI (cheap, useful before copy) |
| 5 | Land **M2** preflight (no copy) so dest refusal is visible |
| 6 | Land **M3–M6** job + wizard; then **M7** rebind |
| 7 | Wiki + ADMIN before freeze |

---

*End of planning capture — not a commitment to ship every Cap in one minor.*
