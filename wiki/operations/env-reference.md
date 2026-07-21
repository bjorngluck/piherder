# Environment reference

## What this is

The knobs that live in **`.env`** (not the Settings UI): encryption keys, public URL, ports, Celery, metrics, feature toggles.

## Why `.env` vs Settings

Secrets and process-level config must be available **before** the app boots. Policy that belongs in the database (timezone, force 2FA, schedules) lives under [Settings](settings.md) so it rides along with self-backup.

Full commented catalog: [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example) in the repo. Copy to `.env`.

Compose injects matching keys into **web** and **celery-worker**. Caddy mainly needs `PIHERDER_HOSTNAME`.

## Required

| Variable | Purpose |
|----------|---------|
| `PIHERDER_MASTER_KEY` | Fernet key — SSH keys, integration tokens, template secrets, VAPID private |
| `SECRET_KEY` | Session / JWT signing — long random in production (not the compose default) |

Generate master key:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Public URL / TLS

| Variable | Purpose |
|----------|---------|
| `PIHERDER_HOSTNAME` | Caddy site hostname; must match cert SANs |
| `PIHERDER_PUBLIC_URL` | Canonical origin (include `:8443` if mapped); HTTPS enables Secure cookies |

## Host paths

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIHERDER_BACKUP_HOST_PATH` | `./backups` | Host side of `/backups` mount |
| `PIHERDER_NMAP_VULN_PATH` | `./piherder_nmap_vuln` | Host dir for LAN Discovery vuln pack (profile **nmap**) |

Other mounts fixed in `docker-compose.yml`: `piherder_backups`, `piherder_data`, `certs`.

## Database / Redis / Celery

| Variable | Default idea |
|----------|--------------|
| `DATABASE_URL` | `postgresql://piherder:piherder@db:5432/piherder` |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | same Redis |
| `CELERY_CONCURRENCY` | `2` pool slots (compose → celery-worker) |
| `PIHERDER_SERVER_LOCK_TTL` | `7200` backup mutex TTL |
| `REDIS_URL` | Optional alias used in some deploy notes — broker/result URLs are authoritative |

## LAN Discovery (nmap) — opt-in

Default `docker compose up` does **not** start the nmap worker. See [LAN Discovery](../integrations/lan-discovery.md) · [install](../getting-started/install.md#6-optional-lan-discovery-nmap-worker).

### Worker fence (compose-owned — usually not in `.env`)

| Where | `PIHERDER_NMAP_WORKER` | Meaning |
|-------|------------------------|---------|
| **web** + main **celery-worker** | **`0`** (hard-coded in `docker-compose.yml` `x-piherder-app-env`) | Tasks refuse to run nmap (`worker_guard`) |
| **celery-worker-nmap** | **`1`** (overrides anchor) | Only allowed scan / vuln-pack executor |
| **`Dockerfile.nmap`** | **`1`** (`ENV`) | Image default for the nmap worker |

You normally **do not** set `PIHERDER_NMAP_WORKER` in `.env` — compose owns it. Never add `-Q nmap` to the main celery-worker command.

Task code also refuses when the **`nmap` binary is missing** (main image has no nmap).

### Optional overrides (`.env` / shell)

| Variable | Default idea | Purpose |
|----------|--------------|---------|
| `PIHERDER_NMAP_VULN_PATH` | `./piherder_nmap_vuln` | Host bind for vuln pack volume |
| `PIHERDER_NMAP_VULN_ROOT` | `/var/lib/piherder/nmap-vuln` | In-container path (web **:ro**, nmap worker **rw**) |
| `PIHERDER_NMAP_IMAGE` | `piherder:nmap-local` | Image tag for profile `nmap` |
| `PIHERDER_NMAP_DATABASE_URL` | loopback Postgres | Host-network worker → `127.0.0.1:5432` |
| `PIHERDER_NMAP_REDIS_URL` | loopback Redis | Host-network worker → `127.0.0.1:6379` |

```bash
docker build -f Dockerfile.nmap -t piherder:nmap-local .
docker compose --profile nmap up -d celery-worker-nmap
```

## Auth / sessions / cookies

| Variable | Purpose |
|----------|---------|
| `ALLOW_OPEN_REGISTRATION` | Default `false`. Empty DB allows first admin via Register; then closed unless `true` (later open-reg users are **operator**) |
| `COOKIE_SECURE` | Empty = auto (`Secure` when `PIHERDER_PUBLIC_URL` is `https://…`); `true`/`false` to force |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Session JWT lifetime (default 10080 = 7 days) |
| `ALGORITHM` | JWT algorithm (default `HS256`) |
| `TRUSTED_DEVICE_DAYS` | 2FA “trust this device” cookie age (default 30) |
| `AVATAR_MAX_BYTES` | Max avatar upload size (default 2 MiB) |

## Metrics / CORS / webhooks

| Variable | Purpose |
|----------|---------|
| `METRICS_TOKEN` | Bearer for `GET /metrics` — **set in production** (empty = open scrape on app port) |
| `METRICS_BACKUP_STALE_HOURS` | Stale backup gauge (default 36) |
| `CORS_ORIGINS` | Exact browser origins for `/api/v1` (empty = off) |
| `WEBHOOK_URL` / `WEBHOOK_NUMBER` | Legacy webhook → e.g. Signal via n8n |
| `WEBHOOK_RECIPIENTS` | Optional JSON list of recipients for some webhook paths |
| `VAPID_*` | Optional pin; auto-gen is default |
| `PIHOLE_URL` | Dashboard quick-link (legacy single URL; multi Pi-hole lives under Catalog) |
| `PIHERDER_UPDATE_CHECK` | Default `true`. Check GitHub Releases for a newer version (About + banner). Set `false` for air-gapped |
| `PIHERDER_UPDATE_CHECK_TTL_HOURS` | Cache TTL for update check (default 12) |

## Herder schedule (optional seed)

| Variable | Purpose |
|----------|---------|
| `HERDER_BACKUP_SCHEDULE` | Optional cron seed for self-backup; Settings UI / DB wins after first save |

## Inside-container paths (rarely change)

`BACKUP_ROOT`, `HERDER_BACKUP_ROOT`, `DATA_ROOT`, `DEFAULT_DOCKER_BASE`, …

## Related

- [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example) — full commented catalog  
- [Install — nmap worker](../getting-started/install.md#6-optional-lan-discovery-nmap-worker)  
- [LAN Discovery](../integrations/lan-discovery.md)  
- [Volumes](volumes.md)  
- [ADMIN.md — production env](https://github.com/bjorngluck/piherder/blob/main/docs/ADMIN.md)
