# Feature plan — Service migration

**Status:** Design capture 2026-08-17 · **not started**  
**Train:** [PLAN_v1.4.0.md](PLAN_v1.4.0.md) Stream **M** (after v1.3)  
**Horizon:** H2.5 leftover — “Service migrate / remove” ([ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)) · [SPEC.md](../SPEC.md) Phase 7  
**Related:** [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md)

## Goal

Move a **Docker Compose project** from fleet host A to fleet host B as one audited Job:

1. Stop the stack on A  
2. Copy project folder, configuration, bind data, and named volumes to B  
3. Retarget DNS (CNAME off A’s host name, onto B’s)  
4. Restart **both** Pi-hole resolvers / flush cache  
5. Start the stack on B  
6. Validate TLS and Uptime Kuma (when those rows exist)  
7. Honour **host lock** — HAOS and hardware-bound services cannot move  

PiHerder stays **SSH-first**. No agent on the Pis. The herder is the staging hop.

**Non-goals for this plan:** zero-downtime live migrate; Swarm/k8s; HAOS add-ons; NPM proxy-host CRUD; destructive volume wipe as the default; using 1.3 Files as the copy engine.

---

## Decisions (locked unless reversed at train open)

| # | Decision |
|---|----------|
| 1 | **v1.4.0** — not 1.2, not 1.3. Planning only until `v1.4.0-dev` opens |
| 2 | Unit of move = one **compose project** (same boundary as Stop all / Start all). Visual stacks are presentation only |
| 3 | Thin-slice cutover = **stop-first** (consistent files). Running-copy + final rsync is a Cap |
| 4 | Copy transport = **herder staging** under `BACKUP_ROOT/_migrate/{job_id}/` — reuse existing rsync/SSH. No dest-pulls-source key mesh in v1 |
| 5 | Thin-slice DNS = fabric rows with **`via_proxy=false`** (direct TLS / host-identity). NPM-in-front = refuse or checklist |
| 6 | Safer pipeline order (default): copy → **start dest + health** → flip DNS + `restartdns` → validate. Operator-stated “DNS then start” is an advanced option (clients can race an empty dest) |
| 7 | After success, **leave source stopped with data**. Optional `compose down` (keep volumes) is Should. Wipe is **M-rm**, later |
| 8 | **HAOS** (`os_type=haos`) is never a source or destination |
| 9 | Per-project **host lock** is first-class (Frigate + TPU). Implied `devices:` is a warning, not the only gate |
| 10 | Privileged actions: **preview → confirm → audit** + Job with live log |
| 11 | Lock **both** hosts for the job duration (backup + stack-mutating exclusive) |
| 12 | Reuse primitives — do not fork compose, rsync, fabric upsert, Pi-hole `restartdns`, cert verify |

---

## Operator examples (this fleet)

| Project | Move? | Why |
|---------|-------|-----|
| **Frigate** on rpi5-4 | **Lock** | Coral / TPU (`devices:` / USB). Operator sets host lock + reason |
| **Anything on HAOS** | **Refuse** | Unique appliance; not a compose dest |
| **Grafana** / **Authentik** (`sso`) on rpi5-6 | **Yes** (thin slice) | Direct TLS, no accelerator. DNS `login.hacknow.info` CNAME follows dest host. Cert target + `/certs` bind must be re-applied on dest |
| **Pi-hole** itself | **Out of happy path** | Fabric depends on it; document “do not migrate the resolver you are using to migrate” |
| **NPM** edge | **Out of thin slice** | `via_proxy` paths and proxy-host backend IPs |

---

## What exists (inventory)

Do not re-implement these. Wrap them.

