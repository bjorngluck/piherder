# Multi-worker Celery

Backups run **in parallel across different hosts**. The same host never has two active backups (Redis mutex `piherder:server_lock:backup:{server_id}`).

| Concept | Meaning |
|---------|---------|
| **Node** | One Celery worker process/container |
| **Pool slots** | Prefork children (`CELERY_CONCURRENCY`) |

**Default:** `1 node · 2 pool slots`. Prefer raising `CELERY_CONCURRENCY` before scaling containers.

| Knob | Default | Notes |
|------|---------|--------|
| `CELERY_CONCURRENCY` | `2` | Pool slots per Celery node |
| `PIHERDER_SERVER_LOCK_TTL` | `7200` | Lock TTL if worker dies mid-rsync |
| Shared volumes | required | Same `/backups` (and herder/data mounts) on **web** + **celery-worker** |
| Cancel | — | Revoke via `celery_task_id`; mutex released in `finally` |

Optional multi-container:

```bash
# remove fixed container_name from celery-worker in compose, then:
docker compose up -d --scale celery-worker=N
```

Status shows **N nodes** and sum of pool slots.

## Auth rate limiting

Login / 2FA attempt limits are **in-process memory** (per web process). With the default
single Uvicorn worker this is fine. If you run multiple web replicas, each process has its
own counter — prefer a reverse-proxy rate limit or a future Redis-backed limiter for HA.

## What Celery does **not** run

OS patch, container patch, and OS/container **update checks** run on the **web** container (FastAPI `BackgroundTasks` and small thread pools). They are **not** Celery tasks.

| Job family | Execution | Parallelism rule |
|------------|-----------|------------------|
| `backup` | Celery | Many hosts in parallel; **one backup per host** (Redis mutex) |
| `os_patch` / `container_patch` | Web process | **One active job of that type per host** (DB exclusive) |
| `os_update_check` / `container_update_check` | Web process | **One active check of that type per host** |
| `docker_stack_check` / `docker_stack_deploy` | Web process | **One active stack job of that type per host** |

Raising `CELERY_CONCURRENCY` or adding Celery nodes does **not** cause a single container patch to run twice. Double-triggers from the UI or bulk queue attach to the existing job instead (HTTP **409** on the API).
