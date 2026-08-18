# Architecture

```mermaid
flowchart TB
    Browser["Browser (HTMX + Alpine)"] -->|HTTPS| Caddy
    Caddy --> FastAPI["FastAPI (web)"]

    subgraph Core["Docker Compose (supported)"]
        FastAPI --> DB[(PostgreSQL)]
        FastAPI --> Scheduler["APScheduler"]
        FastAPI --> Celery["Celery worker(s)"]
        FastAPI --> CeleryNmap["celery-worker-nmap (profile nmap)"]
    end

    Scheduler -->|backup cron| Celery
    Scheduler -->|nmap schedules / stale cleanup| Celery
    Scheduler -->|patch/check cron| FastAPI
    Celery -->|reads/writes| DB
    Celery -->|SSH + rsync| PiFleet["Remote fleet"]
    CeleryNmap -->|nmap -oX / vuln pack| LAN["Configured LAN CIDR(s)"]
    CeleryNmap -->|reads/writes| DB
    FastAPI -->|SSH · apt · docker| PiFleet
    FastAPI -->|DB reads for UI| DB
    FastAPI -.->|Job.details progress| Celery
    FastAPI -.->|enqueue -Q nmap| CeleryNmap
```

## Job execution paths

| Work | Runs on | Concurrency rule |
|------|---------|------------------|
| Backups | Celery | Parallel across hosts; one backup per host (Redis mutex) |
| OS/container patch & update checks | Web (`BackgroundTasks` / thread pools) | One active job of that type per host |
| Bulk fleet actions | Web → same enqueue paths | Feature-flag skip + exclusive rules |
| LAN nmap scans / vuln pack update | **celery-worker-nmap** (`-Q nmap`, concurrency 1) | Opt-in profile; host network; `PIHERDER_NMAP_WORKER=1` only here |
| Stale Jobs/Audit/nmap-run purge | Celery (default queue) | Opt-in Settings schedule |

