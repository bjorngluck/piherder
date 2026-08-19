# Getting started

This section takes you from **zero** to a **running PiHerder** with a first admin account and a clear idea of what to do next.

!!! tip "Prefer a click-through first?"
    The **[public demo](../operations/demo-site.md)** is a limited **view-only** sandbox ([piherder-demo.hacknow.info](https://piherder-demo.hacknow.info)) with a shared viewer login. Credentials and limits are on that page — password may rotate; the live wiki always has the current one.

## What you are setting up

| Piece | Role |
|-------|------|
| **PiHerder stack** | Web UI + API, Postgres, Redis, Celery workers, Caddy TLS — on **one** Linux host |
| **Fleet hosts** | Pis / Linux boxes you manage later over SSH |
| **You (first admin)** | First registered account owns the instance; then you invite others |

You do **not** need Catalog integrations, templates, or Web Push on day one. Those are optional after the fleet basics work.

## Path (recommended order)

| Step | Doc | Why this step exists |
|------|-----|----------------------|
| 1 | [Requirements](requirements.md) | Avoid install surprises (disk, ports, remote tools) |
| 2 | [Install (Docker Compose)](install.md) | Supported way to run the stack and secrets |
| 3 | [First login](first-login.md) | Create the only self-serve admin; lock registration |
| 4 | [Trusted HTTPS & TLS](https-tls.md) | Needed for reliable mobile PWA / Web Push |
| 5 | [Appearance](appearance.md) | Light/dark (optional comfort) |
| 6 | [Operator scenarios](operator-scenarios.md) | Map goals → docs for everything after install |

Then: [Add a server](../day-to-day/add-server.md) (guided wizard) → [Dashboard](../day-to-day/dashboard-and-services.md) → [Reports](../day-to-day/reports.md) once Jobs exist.  
HAOS appliance: [HAOS hosts](../day-to-day/haos-hosts.md). Optional [Host Files](../day-to-day/host-files.md) (`PIHERDER_HOST_FILES`). Templates / from-host: [Service templates](../service-templates/overview.md).

## Supported install path

!!! success "Docker Compose only"
    The **supported** topology is the compose stack in this repository (`web`, `db`, `redis`, `celery-worker`, `caddy`).

    Kubernetes and bare-metal installs are **under consideration only** — not documented as supported paths.

## Time estimate

| Step | Typical |
|------|---------|
| Clone + generate secrets | 5 min |
| First `compose up --build` | 5–15 min (image build) |
| Register + open UI | 2 min |
| Trusted certs (if not ready) | depends on your CA / ACME |
| First server + key deploy | 10–20 min |

## First successful week (end-to-end sketch)

A realistic production onboarding path:

1. **Install + first admin** — this section.  
2. **Add one non-critical Pi** — [Add a server](../day-to-day/add-server.md) wizard (Identity → Trust → Connect: install key → test → clear password → Features).  
3. **Run one manual backup** — [Backups](../day-to-day/backups.md) (confirm Celery + rsync path before schedules).  
4. **Run one OS update check** — [Updates](../day-to-day/updates-and-patching.md) (check before any apply schedule; HAOS uses `ha` CLI).  
5. **Open Jobs + Audit** — [Jobs, audit & notifications](../day-to-day/jobs-audit-notifications.md) so you trust the trail.  
6. **Optional:** Kuma / [templates](../service-templates/overview.md) / network / HAOS — only after the host path feels solid.

Detailed “I want to…” tables and longer journeys: [Operator scenarios](operator-scenarios.md).

## Related repo docs

| Doc | Role |
|-----|------|
| [README](https://github.com/bjorngluck/piherder/blob/main/README.md) | Project overview |
| [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example) | Full env catalog |
| [docs/ADMIN.md](https://github.com/bjorngluck/piherder/blob/main/docs/ADMIN.md) | Long-form admin reference (mirrored into this wiki) |
| [v1.2.0 QA](../operations/qa-v1.2.0.md) | Freeze sign-off checklist (before Hub `1.2.0`) |
