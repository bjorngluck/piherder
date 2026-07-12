# Multi-worker Celery

Backups run **in parallel across different hosts**. The same host never has two active backups (Redis mutex `piherder:server_lock:backup:{server_id}`).

| Concept | Meaning |
|---------|---------|
| **Node** | One Celery worker process/container |
| **Pool slots** | Prefork children (`CELERY_CONCURRENCY`) |

**Default:** `1 node · 2 pool slots`. Prefer raising `CELERY_CONCURRENCY` before scaling containers.

| Knob | Default | Notes |
|------|---------|--------|
| `CELERY_CONCURRENCY` | `2` | Pool slots |
| `PIHERDER_SERVER_LOCK_TTL` | `7200` | Lock TTL if worker dies mid-rsync |
| Shared volumes | required | Same `/backups` on web + worker |
| Cancel | | Revoke via `celery_task_id`; mutex released in `finally` |

Optional multi-container:

```bash
# remove fixed container_name from celery-worker in compose, then:
docker compose up -d --scale celery-worker=N
```

Status shows **N nodes** and sum of pool slots.
