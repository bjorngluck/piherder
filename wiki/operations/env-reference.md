# Environment reference

Full commented catalog: [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example) in the repo. Copy to `.env`.

Compose injects matching keys into **web** and **celery-worker**. Caddy mainly needs `PIHERDER_HOSTNAME`.

## Required

| Variable | Purpose |
|----------|---------|
| `PIHERDER_MASTER_KEY` | Fernet key — SSH keys, integration tokens, template secrets, VAPID private |
| `SECRET_KEY` | Session / JWT signing — long random in production |

Generate master key:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Public URL / TLS

| Variable | Purpose |
|----------|---------|
| `PIHERDER_HOSTNAME` | Caddy site hostname; must match cert SANs |
| `PIHERDER_PUBLIC_URL` | Canonical origin (include `:8443` if mapped) |

## Host paths

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIHERDER_BACKUP_HOST_PATH` | `./backups` | Host side of `/backups` mount |

Other mounts fixed in `docker-compose.yml`: `piherder_backups`, `piherder_data`, `certs`.

## Database / Redis

| Variable | Default idea |
|----------|--------------|
| `DATABASE_URL` | `postgresql://piherder:piherder@db:5432/piherder` |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | same Redis |
| `CELERY_CONCURRENCY` | `2` pool slots |
| `PIHERDER_SERVER_LOCK_TTL` | `7200` backup mutex TTL |

## Auth / metrics / CORS

| Variable | Purpose |
|----------|---------|
| `METRICS_TOKEN` | Bearer for `GET /metrics` |
| `METRICS_BACKUP_STALE_HOURS` | Stale backup gauge (default 36) |
| `CORS_ORIGINS` | Exact browser origins for `/api/v1` (empty = off) |
| `WEBHOOK_URL` / `WEBHOOK_NUMBER` | Legacy webhook → e.g. Signal via n8n |
| `VAPID_*` | Optional pin; auto-gen is default |
| `PIHOLE_URL` | Dashboard link |

## Inside-container paths (rarely change)

`BACKUP_ROOT`, `HERDER_BACKUP_ROOT`, `DATA_ROOT`, `DEFAULT_DOCKER_BASE`, …
