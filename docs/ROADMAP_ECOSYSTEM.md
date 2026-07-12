# PiHerder ecosystem roadmap

**Status:** Active  
**Date:** 2026-07-11  
**Related:** [SPEC.md](../SPEC.md) · [ADMIN.md](ADMIN.md) · [RELEASE_v0.3.0.md](RELEASE_v0.3.0.md)

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
| **v0.4.0** | Post-0.3 quality (Docker/jobs/alerts) + templates foundation | H2 + fixes | **In progress** — [PLAN_v0.4.0.md](PLAN_v0.4.0.md) · WIP notes [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) |
| **v0.4.x** | Remaining H1 multi-URL adapters / template pack expansion | H1/H2 | Optional after 0.4.0 |
| **v1.0** | Stable template schema + REST + docs + community process | H0–H2 freeze | Planned |

**Decision:** All fixes after `v0.3.0` ship in **`v0.4.0`** (no intermediate `v0.3.1`). Living bug list for release notes: PLAN §2.

**Note:** Registry image publish (`bjorngluck/piherder:0.3.0`) remains optional until Docker Hub/GHCR credentials are available; the git tag is the source of truth for this release.

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
| **1** | **Remote host dependency check** | **Done** (v0.2.x) | Server detail + SSH access: probe tools for **enabled** features (`rsync`, sudo/plain rsync, `docker`, `apt`). Snapshot on server; auto-refresh after SSH test / key deploy / least-priv. Hints only — no auto-install. |
| **2** | **Settings → Status tab** | **Done** (v0.2.x) | Admin **Status** tab: web, PostgreSQL, Redis, Celery (nodes + pool slots), APScheduler, **mount free** (fast). Backup tree `du` / per-host folders **on demand** (View details). Manual + every 2 min poll; alerts on state change; `/metrics` from last check. |
| **3** | **Multi-worker** | **Done** (v0.2.x) | Celery `CELERY_CONCURRENCY` (default **2** = pool slots in one node); Redis **per-server backup mutex**; parallel across hosts. Prefer raising concurrency over multiple nodes unless HA/scale-out. Shared `/backups`; cancel + lock TTL intact. |
| **4** | **Deploy topologies** | Docs only | See [Deployment architecture](#deployment-architecture) — Compose supported; k8s / bare install **under consideration only**. |

### Remote dependency check (detail)

A host can accept an SSH key while missing tools required for backups or Docker. Probe after key deploy / least-priv (and via “Re-check dependencies” on server detail). Severity depends on **enabled** features:

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
| Integration registry + top-level **Integrations** nav | **Shipped** |
| **Uptime Kuma** — API key + `/metrics`; SSH + host service + Docker bindings; TLS; deep links; notifications; Services pages; logos | **Shipped** |
| **Grafana** — service account token; health; inventory; kinds (metrics/containers/logs); query templates; server + Docker deep links | **Shipped** (v0.3.0) |
| Multi Pi-hole / NPM / HA / Frigate / n8n generic URL | Open (v0.3.x) |

**Uptime Kuma (shipped detail):**

- Auth: API key on `GET /metrics`; optional Kuma login for numeric `/dashboard/{id}` map (Kuma 1.23 often lacks `monitor_id` in metrics).
- Scopes: SSH per server; **host services** (e.g. HAOS); **Docker project/container**.
- UI: Integrations, server **Services**, fleet **`/services`** icon grid, dashboard Services tile, Docker chips.
- Logos: favicon discovery + upload under `DATA_ROOT/service_logos/`.

**Grafana (shipped detail — v0.3.0):**

- Auth: optional service account Bearer token; health without token; inventory requires token.
- Bindings `role=dashboard` with **kind** metrics | containers | logs; Docker scope optional.
- Templates: host metrics, container host, per-container, logs — all `var-` query strings with placeholders.
- UI: Integrations detail (tabbed bind form, clone/edit); server detail rows; Docker **Grafana** chip + ⋯ **Open in Grafana** + expanded-row links (mobile-friendly).
- DR: rows in herder self-backup; same `PIHERDER_MASTER_KEY` for token decrypt.

**Design:** [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · **Ops:** [ADMIN.md](ADMIN.md) § Uptime Kuma / Grafana

---

## Horizon 2 — Service deployment templates (v0.4)

Versioned **templates**: compose/install recipe + variables + post-deploy checklist/actions.

On **add server** or **new Docker project**, offer:

1. Pick a template (or blank)  
2. Optional steps: monitoring (Kuma), DNS (Cloudflare or checklist), TLS/proxy (NPM), PiHerder feature flags  
3. Every automated step: **preview → confirm → audit**  

Curated pack targets a typical ecosystem: Pi-hole, Uptime Kuma, Grafana, Frigate, Home Assistant, NPM, n8n, media stack, generic web app.

Operators can **create / import / export** templates (manual import only — no remote unsigned marketplace at first).

**Active planning:** ship bar, post-0.3 bugfixes, and slice choices are tracked in **[PLAN_v0.4.0.md](PLAN_v0.4.0.md)** (templates v1 = schema + apply + samples; full pack and provider auto-create are stretch / later).

---

## Horizon 3 — Deeper ecosystem

| Area | Direction |
|------|-----------|
| Home Assistant | Token REST first → optional custom component (sensors + safe actions); MQTT later |
| Plugin hooks | Prefer REST + n8n over arbitrary code on the herder host |
| Ansible / cloud-init | Inventory export + first-boot snippets for new Pis |
| Optional AI | OpenAI-compatible BYO (cloud or private LLM); **off by default**; never send private keys; Frigate vision stays on Frigate / AI Hat |

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

1. Install PiHerder from a published image in under ~15 minutes with trusted TLS (Compose).  
2. See PiHerder **stack** health (web/db/redis/worker) and fleet host readiness (remote tools for enabled features).  
3. See fleet health and jump to Grafana / Uptime Kuma for detail.  
4. Onboard a service from a template with monitoring, DNS, and TLS/proxy steps.  
5. Automate via n8n + token API (and later HA).  
6. Optionally use a private LLM for summaries — never required.  
7. Find help on Discord / GitHub and the project story on hacknow.info.  

Criteria 3–7 are H1+; criterion 2 is **H0.5 / v0.2.x**.