**Nmap privilege boundary:** web + main celery set `PIHERDER_NMAP_WORKER=0` in compose; tasks call `worker_guard` and refuse if marker is off or `nmap` is missing. Never put queue `nmap` on the main worker. See [env reference](../operations/env-reference.md#lan-discovery-nmap--opt-in) · [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example).

## Key modules (pointers)

| Concern | Location |
|---------|----------|
| Roles / middleware | `app/security/auth.py` |
| Password policy | `app/services/password_policy.py` |
| Jobs / progress / exclusive types | `app/services/jobs/` (`service.py`; package preserves `patch.object` surface) |
| Docker unused cleanup HTML | `app/services/docker_unused_html.py` |
| Per-server backup lock | `app/services/server_job_lock.py` |
| Scheduler | `app/services/scheduler.py` |
| Backup | `app/services/backup.py` (+ progress, profiles) |
| Docker inventory | `app/services/docker_inventory.py` |
| Templates (domain) | `app/services/service_templates/` — `deploy`, `host_sync` (adopt/migrate), `harden`, `schema`, `from_host`, … |
| Compose project files (pure) | `app/services/compose_project_files.py` — file kinds, sidecar discovery, desired→live merge (no SSH) |
| Compose editor workspace | `app/services/compose_editor.py` — inventory/fallback path, live files, template sidecars, drafts |
| Docker versions / live files | `app/services/docker_versions.py` — SFTP multi-file I/O + discovery via `compose_project_files` |
| Integrations (domain) | `app/services/integrations/` |
| Integrations (HTTP) | `integrations.py` + `integrations_common` / `_kuma` / `_grafana` / `_pihole` / `_npm` / `_nmap` |
| LAN nmap (scan/parse/schedules/vuln) | `app/services/nmap/` (`worker_guard`, `scan`, `device_ops`, `fabric_projection`, …) · router `integrations_nmap.py` · image `Dockerfile.nmap` |
| Stale data cleanup | `app/services/stale_data_cleanup.py` · Settings General |
| Templates (HTTP) | `templates_common` + `templates_svc` (catalog) + `templates_deploy` |
| Auth (HTTP) | `auth.py` + `auth_users.py` (admin users) |
| Network maps | `app/services/dns_fabric/` (`core`, `mesh_physical`, `mesh_logical`, `ports`, `stack_panel`) · `app/routers/dns.py` |
| Published port chips | `app/services/dns_fabric/ports.py` — host→container parse for stack panel |
| Operator pins + host jump | `app/services/nav_shortcuts.py` · `app/routers/favourites.py` · model `UserFavourite` · partials `pin_button` / `host_switcher` / `host_feature_nav` |
| Human-readable cron | `app/services/cron_human.py` — Jinja `cron_human` filter + `cron_presets` global |
| Certificates / deploy targets | `app/services/certificates.py` · `app/routers/certificates.py` — vault, stage+sudo, verify, wizard |
| Ops-hero pulse helpers | `app/services/ops_pulse.py` |
| Push | `app/services/push.py` |
| API tokens | `app/services/api_tokens.py`, `app/routers/api_v1.py` |
| Herder backup | `app/services/herder_backup.py` |
| Metrics | `app/services/metrics.py` |
| Bulk server actions | `app/routers/servers.py` (`POST /servers/bulk`) |
| Server SSH / patch sub-routers | `server_ssh.py`, `server_patch.py`, `server_common.py` (mounted under `/servers`) |
| Trusted client IP | `app/services/request_ip.py` — honour XFF/CF only from `PIHERDER_TRUSTED_PROXY_CIDRS` |
| SSH host-key pin (TOFU) | `app/services/ssh.py` (`HostKeyPinPolicy`) · columns `ssh_hostkey_*` · reset in `server_ssh.py` |
| Weak `SECRET_KEY` | `app/main.py` — refuse boot unless `PIHERDER_ALLOW_INSECURE` / `DEMO_MODE` |
| Docker UI | `server_docker.py` + `server_docker_compose.py` (thin; editor load in `compose_editor`) |
| Theme / map / ops CSS | `themes.css`, `fabric.css` (mesh), `fabric-stack.css`, `dns-hub.css`, `ops.css`, `ops-auth.css`, `ops-pages.css` (`ph-dense-*` lists) |
| Map / stack client | `fabric-mesh.js` (map open/closed + pan/zoom; `#map` / `preferMapOnLoad`) · `fabric-stack-panel.js` (stack drawer + one pointer reorder path) |
| App timezone display | `app/services/app_settings.describe_timezone` · Settings General card |
| Large templates | Prefer `partials/` — e.g. `server_detail_*_modals`, `docker_modals`, `settings_{tab}`, pin/host switcher partials |

## Frontend stack

- **Server-rendered** Jinja2 + HTMX fragments + Alpine for small widgets  
- **Compiled Tailwind** (`app/static/css/tailwind.css` via `scripts/build-tailwind.sh`) + vendored HTMX / Alpine (no runtime CDN, no Play, no `unsafe-eval`)  
- Progressive enhancement vanilla JS for Network maps, job hold, push, compose editor  
- Shared ops-hero grid contract (`ops.css`): full main content width; desktop title left · viz right (≥768px); mobile viz under title  
- **One list markup per surface** — dense rows (`ph-dense-*`, nmap sched cards) reflow with CSS; avoid dual mobile-table + desktop-table DOM  
- Shell nav: single `nav_items` / `secondary_items` source → desktop bar + mobile drawer (chrome only)  
- Mobile orientation reflow in `base.html`; service rows stack actions on narrow viewports  
- Auth pages (login/register + force-password / 2FA) use shared `auth-stage` chrome  
- Empty DB → first register is admin; no default password user; then registration closes  
- Session cookies set `Secure` when `PIHERDER_PUBLIC_URL` is `https://` (or `COOKIE_SECURE=true`)  

## Design principles

- Privileged actions audited (incl. client IP)  
- Secrets encrypted at rest; decrypt only in memory for jobs  
- Offline/air-gapped ready once built (vendored assets)  
- External/dangerous actions opt-in: preview → confirm → audit  
- One exclusive OS/container job type per host (no silent double SSH)  
- Thin routers where practical; domain logic in `app/services/`  
- Compose-first deployment; DB-first operational settings  
- Integrations optional — core fleet never depends on Kuma/Grafana/NPM/Pi-hole
