# PiHerder documentation

<figure class="ph-hero-logo" markdown>
  ![PiHerder logo](assets/piherder-logo.png){ width="280" }
</figure>

**Secure fleet management for Raspberry Pi and Linux hosts** — backups, patching, Docker control, and service templates with secrets encrypted at rest.

| | |
|---|---|
| **Latest release** | [v0.4.0](https://github.com/bjorngluck/piherder/releases/tag/v0.4.0) |
| **In development** | [v0.5.0 plan](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.5.0.md) |
| **Source** | [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder) |
| **Docs (this site)** | [bjorngluck.github.io/piherder](https://bjorngluck.github.io/piherder/) |
| **License** | [PolyForm Noncommercial 1.0.0](https://github.com/bjorngluck/piherder/blob/main/LICENSE) (non-commercial source-available) |

---

## Start here

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Install in ~15 minutes**

    ---

    Docker Compose, master key, first admin user.

    [:octicons-arrow-right-24: Install guide](getting-started/install.md)

-   :material-server:{ .lg .middle } **Add your first Pi**

    ---

    SSH key deploy, least-priv user, feature flags.

    [:octicons-arrow-right-24: Add a server](day-to-day/add-server.md)

-   :material-package-variant:{ .lg .middle } **Deploy a service template**

    ---

    NPM, Uptime Kuma, Pi-hole, Grafana — wizard + secrets.

    [:octicons-arrow-right-24: Templates](service-templates/overview.md)

-   :material-lifebuoy:{ .lg .middle } **Something broken?**

    ---

    SSH, rsync, backups, push, template stacks.

    [:octicons-arrow-right-24: Troubleshooting](troubleshooting/index.md)

</div>

---

## What PiHerder does

```mermaid
flowchart LR
  You[Browser / PWA] --> Caddy[Caddy TLS]
  Caddy --> Web[FastAPI web]
  Web --> DB[(PostgreSQL)]
  Web --> Redis[(Redis)]
  Celery[Celery workers] --> DB
  Celery --> Redis
  Celery -->|SSH · rsync · docker · apt| Fleet[Pi / Linux fleet]
  Web -.->|deep links| Kuma[Uptime Kuma]
  Web -.->|deep links| GF[Grafana]
```

- **Fleet ops:** rsync backups, apt OS patch, Docker compose projects  
- **Safety:** Fernet-encrypted SSH keys, audit trail, RBAC, optional 2FA + Web Push  
- **Templates:** versioned stacks with variables, desired state, step-up secrets  
- **Integrations (optional):** Uptime Kuma + Grafana bindings  

Core fleet work **never** requires Kuma, Grafana, or templates.

---

## Documentation map

| Section | For |
|---------|-----|
| [Getting started](getting-started/index.md) | First install & HTTPS |
| [Day to day](day-to-day/add-server.md) | Servers, backups, updates, jobs |
| [Docker](docker/overview.md) | Host containers & compose |
| [Templates](service-templates/overview.md) | Catalog → Templates button (deploy / from-host / secrets) |
| [Integrations](integrations/overview.md) | Catalog default (`/catalog` → Integrations; Kuma & Grafana) |
| [Account & security](account-security/roles.md) | RBAC, users, 2FA, PWA |
| [Operations](operations/env-reference.md) | Env, DR, metrics, API |
| [Developers](developers/index.md) | Code, tests, schema, publish |

Maintainer roadmaps and release plans stay in the **repo** under [`docs/`](https://github.com/bjorngluck/piherder/tree/main/docs) — not in this user-facing tree.

---

## Screenshots

<figure class="ph-figure" markdown>
  ![Dashboard wireframe](assets/screenshots/dashboard.svg)
  <figcaption>Dashboard — fleet summary and attention table. <span class="ph-wireframe-badge">wireframe</span> Replace with a real capture when ready (see <code>wiki/assets/screenshots/README.md</code>).</figcaption>
</figure>

---

## Quick links

- Interactive API (on your instance): **`/docs`** (OpenAPI, tag `api-v1`)  
- Security policy for the project: [SECURITY.md](https://github.com/bjorngluck/piherder/blob/main/SECURITY.md)  
- Report issues: [GitHub Issues](https://github.com/bjorngluck/piherder/issues)  
