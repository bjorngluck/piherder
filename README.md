# PiHerder

**Secure fleet management for Raspberry Pi clusters — backups, patching, and control with zero plaintext secrets.**

![PiHerder Logo](app/static/images/piherder-logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

PiHerder is a self-hosted web app that manages one or more remote Linux servers (primarily Raspberry Pis). It replaces manual bash scripts with an auditable UI while keeping secrets encrypted at rest.

- **Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)
- **Specification:** [SPEC.md](SPEC.md) — link this to your [GitHub Project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects) board
- **Ecosystem roadmap:** [docs/ROADMAP_ECOSYSTEM.md](docs/ROADMAP_ECOSYSTEM.md) (production → integrations → templates → community)
- **Admin guide (RBAC, users, schedules, jobs, TLS/PWA/push, API tokens):** [docs/ADMIN.md](docs/ADMIN.md)
- **Publish image:** [docs/PUBLISH_IMAGE.md](docs/PUBLISH_IMAGE.md)
- **Security:** [SECURITY.md](SECURITY.md)
- **IAM / 2FA / update checks / notifications:** [docs/FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](docs/FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md)
- **PWA + Web Push:** [docs/FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md](docs/FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md) · iOS: [docs/DECISION_IOS_PUSH.md](docs/DECISION_IOS_PUSH.md)
- **Stabilisation plan:** [docs/DECISION_PLAN_STABILISATION.md](docs/DECISION_PLAN_STABILISATION.md)
- **UI unification plan:** [UI_UNIFICATION_PLAN.md](UI_UNIFICATION_PLAN.md) (complete)

