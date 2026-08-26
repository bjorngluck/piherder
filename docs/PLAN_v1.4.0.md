# PiHerder v1.4.0 — service migration

**Status:** **Active** — train opened 2026-08-25 on `v1.4.0-dev`  
**Date opened:** 2026-08-25 (planning capture 2026-08-17)  
**Git branch:** `v1.4.0-dev` (integration) · merge → `main` → tag `v1.4.0`  
**Package / image version (at tag):** `1.4.0` — tree stays **`1.3.0` until freeze**  
**Theme:** **Service migration** — move a Docker Compose project host→host with dataset copy, DNS / NPM retarget, resolver flush, TLS / Kuma validate, **host lock**, and leftover policy  
**Baseline:** `v1.3.0` (tagged 2026-08-22)  
**Mode:** Active train · Must signed · **M1–M9** + **M-npm** + **D-F** + **M-rm** landed; next is freeze / QA  
**QA:** [QA_v1.4.0.md](QA_v1.4.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md) · [PLAN_v1.3.0.md](PLAN_v1.3.0.md) · [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [SPEC.md](../SPEC.md) · wiki [Docker](../wiki/docker/overview.md) · [DNS fabric](../wiki/integrations/dns-fabric.md) · [Backups](../wiki/day-to-day/backups.md) · [HAOS](../wiki/day-to-day/haos-hosts.md)

> **Single development target.** Production stays **v1.3.x on `main`**. This branch implements Stream **M**. **v1.4.0 does not tag without a working stop-first migrate** of an unlocked compose project (direct TLS **and** NPM-fronted).

---

## 0. Intent

Operators already stop/start a stack on one host, rsync that host’s trees to the herder, retarget a CNAME in the DNS fabric, restart Pi-hole FTL, deploy a vault cert, and bind Kuma. They **cannot** do those as **one audited cutover** to another host.

Wanted pipeline:

1. Shut down the compose project on the **source** host  
2. Copy the dataset to the **destination** (named volumes, bind data, project folder, config)  
3. **Direct TLS:** CNAME off old host, onto dest · **NPM-in-front:** keep the public CNAME on NPM; retarget the proxy-host **backend** to dest  
4. Restart **both** Pi-hole DNS resolvers / flush cache (direct path; still after fabric sync)  
5. Start the service on the new host (default order: dest health **before** name/proxy flip)  
6. Validate TLS (vault / `openssl` probe) and Uptime Kuma (when those rows exist)  
7. **Host lock** so HAOS and hardware-bound stacks cannot move  
8. Leftover: leave source stopped (default) · optional `compose down` keep volumes (**M8**) · optional source remove + volume delete (**M-rm**, Should)

This is SPEC / H2.5 **“Service migrate host→host; destructive service remove”** — named **service migration**. Destructive wipe is **Should**, default **off**.

**Out of 1.4:** live zero-downtime cutover, ACME-in-herder, full NPM proxy CRUD, auto hardware detection as the only lock, cross-arch image rebuild, richer Files token API.

**Must (not M):** **D-F** demo simulated Files — canned tree, same chrome as 1.3 Files, no SFTP. Real SFTP stays **demo never**.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.4.0-dev`** |
| Production line | **`main` @ `v1.3.0`** — hotfixes → **`v1.3.x`**, port into `v1.4.0-dev` |
| Git tag (freeze) | **`v1.4.0`** (RCs: `1.4.0-rc.N` if needed) |
| Image tags (freeze) | `1.4.0` · `1.4` · `latest` (multi-arch); keep `1.3` / `1.3.x` pins valid |
| In-scope streams | **M** service migration (**M1–M9** + **M-npm**) · **D-F** demo simulated Files · **Q** quality/freeze. **M-rm** Should |
| Out-of-focus | **ACME-in-herder** · **M-live** · full NPM CRUD · Files token API · **W-mux** · **AC-fg** · **N3** · CSP nonces · branding · multi-tenant · Swarm/k8s |
| Mode | Stop-first migrate · no half-built two-host jobs · Must → freeze |
| Coverage | **≥ 55%** unit; focused tests for preflight, lock, path remap, CNAME, NPM PUT, HAOS refuse |
| E2E | Wizard chrome + lock disabled CTA (no live two-host in CI) |
| Semver | Additive minor; new job type + `ComposeProjectMeta` |
| Version bump | `1.4.0` **at freeze only** |
| Kill switch | **`PIHERDER_SERVICE_MIGRATE=false`** until M is complete enough to turn on. Demo never runs a real copy |

```text
main @ v1.3.0 (+ v1.3.x patches)
  └─ v1.4.0-dev → merge → main → tag v1.4.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | Do not start Out-of-1.4 items while Must is open |
| Prod critical bugs | **main** as **1.3.x** first, then port here |
| Demo never grows teeth | Real migrate off · real Files SFTP off · **D-F** is canned only |
| Residual Cap | **Do not pull.** ACME · W-mux · AC-fg · N3 · CSP nonces · branding stay out |
| Tag honesty | **v1.4.0 tags only with migrate shipped** |

---

## 1a. Kickoff leans (locked 2026-08-25)

| # | Question | Decision |
|---|----------|----------|
| 1 | Theme / Must | **M1–M9** + **M-npm** + **D-F**. Tag requires a working stop-first migrate (direct **and** NPM-fronted) |
| 1a | **M1–M7** | Host lock, preflight, stop+copy, fabric / NPM follow, dest `up -d`, TLS/Kuma when rows exist, control-plane rebind |
| 1b | **M8** leftover | After success, operator can **`compose down` on source (keep volumes)**. Default: leave stopped + data intact |
| 1c | **M9** hardware warn | Compose `devices:` / privileged / host-network → preflight **warning** + lock-or-acknowledge |
| 1d | **M-npm** | `via_proxy=true`: **Must** PUT NPM proxy-host `forward_host` (and port if needed) to dest. Public CNAME **stays on NPM**. Narrow write only — no create/delete proxy, no ACME |
| 1e | **D-F** | Demo simulated Files (canned tree, 1.3 chrome, no SFTP). Real Files **demo never** |
| 1f | **M-rm** | **Should** (not default): after green migrate, optionally remove source project + `docker volume rm` for copied named volumes. Preview + danger confirm. Never wipe dest |
| 2 | Out of 1.4 | **ACME-in-herder**. Also: **M-live** · full NPM CRUD · Files token API · W-mux · AC-fg · N3 · CSP nonces · branding |
| 3 | Cutover | **Stop-first**. Default pipeline **`health_then_dns`**. Checkbox `dns_then_start` default **off** |
| 4 | Copy transport | Herder staging `BACKUP_ROOT/_migrate/{job_id}/` |
| 5 | DNS + NPM | **Direct:** fabric upsert CNAME → dest `dns_name` + both Pi-hole `restartdns`. **NPM-in-front:** keep CNAME on the edge; PUT `forward_host`; `backend_server_id` → dest. Missing NPM integration or unmatched proxy host → preflight **fail** |
| 6 | Host lock | HAOS never source or dest. Per-project `ComposeProjectMeta.host_locked` + reason |
| 7 | Named volumes | Spike both (docker-group rsync `_data` vs `docker run` tar helper). Prefer helper if sudo-less. Decision before **M3** |
| 8 | Cert targets | **Clone** onto dest + deploy + verify; disable source target until leftover policy |
| 9 | `Job.server_id` | **Source**. `details` JSON holds `dest_server_id` |
| 10 | Fabric rows | Move **all** matching `docker_project` + `backend_server_id=source` |
| 11 | Unit of move | One **compose project** (same boundary as Stop all / Start all) |
| 12 | Dual-host lock | Exclusive with backup **and** stack-mutating jobs on **both** ids |

**Reuse, do not fork:** `compose_action` / `resolve_compose_project_path` · backup rsync + path policy · `upsert_service_record` / `fanout_pihole_dns` · Pi-hole `restartdns` · NPM `list_proxy_hosts` + new narrow PUT · cert deploy + TLS verify · inventory refresh · JobHold live log · audit preview → confirm.

---

## 1b. Recommended delivery order

```text
Phase 0  Open train + docs lock            ← done 2026-08-25
Phase 1  M1 host lock model + UI           first product slice
Phase 2  M2 preflight (no copy)            dest port clash · NPM match when via_proxy
Phase 3  M3–M6 job + Move wizard           named-volume spike lands here
         M-npm with M4/M6                  dest healthy → NPM PUT backend → TLS via FQDN
Phase 4  M7 control-plane rebind
Phase 5  M8 leftover down · M9 devices:    Must
         M-rm source remove + volumes      Should
         D-F demo simulated Files          Must; can parallel after Phase 1
Phase 6  Q freeze: tests ≥55% · wiki       version 1.4.0 · tag · Hub
         · QA · kill-switch review
```

**D-F** must not block **M3**. **M-rm** does not block the tag if leftover wipe slips.

---

## 2. Stream **M** — Service migration

**Living design:** [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md)

### 2.1 What exists today (discovery)

Discovery snapshot at train open. **Now orchestrated** on `v1.4.0-dev` as Job `service_migrate` (wiki [Move a service](../wiki/docker/service-migration.md)). The table is the primitives migrate wraps — do not treat “Limit vs migrate” as current product gaps.

| Layer | Primitive | Limit vs migrate |
|-------|-----------|------------------|
| Stop / start | `compose_action` + Jobs `docker_stack_stop` / `_start` / `_restart` / `_deploy` (`up -d`) | One host; exclusive **per host** |
| Project files | SFTP compose / `.env` / one-level sidecars; template `files_json` + desired state | Text; not trees or named volumes |
| Bind / volume inventory | `compose_project_files.classify_volume_source`; Docker inspect + `du` on mount Sources (incl. `/var/lib/docker/volumes/…/_data`) | Read-only sizes; no copy |
| Backup | Celery rsync **source host → herder** `/backups`; default sources include `~/docker` and `/var/lib/docker/volumes/` | Whole-host trees, not one project; Redis mutex **per source host** |
| Restore | `restore_backup_source` reverse rsync **herder → same server** | No dest remapping; no other host |
| DNS fabric | `upsert_service_record` already **deletes old CNAME** when target FQDN changes, then `fanout_pihole_dns` add | Row still points at one backend; no job wrapper; no FTL restart |
| Pi-hole cache | `run_action(..., "restartdns")` + UI on each integration | Manual per instance; migrate must **fan out both** |
| NPM | `list_proxy_hosts` (GET) — `forward_host` / `forward_port` / domains | **Read-only.** **M-npm** adds PUT backend only |
| TLS | Vault + `CertificateTarget` (server-scoped) + `verify_tls_endpoint_fingerprint` (`openssl s_client` + SNI) | Target stays on old `server_id` until rewritten + redeployed |
| Kuma | `IntegrationBinding` (`server_id` + `docker_project` [+ container]); metrics poll; stack-down alerts | **Read** metrics. HTTP-on-FQDN keeps working after DNS; **IP/host** monitors are a checklist (no Kuma write API) |
| Topology | `RuntimeEdge`, `ContainerAnnotation`, `VisualServiceStack`, `PortAnnotation`, `StackDeployment` | All keyed `server_id` + project — must **rebind**, not orphan |
| HAOS | `Server.os_type=haos`; Docker feature off on pure appliances | Not a compose box; refuse migrate |
| Host lock | **None** | New |
| Cross-host copy | **None** | New (stage on herder) |
| Two-host job | `Job.server_id` is singular; exclusive types are per-server | New type + **lock both** hosts |

**Does not depend on 1.3 Stream F.** Migration is bulk rsync. F is a useful operator peek, not a prerequisite.

### 2.2 Wanted

One **operator+** wizard + one **Job** (`service_migrate`) with a live log, preview of payload (paths + bytes + dest free space), and a hard stop on **host lock** / HAOS / preflight fail.

| ID | Item | Priority | Notes |
|----|------|----------|--------|
| M0 | **Discovery** | — | **Done** 2026-08-17 |
| M1 | **Host lock** | Must | Per-project `host_locked` + reason. HAOS host = implicit lock. Locked: hide/disable **Move…**, 403 + audit if posted |
| M2 | **Preflight** | Must | See feature plan **preflight matrix**. Dest Docker on, not HAOS; arch; ports; disk; project name free; dual-host exclusive; **NPM match when `via_proxy`** |
| M3 | **Copy** | Must | Stop source → rsync project dir + classified binds + named volumes via herder staging → dest paths remapped |
| M4 | **DNS / NPM** | Must | Direct: fabric CNAME + both `restartdns`. NPM: PUT `forward_host`; keep edge CNAME |
| M5 | **Start dest** | Must | `docker compose up -d` on dest project path; refresh inventory both hosts |
| M6 | **Validate** | Must when rows exist | TLS probe; Kuma poll; fail ≠ auto rollback (operator **Start on source** CTA) |
| M7 | **Control-plane rebind** | Must | Maps / Services / certs / Kuma coverage follow dest |
| M8 | **Source leftover down** | Must | After green: leave stopped (default) · optional `compose down` (keep volumes) |
| M9 | **`devices:` warning** | Must | Warn + lock-or-acknowledge; not the only gate |
| M-npm | **NPM backend retarget** | Must | Narrow PUT on existing proxy host. Unmatched host fails preflight |
| M-rm | **Source remove + volumes** | Should | **Done** — optional post-success wipe of source project + copied named volumes (preview + ack; dest never wiped) |
| D-F | **Demo simulated Files** | Must | Canned tree; no SFTP |

**Product shape:**

```text
Docker → project ⋯ → Move to another host…
┌─────────────────────────────────────────────────────┐
│  Move  frigate  ?  — LOCKED (Coral TPU on rpi5-4)   │
│  Move  grafana                                        │
│    From: rpi5-6    To: [ rpi5-3 ▾ ]                   │
│    Dataset: ./ + grafana-data (1.2 GiB)               │
│    DNS: grafana.hacknow.info  CNAME → rpi5-3…         │
│    NPM: (if via_proxy) proxy #12  10.0.0.6 → rpi5-3   │
│    After: leave source stopped (data kept)            │
│    [ Cancel ]  [ Preview ]  [ Move service ]          │
└─────────────────────────────────────────────────────┘
        → JobHold → Jobs / Audit
```

### 2.3 Security / ops notes

- operator+ only; viewer 403; demo: no real copy.  
- Preview lists **every** path and byte estimate; confirm is a danger modal (downtime).  
- Staging dir is job-scoped, mode 700, wiped in `finally` (keep on failure until operator dismisses).  
- Path policy: no `..`, refuse absolute binds **outside** source `docker_base_dir` unless allow-listed in the preview.  
- Dual host lock: do not interleave backup / stack mutate / a second migrate on source **or** dest.  
- Audit: `service_migrate_preview` / `_start` / `_step` / `_done` / `_fail` with source, dest, project, bytes, fqdns, NPM old/new host — never PEM / `.env` / NPM password.  
- Default pipeline: **copy → start dest health → DNS/NPM → validate**. `dns_then_start` is an explicit option.

### 2.4 Non-goals (M) / defer past this minor

| Defer | Why |
|-------|-----|
| Zero-downtime / rsync-while-running (**M-live**) | Consistency + compose file locks |
| Full NPM proxy CRUD | **M-npm** is backend retarget only |
| **ACME-in-herder** | Out of 1.4; education/NPM-pull stays |
| HAOS / Supervisor add-ons | Not compose; unique appliance |
| Cross-arch image rebuild | Preflight refuse |
| Swarm / k8s | Out of product topology |
| Auto-detect every hardware bind as lock | Heuristic `devices:` warning only (**M9**) |
| 1.3 Files as the copy engine | Wrong size/trust model |
| Real SFTP on public demo | **demo never** — canned tree only (**D-F**) |
| Live DB logical replication | Stop-first file copy only |
| Auto rollback (revert DNS + start source) | Cap; two-host undo is its own design |

---

## 3. Ship bar (locked 2026-08-25)

| Priority | Item | Bar |
|----------|------|-----|
| **Must** | **M1–M7** migrate pipeline | One unlocked compose project moves host→host (stop → copy → dest up → name/proxy follow → TLS/Kuma when rows exist → rebind). Locked/HAOS cannot. Downtime accepted |
| **Must** | **M-npm** | `via_proxy` stacks: NPM `forward_host` (and port if needed) updates to dest. Public CNAME stays on NPM. Unmatched proxy host fails preflight |
| **Must** | **M8** leftover | After green validate, operator can `compose down` on source (volumes kept). Default: leave stopped + data intact |
| **Must** | **M9** | `devices:` / privileged / host-network warning + lock-or-acknowledge |
| **Must** | **D-F** | Public demo: canned Files tree, same chrome, no SFTP, mutates refused |
| **Should** | **M-rm** | Optional post-success: remove source project + named volumes. Preview + danger confirm. Default off |
| **Out** | ACME-in-herder · M-live · full NPM CRUD · Files token API · W-mux · AC-fg · N3 · CSP nonces · branding | Not this tag |

Success criteria:

1. Operator can lock Frigate on rpi5-4 with reason “Coral TPU”; **Move** is refused.  
2. HAOS host never appears as migrate source or destination.  
3. Unlocked **direct** stack: stop → copy → dest up → CNAME to dest `dns_name` → both Pi-holes `restartdns` → TLS/Kuma green when those rows exist.  
4. Unlocked **NPM-fronted** stack: dest up → proxy-host backend updates to dest; public name stays on NPM.  
5. Control-plane rows follow the dest host (maps, Services, stack panel).  
6. Source data still on disk after success unless **M8** / **M-rm** is chosen.  
7. Viewer cannot start a migrate; demo does not copy real stacks.  
8. Public demo Files tour (**D-F**) works without SFTP.

---

## 4. Quality bar

| Gate | Target |
|------|--------|
| Unit | Hold ≥ 55%; preflight matrix · lock enforce · path remap · CNAME old/new · NPM PUT backend · HAOS refuse |
| Tests | `tests/test_service_migrate.py` (new) — no live SSH; mock compose/rsync/Pi-hole/NPM |
| E2E | Wizard chrome + lock disabled CTA (no live two-host in CI) |
| Docs | FEATURE_PLAN + wiki Docker “Move a service” + DNS fabric + HAOS note; `mkdocs build --strict` at freeze |
| Security | Dual-host lock, staging wipe, path jail, audit without secret bodies, demo off |

---

## 5. Dependencies

| Deliverable | Why 1.4 needs it |
|-------------|------------------|
| 1.2 DNS fabric upsert + delete-old CNAME | **M4** direct |
| 1.x NPM `list_proxy_hosts` | **M-npm** match; new PUT |
| 1.2 cert verify (`openssl s_client` + SNI) | **M6** |
| 1.x stack lifecycle Jobs + exclusivity | **M3** / **M5**; extend exclusive set |
| 1.x backup rsync + path policy | **M3** staging |
| 1.x Pi-hole `restartdns` | **M4** fan-out |
| 1.x Kuma bindings + inventory-down alerts | **M6** / **M7** |
| 1.3 **F** (optional) | Operator inspect dest tree; **not** required to code M |
| 1.3 **W-id** (optional) | Named-volume `_data` may need privileged identity; spike may stay on docker-group + helper container |

1.3 bugs found during this train: **main as 1.3.x first**, then port here.

---

## 6. Out of scope (stay honest)

- Implementing any of **M** or **D-F** on `v1.3.0-dev` / `main` 1.3.x except hotfix ports  
- **ACME-in-herder**  
- Replacing backups, templates, or Files  
- Moving the PiHerder stack itself, or the only Pi-hole, as a happy-path demo  
- Full NPM proxy create/delete/SSL  
- Multi-tenant / two-person approve  
- Cloudflare / external DNS (still checklist)  

---

## 7. Capture log

| Date | Note |
|------|------|
| 2026-08-17 | Opened from operator request during 1.2 QA / Authentik TLS work. Feature name **service migration**. Pipeline + HAOS / Frigate host-lock captured. H2.5 + SPEC item promoted to this train. **Not** a 1.3 add. |
| 2026-08-20 | **Under consideration:** 1.3 Files API expansions (zip/edit/chmod/recursive delete/privileged tokens). Stay out of 1.4 Must. |
| 2026-08-22 | **D-F** parked from 1.3 freeze: canned demo Files tree. Promoted to **Must** at train open. |
| 2026-08-25 | **Train opened** on `v1.4.0-dev`. Must = **M1–M9** + **M-npm** + **D-F**. **M-rm** Should. **ACME** out. NPM-in-front is backend PUT, not refuse. Package version stays `1.3.0` until freeze. |
| 2026-08-25 | **M-rm** landed: leftover `remove` (source project + copied named volumes). Default still leave stopped. Extra ack. Dest never wiped. |
| 2026-08-25 | Operator wiki + maintainer docs pass for live validation (Move page, leftover, D-F demo Files, QA checklist). Freeze / version bump still later. |
| 2026-08-26 | Live-lab: dest picker wait modal; dest **project name/folder** override; dest **published port** remap (compose rewrite + NPM `forward_port` when the mapped port was the proxy backend). |
| 2026-08-26 | Live-lab: full bind paths; Recheck no longer leaves wait overlay stuck; outside binds remap into dest docker base (or skip). |
| 2026-08-26 | Job preflight ignored its own `service_migrate` row (busy_source/busy_dest self-block). |
| 2026-08-26 | Do not rsync `docker ps` truncated mounts (`…`). Migrate inspects container Source paths. |

---

## 8. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Finish **v1.3.0** freeze / tag / Hub | **Done** |
| 2 | Open **`v1.4.0-dev`** + lock Must/Should | **Done** 2026-08-25 |
| 3 | Named-volume spike note (helper vs docker-group) | **Done** — Mountpoint rsync (recipe A) |
| 4 | Land **M1** lock model + UI | **Done** |
| 5 | Land **M2** preflight (no copy) | **Done** |
| 6 | Land **M3–M6** + **M-npm** job + wizard; then **M7** | **Done** |
| 7 | **M8** leftover down · **M9** devices: · **M-rm** Should · **D-F** | **Done** |
| 8 | Wiki + ADMIN + QA + freeze · version `1.4.0` · tag · Hub | Operator wiki + QA stub ready for live two-host validation; **freeze / version bump not started** |

---

## 9. **D-F** — demo simulated Files (Must, **landed**)

**Not Stream M.** Independent of copy. 1.3 Files is flag-off; operator+ on a real herder; **demo** serves a canned tree (viewers may browse; writes refused; no SFTP). Console already has a **simulated** xterm (D5, 1.2).

| ID | Item | Notes |
|----|------|--------|
| D-F0 | **Discovery** | Same UI chrome as 1.3 Files. Data is **canned** in-process. **No SFTP, no Paramiko, no host disk.** |
| D-F1 | **Read tour** | List folders, open a small UTF-8 file, image preview ‹ ›, path bar with no `//`. Banner: demo / simulated. Shared **viewer** may open |
| D-F2 | **Mutates** | Upload / delete / chmod / zip / privileged: refuse or no-op with a clear demo message |
| D-F3 | **Seed** | Fake jail under something like `home/pi/docker/…` matching demo hosts; no `.env` / PEM bodies |
| D-F4 | **Kill switch** | Still **never** enable `PIHERDER_HOST_FILES` live SFTP on the public demo VPS. Simulated route is `DEMO_MODE` only |

**Out:** real jailed SFTP on Nomad · privileged `/` · zip-on-host · token `files` against demo · using this as the migrate copy engine.

Success: visitor opens **Files** on a seeded host, browses a fake tree, cannot exfiltrate or mutate a real filesystem; lab with the flag on is unchanged.

---

*Living on `v1.4.0-dev` until freeze into `RELEASE_v1.4.0.md`.*
