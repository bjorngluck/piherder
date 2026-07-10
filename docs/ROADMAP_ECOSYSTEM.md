# PiHerder ecosystem roadmap

**Status:** Active  
**Date:** 2026-07-10  
**Related:** [SPEC.md](../SPEC.md) · [ADMIN.md](ADMIN.md)

This document is the public multi-horizon roadmap for taking PiHerder from a production-ready **fleet manager** to the hub of a self-hosted **homelab / security ops** ecosystem (DNS, proxy, monitoring, smart home, media, automation).

Design principles stay the same as SPEC:

- Auditable privileged actions  
- Secrets encrypted at rest; decrypt only in memory for jobs  
- Offline / air-gapped ready once built  
- Dangerous or external actions are **opt-in** (preview → confirm → audit)  
- Integrations are **optional** — core fleet ops work alone  

---

## Release track

| Version | Theme | Horizon |
|---------|--------|---------|
| **v0.2** | Production install story (image, compose, token REST, prod docs) | H0 |
| **v0.2.x** | Platform reliability (host deps, stack Status tab, multi-worker design) | H0.5 |
| **v0.3** | Integration hub (links, Uptime Kuma status, Grafana deep links) | H1 |
| **v0.4** | Service templates + onboard wizards (monitor / DNS / TLS) | H2 |
| **v1.0** | Stable template schema + REST + docs + community process | H0–H2 freeze |

---

## Horizon 0 — Production readiness (v0.2)

| Item | Notes |
|------|--------|
| Docker Hub (or GHCR) image | Documented pull path; tags `0.2.x` + `latest` when published |
| Clean compose example | Relative volumes; no `~/` bind-mount assumptions |
| Token REST API | Admin-managed Bearer tokens: `read`/`jobs`/`edit` + feature scopes + IP allowlist; [API.md](API.md) |
| Production ADMIN guide | TLS, upgrades, metrics scrape, webhooks → Signal |
| Community scaffolding | SECURITY.md, Discussions/Discord pointers in README |

**Out of scope for H0:** templates, Uptime Kuma/Grafana product UI, HA plugin, AI.  
**Ship bar:** published image + compose + docs. Platform reliability (below) can ship as **v0.2.x** patches and is not required to tag `0.2.0`.

---

## Horizon 0.5 — Platform reliability & scale (v0.2.x)

Ops hardening that sits **between** production install and product integrations. Implementation order:

| # | Item | Stance | Notes |
|---|------|--------|-------|
| **1** | **Remote host dependency check** | Next implement | After SSH / least-priv onboard, probe tools needed for **enabled** features (`rsync`, `sudo -n rsync` or plain rsync, `docker`, `apt`). UI chips + “Re-check”; store last snapshot. **No auto-install** — report + install hints only. |
| **2** | **Settings → Status tab** | Next implement (after #1) | Admin view of PiHerder stack: web, PostgreSQL, Redis, Celery worker(s), APScheduler, disk free on backup/data volumes. Manual refresh + **scheduled** poll; alert on healthy→unhealthy **state change** (in-app + existing webhook/push); resolve when healthy again. Optional `/metrics` gauges. |
| **3** | **Multi-worker** | Design then implement (after #1–2) | Today: single Celery worker, `concurrency=1`. Goal: parallel jobs **across** hosts with a **per-server mutex** (one active backup/patch per server). Shared volumes for `/backups` required; cancel via `Job.celery_task_id` must keep working. **Not** a v0.2.0 ship blocker. |
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
| Celery | `inspect().ping()` / worker count ≥ 1 |
| APScheduler | Running + jobs registered |
| Disk | Free space on `/backups`, `/data`, `/herder_backups` |

Notifications must **not** spam: only on state transition (plus optional cooldown).

### Multi-worker (design constraints)

- Prefer documenting scale of `celery-worker` (replicas or concurrency) after mutex design.  
- Only one active backup (and patch apply) **per server** under N workers.  
- Stale-job recovery on worker death remains required.  
- Prefer shipping Status tab first so operators can see worker count.

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

Read-mostly integrations: config + status + deep links.

- **Integration registry** — types such as Uptime Kuma, Grafana, Pi-hole (multi), NPM, Home Assistant, Frigate, n8n, generic URL  
- **Uptime Kuma** — poll availability; badges; “Open in Kuma”; optional down notifications  
- **Grafana** — URL templates (`{{host}}`); “Open in Grafana”; native high-level chips from existing fleet data  
- **Pi-hole** — generalize beyond single `PIHOLE_URL`  
- **NPM / Cloudflare / n8n** — admin URLs + docs (certs: NPM → n8n → consumers); no full zone control yet  

See planned design: `docs/FEATURE_PLAN_INTEGRATIONS.md` (when implemented).

---

## Horizon 2 — Service deployment templates (v0.4)

Versioned **templates**: compose/install recipe + variables + post-deploy checklist/actions.

On **add server** or **new Docker project**, offer:

1. Pick a template (or blank)  
2. Optional steps: monitoring (Kuma), DNS (Cloudflare or checklist), TLS/proxy (NPM), PiHerder feature flags  
3. Every automated step: **preview → confirm → audit**  

Curated pack targets a typical ecosystem: Pi-hole, Uptime Kuma, Grafana, Frigate, Home Assistant, NPM, n8n, media stack, generic web app.

Operators can **create / import / export** templates (manual import only — no remote unsigned marketplace at first).

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
| **Multi-worker** | Allowed after per-server job mutex + Status visibility; not a v0.2.0 ship blocker |
| **Host dep / stack health** | Planned v0.2.x (implement order: host deps → Status tab → multi-worker) |

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