**Community:** use [GitHub Issues](https://github.com/bjorngluck/piherder/issues) for bugs and features; [Discussions](https://github.com/bjorngluck/piherder/discussions) for Q&A when enabled. Discord link will be added when the server is public.

## Features

### Fleet & jobs
- Add servers via SSH keypair (generated in-app or uploaded) — private key encrypted immediately with Fernet.
- **SSH access** on each server: test connection, deploy public key (optional password bootstrap), rotate keypair, least-priv user scripts (**Pi OS / Ubuntu**), copy-paste install commands. HAOS: key deploy + plain rsync guidance.
- Per-server **feature flags** (Edit → Features): Backups, OS patch, **Docker / containers** — disabled features are **hard-hidden** from dest cards, host status, and ⋯ menus.
- Optional OS/container **update check** and **patch apply** schedules (Edit → Schedules; check-only vs opt-in apply; only-if-updates; audited as system/scheduler).
- **Backups** (rsync over SSH) — multi-source paths, retention, schedules; path allow/deny policy; **restore wizard** (dry-run then confirm).
- **Container patching** — `docker compose pull` + conditional `up -d`; live JobHold logs; Docker browser (list, logs, compose edit, multi-file deploy).
- **Docker inventory cache** — DB snapshot of stacks/containers; opens instantly from last collect; background SSH refresh (prefetch, after mutations, fleet interval); Force refresh for a full re-collect.
- **OS patching** — apt update / upgrade **or** full-upgrade / autoremove; live progress; Ubuntu phased-update awareness; reboot-required.
- **Jobs** — per-server card panel + fleet **Jobs** page (`/jobs`) with filters, date range, pagination, detail modal.
- **Docker details** — full mount paths on expand; per-mount host disk usage (`du`); container size = writable+image (not volumes).
- **Fleet dashboard** — patch/update attention across hosts; servers list filters and ⋯ action menus (feature-gated).
- Diagnostics (ping, DNS, system info).
- Full **audit** trail (filters, pagination 10/20/50); scheduled jobs as system/scheduler.
- Self-backup of PiHerder — servers, full users/IAM/2FA, compose versions, Web Push (VAPID + devices), notifications, herder settings, avatars; optional audit; restore with dry-run preview.
- In-app **notification center** (bell, dismiss, deep links).
- Optional **Web Push** (VAPID) for fleet alerts on Android and iOS Home Screen PWAs (16.4+); per-user prefs under Account.
- Installable **PWA** (manifest + service worker + home-screen install).
- Link to Pi-hole admin from dashboard (configurable).
- HTTPS via Caddy with **operator-supplied TLS certs** (volume `./certs`) and `PIHERDER_HOSTNAME` (default ports **8888** HTTP / **8443** HTTPS).

### Account & security
- User profile: display name, email, avatar, password change; registration locks after first user.
- **RBAC:** admin / operator / viewer; admin **Users** page (create with password generator + invite copy, roles, delete modal).
- **Password policy** (min 10 + complexity); admin-created users **must change password on first login**.
- Optional **2FA** (TOTP + backup codes + trusted device); optional **force 2FA for all** (Settings → Security policy).
- Basic rate limiting on login / 2FA endpoints.
- Schema via **Alembic** on startup; unit tests with `pytest`.

**Volumes (docker-compose.yml):**
- `./backups:/backups` — destination root for per-server rsync backups (override path in compose if you prefer another host dir).
- `./piherder_backups:/herder_backups` — PiHerder self-backup archives (fleet config, IAM, push keys, avatars, optional audit).
- `./piherder_data:/data` — avatars, app data, future templates.
- `./certs:/certs` (Caddy, read-only) — `fullchain.pem` + `privkey.pem` for trusted HTTPS (see `certs/README.md`).

## Tech Stack

FastAPI + SQLModel + PostgreSQL + paramiko + cryptography (Fernet) + Jinja2 + (vendored) Tailwind + HTMX + Alpine + APScheduler + Celery.

**Offline / air-gapped ready**: Once built, the container has no external CDN dependencies.
All frontend assets (Tailwind Play, HTMX, Alpine) are vendored during `docker build`.

**Code structure**: Small focused modules (routers for servers/docker/backups/audit/auth; services for backup, SSH, onboarding, patching, docker inventory, notifications, fleet status). Behavior-preserving splits over god files.

**Important for building the image yourself:**
The build step requires internet access (to download the frontend assets).
The build will **fail hard** with a clear error if `tailwind.js` is missing or invalid.

If you see SSL/certificate errors (or the download gets a Pi-hole page) while vendoring:
- Whitelist `cdn.tailwindcss.com` in Pi-hole temporarily, or
- `VENDOR_INSECURE=1 bash scripts/vendor_cdns.sh`, or
- `curl -kL -o app/static/tailwind.js https://cdn.tailwindcss.com`

Pre-built images will be available on Docker Hub so most people don't need to build.

## Quick Start

1. Clone / enter this dir:
   ```bash
   cd ~/docker/piherder
   cp .env.example .env
   ```

2. **Generate master key** (critical):
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Paste into `.env` as `PIHERDER_MASTER_KEY=...`

3. **Public hostname + TLS (recommended for PWA / push on phones):**
   ```bash
   # .env
   PIHERDER_HOSTNAME=piherder.example.com
   PIHERDER_PUBLIC_URL=https://piherder.example.com:8443
   ```
   Place trusted PEMs in `certs/fullchain.pem` and `certs/privkey.pem` (gitignored).  
   Local-only / no certs yet: mount `Caddyfile.dev` instead of `Caddyfile` (self-signed; Android push unreliable).  
   Full steps: [docs/ADMIN.md](docs/ADMIN.md) §6.

4. Start everything:
   ```bash
   docker compose up -d --build
   ```

5. Open the app:
   - With certs + hostname: `https://your.host:8443` (or your `PIHERDER_PUBLIC_URL`)
   - Direct to the web container (no Caddy): `http://localhost:8000`
   - Compose Caddy ports: **8888** → HTTP, **8443** → HTTPS

   - First visit: register the initial admin user (further open registration is locked after the first account).
   - Account → optional 2FA, profile, avatar; optional **Push notifications** after VAPID keys are set.
   - Add your first server (generate keypair recommended). Optionally store a one-time SSH password for deploy.

6. On the target host / from PiHerder **SSH access**:
   - **Deploy key** (password session if needed) or copy the install script into `authorized_keys`.
   - **Test connection**, then clear any stored password once key auth works.
   - Optional least-priv user (**Pi OS / Ubuntu**): limited sudoers + docker group; **Run on host** or copy-paste script. **HAOS:** deploy key as root; plain rsync is auto-detected.
   - **Option B (recommended for least-priv + existing stacks under another home):** set **Docker base dir** to an absolute path (e.g. `/home/bjorn/docker`), then run the **Option B ACL script** from SSH access so the service user can traverse that tree. `~/docker` expands to the *SSH* user’s home and breaks restart/build/logs after re-pointing to `piherder`.
   - Otherwise ensure passwordless sudo for apt/docker/rsync as needed, and `docker` group for container ops.

7. Per server: **Edit → Features** to enable Backups / OS / Docker; **Edit → Schedules** for update checks (and optional apply). Server list / dashboard show pending OS and container updates for enabled features.

8. Optional **Web Push:** VAPID keys are **auto-generated at web startup** and stored encrypted in the DB (optional `VAPID_*` env override). Over trusted HTTPS: Account → **Enable on this device** (Android Chrome, or iOS 16.4+ after Safari → Add to Home Screen).

## Configuration from Legacy Scripts

PiHerder replicates the exact behavior of:
- `~/docker/backup_script.sh` + `backup_cleanup.sh`
- `~/docker/scripts/docker-cluster-update.sh`

After adding a server, set:
- `backup_paths` (JSON list)
- `docker_base_dir`
- `excluded_projects`
- `retention_days`

These match the variables in the old scripts.

## Running Jobs

From the server detail page (⋯ menu — only actions for **enabled** features) and related pages:
- Run Backup / retention (when Backups is on)
- Run Container Patch / OS Patch; check OS / container updates (when those features are on)
- Reboot, diagnostics
- Docker: compose edit, build, logs, redeploy (when Docker / containers is on)

All actions create AuditLog entries with status + snippet. Actionable alerts also appear in the notification center when configured checks/jobs raise them.

**Edit server** (modal tabs): **General** (connection), **Features** (flags), **Schedules** (update checks + patch apply). Backup cron remains on the Backups page.

## Replacing Cron Jobs

Use the built-in scheduler:
- Per-server **backup** schedules (Backups page)
- Per-server OS / container **update check** and optional **apply** schedules (Edit → Schedules)
- Fleet **Docker inventory** refresh (~every 10 minutes for hosts with Docker enabled)
- PiHerder self-backup schedule (Settings / herder backups)

Silent auto-upgrade is never the default: apply schedules are opt-in and prefer “only if updates pending”.

**Automation (token REST API):** admins can create API tokens under **Settings → API tokens** (or via the users/settings area). Use `Authorization: Bearer ph_…` against `/api/v1/*` for fleet inventory and job triggers (n8n, Home Assistant, scripts). See [docs/ADMIN.md](docs/ADMIN.md) § API tokens.

## Security Notes

- `PIHERDER_MASTER_KEY` is the only master secret. Never commit it.
- SSH private keys (and optional SSH passwords) are encrypted at rest. Decrypted **only** in memory for jobs / onboarding actions.
- Prefer key auth; clear stored SSH passwords after **Deploy key** succeeds.
- Optional app 2FA (TOTP); backup codes for recovery; trusted devices are revocable.
- All privileged access audited.
- Use strong unique passwords + **trusted** HTTPS for production and mobile push.
- Do not commit `certs/*.pem`, `.env`, or VAPID private keys.

## Development

```bash
# Local python (after docker db or sqlite for quick dev)
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Schema is applied on web startup via Alembic (`migrations/`, `alembic upgrade head`). You can also run manually:
```bash
docker compose exec web alembic upgrade head
# or with DATABASE_URL set:
alembic upgrade head
```

Unit tests (no live SSH required):
```bash
docker compose run --rm --no-deps web pytest -q
# or locally with: pip install -e '.[dev]' && pytest -q
```

## Volumes

- `./backups:/backups` — per-server backup destination (rsync targets land here). Change the host side if you already use e.g. `/home/you/backup`.
- `./piherder_backups:/herder_backups` — self-backup archives for PiHerder itself.
- `./piherder_data:/data` — avatars / app data.
- `./certs` → Caddy `/certs` — TLS PEMs (not committed).

Bind-mount host directories as needed for persistence.

## Roadmap

- **Full phases:** [SPEC.md](SPEC.md)  
- **Ecosystem horizons (v0.2 → v1.0):** [docs/ROADMAP_ECOSYSTEM.md](docs/ROADMAP_ECOSYSTEM.md)

| Track | Theme |
|-------|--------|
| **v0.2 / H0** | Production: clean compose, token REST API, prod ADMIN, published image |
| **v0.3 / H1** | Integration hub: Uptime Kuma, Grafana links, multi Pi-hole / NPM / HA |
| **v0.4 / H2** | Service templates + onboard (monitor / DNS / TLS) |
| **Later / H3** | HA plugin, optional BYO LLM, Ansible, community on Discord + hacknow.info |

**Recently completed (high level):** Docker inventory cache, Edit tabs + feature hard-hide, Prometheus `/metrics`, multi-file compose, PWA + Web Push, trusted TLS, patch apply schedules, RBAC, Jobs page, restore wizard, password policy / force-2FA, IAM/2FA, update checks, SSH onboarding, path policy, Alembic + pytest, token REST API (`/api/v1`), ecosystem roadmap docs.

**Still open (examples):** published Docker Hub/GHCR image, integration registry, service templates, HA plugin, optional AI.

To track work in a GitHub Project: link the `piherder` repo, then create issues from the unchecked items in SPEC.md.

## License

MIT — see LICENSE.

## Credits

Logic for backups and container patching was ported from the author's battle-tested shell scripts (see references in source).
