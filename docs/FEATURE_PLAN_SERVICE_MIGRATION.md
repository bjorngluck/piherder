# Feature plan — Service migration

**Status:** Train open on `v1.4.0-dev` · M1–M9 + M-npm + D-F + M-rm landed · freeze next  
**Train:** [PLAN_v1.4.0.md](PLAN_v1.4.0.md) Stream **M** (active)  
**Horizon:** H2.5 leftover — “Service migrate / remove” ([ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)) · [SPEC.md](../SPEC.md) Phase 7  
**Related:** [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md)

## Goal

Move a **Docker Compose project** from fleet host A to fleet host B as one audited Job:

1. Stop the stack on A  
2. Copy project folder, configuration, bind data, and named volumes to B  
3. Retarget **direct** DNS (CNAME off A onto B) **or** retarget the **NPM** proxy-host backend to B (public CNAME stays on NPM)  
4. Restart **both** Pi-hole resolvers / flush cache (direct path; after fabric sync)  
5. Start the stack on B  
6. Validate TLS and Uptime Kuma (when those rows exist)  
7. Honour **host lock** — HAOS and hardware-bound services cannot move  

PiHerder stays **SSH-first**. No agent on the Pis. The herder is the staging hop.

**Non-goals for this plan:** zero-downtime live migrate; Swarm/k8s; HAOS add-ons; full NPM proxy CRUD (backend retarget **is** in); ACME-in-herder; destructive volume wipe as the **default**; using 1.3 Files as the copy engine.

---

## Decisions (locked 2026-08-25)

| # | Decision |
|---|----------|
| 1 | **v1.4.0** on `v1.4.0-dev`. Tag does not ship without migrate |
| 2 | Unit of move = one **compose project** (same boundary as Stop all / Start all). Visual stacks are presentation only |
| 3 | Cutover = **stop-first** (consistent files). Running-copy + final rsync is **M-live**, out |
| 4 | Copy transport = **herder staging** under `BACKUP_ROOT/_migrate/{job_id}/` — reuse existing rsync/SSH. No dest-pulls-source key mesh |
| 5 | **Direct** (`via_proxy=false`): fabric CNAME → dest `dns_name` + both `restartdns`. **NPM-in-front** (`via_proxy=true`): keep edge CNAME; **PUT** proxy-host `forward_host` to dest. Unmatched proxy host fails preflight |
| 6 | Safer pipeline order (default): copy → **start dest + health** → flip DNS **or** NPM backend → `restartdns` (direct) → validate. Operator-stated “DNS then start” is an advanced option (default **off**) |
| 7 | After success, **leave source stopped with data**. **M8** `compose down` (keep volumes) is Must. **M-rm** source remove + volume delete is Should (default off) |
| 8 | **HAOS** (`os_type=haos`) is never a source or destination |
| 9 | Per-project **host lock** is first-class (Frigate + TPU). Implied `devices:` is **M9** warning, not the only gate |
| 10 | Privileged actions: **preview → confirm → audit** + Job with live log |
| 11 | Lock **both** hosts for the job duration (backup + stack-mutating exclusive) |
| 12 | Reuse primitives — do not fork compose, rsync, fabric upsert, Pi-hole `restartdns`, cert verify. NPM GET already exists; add **narrow PUT** only |
| 13 | Cert targets: **clone** onto dest + deploy + verify; disable source target until leftover |
| 14 | `Job.server_id` = **source**; `details.dest_server_id` = dest |
| 15 | Kill switch `PIHERDER_SERVICE_MIGRATE=false` until GA-enough. Demo never copies |
| 16 | **ACME-in-herder** is out of 1.4 |

---

## Operator examples (this fleet)

