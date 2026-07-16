# Environment reference

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
