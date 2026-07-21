# PiHerder ecosystem roadmap

**Status:** Active  
**Date:** 2026-07-12 · **Refreshed:** 2026-07-21 (**v0.7.0 tagged** · active **v0.8.0 RC3** — LAN nmap **product complete N0–N10** (identity, lifecycle, Hosts dual layout + chrome, soft embed) · remaining: screenshot pack · ~50% coverage · polish)  
**Related:** [SPEC.md](../SPEC.md) · [ADMIN.md](ADMIN.md) · [PLAN_v0.8.0.md](PLAN_v0.8.0.md) · [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) · [PLAN_v0.7.0.md](PLAN_v0.7.0.md) · [PLAN_v0.6.0.md](PLAN_v0.6.0.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [RELEASE_v0.6.0.md](RELEASE_v0.6.0.md)  
**License:** MIT open source (see [LICENSE](../LICENSE)).

This document is the public multi-horizon roadmap for taking PiHerder from a production-ready **fleet manager** to the hub of a self-hosted **homelab / security ops** ecosystem (DNS, proxy, monitoring, smart home, media, automation).

Design principles stay the same as SPEC:

- Auditable privileged actions  
- Secrets encrypted at rest; decrypt only in memory for jobs  
- Offline / air-gapped ready once built  
- Dangerous or external actions are **opt-in** (preview → confirm → audit)  
- Integrations are **optional** — core fleet ops work alone  

---

## Release track

| Version | Theme | Horizon | Status |
|---------|--------|---------|--------|
| **v0.2.0** | Production install story (compose, token REST, prod docs) + H0.5 + early Kuma | H0 / H0.5 | **Tagged** 2026-07-10 — [RELEASE_v0.2.0.md](RELEASE_v0.2.0.md) |
| **v0.2.x** | Platform reliability (host deps, stack Status tab, multi-worker) | H0.5 | Shipped on main (included in v0.2.0) |
| **v0.3.0** | Integration hub — Kuma + **Grafana** (kinds, templates, Docker chips) | H1 | **Tagged** 2026-07-11 — [RELEASE_v0.3.0.md](RELEASE_v0.3.0.md) |
| **v0.4.0** | Post-0.3 quality + **service templates** foundation (wizard, volumes/booleans, from-host, step-up secrets, wait modal, OOTB pack, desired state V1) | H2 + fixes | **Tagged** 2026-07-12 — [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [PLAN_v0.4.0.md](PLAN_v0.4.0.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) |
| **v0.4.x** | *(folded)* Former ops track — drift, NPM connector, git catalog, `.env` migrate | H1/H2 | **Absorbed into v0.5.0** (no separate planning phase) |
| **v0.5.0** | **First RC** — ops depth + template polish + restore + DNS fabric + Pi-hole/NPM/certs + production wikis + multi-arch + freeze bar | RC | **Tagged** 2026-07-17 — [RELEASE_v0.5.0.md](RELEASE_v0.5.0.md) · [PLAN_v0.5.0.md](PLAN_v0.5.0.md) |
| **v0.6.0** | **RC2 polish** — template Jobs, cert UX (edge map, presets), Docker bulk, topology+coverage; wizard **out** | H2.75 P1 + H2.5 stretch + polish | **Tagged** 2026-07-18 — [RELEASE_v0.6.0.md](RELEASE_v0.6.0.md) · [PLAN_v0.6.0.md](PLAN_v0.6.0.md) |
| **v0.7.0** | **Add-host wizard** + **Playwright E2E** + topology annotations + **compose sets** + drift Job | H2.75 P2 + quality | **Tagged** 2026-07-19 — [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) · [PLAN_v0.7.0.md](PLAN_v0.7.0.md) |
| **v0.8.0** | **RC3** — overall polish · extend E2E + **~50% coverage** · **full docs review + screenshots** · **LAN nmap** (auto-create + network view; product largely on main) | Quality + H2.5 H1 | **Active** — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · wiki [lan-discovery](../wiki/integrations/lan-discovery.md) · [screenshots README](../wiki/assets/screenshots/README.md) |
| **v0.8.x / later** | Host stats/commands, bootstrap depth, web SSH; topology column profiles | H2.75 P3–P5 + residual | After RC3 as capacity |
| **v1.0** | Stable template schema + REST + docs + community process | H0–H2 freeze | Planned |

**Decision:** All fixes after `v0.3.0` shipped in **`v0.4.0`** (no intermediate `v0.3.1`). Historical bug list: [PLAN_v0.4.0.md](PLAN_v0.4.0.md) §2.

**Decision (2026-07-12):** **Single development target `v0.5.0`** — former “v0.4.x ops” and “RC polish” are one cycle. Optional intermediate git tags only if something must ship early.

**Decision (2026-07-17):** **Single development target `v0.6.0` (RC2)** — operator polish + selected H2.75 slices. See [PLAN_v0.6.0.md](PLAN_v0.6.0.md).

**Decision (2026-07-18):** Runtime topology stream (**H2**) + Kuma coverage (**H3**) **closed for 0.6**. **LAN discovery / nmap (H1)** scheduled for **v0.8.0 RC3** (not 0.6). Topology residual (configurable columns / link-to-column) is post-0.6 polish → RC3 capacity.

**Decision (2026-07-18 freeze):** **v0.6.0 product code frozen**. **Add-host wizard (H2.75 P2)** and **wiki screenshot refresh** deferred to **v0.7.0**. Onboarding for 0.6 remains form + SSH access panel.

**Decision (2026-07-18):** **Single development target `v0.7.0`** — add-host wizard + screenshot pack + **Playwright E2E** (hard tag gate; separate CI job on main/PR); residual polish only as capacity. See [PLAN_v0.7.0.md](PLAN_v0.7.0.md).

**Decision (2026-07-19):** **v0.7.0 feature-locked and tagged**. Wizard, E2E A+B, annotations, compose sets, drift Job shipped. Screenshot pack deferred; residual polish + E2E/coverage growth + full docs review + **nmap** → **v0.8.0 RC3**. See [PLAN_v0.8.0.md](PLAN_v0.8.0.md) · [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md).

**Production path:** ~~v0.4.0 templates~~ **done** → ~~**v0.5.0 RC1**~~ → ~~**v0.6.0 RC2**~~ → ~~**v0.7.0**~~ **tagged** → **v0.8.0 RC3** (active) → **v1.0** refined production.

**Note:** Multi-arch image **published** — [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) (`0.7.0` / `0.7` / `latest`).

---

## Horizon 0 — Production readiness (v0.2)

| Item | Notes |
|------|--------|
| Docker Hub (or GHCR) image | Documented pull path; tags `0.2.x` + `latest` when published |
| Clean compose example | Relative volumes; no `~/` bind-mount assumptions |
| Token REST API | Admin-managed Bearer tokens: `read`/`jobs`/`edit` + feature scopes + IP allowlist; [API.md](API.md) |
| Production ADMIN guide | TLS, upgrades, metrics scrape, webhooks → Signal |
| Community scaffolding | SECURITY.md, Discussions/Discord pointers in README |

**Out of scope for H0:** templates, Grafana product UI, HA plugin, AI.  
**v0.2.0 ship bar (git tag):** compose defaults + token REST + prod docs + H0.5 reliability.  
Uptime Kuma (H1) also landed on `main` before the tag and is included.  
**Optional:** multi-arch image on Docker Hub/GHCR ([PUBLISH_IMAGE.md](PUBLISH_IMAGE.md)).

---

## Horizon 0.5 — Platform reliability & scale (v0.2.x)

Ops hardening that sits **between** production install and product integrations. Implementation order:

| # | Item | Stance | Notes |
|---|------|--------|-------|
| **1** | **Remote host dependency check** | **Done** (v0.2.x) | Read-only chips on server detail; re-check under **SSH access** (and auto after **Test connection** / key deploy / least-priv). Probes tools for **enabled** features (`rsync`, sudo/plain rsync, `docker`, `apt`). Hints only — no auto-install. |
| **2** | **Settings → Status tab** | **Done** (v0.2.x) | Admin **Status** tab: web, PostgreSQL, Redis, Celery (nodes + pool slots), APScheduler, **mount free** (fast). Backup tree `du` / per-host folders **on demand** (View details). Manual + every 2 min poll; alerts on state change; `/metrics` from last check. |
| **2b** | **DB retention / data grooming** | **Done** (RC3 stream **R1**, 2026-07-19) | Settings → **Stale data cleanup**: opt-in schedule + Run now · job `stale_data_cleanup` · Jobs + Audit (default 30d each when on) · optional nmap runs/XML. Never deletes pending/running. Distinct from per-server **backup file** retention. See [PLAN_v0.8.0.md](PLAN_v0.8.0.md) § **R** · wiki [Settings](../wiki/operations/settings.md#stale-data-cleanup). |
| **2c** | **Entity delete cascades** | **Partial today** · matrix later | **Server remove:** cancel jobs, drop compose drafts, DNS cleanup, **null** job/audit FKs (history kept) — documented. Product trees (integrations, nmap devices, cert maps, annotations) need explicit cascade + UI preview. Never silent remote wipe. |
| **3** | **Multi-worker** | **Done** (v0.2.x) | Celery `CELERY_CONCURRENCY` (default **2** = pool slots in one node); Redis **per-server backup mutex**; parallel across hosts. Prefer raising concurrency over multiple nodes unless HA/scale-out. Shared `/backups`; cancel + lock TTL intact. |
| **4** | **Deploy topologies** | Docs only | See [Deployment architecture](#deployment-architecture) — Compose supported; k8s / bare install **under consideration only**. |

### Remote dependency check (detail)

A host can accept an SSH key while missing tools required for backups or Docker. Probe after key deploy / least-priv, after **Test connection**, or via **SSH access → Check dependencies**. Server detail shows the last snapshot only. Severity depends on **enabled** features:

| Check | Required when |
|-------|----------------|
| SSH + shell | always |
| `rsync` on PATH | backups enabled |
| `sudo -n rsync` or plain rsync (root/HAOS) | backups enabled (same path as runtime backup probe) |
| `docker` (+ group/socket if practical) | container feature on |
| `apt-get` / apt | OS patch on |

### Stack Status tab (detail)

| Component | Check idea |
|-----------|------------|
| Web | Process self / `/health` |
| PostgreSQL | `SELECT 1` (already partial on `/metrics`) |
| Redis | Broker ping |
| Celery | `inspect().ping()` + `stats()` → **nodes** and **pool slots** (`CELERY_CONCURRENCY`) |
| APScheduler | Running + jobs registered |
| Disk (fast) | Mount free space on `/backups`, `/data`, `/herder_backups` (deduped by device) |
| Disk (lazy) | Full tree size + top-level host folders under `/backups` — **View details** only (`GET …/status/backup-usage`) |

Notifications must **not** spam: only on state transition (plus optional cooldown). Scheduled/Check now stay fast; expensive `du` is never part of the default poll.

### Multi-worker (implemented)

- **Default:** one `celery-worker` container with `CELERY_CONCURRENCY=2` (two **pool slots**, one **node**). Override in `.env`.  
- **Nodes vs slots:** pool children run backups independently. Two nodes only help HA / multi-machine scale — not required for “two parallel backups.”  
- **Mutex:** Redis key `piherder:server_lock:backup:{server_id}` (SET NX + token release). Busy tasks requeue (~20s) until free or ~1h timeout; job stays `pending` with `waiting_for_server`.  
- **Parallelism:** different servers at once; same server serializes. Patch apply still uses DB active-job check + web thread pool.  
- **Cancel:** `Job.celery_task_id` + `revoke(terminate=True)`; lock released in task `finally` (or TTL on crash).  
- **Scale:** raise `CELERY_CONCURRENCY` first; multi-container needs shared volumes and no fixed `container_name` (`docker compose up --scale celery-worker=N`).  
- **Ops:** Settings → Status shows e.g. `1 node(s) · 2 pool slot(s)`; metrics expose `piherder_celery_workers` (nodes) and `piherder_celery_pool_slots`.  
- **Env catalog:** [`.env.example`](../.env.example).

---

## Deployment architecture

**Committed / supported today:** single **Docker Compose** stack (`web`, `db`, `redis`, `celery-worker`, `caddy`) as in this repo. That is the primary install and upgrade path.

| Topology | Stance | Notes |
|----------|--------|-------|
| **Docker Compose** | **Supported** | Default; documented volumes, TLS, upgrades |
| **Kubernetes** | **Under consideration only** | No Helm chart or delivery date. Needs PVC story for backups, secrets, multi-writer rsync, scheduler singleton |
| **Local / bare install** | **Under consideration only** | No dual-path installer. Would mean systemd + venv + Postgres/Redis on the host — high docs tax |

Do **not** treat k8s or bare-metal as promised deliverables in H0–H2 success criteria.

---

## Horizon 1 — Integration hub (v0.3)

Read-mostly integrations: config + status + deep links + **server / host / Docker bindings**.

| Item | Status |
|------|--------|
| Integration registry + **Catalog** nav (`/catalog` → Integrations; Settings-style tabs) | **Shipped** |
| **Uptime Kuma** — API key + `/metrics`; SSH + host service + Docker bindings; TLS; deep links; notifications; Services pages; logos | **Shipped** |
| **Grafana** — service account token; health; inventory; kinds (metrics/containers/logs); query templates; server + Docker deep links | **Shipped** (v0.3.0) |
| Multi Pi-hole (v6) + NPM + managed certs | **Shipped** (v0.5.0 track) — [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) |
| HA / Frigate / n8n generic URL | Open |

**Uptime Kuma (shipped detail):**

- Auth: API key on `GET /metrics`; optional Kuma login for numeric `/dashboard/{id}` map (Kuma 1.23 often lacks `monitor_id` in metrics).
- Scopes: SSH per server; **host services** (e.g. HAOS); **Docker project/container**.
- UI: Integrations, server **Services**, fleet **`/services`** icon grid, dashboard Services tile, Docker chips.
- Logos: favicon discovery + upload under `DATA_ROOT/service_logos/`.

**Grafana (shipped detail — v0.3.0):**

- Auth: optional service account Bearer token; health without token; inventory requires token.
- Bindings `role=dashboard` with **kind** metrics | containers | logs; Docker scope optional.
- Templates: host metrics, container host, per-container, logs — all `var-` query strings with placeholders.
- UI: Integrations detail (Settings-style tabs; Inventory preferred names; bind Clone/Remove); server detail rows; Docker **Grafana** chip + ⋯ **Open in Grafana** + expanded-row links (mobile-friendly).
- DR: rows in herder self-backup; same `PIHERDER_MASTER_KEY` for token decrypt.

**Design:** [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · **Ops:** [ADMIN.md](ADMIN.md) § Uptime Kuma / Grafana

---

## Horizon 2 — Service deployment templates (v0.4)

Versioned **templates**: compose/install recipe + variables + post-deploy checklist/actions.

### Phase 1 — v0.4.0 (ship bar)

1. Pick a template (or blank / import)  
2. Configure variables (secrets generated or entered)  
3. Select host (Docker inventory counts)  
4. **Preview → confirm → audit**; store encrypted desired state V1  
5. Manual DNS checklist (no API automation yet)  
6. Optional 2FA gate for deploy / secret view  

**OOTB pack:** Nginx Proxy Manager, Uptime Kuma, Pi-hole, Grafana.  
**Plan:** [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [PLAN_v0.4.0.md](PLAN_v0.4.0.md)

### Phase 2 — v0.5.0 (ops + RC; single target)

**Plan:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md)

**Must-have / primary:**

- Scheduled **config drift** validation vs desired state; alert + audit — **done**  
- Migrate existing host `.env` into PiHerder encrypted store — **done**  
- Template UX polish (redeploy volume editor, from-host edge cases) — **done**  
- Restore service from backup **+ last known config** from PiHerder — **done**  
- Production user wiki + dev wiki (scaffold **live**; screenshots ongoing)  
- Docker Hub / GHCR multi-arch image  
- RC freeze bar (pytest, smoke, secret-path review)  
- **Audit client IP** on request-driven events (Caddy XFF) — **done**  
- Pi-hole multi + NPM RO + managed certs — **done** (workstream F)  

**Nice-to-have in same tag (partially landed):**

- **Git** template catalog pull (preview before enable)  
- ~~NPM connector~~ **done**  
- ~~Stack Docker Deploy/Check as Jobs + live log (B07)~~ **done**  
- B08 logos in self-backup · B09 push on auto-resolve — **done**  
- Template deploy as Jobs + live log (**done** in 0.6; preview/from-host may still use wait modal)  
- Contribute path remains Issues/PR for builtin inclusion  

**Secrets stance (home production):** templates use **locked-down host `.env` (`chmod 600`)** + PiHerder encrypted source of truth; restarts do not call PiHerder. Advanced options (Swarm secrets, vault, sealed host blob) stay **post-0.5 / Horizon 3** exploration — not the default path.

Curated pack beyond the four stacks (Frigate, HA, n8n, media…) and DNS provider automation remain **post-RC**.

---

## Quality & platform (post-RC / post-1.0 first production)

**Out of v0.5.0 freeze.** After the first RC tag and first official production release, raise confidence with automated depth — not a second feature stream during QA.

| Track | Direction |
|-------|-----------|
| **Unit / service coverage** | Grow beyond ~30% line coverage intentionally: critical paths first (crypto, RBAC, path policy, fabric pure functions, cert vault). **No** 100% target. Prefer meaningful service tests over chasing router %. |
| **HTTP smoke (pytest TestClient)** | **Should** — thin layer for auth redirects + main page 200s; runs in unit job. Target if not free in 0.7 freeze: **v0.8.0 RC3**. |
| **UI walkthrough — Playwright** | **Must for v0.7.0** — Phase A shell + Phase B wizard **product-done** (`e2e/`); **separate CI job** on main + PR; Chromium only; hard tag gate. Depth (B6, Phase C) → [PLAN_v0.8.0.md](PLAN_v0.8.0.md). |
| **Dependency hygiene** | **Done for RC path:** `uv.lock` + hashed `requirements*.lock.txt`; Dockerfile/CI install with `--require-hashes`. Ongoing: periodic `pip-audit` / Dependabot; intentional bumps via `scripts/refresh-lockfiles.sh`. |
| **JWT stack** | **Done (pre-0.5.0 tag):** sessions use **PyJWT[crypto]** HS256 — `python-jose` / `ecdsa` removed. |
| **Custom branding** | Operator logo + accent colours — **far horizon** (well after 1.0 production). Not near-term polish. Built-in light/dark only for now. |
| **Custom password policy** | Admin-configurable policy (min length, required classes, optional specials) instead of fixed code defaults. First-time setup still creates the initial admin when none exist. Soft max remains ~72 characters (storage limit). |

### Playwright phases

| Phase | Scope | Goal | Stance |
|-------|--------|------|--------|
| **A — Smoke** | Login → Dashboard, Servers, Catalog (Integrations / Certificates / Templates / Network maps), Jobs, Audit, Settings; one theme toggle | “Shell still works” on every PR/main | **Done** for 0.7 product (tag reconfirm) |
| **B — Critical paths** | **v0.7:** add-host wizard (primary CTA, identity/trust, save & exit, advanced form); later: template first step, cert list chrome, bulk bar, B6 viewer | Catch HTMX/form regressions on money paths | **Wizard slice done** in 0.7; expand in **0.8 RC3** |
| **C — Optional depth** | Screenshot baselines on 3–5 pages; a11y (`axe`) on main shells | Visual/a11y guardrails without full matrix | **Out of 0.7** → capacity in **0.8+** |

**0.7 defaults (landed):** Chromium only · compose set under project `piherder` · no live SSH in CI · failure traces as CI artifacts · wiki PNG pack remains **manual**.

Docs screenshots stay **light + desktop** by default; a couple of showcase shots for dark/mobile only.

---

## Horizon 2.5 — Service fabric & topology (post-0.5 / pre-1.0)

**Network maps / DNS fabric** (host A + service names, Pi-hole adopt, Hosts map + Path map, LAN/gateway/public IP, cloud hosts, optional Kuma on router/WAN, mobile list-first + fullscreen vs hamburger) lands in **v0.5.0**. Next topology depth:

**Living design:** [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) — dual altitude (customer path vs runtime stack), expand-one-stack, suggest + **manual** dependency edges, Kuma HTTP/TCP/Docker mapping.

| Item | Direction |
|------|-----------|
| **Service → container mapping** | **Done (H2):** Stack panel + map expand from path/project; Docker inventory + compose graph |
| **Container dependency graph** | **Done (H2):** suggest/accept/dismiss + manual `RuntimeEdge`; compose `depends_on` + heuristics |
| **Expand stack on map** | **Done (H2):** sideways fan (edge/app/queue/data); stack order drives column L→R; panel owns deep-links (no map chips) |
| **Stack container order** | **Done:** long-press/drag reorder; `stack_container_order_json`; e.g. celery last → queue column rightmost |
| **Published ports on maps** | Ports in stack expand/detail; broader Hosts/Path port chips may still grow |
| **Monitoring coverage audit** | **Done (H3):** `/dns/coverage` + hub teaser; optional inventory-down alerts for Kuma-bound containers |
| **Configurable columns / link-to-column** | **Later** (post-0.6 residual) — operator-defined map columns and explicit edge placement (runtime topology § 12b) |
| **LAN discovery (nmap-class)** | **Product complete (N0–N10)** — worker, devices, network modal, multi-schedule **edit**, vuln pack, **script presets**, kind heuristics + **override**, **map identity** (name + gateway role), **known/new** + MAC/DHCP, **Hosts map** dual compact/full + radar chrome + **1:1** compact fit, fleet soft embed, unit/E2E shells — **v0.8.0 RC3** remaining for stream N: **screenshots** ([FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · wiki [lan-discovery](../wiki/integrations/lan-discovery.md)); orthogonal to stack deps |
| **Discovery map icons / shapes** | **Future** — Hosts map + Network cards use **icons or node shapes by device kind** (Pi, printer, camera, router, IoT…) instead of text badges only; keep compact chips readable on mobile |
| **Discovery service / port labels** | **Future** — optional operator labels for individual open services (e.g. name a host’s admin UI port), not only host-level map name |
| **Richer topology** | Focused dep edges polish, force LAN/cloud overrides |
| **External DNS providers** | Cloudflare (etc.) automation; until then external checklist remains |
| **Service migrate / remove** | Move stack host↔host with DNS retarget; destructive remove with volume cleanup |

**Design principle:** one **entity graph** (name, NPM, host, project, container, volume, dep edge, monitor bind, discovered device) — views are projections, not separate data models.

**v0.6 track (closed):** H3 coverage + H2 runtime topology (panel, edges, expand, order) shipped — [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md). **v0.7:** annotations + compose sets. Residual column/layout polish later. **H1 nmap → v0.8.0 RC3.**

---

## Horizon 2.75 — Host lifecycle & operator console (post-RC)

**Captured:** 2026-07-17 (operator planning discussion).  
**Feature plan:** [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) (phases P1–P5, UX sketches, acceptance criteria, security bar).  
**Stance:** **P1 Docker bulk** shipped in **v0.6.0**. **P2 wizard product-done in v0.7.0** ([PLAN_v0.7.0.md](PLAN_v0.7.0.md)). **P3** capacity in **v0.8.0 RC3** ([PLAN_v0.8.0.md](PLAN_v0.8.0.md)); P4–P5 later / not required for minimal **1.0.0**.

These ideas deepen **day-to-day host operations** and **first-time host bring-up**. They sit next to H2.5 (topology) but focus on **SSH lifecycle** rather than DNS/proxy graphs.

### Themes (operator intent)

| Theme | Intent |
|-------|--------|
| **Docker project bulk control** | From a host Docker / stack UI: **start / stop / restart all services** in a project (and later multi-project bulk) without one-by-one container actions |
| **Richer host health** | More **host stats**, **healthchecks**, and **safe remote commands** from the UI (beyond today’s short system-info snapshot) |
| **In-browser SSH console** | Web terminal with **key-based credential injection** so operators never paste private keys into a laptop SSH client |
| **Wizard onboarding** | Guided multi-step “add host” instead of form → SSH access panel discovery |
| **Bootstrap / first boot** | Scripts + settings to create the **PiHerder user**, sudoers/ACL, hostname (and maybe IP), report DHCP-assigned address on first boot, create **Pi-hole / DNS** records |

### Recommended order (risk vs value)

| # | Item | Effort | Risk | Why this order |
|---|------|--------|------|----------------|
| **1** | **Docker bulk start/stop/restart** (project-level, then optional multi-select) | Low–med | Low | Extends existing Docker actions + Jobs/Audit pattern; high weekly value; no new trust model |
| **2** | **Improved add-host wizard** (UI orchestration of today’s steps) | Med | Low | Same SSH/key/least-priv building blocks; fewer wrong-order mistakes for new operators |
| **3** | **Host stats + healthcheck + allowlisted commands** | Med | Med | Needs clear **command allowlist**, RBAC, audit, rate limits — not a free shell |
| **4** | **Bootstrap scripts + DNS handoff** (hostname, PiHerder user, Pi-hole A) | Med–high | Med | Builds on least-priv + Network/Pi-hole; “first boot report DHCP IP” needs a callback design |
| **5** | **Web SSH client (key injection)** | High | **High** | Browser ↔ PTY bridge is a privileged attack surface; ship only with hard security bar (below) |

### 1 — Docker bulk service control

| Aspect | Direction |
|--------|-----------|
| **Scope (v1)** | Per **compose project**: Stop all / Start all / Restart all (map to `docker compose stop|start|restart` or service-level batch) |
| **Scope (later)** | Multi-project multi-select on host Docker page; optional fleet-wide “restart all *X*” only with strong confirm |
| **UX** | Confirm modal (list services); run as **Job** with live log; exclusive with other stack mutations on that host if needed |
| **Not** | Unconditional `docker kill` across the whole host without project context |

### 2 — Host stats, healthcheck, run command

| Aspect | Direction |
|--------|-----------|
| **Stats** | Extend host status snapshot: load, memory, disk, uptime, temperature (where available), reboot-pending — **cached**, not continuous SSH poll on every page open |
| **Healthcheck** | Operator-defined or built-in probes (e.g. “can resolve DNS”, “docker ps exits 0”) stored per host; schedule optional |
| **Run command** | **Allowlist only** (or template commands with args), operator+ role, preview → confirm → audit + job log. **No** arbitrary root shell from the UI in the first cut |
| **Why not free shell here** | Free shell belongs in the web SSH item (if ever); stats/commands stay structured so viewers never get a prompt |

### 3 — Web-based SSH client (credential injection)

| Aspect | Direction |
|--------|-----------|
| **UX** | xterm.js (or similar) in the browser → authenticated WebSocket → PiHerder server opens SSH with the **stored host key** (never send the private key to the browser) |
| **Auth model** | Session must be **operator/admin**; prefer **step-up 2FA** (or force-2FA policy); optional “console session” time limit; idle disconnect |
| **Injection** | Server-side only: decrypt key in worker/web process memory for that PTY session; browser never sees PEM |
| **Audit** | `ssh_console_open` / `ssh_console_close` (+ client IP, duration); optional command logging is **hard** (interactive) — document limitations |
| **Threats** | XSS → terminal takeover; shared admin sessions; browser extensions; long-lived websockets; herder host becomes jump box |
| **Mitigations (ship bar)** | CSP + trusted TLS; short-lived console tickets; concurrent session limit; no console for **viewer**; kill switch env `PIHERDER_SSH_CONSOLE=false`; never log key material |
| **Stance** | **Under consideration** — attractive for tablets/homelab, but **not** a 1.0 requirement. Prefer landing 1–4 first. |

### 4 — Wizard-driven host onboarding

Turn today’s multi-surface flow into one guided path:

1. Identity (name, address/port, SSH user)  
2. Trust (generate/upload key, optional bootstrap password)  
3. Connect & deploy key  
4. Least-priv / Docker base dir / ACL (Debian/Pi OS path)  
5. Feature flags (Backups / OS / Docker)  
6. Optional schedules (checks only by default)  
7. Optional Network (FQDN, manage A on Pi-holes)  
8. Summary + “first jobs” CTAs (test backup, update check)

Reuse existing SSH access actions; the wizard is **orchestration + progress**, not a second implementation.

### 5 — Bootstrap / first boot (scripts + DNS)

| Layer | Direction |
|-------|-----------|
| **A — Offline scripts (near-term)** | Downloadable / copyable scripts from the wizard: create `piherder` user, authorized_keys, sudoers, docker group, optional hostname set — same spirit as today’s least-priv + cleanup scripts, but **pre-join** |
| **B — Network identity** | Operator enters desired **hostname** + optional static IP notes; PiHerder can apply hostname over SSH after first connect; static IP remains **document + script**, not full network-manager product |
| **C — DNS** | After connect (or after operator confirms IP): create/update **Pi-hole A** via existing fan-out when “Manage A” is on — already close to today’s Host DNS |
| **D — First-boot callback (later)** | Image/cloud-init that phones home to PiHerder (“I booted; DHCP gave me *x.x.x.x*”) — needs enrollment token, one-time code, or mTLS; **do not** accept anonymous host registration on the open internet |
| **E — Full imaging** | cloud-init / Raspberry Pi Imager integration, inventory export for Ansible — stays **Horizon 3** alignment |

**Open design questions (resolve before building D/E):**

- Enrollment secret: per-host one-time token vs shared fleet join key?  
- Who owns DHCP (router only vs PiHerder-suggested static)?  
- Does first boot create the server row, or only report IP for a pre-created row?  
- How does this interact with HAOS / non-Debian (script matrix)?

### Explicit non-goals (for this horizon)

- Replacing Uptime Kuma for continuous monitoring  
- Full remote desktop / VNC  
- Agent-based management (PiHerder remains **SSH-first**)  
- Shipping web SSH without step-up + audit + kill switch  

### Links to existing code / docs

| Today | Leverages |
|-------|-----------|
| Server bulk OS/container/backup | Pattern for Docker bulk confirm + Jobs |
| Host status system-info snapshot | Seed for richer stats |
| Least-priv + cleanup scripts | Seed for bootstrap scripts |
| Network maps + Pi-hole A fan-out | DNS after onboard |
| H3 Ansible / cloud-init | First-boot callback / imaging |

---

## Horizon 3 — Deeper ecosystem

| Area | Direction |
|------|-----------|
| Home Assistant | Token REST first → optional custom component (sensors + safe actions); MQTT later |
| Plugin hooks | Prefer REST + n8n over arbitrary code on the herder host |
| Ansible / cloud-init | Inventory export + first-boot snippets for new Pis — **overlaps H2.75 bootstrap D/E**; keep imaging depth here |
| **Advanced secrets** | Explore beyond locked `.env`: Swarm/file permissions hardening, sealed host store for offline recreate, optional vault — never require PiHerder for normal container restart |
| Optional AI | OpenAI-compatible BYO (cloud or private LLM); **off by default**; never send private keys; Frigate vision stays on Frigate / AI Hat |
| **Topology plugins** | Optional export to graph tools (e.g. Mermaid, Graphviz DOT, or browser libraries like Cytoscape.js / vis-network) for large fleets — keep core views offline-first CSS/SVG |
| **Custom theme / branding** | Operator logo + primary colours (instance skin). **Not** in v0.5 / v1.0 first production — revisit only after quality + fabric depth |

---

## Community & awareness (parallel)

| Channel | Role |
|---------|------|
| GitHub Issues | Bugs, features, template proposals |
| GitHub Discussions | Q&A, show-and-tell |
| Discord | Real-time help (link from README when live) |
| SECURITY.md | Vulnerability reporting |
| Project website | Story, clickthrough, professional context (when published) |

---

## Architecture (target)

```mermaid
flowchart TB
  subgraph Compose["Docker Compose (supported)"]
    UI[Web UI]
    Core[Fleet core]
    Workers[Celery worker(s)]
    DB[(PostgreSQL)]
    Redis[(Redis)]
    Caddy[Caddy TLS]
  end

  subgraph Future["Future topologies — under consideration only"]
    K8s[Kubernetes]
    Bare[Local / bare install]
  end

  subgraph Product["Product layers (H1+)"]
    Reg[Integration registry]
    Tpl[Template engine]
    API[Token REST API]
    AIOpt[Optional AI]
  end

  Fleet[Linux / Pi hosts]
  UK[Uptime Kuma]
  GF[Grafana]
  NPM[Nginx Proxy Manager]
  HA[Home Assistant]
  N8N[n8n]
  LLM[OpenAI-compatible LLM]

  Caddy --> UI
  UI --> Core
  Core --> DB
  Core --> Redis
  Workers --> DB
  Workers --> Redis
  Workers -->|SSH + rsync/docker/apt| Fleet
  Core -->|SSH onboard + dep check| Fleet
  UI --> Reg
  UI --> Tpl
  Reg -->|status| UK
  Reg -->|deep links| GF
  Tpl -->|optional provision| UK
  Tpl -->|optional provision| NPM
  API --> HA
  API --> N8N
  AIOpt -.->|optional| LLM
  Compose -.->|not committed| Future
```

---

## Decisions (locked unless reversed)

| Topic | Choice |
|-------|--------|
| Core vs integration | Integrations optional |
| Provisioning | Preview → confirm → audit |
| Automation glue | Prefer n8n + REST over embedding every vendor |
| AI | BYO OpenAI-compatible; off by default |
| Vision / Frigate LLM | Link only; not core PiHerder |
| Templates | DB metadata + files under `DATA_ROOT` |
| Multi-tenant orgs | Deferred |
| **Deployment** | **Docker Compose is the supported architecture** |
| **Kubernetes / bare install** | **Under consideration only** — not promised in H0–H2 |
| **Multi-worker** | Done — per-server Redis mutex + `CELERY_CONCURRENCY`; not a v0.2.0 ship blocker |
| **Host dep / stack health** | Done (v0.2.x): host deps → Status tab → multi-worker |
| **Host lifecycle H2.75** | P1 done (0.6); **P2 wizard product-done (0.7)** — [PLAN_v0.7.0.md](PLAN_v0.7.0.md); **P3 capacity in RC3** — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md); web SSH (P5) last |
| **Web SSH console** | Server-side key only; step-up 2FA; kill switch; not a 1.0 requirement |
| **First-boot join** | No anonymous host registration; enrollment token required if built |

---

## Success criteria

An operator can:

1. Install PiHerder from a published image in under ~15 minutes with trusted TLS (Compose) — **done at v0.5.0** (`bjorngluck/piherder:latest`).  
2. See PiHerder **stack** health (web/db/redis/worker) and fleet host readiness (remote tools for enabled features) — **done** (H0.5).  
3. See fleet health and jump to Grafana / Uptime Kuma for detail — **done** (H1).  
4. Onboard a service from a template with monitoring, DNS, and TLS/proxy steps — foundation **done** (v0.4.0); polish + restore **v0.5.0**.  
5. Automate via n8n + token API (and later HA) — post-RC / parallel.  
6. Optionally use a private LLM for summaries — never required; post-RC.  
7. Find help on Discord / GitHub and the project website / story page — parallel.
