# Backup destinations — Google Drive copy

**Status:** Built on `v1.8.0-dev` 2026-09-29. Walk still open. May slip the tag.  
**Train:** [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §3 (path A).  
**Not this product:** the instance self-backup (pg_dump / `/herder_backups` archives). The Drive section sits under that same Settings tab. Restore stays the local tree.

## What stays

`run_backup` still pulls chosen remote directories over SSH rsync into `/backups/{host}/{dest_name}`. The remote only needs SSH and `rsync`. Path policy is unchanged. There is no typed exclude field and no client on the Pi.

## The copy

A **backup destination** is its own row, aimed at the whole backup drive (`BACKUP_ROOT`). It is not a checkbox on one host. The page is an extra section on **Settings → PiHerder backup**, under the instance self-backup. The running copy is a row on **Jobs**.

| Piece | Lock |
|-------|------|
| Provider | The service list shows Google Drive, which is the only one that can be saved. OneDrive and LAN NAS / SMB are in that list and cannot be selected yet. No plugin framework. |
| Secret | Fernet (`PIHERDER_MASTER_KEY`). The form asks for the service account email and private key, and links the Google Cloud pages plus the operator steps. The key is not shown again. A blank key keeps the saved one. |
| When | The same schedule presets as the rest of Settings, an on-demand button, and an optional follow-up after one host backup finishes. The follow-up copies only checked paths under that host folder. |
| Failure | The copy job fails. `last_backup_at` and the rsync job stay as they were. |
| Job | `backup_replicate` on the existing `celery-worker` default queue. Web only enqueues. Not a dedicated container (that pattern is nmap, which needs host networking). A long upload uses one concurrency slot, the same as a long rsync. Full sync has `server_id` null. Not in `JOB_FEATURE_KEY`, so MCP and the token API cannot start it. |
| Demo | Refuses. No upload. |
| Tool | `rclone` **1.68.2** in the herder image (amd64 and arm64). Temp config `0600`, deleted after the run. The private key is not logged. Rebuild the image before a real upload. |
| Schema | Table `backup_destination`. Alembic **047**. |

## Selection

The page matches the host file manager and is read-only: folder tree on the left, the open folder as rows, a checkbox on each row, select-all for that directory. A checked folder stays checked when you open it. No upload, edit, rename, or delete.

- Tick a folder to take that folder and everything in it. A large directory is not listed file by file to do that.
- Open the folder to leave something behind. Select all, then untick files or child folders. Those unticked rows are the only exceptions.
- Stored as real relative paths (`checked` and `skipped`). The worker turns skipped children into rclone `--exclude`. Nobody types a pattern.

`rclone sync` uses `--drive-use-trash`. A checked file is copied on its own.

The service account’s Drive folder can be marked **shared with this email**. That sets rclone `shared_with_me`. Restore does not read Drive.

## Out

OneDrive and SMB are listed and cannot be selected. Path C (a client on each Pi), restic, borg, kopia, rclone crypt, restoring from Drive, and copying the herder self-backup stay out.
