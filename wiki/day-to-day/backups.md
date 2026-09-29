# Backups & restore

## What this is

**Server backups** copy chosen directories from a fleet host into the PiHerder host’s backup volume using **rsync over SSH**. Runs are **jobs** on **Celery** (not inside a web request), so a long rsync does not freeze the UI.

## Why it exists

Homelab hosts hold compose data, configs, and media that are painful to rebuild. PiHerder makes backup **repeatable, scheduled, audited, and restorable** without maintaining a separate cron library on every Pi. You choose **paths** and **policy**; the control plane handles queueing and history.

## What it is *not*

| Server backups (this page) | PiHerder self-backup |
|----------------------------|----------------------|
| Files from **fleet** hosts | Config of the **herder** itself (DB, users, keys…) |
| Server → **Backups** UI | Settings → **PiHerder backup** |
| Volume `/backups` (or host map) | Volume `/herder_backups` |

See [Self-backup & DR](../operations/self-backup.md) for the control plane.

### Copy to Google Drive (v1.8 train)

Not in the published **1.7.0** image. On branch `v1.8.0-dev`, **Settings → PiHerder backup** has a section under the self-backup cards.

| | |
|--|--|
| What it copies | Checked folders on the herder backup drive (`/backups`), not one host’s source list |
| Service | **Google Drive** can be saved. **OneDrive** and **LAN NAS / SMB** are in the list and cannot be selected |
| Account | Service account email and private key. The key is not shown again. Leave it blank to keep the saved one |
| Schedule | The same presets as other schedules, **Copy now**, and an optional copy after a host backup succeeds |
| Browser | Folder tree on the left, the open folder on the right. A ticked folder stays ticked inside. Untick a child to leave it behind |
| Job | **Drive copy** (`backup_replicate`) on the existing Celery worker. A failure fails that job only. The host backup time stays |
| Restore | Still the local tree. The demo does not upload |

Rebuild the image before a real copy. rclone is in that image. Design: [FEATURE_PLAN_BACKUP_DESTINATIONS.md](https://github.com/bjorngluck/piherder/blob/v1.8.0-dev/docs/FEATURE_PLAN_BACKUP_DESTINATIONS.md).

## Set up Google Drive

**Where in PiHerder:** Settings → **PiHerder backup** → **Copy the backup drive**. Pick **Google Drive**. OneDrive and a LAN share are listed and cannot be selected yet.

PiHerder does not sign in as you. It uses a **Google service account**. You create that in Google Cloud, then paste its email and private key. Share one Drive folder with that email.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select a project, or [create one](https://console.cloud.google.com/projectcreate).
2. [Enable the Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com) on that project.
3. Open [Service accounts](https://console.cloud.google.com/iam-admin/serviceaccounts). Create an account. On it, **Keys → Add key → Create new key → JSON**. Download the file. You do not need to grant the account a project role.
4. From that JSON file, copy `client_email` into **Email**. Copy `private_key` into **Private key**, including the `BEGIN PRIVATE KEY` and `END PRIVATE KEY` lines. Do not paste the whole file. Save. The key is not shown again; leave the box blank later to keep it.
5. In [Google Drive](https://drive.google.com/drive/my-drive), create a folder (or use one you already have). Share it with the service account email as **Editor**. The name you type in **Folder on Drive** must be that folder’s name. Tick **The Drive folder is shared with this email**.
6. In the folder tree, tick the backup folders to copy. **Copy now**, or set a schedule. A failed copy fails that job only. Restore still uses the copies on this PiHerder.

**Done when:** the Drive copy job succeeds, and the shared folder on Google Drive contains those trees.

---

## End-to-end: first backup you trust

1. On the server: **Edit → Features** → enable **Backups**.  
2. Open **Backups** → add at least one **source path** that exists on the remote (e.g. a compose data directory).  
3. Confirm path policy is not blocking that prefix.  
4. **Run backup** (manual) once.  
5. Watch [Jobs](jobs-audit-notifications.md) until **success**; open Audit for size summary.  
6. On the herder host, confirm files under the backup volume ([Volumes](../operations/volumes.md)).  
7. Restore wizard → **dry-run** for that source (no write).  
8. Only then enable a **cron schedule**.

**Done when:** success updates `last_backup_at`; you know how to reverse with dry-run first.

Full journey: [Operator scenarios — Journey B](../getting-started/operator-scenarios.md#journey-b).

### Busy sources (vanished files)

On Frigate/NVR-style trees, rsync may hit **code 24** (vanished files). From **v1.2**, PiHerder **retries** and can mark the source **soft-OK** instead of failing the whole job. Details: [Troubleshooting — vanished files](../troubleshooting/backups.md#vanished-files-busy-sources).

---

## Enable backups

1. Server **Edit → Features** → enable **Backups**.  
2. Open the server’s **Backups** page (ops-hero + source cards — same width as other host pages).  
3. Use header **Configure** for schedule, destination paths, and sources (there is no second “Full configure” under sources).  
4. Add **source paths** on the remote host (configure form lists current sources; empty only when none are set).  
5. Optional: destination override, retention, cron schedule (UI shows **plain English** next to the expression, e.g. “Daily at 02:00”).  
6. **Path allow/deny** — default deny of OS roots; optional prefixes.

<figure class="ph-figure" markdown>
  ![Backups page](../assets/screenshots/backups-page.png)
  <figcaption>Sources, policy, schedule, restore wizard.</figcaption>
</figure>

## How success is decided

| Outcome | Meaning |
|---------|---------|
| Success | Each source finishes with `rc == 0` and no error classification; `last_backup_at` updates |
| Failed | Status **failed**, audit error details, **`last_backup_at` not updated** |
| After success | Open `backup_failed` notifications resolve |

### rsync path

- Default: `--rsync-path "sudo -n rsync"` (or local sudo) so a least-priv user can still read protected trees.  
- **Root user / HAOS:** plain `rsync` is auto-probed and used when sudo is not available.

## Schedules

Enable + cron on the Backups page. Same server never runs two backups at once ([Redis mutex](../operations/multi-worker.md)); different hosts can run in parallel.

**Why a mutex:** overlapping rsync to the same destination corrupts snapshots and confuses retention.

From the **Servers** list you can multi-select hosts and run **Backup** in bulk (only hosts with backups enabled) — [Bulk actions](updates-and-patching.md#bulk-actions-servers-list).

## Restore wizard

**Why dry-run first:** restore **writes onto the remote host**. A preview reverse rsync shows what would change without committing.

1. Backups page → restore for a source.  
2. **Dry-run** reverse rsync (preview).  
3. Confirm to apply.  
4. Path policy enforced; audit action `backup_restore`.

!!! warning "Restore is privileged"
    You are writing back onto the remote host. Prefer dry-run first. Prefer restoring to a test path when learning.

## Retention

Retention cleanup is a separate job type (`retention`) driven by configured keep rules. **Why separate:** deleting old trees is independent of “did tonight’s rsync succeed?”

## Troubleshooting

[Backups stuck or failing](../troubleshooting/backups.md)

Success/fail over time and dest-size growth: [Reports](reports.md).

Named-volume copy during [Move a service](../docker/service-migration.md) uses the same Mountpoint rsync privilege as backing up `/var/lib/docker/volumes`. That is a Job, not a backup restore.
