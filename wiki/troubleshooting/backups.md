# Backups stuck or failing

## What this is

Diagnosis when a **server backup** job fails, stays pending, or never updates `last_backup_at`. For product behaviour see [Backups](../day-to-day/backups.md).

## Failed status

- Open job detail + audit for rsync output.  
- Per-source `rc != 0` → overall failed; `last_backup_at` unchanged.  
- Path not allowed by policy?  
- Disk full on PiHerder `/backups` mount? **Status → View details**.

## Vanished files / busy sources

**v1.1.0 known issue** ([RELEASE_v1.1.0.md](../../docs/RELEASE_v1.1.0.md#known-issues-ship-with-awareness) **KI-rsync-vanished**).

On a busy host, files under the backup source can **disappear or move while rsync is running**. Typical examples:

- **Frigate** (and similar NVR/media stacks): recordings are written, indexed, rotated, or deleted continuously  
- App caches, temp segments, or log rotation under a large docker tree  

rsync then reports **code 24** (vanished files) and/or **code 23** (partial transfer). PiHerder marks that **source** failed even if much of the tree transferred and other sources (e.g. volumes) succeeded.

| Expectation in v1.1 | Later |
|---------------------|--------|
| Fail is **honest** — some files were not a consistent snapshot | **v1.2+**: explore softer handling + **retry** ([PLAN_v1.2.0.md](../../docs/PLAN_v1.2.0.md)) |

**What you can do now**

1. Re-run the job off-peak.  
2. Split sources: back up stable config/DB paths separately from high-churn recording dirs (exclude or omit the busiest path if you do not need every frame in the herder backup).  
3. If the error is **`Input/output error`** or a mount shows filesystem **`shutdown`**, fix the disk/mount first — that is not the vanished-file case (see [SSH/rsync](ssh-rsync.md#backups-rsync-code-23-partial-transfer)).

## Stuck pending / waiting_for_server

- Another backup holds the Redis per-server mutex.  
- Wait or cancel the active job.  
- Worker crash: lock TTL eventually expires (`PIHERDER_SERVER_LOCK_TTL`).  
- Celery down? Settings → Status.

## Celery not running jobs

```bash
docker compose ps celery-worker
docker compose logs celery-worker --tail=100
```

Confirm `CELERY_BROKER_URL` and shared volumes.

## Permission on herder_backups volume

Self-backup (not server backup) needs uid 1000 write on `./piherder_backups`:

```bash
sudo chown -R 1000:1000 piherder_backups
```