| Project | Move? | Why |
|---------|-------|-----|
| **Frigate** on rpi5-4 | **Lock** | Coral / TPU (`devices:` / USB). Operator sets host lock + reason |
| **Anything on HAOS** | **Refuse** | Unique appliance; not a compose dest |
| **Grafana** / **Authentik** (`sso`) on rpi5-6 | **Yes** (direct) | Direct TLS. DNS CNAME follows dest host. Cert target + `/certs` bind re-applied on dest |
| **Pi-hole** itself | **Out of happy path** | Fabric depends on it; document “do not migrate the resolver you are using to migrate” |
| **NPM-fronted** app (CNAME → NPM, `via_proxy=true`) | **Yes** (**M-npm**) | Public name stays on NPM; proxy-host `forward_host` updates to dest LAN/SSH address. Do not migrate the **NPM** edge box as a happy path |

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
| NPM proxy hosts | GET `/api/nginx/proxy-hosts` (RO) — `forward_host`, `forward_port`, domains | `integrations.npm.list_proxy_hosts` — **M-npm** adds PUT |
| TLS check | `openssl s_client -servername` leaf SHA-256 | `certificates.verify_tls_endpoint_fingerprint` |
| Cert on dest | `CertificateTarget.server_id` + deploy (SFTP or stage_sudo) | `certificates.py` |
| Kuma | Binding `server_id` + `docker_project`; metrics hostname/url | `IntegrationBinding` · `uptime_kuma.py` |
| Desired state | `StackDeployment.server_id` + `project_name` | templates |
| Topology | `RuntimeEdge`, annotations, visual stacks, port notes — all `server_id` + project | models |
| HAOS | `Server.os_type` | `haos.py` · wiki HAOS hosts |
| Host lock | **Missing** | new table |

**Gaps:** no `ComposeProjectMeta`; restore cannot retarget another host; no two-host Job; no named-volume *create + fill* helper; cert/Kuma/topology rows do not follow a project; Pi-hole restart is not part of fabric upsert; NPM has **no write** yet.

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
- **M9 Must:** compose `devices:` / `/dev/apex*` / `/dev/bus/usb` / privileged / host network → preflight **warn** + require the operator lock-or-acknowledge checkbox  

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
  → if via_proxy: PUT NPM proxy-host forward_host → dest
    else: upsert ServiceDnsRecord (delete old CNAME, add new)
         restartdns  (every Pi-hole integration)
  → validate TLS (SNI = service FQDN) + Kuma poll
  → rebind control-plane rows
  → leftover (M8 down / M-rm if chosen)
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
| NPM PUT | Dest up, proxy still on old backend | Retry PUT; public name still on NPM |
| Validate | Dest up, DNS/NPM flipped, probe red | Do **not** auto-revert in v1; **Revert DNS/NPM + start source** is a Cap |

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

## DNS + Pi-hole + NPM

Existing `upsert_service_record` already:

- deletes the old CNAME when FQDN or target host DNS name changed  
- adds the new CNAME (or host A if identity name — **do not** migrate a host-identity A; that *is* the host)

### Direct (`via_proxy=false`)

1. Select rows where `docker_project` matches **and** `backend_server_id == source`  
2. Set backend **and** target to dest — dest **must** have `dns_name`  
3. `sync_now=True`  
4. Call `restartdns` on **all** configured Pi-hole integrations, not only the primary  

### NPM-in-front (`via_proxy=true`) — **M-npm** Must

1. Keep the public CNAME on the NPM edge (`target_server_id` stays the proxy identity).  
2. Set `backend_server_id` → dest.  
3. Match an NPM proxy host whose `domain_names` include the service FQDN.  
4. **PUT** that host: `forward_host` → dest reachable address (wizard shows old → new: dest SSH/LAN hostname or dest `dns_name`). Keep `forward_port` unless dest published port changed (then update + preview).  
5. Missing NPM integration, login fail, or **no matching proxy host** → **preflight fail**, not a checklist.  
6. Do **not** create, delete, or rewrite SSL/ACME on the proxy host.

External DNS (Cloudflare) stays the existing checklist flag on the row.

### NPM PUT contract (unofficial API)

NPM is not a documented public API. Observed today: GET `/api/nginx/proxy-hosts`. Write path for 1.4:

1. GET list (or GET `/api/nginx/proxy-hosts/{id}` if the instance supports it).  
2. Copy the existing host object.  
3. Replace `forward_host` (and `forward_port` only when the preview said so).  
4. PUT `/api/nginx/proxy-hosts/{id}` with the **full** object NPM expects (partial PATCH is unreliable on this API).  
5. Audit `npm_id`, domains, old host, new host — **never** NPM password.  
6. Tests mock httpx; no live NPM in CI.

Not in 1.4: POST new proxy host, DELETE, certificate_id changes, `enabled` toggle as a migrate step, ACME.

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
3. DNS / NPM preview (FQDNs that will flip **or** proxy-host backend old → new) + cert / Kuma rows  
4. Leftover policy: leave stopped (default) · **M8** down keep volumes · **M-rm** remove project + volumes (Should, extra confirm)  
5. Confirm (downtime copy, danger styling)

