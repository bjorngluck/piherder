# Backup destinations — Google Drive copy

**Status:** Locked 2026-09-28. First slice is the Drive copy below.  
**Train:** [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §3 (path A).  
**Not this product:** Settings → PiHerder backup (`/herder_backups`). Restore stays the local tree.

## What stays

`run_backup` still pulls chosen remote directories over SSH rsync into `/backups/{host}/{dest_name}`. The remote only needs SSH and `rsync`. Path policy is unchanged. There is no typed exclude field and no client on the Pi.

## The copy

A **backup destination** is its own row, aimed at the whole backup drive (`BACKUP_ROOT`). It is not a checkbox on one host.

| Piece | Lock |
|-------|------|
| Provider | `drive` now. `onedrive` and `smb` are later rows of the same table. No plugin framework. |
| Secret | Fernet (`PIHERDER_MASTER_KEY`). Drive stores the rclone OAuth token JSON. The UI never shows it again. Empty save keeps the previous token. |
| When | Its own cron, an on-demand button, and an optional follow-up after one host backup finishes. The follow-up copies only checked paths under that host folder. |
| Failure | The copy job fails. `last_backup_at` and the rsync job stay as they were. |
| Job | `backup_replicate`. Full sync has `server_id` null. Not in `JOB_FEATURE_KEY`, so MCP and the token API cannot start it. |
| Demo | Refuses. No upload. |
| Tool | `rclone` in the herder image. Temp config `0600`, deleted after the run. Token is not logged. |

## Selection

The page matches the host file manager and is read-only: folder tree, current directory as rows, a checkbox on each row, select-all for that directory. No upload, edit, rename, or delete.

- Tick a folder to take that folder and everything in it. A large directory is not listed file by file to do that.
- Open the folder to leave something behind. Select all, then untick files or child folders. Those unticked rows are the only exceptions.
- Stored as real relative paths (`checked` and `skipped`). The worker turns skipped children into rclone `--exclude`. Nobody types a pattern.

`rclone sync` uses `--drive-use-trash`. A checked file is copied on its own.

## Out

OneDrive, SMB mount, path C (a client on each Pi), restic, borg, kopia, rclone crypt, restoring from Drive, copying the herder self-backup.