| Need | Today | File / symbol |
|------|--------|----------------|
| Stop / start / up | Jobs + `compose_action(..., stop\|start\|restart\|down)`; deploy `up -d --remove-orphans` | `docker_management.compose_action` · `jobs/service.py` `docker_stack_*` |
| Project path jail | Compose **name** → host path; reject `/`, `..` | `resolve_compose_project_path` |
| Classify mounts | `named` / `bind_relative` / `bind_absolute` | `compose_project_files.classify_volume_source` |
| Volume sizes | `docker inspect` Mounts + `du -sb` (sudo fallback on `/var/lib/docker/volumes`) | `docker_management._enrich_container_mounts` |
| Host → herder copy | Celery rsync, per-server Redis mutex, default sources include docker tree + volume store | `backup.py` · `server_job_lock` kind `backup` |
| Herder → host copy | Reverse rsync to **same** server + path policy | `backup_restore.restore_backup_source` |
| CNAME retarget | Upsert deletes previous CNAME when target name changes, then fan-out add | `dns_fabric.core.upsert_service_record` |
| Flush resolvers | Pi-hole v6 `POST /action/restartdns` | `integrations.pihole.run_action` · `integrations_pihole` router |
| TLS check | `openssl s_client -servername` leaf SHA-256 | `certificates.verify_tls_endpoint_fingerprint` |
| Cert on dest | `CertificateTarget.server_id` + deploy (SFTP or stage_sudo) | `certificates.py` |
| Kuma | Binding `server_id` + `docker_project`; metrics hostname/url | `IntegrationBinding` · `uptime_kuma.py` |
| Desired state | `StackDeployment.server_id` + `project_name` | templates |
| Topology | `RuntimeEdge`, annotations, visual stacks, port notes — all `server_id` + project | models |
| HAOS | `Server.os_type` | `haos.py` · wiki HAOS hosts |
| Host lock | **Missing** | new table |

**Gaps:** no `ComposeProjectMeta`; restore cannot retarget another host; no two-host Job; no named-volume *create + fill* helper; cert/Kuma/topology rows do not follow a project; Pi-hole restart is not part of fabric upsert.

---

## Data model (lean)

### `ComposeProjectMeta` (new)

Per host + compose project (not only template-managed stacks):

| Column | Notes |
|--------|--------|
| `server_id` | FK Server |
| `compose_project` | Same identity as inventory / `ServiceDnsRecord.docker_project` |
| `host_locked` | bool, default false |
| `lock_reason` | `operator` \| `hardware` \| `haos` \| `infra` (short enum) |
| `lock_note` | e.g. “Coral TPU on USB bus 001” |
| `locked_at` / `locked_by_user_id` | audit |
| unique `(server_id, compose_project)` | |

Unlock is operator+ with confirm + audit. HAOS does **not** need a row per add-on — host `os_type` is enough.

Implied locks (no row required):

- `Server.os_type == "haos"` → refuse  
- Optional Should: compose `devices:` / `/dev/apex*` / `/dev/bus/usb` → preflight **warn** + require the operator lock-or-acknowledge checkbox  

### `Job`

- New type `service_migrate` (and later `service_remove`).  
- `Job.server_id` = **source** (history stays on the host you left).  
- `details` JSON: `dest_server_id`, `project`, `steps[]`, `bytes`, `fqdns`, `staging_dir`, `pipeline` (`health_then_dns` \| `dns_then_start`).  
- Exclusive: treat as stack-mutating **and** backup-like on **both** server ids. Extend `server_job_lock` with kind `migrate` **or** acquire `backup`+existing stack lane on both ids.

### Control-plane rebind (same transaction after dest is healthy)

| Row | Action |
|-----|--------|
| `ServiceDnsRecord` | `backend_server_id` → dest; if not `via_proxy`, `target_server_id` → dest; then sync |
| `StackDeployment` | `server_id` → dest (same `project_name`) |
| `IntegrationBinding` role=service | `server_id` → dest when `docker_project` matches |
| `CertificateTarget` | **Clone** onto dest (keep source row until leftover policy) + deploy + verify; or move if operator chose “this cert only served that stack” |
| `RuntimeEdge` | rewrite `from_server_id` / `to_server_id` when the endpoint project moved |
| `ContainerAnnotation`, `VisualServiceStack`, `PortAnnotation` | `server_id` → dest for that project |
| Docker inventory | refresh source + dest |

Do **not** move host-level SSH Kuma binds or `Server.dns_name` A records.

---

## Pipeline

### Default order (`health_then_dns`)

Safer for clients: they keep hitting the **stopped** source (fail closed / Kuma down) until dest answers TLS, then names flip.

```text
preflight
  → lock source + dest
  → compose stop (source)
  → rsync dataset → herder staging → dest
  → compose up -d (dest)
  → dest health (compose ps + optional TLS to dest IP:port)
  → upsert ServiceDnsRecord (delete old CNAME, add new)
  → restartdns  (every Pi-hole integration)
  → validate TLS (SNI = service FQDN) + Kuma poll
  → rebind control-plane rows
  → refresh inventories
  → release locks
  → wipe staging (success) / keep staging (failure)
```

### Alternate order (`dns_then_start`) — operator request