Then existing **JobHold** live log with step headers.

Viewer: 403. Demo: disabled or fake preview.

---

## Phases

| Phase | Name | Priority | Status |
|-------|------|----------|--------|
| **M0** | Discovery + this plan | — | **Done** 2026-08-17 |
| **M1** | Host lock model + UI | Must | **Done** |
| **M2** | Preflight (no copy) | Must | **Done** |
| **M3** | Stop + stage + copy | Must | **Done** (Mountpoint rsync) |
| **M4** | Fabric upsert + both `restartdns` | Must | **Done** |
| **M5** | Start dest (`compose up -d`) | Must | **Done** (with M3 job) |
| **M6** | Validate TLS / Kuma | Must when rows exist | **Done** |
| **M7** | Control-plane rebind | Must | **Done** |
| **M8** | Source leftover `compose down` | Must | **Done** (optional; default leave stopped) |
| **M9** | `devices:` warning / lock suggest | Must | **Done** (ack checkbox) |
| **M-npm** | NPM proxy backend retarget | Must | **Done** (PUT `forward_host`) |
| **M-rm** | Destructive remove (source wipe, unused vols) | Should | **Done** (default off; extra ack) |
| **D-F** | Demo simulated Files | Must | **Done** (canned tree; no SFTP) |
| **M-live** | Rsync while running + final sync | Out | Parked |

### Acceptance (Must)

- [ ] Lock Frigate-style project; Move refused; audit `service_host_lock`  
- [ ] HAOS never in dest list; migrate from HAOS 403  
- [ ] Unlocked **direct** project: stop → copy → dest up → CNAME to dest `dns_name` → both Pi-holes `restartdns` → dest inventory shows stack  
- [ ] Unlocked **`via_proxy`** project: dest up → NPM `forward_host` updates to dest; public CNAME stays on NPM  
- [ ] TLS probe uses SNI = service FQDN when a cert is linked  
- [ ] Kuma service bindings follow dest `server_id`  
- [ ] `StackDeployment` + annotations follow dest  
- [ ] Source files still on disk after success unless M8 / M-rm  
- [ ] Concurrent backup/stack job on either host → 409  
- [ ] Viewer 403; demo does not copy  
- [ ] Wiki Docker + DNS fabric + HAOS note  
- [ ] **D-F:** demo Files tour, no SFTP  

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
| NPM still sending to old IP | **M-npm** PUT backend; unmatched host fails preflight |
| Herder disk fill | Preflight dest **and** herder free space |

Align with design principles: auditable privileged actions; secrets encrypted at rest; opt-in dangerous surfaces.

---

## File map (indicative)

| Area | Path |
|------|------|
| Model + alembic | `app/models.py` `ComposeProjectMeta` · `migrations/versions/` |
| Service | `app/services/service_migrate/` (preflight, copy, pipeline, leftover) |
| NPM write | `app/services/integrations/npm.py` PUT `/api/nginx/proxy-hosts/{id}` backend only |
| Jobs | `app/services/jobs/service.py` type `service_migrate` |
| Config | `PIHERDER_SERVICE_MIGRATE` default false |
| Router / UI | Docker project ⋯ + wizard partial + JobHold |
| DNS / FTL | wrap `upsert_service_record` + pihole `run_action` fan-out |
| Tests | `tests/test_service_migrate.py` |
| Wiki | `wiki/docker/overview.md` + `wiki/docker/service-migration.md` (lock + preflight; copy TBD) |

---

## Open questions (train open 2026-08-25)

| # | Question | Decision |
|---|----------|----------|
| 1 | Named volume copy: rsync `_data` as docker group vs helper container tar? | **Spike** — prefer helper if sudo-less. See **Named-volume spike** below. Lock before M3 |
| 2 | Cert target: move vs clone until source leftover? | **Locked:** clone + deploy dest; disable source target |
| 3 | Job.server_id source or dest? | **Locked:** source |
| 4 | Multiple fabric rows per project? | **Locked:** all matching backend+project |
| 5 | Default pipeline order | **Locked:** `health_then_dns` |
| 6 | Kill switch env? | **Locked:** `PIHERDER_SERVICE_MIGRATE=false` until GA-enough |

---

## Preflight matrix (M2)

