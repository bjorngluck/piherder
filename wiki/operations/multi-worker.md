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

## Exclusive jobs on the default queue

OS patch, container patch, OS/container/stack **update checks**, compose stack mutations, and template deploy / redeploy / drift check run as `app.tasks.exclusive_job` on the **default** Celery queue (the same `celery-worker` as backups). They do **not** take Move’s dual-host backup mutex. nmap stays on `celery-worker-nmap` (`-Q nmap`). `retention` and the herder’s own backup still run in the **web** process.

Recycling **web** does not fail these exclusive jobs. Recycling **celery-worker** while one is **running** fails it (a patch or compose action is not resumed mid-flight). If the host’s SSH is down when the worker picks the job up, the row stays **pending**, the worker probes again every 30 seconds, and the exclusive slot stays held. The wait ends when SSH works or after the max wait. Default **30 minutes**. Change it under **Settings → General → Jobs**. `PIHERDER_EXCLUSIVE_HOST_WAIT_SEC` locks that field when it is set (minimum 30 seconds, maximum 86400). Uptime Kuma showing the host down, or `last_seen` older than 15 minutes, can label the job **waiting on host**. Only a successful SSH probe resumes it. Neither signal fails the job.

| Job family | Execution | Parallelism rule |
|------------|-----------|------------------|
| `backup` | Celery | Many hosts in parallel; **one backup per host** (Redis mutex) |
| `service_migrate` | Celery | Dual-host backup mutex + DB exclusive with stack mutation on **both** ids. Recycle **web** is safe; recycle **worker** fails a running Move |
| `service_migrate_undo` | Celery | Same mutex. Only a failed post-flip Move. `compose stop` on dest, never `down -v`. Recycle **worker** fails a running undo and leaves the dest tree |
| `os_patch` / `container_patch` | Celery default queue | **One active job of that type per host** (DB exclusive). No backup mutex |
| `os_update_check` / `container_update_check` / `docker_stack_check` | Celery default queue | **One active check of that type per host**. SSH down keeps the job pending |
| `docker_stack_deploy` / `_stop` / `_start` / `_restart` / `_down` / `_remove` / template deploy and redeploy | Celery default queue | **One active stack mutation per host** (shared lane) |
| `template_drift_check` | Celery default queue | One drift check per host. Not a stack write |
| `retention` / `herder_backup` / `host_facts` | Web process | A web recycle fails a pending or running row |

Raising `CELERY_CONCURRENCY` or adding Celery nodes does **not** cause a single container patch to run twice. Double-triggers from the UI or bulk queue attach to the existing job instead (HTTP **409** on the API). A single-host patch does not block a backup or Move on a different host.
