# Updates & patching

Two layers: **check** (safe, detect only) and **apply** (real upgrades). Silent auto-upgrade is **never** the default.

## Feature flags

| Feature | Edit → Features |
|---------|-----------------|
| OS packages | OS patch |
| Container images | Docker / containers |

## Update checks (safe)

Configured under **Edit → Schedules**.

| Schedule | Does | Does **not** |
|----------|------|----------------|
| **OS packages (apt)** | Count ready packages, phased count, reboot-pending | Run upgrade |
| **Container images** | Pull/compare image IDs per compose project | `compose up -d` |

Results feed the dashboard, badges, and notifications.

## Patch apply (opt-in)

Off by default. Requires the matching feature flag.

| Option | Behaviour |
|--------|-----------|
| Enable scheduled apply | Registers APScheduler job |
| Only when last check found updates | Skips if last check count is `0` |
| OS: full-upgrade | Uses `full-upgrade` instead of `upgrade` (+ update + autoremove) |
| Cron | e.g. weekly Sunday `30 3 * * 0` |

Also skipped when a job of the same type is already **pending/running** on that server.

Scheduled work is audited as **system / scheduler**.

### Manual apply

- Server list / detail: update / **upgrade XOR full-upgrade** / autoremove.  
- Live progress modal streams apt output.  
- Ubuntu **phased** packages counted separately (listed vs installable).  
- Container patch: `compose pull` + conditional `up -d` with live logs.

### One active job per host (no double-run)

For a given server, PiHerder allows **at most one** active job of each exclusive type:

| Type | Meaning |
|------|---------|
| `os_patch` / `container_patch` | Apply |
| `os_update_check` / `container_update_check` | Check-only |

A second trigger (double-click, concurrent bulk, scheduler overlap) **does not** start a second run. The UI attaches to the existing job; the API returns **HTTP 409** with the existing `job_id`.

!!! note "Celery workers vs container jobs"
    **Celery multi-slot concurrency** (`CELERY_CONCURRENCY`, default 2) applies to **backups** only. OS/container patch and update checks run on the **web** process (BackgroundTasks / thread pools). Scaling Celery workers does not re-execute a container job twice. See [Multi-worker](../operations/multi-worker.md).

### Docker: Check updates vs Deploy

| UI action | Meaning |
|-----------|---------|
| **Check updates** | Pull / compare only |
| **Deploy** | Pull + `up -d` — surfaces pull/up results (not silent success) |

Successful Deploy clears pending stack badges and resolves `container_updates` when none remain.

## Bulk actions (Servers list)

On **Servers** (`/servers`):

1. Tick one or more host checkboxes (or **Select all visible**).  
2. Use the bulk bar:

| Action | Requires feature on host |
|--------|---------------------------|
| **Check OS** | OS patch enabled |
| **Upgrade OS** | OS patch enabled |
| **Check containers** | Docker / containers enabled |
| **Patch containers** | Docker / containers enabled |
| **Backup** | Backups enabled |

Hosts without the matching feature flag are **skipped** (not failed). Confirm dialog shows which hosts will run. Progress is on **Jobs**; a banner summarises started / skipped / failed.

Bulk does not bypass exclusive-job rules: if a host already has that job type running, it is skipped as already active.

## Reboot

Least-priv sudoers may allow `/usr/sbin/reboot` (and common alternate paths). PiHerder:

1. Schedules reboot in the background (`sleep 1` then reboot) so the SSH command returns quickly.  
2. Closes SSH with a short timeout (hosts dying mid-session no longer hang the request).  
3. Clears local `reboot_pending` after a successful send so the UI does not stick.

This matters most when rebooting the **same host that runs PiHerder** — the stack goes down moments later; the HTTP response and audit row should already be finished.
