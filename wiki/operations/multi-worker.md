# Multi-worker Celery

## What this is

How PiHerder runs **backup** and **Move** jobs on Celery: pool slots, optional multiple worker containers, and a **per-server Redis mutex** so one host is never rsync’d twice at once.

## Why it exists

Large fleets want parallel backups; a single host’s destination tree must stay consistent. Mutex + concurrency knobs give both without inventing a second queue system.

Backups run **in parallel across different hosts**. The same host never has two active backups (Redis mutex `piherder:server_lock:backup:{server_id}`). **Move** takes that same mutex on **both** source and dest (lower server id first) so a backup cannot overlap a copy.

| Concept | Meaning |
|---------|---------|
| **Node** | One Celery worker process/container |
| **Pool slots** | Prefork children (`CELERY_CONCURRENCY`) |

**Default:** `1 node · 2 pool slots`. Prefer raising `CELERY_CONCURRENCY` before scaling containers.

| Knob | Default | Notes |
|------|---------|--------|
| `CELERY_CONCURRENCY` | `2` | Pool slots per Celery node |
| `PIHERDER_SERVER_LOCK_TTL` | `7200` | Lock TTL if worker dies mid-rsync. **Not refreshed** during a long copy — a Move/backup longer than this can lose the mutex. Raise the TTL for huge datasets. |
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

OS patch, container patch, and OS/container **update checks** run on the **web** container (FastAPI `BackgroundTasks` and small thread pools). They are **not** Celery tasks on this image. Moving those exclusive types onto Celery (and waiting when the host is down) is **v1.7 Jr-1**, opened on `v1.7.0-dev` — not this **1.6.0** image. [PLAN_v1.7.0](https://github.com/bjorngluck/piherder/blob/v1.7.0-dev/docs/PLAN_v1.7.0.md).

| Job family | Execution | Parallelism rule |
|------------|-----------|------------------|
| `backup` | Celery | Many hosts in parallel; **one backup per host** (Redis mutex) |
| `service_migrate` | Celery | Dual-host backup mutex + DB exclusive with stack mutation on **both** ids. Recycle **web** is safe; recycle **worker** fails a running Move |
| `service_migrate_undo` | Celery | Same mutex. Only a failed post-flip Move. `compose stop` on dest, never `down -v`. Recycle **worker** fails a running undo and leaves the dest tree |
| `os_patch` / `container_patch` | Web process | **One active job of that type per host** (DB exclusive) |
| `os_update_check` / `container_update_check` | Web process | **One active check of that type per host** |
| `docker_stack_check` | Web process | **One active stack check per host** |
| `docker_stack_deploy` / `docker_stack_stop` / `_start` / `_restart` / template deploy | Web process | **One active stack mutation per host** (shared lane) |

Raising `CELERY_CONCURRENCY` or adding Celery nodes does **not** cause a single container patch to run twice. Double-triggers from the UI or bulk queue attach to the existing job instead (HTTP **409** on the API).