Implement as a pure function + wizard list. First failing **block** stops the job; **warn** needs acknowledge.

| Check | Result | Notes |
|-------|--------|--------|
| Kill switch `PIHERDER_SERVICE_MIGRATE` | Block | Flag off |
| Role | Block | viewer 403; demo never copies |
| Source HAOS / dest HAOS | Block | `os_type=haos` |
| Source project `host_locked` | Block | Show reason |
| Dest Docker feature off | Block | |
| Dest == source | Block | |
| Dest project name already exists | Block | |
| Arch mismatch (`uname -m`) | Block | |
| Dest `docker_base_dir` not writable | Block | |
| Dest or herder free space < payload + margin | Block | |
| Published port clash on dest | Block | |
| Active backup or stack-mutating job on source **or** dest | Block | 409 |
| Absolute bind outside `docker_base_dir` | Block unless allow-listed in preview | |
| Direct row: dest missing `dns_name` | Block | |
| `via_proxy` and no NPM integration | Block | |
| `via_proxy` and no matching proxy host (FQDN in `domain_names`) | Block | |
| `devices:` / privileged / host network | **Warn** | **M9** lock-or-acknowledge |
| Kuma monitor is IP-based | **Warn** | checklist; no Kuma write |
| Cloudflare / external DNS flag | **Warn** | checklist |

---

## Named-volume spike (before M3)

Two recipes. Prefer **B** if the fleet identity can run it without password sudo.

| | **A — rsync `_data`** | **B — helper container tar** |
|--|----------------------|------------------------------|
| Idea | After `docker volume create` on dest, rsync source `/var/lib/docker/volumes/NAME/_data/` → dest same path as docker group / sudo rsync | `docker run --rm -v NAME:/data alpine tar czf - -C /data .` on source, pipe via herder, extract into dest volume |
| Privilege | Often needs `sudo -n rsync` on `/var/lib/docker/volumes` (same as backup volume trees) | Needs docker.sock / docker group on **fleet** identity; no host-path sudo if docker works |
| Risk | Path jail + dest volume name collision | Image pull of helper; pipe size through herder |
| Lean | Fallback if docker run is refused | **Prefer** if sudo-less |

**v1.4.0-dev decision (2026-08-25):** ship **A — rsync volume Mountpoint** (`docker volume inspect … Mountpoint`, typically `/var/lib/docker/volumes/NAME/_data`). Same privilege as existing backup of the volume store. Recipe **B** (helper container tar) remains the fallback if a fleet identity cannot read `_data`.

---

## Success criteria (horizon)

An operator can:

1. Lock a hardware-bound stack so it cannot move.  
2. Move an unlocked **direct** stack (Grafana / Authentik-class) to another Pi with its data, name, TLS, and Kuma coverage.  
3. Move an unlocked **NPM-fronted** stack: proxy backend follows dest; public name stays on NPM.  
4. See both Pi-holes restart after a **direct** CNAME flip.  
5. Keep the source dataset until they choose **M8** / **M-rm**.  
6. Never treat HAOS as a compose suitcase.

---

## Changelog

| Date | Note |
|------|------|
| 2026-08-17 | Initial plan from operator pipeline + HAOS / Frigate lock. Discovery of existing compose, rsync, fabric, restartdns, TLS, Kuma primitives. Parked on **v1.4.0**. |
| 2026-08-25 | Train open. Must = M1–M9 + M-npm + D-F. M-rm Should. ACME out. NPM backend PUT (not refuse `via_proxy`). Q2–Q6 locked. Preflight matrix + named-volume spike + NPM PUT contract recorded. |
| 2026-08-25 | **M1** lock + **M2** preflight + **M3/M5** stop → herder rsync → dest `up -d`. Named volumes = Mountpoint rsync (recipe A). |
| 2026-08-25 | **M4 / M-npm:** direct CNAME + both Pi-holes `restartdns`; NPM PUT `forward_host` (public CNAME stays on NPM). |
| 2026-08-25 | **M6–M9:** TLS SNI probe + Kuma last_state; rebind maps/Kuma/templates/clone cert targets; leftover `compose down`; devices ack. |
| 2026-08-25 | **D-F:** demo simulated Files (canned tree, viewer browse, writes refused). |
| 2026-08-25 | **M-rm:** optional source project + named volume delete after green migrate. Preview + danger confirm + `leftover_remove_ack`. Dest never wiped. |
