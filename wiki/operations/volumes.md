# Volumes

## What this is

Host directories Docker Compose bind-mounts into the stack for **fleet backups**, **herder DR archives**, **avatars/logos**, and **TLS PEMs**.

## Why it matters

Backups and self-backup are only as durable as the disk under these paths. Celery workers must see the **same** `/backups` (and usually `/data`, `/herder_backups`) as web.

| Host path | Container | Purpose |
|-----------|-----------|---------|
| `${PIHERDER_BACKUP_HOST_PATH:-./backups}` | `/backups` | rsync destinations for server backups |
| `./piherder_backups` | `/herder_backups` | PiHerder self-backup archives (chown uid **1000** if permission errors) |
| `./piherder_data` | `/data` | Avatars + service logos (Settings live in Postgres) |
| `./certs` | `/certs` (Caddy, ro) | `fullchain.pem` + `privkey.pem` |

Secondary disk example:

```bash
PIHERDER_BACKUP_HOST_PATH=/home/you/backup
```

`web` and `celery-worker` must share the same `/backups` (and usually `/data`, `/herder_backups`) for multi-worker correctness.
