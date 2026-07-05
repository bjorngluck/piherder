# PiHerder

**Secure fleet management for Raspberry Pi clusters — backups, patching, and control with zero plaintext secrets.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

PiHerder is a self-hosted web app that manages one or more remote Linux servers (primarily Raspberry Pis). It replaces manual bash scripts with an auditable UI while keeping secrets encrypted at rest.

- **Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)
- **Specification & roadmap:** [SPEC.md](SPEC.md) — link this to your [GitHub Project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects) board

## Features (v1)

- Add servers via SSH keypair (generated in-app or uploaded) — private key encrypted immediately with Fernet.
- Per-server toggles: Backups, OS Patching, Container Patching.
- **Backups** (rsync over SSH) — replicates `~/docker/backup_script.sh` + retention logic.
- **Container Patching** — `docker compose pull` + conditional `up -d` only on real image change (replicates improved `docker-cluster-update.sh`).
- OS patching (apt full sequence + reboot-required detection).
- Diagnostics (ping, DNS, system info, etc.).
- Full audit trail + job logs (filter by user/status/action/server + action links).
- Self-backup of PiHerder config (servers + encrypted keys) — scheduled via UI (Settings), compressed archives, restore with preview.
- Link to Pi-hole admin from dashboard (configurable).
- HTTPS via Caddy (Let's Encrypt).

**Volumes (docker-compose.yml):**
- `~/backup:/backups` — destination root for per-server rsync backups.
- `./piherder_backups:/herder_backups` — PiHerder self-backup archives (config, encrypted keys, optional audit). Map a persistent host directory here.

## Tech Stack (per spec)

FastAPI + SQLModel + PostgreSQL + paramiko + cryptography (Fernet) + Jinja2 + (vendored) Tailwind + HTMX + Alpine.

**Offline / air-gapped ready**: Once built, the container has no external CDN dependencies.
All frontend assets (Tailwind Play, HTMX, Alpine) are vendored during `docker build`.

**Important for building the image yourself:**
The build step requires internet access (to download the frontend assets).
The build will **fail hard** with a clear error if `tailwind.js` is missing or invalid.
This protects users who build the image themselves.

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

   - First visit: register the initial admin user.
   - Add your first server (generate keypair recommended).

5. On the target Pi(s):
   - Add the displayed public key to `~/.ssh/authorized_keys`.
   - Ensure passwordless sudo for the SSH user (for apt, docker compose, and rsync on most systems).
   - For HAOS or root SSH users, backups can use plain rsync (auto-detected; no sudo required for rsync).
   - `docker` group membership for container ops.

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

From the server detail page:
- Run Backup
- Run Retention (cleanup)
- Run Container Patch (all or selected projects)
- Run OS Patch
- Run Diagnostics

All actions create AuditLog entries with status + snippet.

## Replacing Cron Jobs

Use the built-in scheduler (UI-configured per-server backup schedules + global herder self-backup at Settings) or manual triggers.

For other job types or external systems you can still call the API:

```bash
# Example: trigger container patch on a server (requires auth token)
curl -H "Authorization: Bearer $TOKEN" \
  -X POST http://piherder.local/api/servers/1/jobs/container_patch
```

## Security Notes

- `PIHERDER_MASTER_KEY` is the only secret. Never commit it.
- SSH private keys are encrypted at rest. Decrypted **only** in memory for the duration of a job.
- All access audited.
- Use strong unique passwords + HTTPS.

## Development

```bash
# Local python (after docker db or sqlite for quick dev)
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Run alembic migrations (inside container or with DATABASE_URL):
```bash
alembic upgrade head
```

## Volumes

- `~/backup:/backups` — per-server backup destination (rsync targets land here).
- `./piherder_backups:/herder_backups` — self-backup archives for PiHerder itself.

Bind-mount host directories as needed for persistence.

## Roadmap

See **[SPEC.md](SPEC.md)** for the full specification, architecture, and phased roadmap (Phase 2–4: scheduling UI, API tokens, multi-user roles, fleet dashboard, etc.).

To track work in a GitHub Project: link the `piherder` repo, then create issues from the unchecked items in SPEC.md.

## License

MIT — see LICENSE.

## Credits

Logic for backups and container patching was ported from the author's battle-tested shell scripts (see references in source).
