# Backup destinations — Google Drive copy

**Status:** Google Drive copy **shipped** in **v1.8.0** (2026-10-01). LAN NAS / SMB is selectable beside Drive as of **v1.9.0**, and Settings can remove one provider. **Copy now** on a dedicated NAS, a selectable OneDrive hop, and a copy of the herder self-backup are Must on the **v1.10** train. Not built yet. Package stays **1.9.0** until that freeze.  
**Train:** [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §3 (path A) · [PLAN_v1.9.0.md](PLAN_v1.9.0.md) · [PLAN_v1.10.0.md](PLAN_v1.10.0.md).  
**Not this product:** the instance self-backup (pg_dump / `/herder_backups` archives). The copy section sits under that same Settings tab. Restore stays the local tree.

## What stays

`run_backup` still pulls chosen remote directories over SSH rsync into `/backups/{host}/{dest_name}`. The remote only needs SSH and `rsync`. Path policy is unchanged. There is no typed exclude field and no client on the Pi.

## The copy

A **backup destination** is its own row, aimed at the whole backup drive (`BACKUP_ROOT`). It is not a checkbox on one host. The page is an extra section on **Settings → PiHerder backup**, under the instance self-backup. The running copy is a row on **Jobs**.

| Piece | Lock |
|-------|------|
| Provider | Google Drive (shipped **1.8.0**) and **LAN NAS / SMB** (selectable on the **v1.9** train) are each their own second hop. Both can be saved and live at once. OneDrive is in the list and cannot be selected (discovery only). No plugin framework. |
| Secret | Fernet (`PIHERDER_MASTER_KEY`). The operator creates a Google web OAuth client and pastes its ID and secret. **Connect Google** stores a refresh token. A blank secret keeps a saved one. The sign-in is not shown again. A service account is not the upload account. |
| When | The same schedule presets as the rest of Settings, an on-demand button, and an optional follow-up after one host backup finishes. The follow-up copies only checked paths under that host folder. |
| Failure | The copy job fails. `last_backup_at` and the rsync job stay as they were. |
| Job | `backup_replicate` on the existing `celery-worker` default queue. Web only enqueues. Not a dedicated container (that pattern is nmap, which needs host networking). A long upload uses one concurrency slot, the same as a long rsync. The task may run for 7 days. It is acknowledged on receive, so the 1-hour Redis redelivery does not start a second rclone. Other tasks still use the 2-hour hard limit, and Redis waits 3 hours before redelivering one of those. If the worker process dies, the job row is failed. Full sync has `server_id` null. Not in `JOB_FEATURE_KEY`, so MCP and the token API cannot start it. |
| Demo | Refuses. No upload. |
| Tool | `rclone` **1.68.2** in the herder image (amd64 and arm64). Temp config `0600`, deleted after the run. The refresh token and client secret are not logged. Rebuild the image before a real upload. |
| Schema | Table `backup_destination`. Alembic **047**. |

## Selection

The page matches the host file manager and is read-only: folder tree on the left, the open folder as rows, a checkbox on each row, select-all for that directory. A checked folder stays checked when you open it. No upload, edit, rename, or delete.

- Tick a folder to take that folder and everything in it. A large directory is not listed file by file to do that.
- Open the folder to leave something behind. Select all, then untick files or child folders. Those unticked rows are the only exceptions.
- Stored as real relative paths (`checked` and `skipped`). The worker turns skipped children into rclone `--exclude`. Nobody types a pattern.

`rclone sync` uses `--drive-use-trash`. A checked file is copied on its own.

The copy signs in as the operator’s Google account. PiHerder runs the redirect and stores the refresh token. New files are owned by that account and use its Drive quota. A service account is not the upload account: it has no Drive quota, so a personal My Drive folder rejects the file bytes. **Test** lists the folder and does not copy. Restore does not read Drive.

## Out

OneDrive is listed and cannot be selected. Path C (a client on each Pi), restic, borg, kopia, rclone crypt, restoring from Drive or SMB, and copying the herder self-backup stay out. Removing a destination does not delete remote files.

## LAN NAS / SMB (v1.9 path A)

**Status:** Built on `v1.9.0-dev`. Not a version bump. Selectable. Not discovery-only.

Same card, same job `backup_replicate`, same checked/skipped tree. The operator picks **LAN NAS / SMB** and saves one share: host, share, optional path, username, password, optional domain or workgroup. Username and password both empty is a guest share. A blank password with a username keeps the saved password. Username without a password, or a password without a username, is refused. Kerberos is not built. The Settings list shows each saved Drive and SMB destination on its own row.

The username and password are Fernet (`PIHERDER_MASTER_KEY`). The password is not written to the job log. rclone runs from a temp config, mode `0600`, deleted after the run. **Test** is `rclone lsd` (list only). The public demo does not upload. The job is not on the token or MCP list. A failed copy fails that job only.

Drive and SMB are independent. Saving one does not replace the other. Each row has its own secret and its own schedule. Path B (mount the share as `backup_dest_root`) stays out. The local rsync directory stays the default.

## Remove one destination (v1.9, #25)

**Status:** Built on `v1.9.0-dev`. Settings only.

Admin, Settings → PiHerder backup, `confirm=remove`. That deletes one provider's `backup_destination` row and the Fernet secret on it. The other provider's row stays. Files already on Drive or on the SMB share are kept. Demo refuses. There is no job type, no token route, and no MCP tool.