Matches the original verbal list (DNS before dest listen). Shorter “wrong host” window if dest start is instant; longer NXDOMAIN/empty if dest is slow. Offer as a checkbox, default **off**.

### Failure

| Step failed | State | Operator CTA |
|-------------|--------|----------------|
| Copy | Source stopped, dest untouched, staging kept | Retry copy, or **Start on source** |
| Dest up | Source stopped, dest partial, DNS **unchanged** | Logs; retry up; or start source |
| DNS / FTL | Dest up, names maybe split across Pi-holes | Re-sync fabric + restartdns |
| Validate | Dest up, DNS flipped, probe red | Do **not** auto-revert in v1; **Revert DNS + start source** is a Cap |

Auto-rollback is explicitly **not** Must — two-host undo is its own design.

---

## Copy rules

| Kind | Thin slice | Notes |
|------|------------|--------|
| Project directory | **Yes** | `docker_base_dir/<project>/` → dest `docker_base_dir/<project>/` (create dest dir) |
| Relative binds (`./data`, `./config.yml`) | **Yes** | Already under the project tree if they live there; extra relative paths one level up → include if still inside `docker_base_dir` |
| Named volumes | **Yes** | `docker volume create` on dest; copy `_data` as docker user **or** `docker run --rm -v NAME:/data alpine tar` pipe via herder |
| Absolute binds inside `docker_base_dir` | **Yes** | Remap prefix source base → dest base |
| Absolute binds **outside** jail (e.g. `/mnt/media`, `/dev`) | **Refuse** or acknowledge | Media libraries and devices are not a silent copy |
| `devices:` / privileged / host network | **Warn** | Pair with host lock |
| Images | **Pull on dest** (`up -d` pulls) | Preflight: dest arch == source arch (`uname -m` / inventory) |
| Build contexts | **Copy context** if present under project | Do not start a remote build farm |

Staging: `BACKUP_ROOT/_migrate/{job_id}/{project}/` + `volumes/{name}/`. Mode 700. Retention: delete on success; keep 24h or until Job dismissed on failure.

Path policy: reuse `backup_path_policy` + reject `..` / NUL. Do not rsync `/` or another host’s `docker_base_dir` by accident.

---

## DNS + Pi-hole

Existing `upsert_service_record` already:

- deletes the old CNAME when FQDN or target host DNS name changed  
- adds the new CNAME (or host A if identity name — **do not** migrate a host-identity A; that *is* the host)

Migrate must:

1. Select rows where `docker_project` matches **and** `backend_server_id == source`  
2. Refuse thin slice if any selected row has `via_proxy=true` (NPM still points at old backend IP)  
3. Set backend (and target if direct) to dest — dest **must** have `dns_name`  
4. `sync_now=True`  
5. Call `restartdns` on **all** configured Pi-hole integrations (this lab: both resolvers), not only the primary  

External DNS (Cloudflare) stays the existing checklist flag on the row.

---

## Validate

| Check | When | Pass |
|-------|------|------|
| `docker compose ps` dest | always | expected services running |
| TLS fingerprint | `CertificateTarget.verify_url` or fabric `certificate_id` + known port | `openssl s_client -servername <fqdn>` matches vault fingerprint (SNI matters — Authentik default cert vs `*.hacknow.info`) |
| Kuma | any service binding | metrics `up` for that monitor after DNS; rebind `server_id` so coverage / down-alerts follow dest |
| Inventory | always | dest project present; source project stopped or absent from running |

Kuma **write** (change monitor hostname from IP → FQDN) is **out**. If the monitor is IP-based, wizard shows a checklist.

---

## UX

### Lock

Docker project ⋯ / stack panel:

- **Lock to this host…** (reason + note)  
- **Unlock…** (confirm)  
- Locked badge + tooltip  

Move is hidden or disabled with the reason.

### Move wizard

Entry: project ⋯ **Move to another host…** (operator+, Docker on, not locked, not HAOS).

Steps:

1. Destination picker (Docker hosts only; exclude self, HAOS, dest with same project name)  
2. Dataset preview (paths, kinds, bytes, dest free)  
3. DNS preview (FQDNs that will flip) + cert / Kuma rows  
4. Leftover policy (keep stopped / down keep volumes)  
5. Confirm (downtime copy, danger styling)

Then existing **JobHold** live log with step headers.

Viewer: 403. Demo: disabled or fake preview.

---

## Phases

