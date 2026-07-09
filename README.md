# PiHerder

**Secure fleet management for Raspberry Pi clusters — backups, patching, and control with zero plaintext secrets.**

![PiHerder Logo](app/static/images/piherder-logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

PiHerder is a self-hosted web app that manages one or more remote Linux servers (primarily Raspberry Pis). It replaces manual bash scripts with an auditable UI while keeping secrets encrypted at rest.

- **Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)
- **Specification & roadmap:** [SPEC.md](SPEC.md) — link this to your [GitHub Project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects) board
- **IAM / 2FA / update checks / notifications (design + status):** [docs/FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](docs/FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md)
- **UI unification plan:** [UI_UNIFICATION_PLAN.md](UI_UNIFICATION_PLAN.md) (complete)

## Features

### Fleet & jobs
- Add servers via SSH keypair (generated in-app or uploaded) — private key encrypted immediately with Fernet.
- **SSH access** on each server: test connection, deploy public key (optional password bootstrap), rotate keypair, least-priv user scripts (**Pi OS / Ubuntu**), copy-paste install commands. HAOS: key deploy + plain rsync guidance.
- Per-server toggles: Backups, OS Patching, Container Patching; optional OS/container **update check** schedules (check-only).
- Optional **OS / container patch apply schedules** (opt-in, default off; only-if-updates; audited as system/scheduler).
- **Backups** (rsync over SSH) — multi-source paths, retention, schedules; path allow/deny policy; **restore wizard** (dry-run then confirm).
- **Container patching** — `docker compose pull` + conditional `up -d`; live JobHold logs; Docker browser (list, logs, compose edit, build).
- **OS patching** — apt update / upgrade **or** full-upgrade / autoremove; live progress; Ubuntu phased-update awareness; reboot-required.
- **Jobs** — per-server card panel + fleet **Jobs** page (`/jobs`) with filters, date range, pagination, detail modal.
- **Docker details** — full mount paths; per-mount host disk usage (`du`); container size = writable+image (not volumes).
- **Fleet dashboard** — patch/update attention across hosts; servers list filters and ⋯ action menus.
- Diagnostics (ping, DNS, system info).
- Full **audit** trail (filters, pagination 10/20/50); scheduled jobs as system/scheduler.
- Self-backup of PiHerder config — scheduled via Settings, restore with preview.
- In-app **notification center** (bell, dismiss, deep links).
- Link to Pi-hole admin from dashboard (configurable).
- HTTPS via Caddy (Let's Encrypt).

### Account & security
- User profile: display name, email, avatar, password change; registration locks after first user.
- **RBAC:** admin / operator / viewer; admin **Users** page (create with password generator + invite copy, roles, delete modal).
- **Password policy** (min 10 + complexity); admin-created users **must change password on first login**.
- Optional **2FA** (TOTP + backup codes + trusted device); optional **force 2FA for all** (Settings → Security policy).
- Basic rate limiting on login / 2FA endpoints.
- Schema via **Alembic** on startup; unit tests with `pytest`.

**Volumes (docker-compose.yml):**
- `~/backup:/backups` — destination root for per-server rsync backups.
- `./piherder_backups:/herder_backups` — PiHerder self-backup archives (config, encrypted keys, optional audit). Map a persistent host directory here.
- `./piherder_data:/data` — avatars and other app data (if configured in compose).

## Tech Stack

FastAPI + SQLModel + PostgreSQL + paramiko + cryptography (Fernet) + Jinja2 + (vendored) Tailwind + HTMX + Alpine + APScheduler + Celery.

**Offline / air-gapped ready**: Once built, the container has no external CDN dependencies.
All frontend assets (Tailwind Play, HTMX, Alpine) are vendored during `docker build`.

**Code structure**: Small focused modules (routers for servers/docker/backups/audit/auth; services for backup, SSH, onboarding, patching, notifications, fleet status). Behavior-preserving splits over god files.

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

3. Start everything:
   ```bash
   docker compose up -d --build
   ```

4. Open https://localhost (or your configured domain). Caddy will handle TLS.

   - First visit: register the initial admin user (further open registration is locked after the first account).
   - Account → optional 2FA, profile, avatar.
   - Add your first server (generate keypair recommended). Optionally store a one-time SSH password for deploy.

5. On the target host / from PiHerder **SSH access**:
   - **Deploy key** (password session if needed) or copy the install script into `authorized_keys`.
   - **Test connection**, then clear any stored password once key auth works.
   - Optional least-priv user (**Pi OS / Ubuntu**): limited sudoers + docker group; **Run on host** or copy-paste script. **HAOS:** deploy key as root; plain rsync is auto-detected.
   - **Option B (recommended for least-priv + existing stacks under another home):** set **Docker base dir** to an absolute path (e.g. `/home/bjorn/docker`), then run the **Option B ACL script** from SSH access so the service user can traverse that tree. `~/docker` expands to the *SSH* user’s home and breaks restart/build/logs after re-pointing to `piherder`.
   - Otherwise ensure passwordless sudo for apt/docker/rsync as needed, and `docker` group for container ops.

6. Optional: Settings → fleet-wide midnight **update check** schedules; server list / dashboard show pending OS and container updates.

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

From the server detail page (⋯ menu) and related pages:
- Run Backup / retention
- Run Container Patch / OS Patch
- Check OS / container updates (manual)
- Reboot, diagnostics
- Docker: compose edit, build, logs, redeploy

All actions create AuditLog entries with status + snippet. Actionable alerts also appear in the notification center when configured checks/jobs raise them.

## Replacing Cron Jobs

Use the built-in scheduler:
- Per-server **backup** schedules
- Per-server or **global** OS / container **update check** schedules (detect only)
- PiHerder self-backup schedule (Settings / herder backups)

Apply of OS/container patches remains **manual** by design (no silent auto-upgrade).

For external systems you can still call HTTP APIs where exposed (auth required); a full token REST surface is still on the roadmap (see SPEC).

## Security Notes

- `PIHERDER_MASTER_KEY` is the only master secret. Never commit it.
- SSH private keys (and optional SSH passwords) are encrypted at rest. Decrypted **only** in memory for jobs / onboarding actions.
- Prefer key auth; clear stored SSH passwords after **Deploy key** succeeds.
- Optional app 2FA (TOTP); backup codes for recovery; trusted devices are revocable.
- All privileged access audited.
- Use strong unique passwords + HTTPS.

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

- `~/backup:/backups` — per-server backup destination (rsync targets land here).
- `./piherder_backups:/herder_backups` — self-backup archives for PiHerder itself.

Bind-mount host directories as needed for persistence.

## Roadmap

See **[SPEC.md](SPEC.md)** for the full specification, architecture, and phased roadmap.

**Recently completed (high level):** patch apply schedules, RBAC + user admin, fleet Jobs page, backup restore wizard, password policy / force-2FA, Docker mount sizes, IAM/2FA, update checks, SSH onboarding, job queue, path policy, Alembic + pytest.

**Still open (examples):** webhooks end-to-end, token REST API, Docker Hub image, compose multi-file/env UI polish, Prometheus, Ansible bootstrap.

To track work in a GitHub Project: link the `piherder` repo, then create issues from the unchecked items in SPEC.md.

## License

MIT — see LICENSE.

## Credits

Logic for backups and container patching was ported from the author's battle-tested shell scripts (see references in source).
