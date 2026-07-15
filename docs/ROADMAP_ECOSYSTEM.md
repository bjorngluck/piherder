# PiHerder ecosystem roadmap

**Status:** Active  
**Date:** 2026-07-12  
**Related:** [SPEC.md](../SPEC.md) · [ADMIN.md](ADMIN.md) · [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md)

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
| **v0.5.0** | **First RC** — ops depth + template polish + restore + DNS fabric + Pi-hole/NPM/certs + production wikis + multi-arch + freeze bar | RC | **QA / release prep** — [PLAN_v0.5.0.md](PLAN_v0.5.0.md) |
| **v1.0** | Stable template schema + REST + docs + community process | H0–H2 freeze | Planned |

**Decision:** All fixes after `v0.3.0` shipped in **`v0.4.0`** (no intermediate `v0.3.1`). Historical bug list: [PLAN_v0.4.0.md](PLAN_v0.4.0.md) §2.

**Decision (2026-07-12):** **Single development target `v0.5.0`** — former “v0.4.x ops” and “RC polish” are one cycle. Optional intermediate git tags only if something must ship early.

**Production path:** ~~v0.4.0 templates~~ **done** → **v0.5.0 in development** (ops + polish + RC) → v1.0.

**Note:** Registry image publish (`bjorngluck/piherder`) remains optional until Docker Hub/GHCR credentials are available; target Hub publish with **v0.5.0 RC**.

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
- Template wizard as Jobs + live log (still wait-modal)  
- Contribute path remains Issues/PR for builtin inclusion  

**Secrets stance (home production):** templates use **locked-down host `.env` (`chmod 600`)** + PiHerder encrypted source of truth; restarts do not call PiHerder. Advanced options (Swarm secrets, vault, sealed host blob) stay **post-0.5 / Horizon 3** exploration — not the default path.

Curated pack beyond the four stacks (Frigate, HA, n8n, media…) and DNS provider automation remain **post-RC**.

---

## Quality & platform (post-RC / post-1.0 first production)

**Out of v0.5.0 freeze.** After the first RC tag and first official production release, raise confidence with automated depth — not a second feature stream during QA.

| Track | Direction |
|-------|-----------|
| **Unit / service coverage** | Grow beyond ~30% line coverage intentionally: critical paths first (crypto, RBAC, path policy, fabric pure functions, cert vault). **No** 100% target. Prefer meaningful service tests over chasing router %. |
| **HTTP smoke (pytest TestClient)** | Optional thin layer for auth redirects + main page 200s — cheap middle ground before full browser tests. |
| **UI walkthrough — Playwright** | See phases below. Separate job from unit `pytest` (slower; nightly or main-only at first). |
| **Dependency hygiene** | **Done for RC path:** `uv.lock` + hashed `requirements*.lock.txt`; Dockerfile/CI install with `--require-hashes`. Ongoing: periodic `pip-audit` / Dependabot; intentional bumps via `scripts/refresh-lockfiles.sh`. |
| **JWT stack** | **Done (pre-0.5.0 tag):** sessions use **PyJWT[crypto]** HS256 — `python-jose` / `ecdsa` removed. |
| **Custom branding** | Operator logo + accent colours — **far horizon** (well after 1.0 production). Not near-term polish. Built-in light/dark only for now. |

### Playwright phases (recommended)

| Phase | Scope | Goal |
|-------|--------|------|
| **A — Smoke** | Login → Dashboard, Servers, Catalog (Integrations / Certificates / Templates / Network maps), Jobs, Audit, Settings; one theme toggle; optional mobile viewport on Network map | “Shell still works” on every release candidate |
| **B — Critical paths** | Template wizard first step; Pi-hole/NPM detail with fixtures; cert list/upload form; bulk-actions UI chrome | Catch HTMX/form regressions on money paths |
| **C — Optional depth** | Screenshot baselines on 3–5 pages; a11y (`axe`) on main shells | Visual/a11y guardrails without full matrix |

Docs screenshots stay **light + desktop** by default; a couple of showcase shots for dark/mobile only.

---

## Horizon 2.5 — Service fabric & topology (post-0.5 / pre-1.0)

**Network maps / DNS fabric** (host A + service names, Pi-hole adopt, Hosts map + Path map, LAN/gateway/public IP, cloud hosts, optional Kuma on router/WAN) lands in **v0.5.0**. Next topology depth:

| Item | Direction |
|------|-----------|
| **Service → container mapping** | First-class link from a published name / deployment to the **compose service + container** (beyond Kuma/NPM inference) |
| **Container dependency graph** | Model runtime deps between containers (e.g. app → **Postgres**, **Redis**, queue workers) — discover from compose `depends_on` / labels + optional operator edges |
| **Richer topology** | Enrich Hosts/Path maps with dep edges, more Kuma health, operator force LAN/cloud overrides |
| **External DNS providers** | Cloudflare (etc.) automation; until then external checklist remains |
| **Service migrate / remove** | Move stack host↔host with DNS retarget; destructive remove with volume cleanup |

**Design principle:** one **entity graph** (name, NPM, host, project, container, volume, dep) — views are projections, not separate data models.

---

## Horizon 3 — Deeper ecosystem

| Area | Direction |
|------|-----------|
| Home Assistant | Token REST first → optional custom component (sensors + safe actions); MQTT later |
| Plugin hooks | Prefer REST + n8n over arbitrary code on the herder host |
| Ansible / cloud-init | Inventory export + first-boot snippets for new Pis |
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
| hacknow.info | Project story, clickthrough, professional context |

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

---

## Success criteria

An operator can:

1. Install PiHerder from a published image in under ~15 minutes with trusted TLS (Compose) — **target at v0.5.0 RC** (credentials permitting; compose-build remains primary until then).  
2. See PiHerder **stack** health (web/db/redis/worker) and fleet host readiness (remote tools for enabled features) — **done** (H0.5).  
3. See fleet health and jump to Grafana / Uptime Kuma for detail — **done** (H1).  
4. Onboard a service from a template with monitoring, DNS, and TLS/proxy steps — foundation **done** (v0.4.0); polish + restore **v0.5.0**.  
5. Automate via n8n + token API (and later HA) — post-RC / parallel.  
6. Optionally use a private LLM for summaries — never required; post-RC.  
7. Find help on Discord / GitHub and the project story on hacknow.info — parallel.
