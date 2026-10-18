# Backups stuck or failing

## What this is

Diagnosis when a **server backup** job fails, stays pending, or never updates `last_backup_at`. For product behaviour see [Backups](../day-to-day/backups.md).

## Failed status

- Open job detail + audit for rsync output.  
- Per-source `rc != 0` → overall failed; `last_backup_at` unchanged.  
- Path not allowed by policy?  
- Disk full on PiHerder `/backups` mount? **Status → View details**. A host set to send files straight out does not write that tree here. A full herder disk is not the cause of that job.

## Direct copy

A host that sends files straight to Drive, OneDrive, or the NAS reads the original files in place. It does not make a second copy on the Pi. The finished job and the audit row name that destination: Google Drive or OneDrive and the folder, or the LAN host, share, and path. Each source row adds the folder on that destination. The sign-in is not in either row.

- **Allow `/var/lib/piherder/rclone`** — the least-privilege script from **SSH access** does not yet include that path. Copy the script and apply it again. Root and Home Assistant OS do not need that line.
- **Permission denied** on a MySQL or Docker data directory — rclone ran as the backup user. The sudoers line above is missing, so the copy could not read as root. The pull still works, because that pull is `sudo rsync`.
- **The host cannot reach the destination** — the Pi itself needs a route to Google, Microsoft, or the NAS. The herder reaching the Pi is not enough.
- **Select a destination** — **Configure** has the straight-out tick on and no saved destination is ticked. Pick Google Drive, OneDrive, or the LAN share, then save.
- **sizes differ** on a live log, including a `.partial` log — the file grew while it was being sent. The copy sends the size it had when the transfer started and continues. A size mismatch on a file that is not changing is still a failed copy.
- Restore of that host from Drive, OneDrive, or the NAS is not available. Restore still reads a tree already on this PiHerder.

## Vanished files / busy sources

**v1.1.0 known issue** ([RELEASE_v1.1.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.1.0.md#known-issues-ship-with-awareness) **KI-rsync-vanished**). **v1.2.0** retries and can soft-OK vanished files (below).

On a busy host, files under the backup source can **disappear or move while rsync is running**. Typical examples:

- **Frigate** (and similar NVR/media stacks): recordings are written, indexed, rotated, or deleted continuously  
- App caches, temp segments, or log rotation under a large docker tree  

rsync then reports **code 24** (vanished files) and/or **code 23** (partial transfer with “vanished” in the log).

### v1.2 B-retry (default behaviour)

| Step | What PiHerder does |
|------|---------------------|
| Detect | Exit **24**, or **23** with `vanished` in stderr |
| Retry | Default **1** extra attempt after a short delay (`PIHERDER_BACKUP_VANISHED_RETRIES`) |
| Soft success | If still vanished after retries: source is **soft-OK** (job can succeed) with a **warning** — not a hard fail (`PIHERDER_BACKUP_VANISHED_SOFT_OK=true`) |

Other failures (permission, I/O, real partial transfer without vanished) still **fail** the source.

```bash
# Defaults (compose / env)
PIHERDER_BACKUP_VANISHED_RETRIES=1
PIHERDER_BACKUP_VANISHED_RETRY_DELAY_SEC=5
PIHERDER_BACKUP_VANISHED_SOFT_OK=true   # set false for strict hard-fail on vanished
```

**Still recommended**

1. Split sources: stable config/DB vs high-churn recording dirs.  
2. Re-run off-peak when you need a tighter snapshot.  
3. If the error is **`Input/output error`** or a mount shows filesystem **`shutdown`**, fix the disk/mount first — that is not vanished-file churn (see [SSH/rsync](ssh-rsync.md#backups-rsync-code-23-partial-transfer)).

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