| Phase | Name | Priority | Status |
|-------|------|----------|--------|
| **M0** | Discovery + this plan | — | **Done** 2026-08-17 |
| **M1** | Host lock model + UI | Must | Planned |
| **M2** | Preflight (no copy) | Must | Planned |
| **M3** | Stop + stage + copy | Must | Planned |
| **M4** | Fabric upsert + both `restartdns` | Must | Planned |
| **M5** | Start dest (`compose up -d`) | Must | Planned |
| **M6** | Validate TLS / Kuma | Must when rows exist | Planned |
| **M7** | Control-plane rebind | Must | Planned |
| **M8** | Source leftover `compose down` | Should | Planned |
| **M9** | `devices:` warning / lock suggest | Should | Planned |
| **M-rm** | Destructive remove (source wipe, unused vols) | Cap / later | Parked |
| **M-live** | Rsync while running + final sync | Cap | Parked |
| **M-npm** | NPM proxy backend rewrite | After NPM write API | Parked |

### Acceptance (Must)

- [ ] Lock Frigate-style project; Move refused; audit `service_host_lock`  
- [ ] HAOS never in dest list; migrate from HAOS 403  
- [ ] Unlocked project: stop → copy → dest up → CNAME to dest `dns_name` → both Pi-holes `restartdns` → dest inventory shows stack  
- [ ] TLS probe uses SNI = service FQDN when a cert is linked  
- [ ] Kuma service bindings follow dest `server_id`  
- [ ] `StackDeployment` + annotations follow dest  
- [ ] Source files still on disk after success  
- [ ] Concurrent backup/stack job on either host → 409  
- [ ] Viewer 403; demo does not copy  
- [ ] Wiki Docker + DNS fabric + HAOS note  

---

## Security

| Risk | Control |
|------|---------|
| Copy `/` or escape `docker_base_dir` | Path policy + preview allow-list |
| Staging leak of `.env` / PEMs | 700 dir; wipe; audit hashes/bytes not bodies |
| Two migrates / backup during copy | Dual-host exclusive lock |
| Move Frigate off the TPU host | Host lock + `devices:` warning |
| Flip DNS before dest listens | Default `health_then_dns` |
| Viewer / demo abuse | RBAC + demo kill |
| NPM still sending to old IP | Refuse `via_proxy` in thin slice |
| Herder disk fill | Preflight dest **and** herder free space |

Align with design principles: auditable privileged actions; secrets encrypted at rest; opt-in dangerous surfaces.

---

## File map (indicative)

| Area | Path |
|------|------|
| Model + alembic | `app/models.py` `ComposeProjectMeta` · `migrations/versions/` |
| Service | `app/services/service_migrate/` (preflight, copy, pipeline) |
| Jobs | `app/services/jobs/service.py` type `service_migrate` |
| Router / UI | Docker project ⋯ + wizard partial + JobHold |
| DNS / FTL | wrap `upsert_service_record` + pihole `run_action` fan-out |
| Tests | `tests/test_service_migrate.py` |
| Wiki | `wiki/docker/overview.md` + new `wiki/docker/service-migration.md` at ship |

---

## Open questions (resolve at train open)

| # | Question | Default lean |
|---|----------|--------------|
| 1 | Named volume copy: rsync `_data` as docker group vs helper container tar? | Spike both; prefer helper if sudo-less |
| 2 | Cert target: move vs clone until source leftover? | **Clone** + deploy dest; disable source target |
| 3 | Job.server_id source or dest? | **Source** |
| 4 | Multiple fabric rows per project? | Move **all** matching backend+project |
| 5 | Default pipeline order | **`health_then_dns`** |
| 6 | Kill switch env? | `PIHERDER_SERVICE_MIGRATE=false` until GA is acceptable |

---

## Success criteria (horizon)

An operator can:

1. Lock a hardware-bound stack so it cannot move.  
2. Move an unlocked stack (Grafana / Authentik-class) to another Pi with its data, name, TLS, and Kuma coverage.  
3. See both Pi-holes restart after the CNAME flip.  
4. Keep the source dataset until they choose a later leftover action.  
5. Never treat HAOS as a compose suitcase.

---

## Changelog

| Date | Note |
|------|------|
| 2026-08-17 | Initial plan from operator pipeline + HAOS / Frigate lock. Discovery of existing compose, rsync, fabric, restartdns, TLS, Kuma primitives. Parked on **v1.4.0**. |
