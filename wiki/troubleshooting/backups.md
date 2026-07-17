# Backups stuck or failing

## What this is

Diagnosis when a **server backup** job fails, stays pending, or never updates `last_backup_at`. For product behaviour see [Backups](../day-to-day/backups.md).

## Failed status

- Open job detail + audit for rsync output.  
- Per-source `rc != 0` → overall failed; `last_backup_at` unchanged.  
- Path not allowed by policy?  
- Disk full on PiHerder `/backups` mount? **Status → View details**.

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
