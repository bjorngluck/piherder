# Updates & patching

## What this is

PiHerder’s update system has two layers:

| Layer | Meaning |
|-------|---------|
| **Check** | Safe detection — count packages / HA components / compare images; **no** upgrade |
| **Apply** | Real change — apt upgrade, **`ha … update` on HAOS**, or compose pull + up |

You can run either **manually** or on a **schedule**. Silent auto-upgrade is **never** the default.

!!! tip "HAOS hosts"
    On **Home Assistant OS**, “OS patch” is labelled **HA updates** and uses the **`ha` CLI** (Core / OS / Supervisor), not apt. See [HAOS hosts](haos-hosts.md).

## Why it exists

Keeping a fleet patched without a shared process leads to “I forgot that Pi for six months.” Checks fill the dashboard **need attention** view; apply is deliberate so you choose the maintenance window. Live logs and exclusive jobs stop double-clicks from stacking conflicting upgrades.

## Feature flags

| Feature | Edit → Features | Why gated |
|---------|-----------------|-----------|
| OS packages / HA updates | OS patch | Debian: apt. **HAOS:** `ha` CLI (same flag; UI says HA updates) |
| Container images | Docker / containers | No Docker → no image checks; **leave off on HAOS** |

---

## End-to-end: one host, check then apply

1. Enable **OS patch** and/or **Docker** on the server.  
2. Run **Check OS** / **Check containers** (manual).  
3. Open [Jobs](jobs-audit-notifications.md) and confirm success; dashboard counts move.  
4. Read package/image results on the host.  
5. When ready, run **Upgrade** / **Patch containers** (or full-upgrade if you understand the extra packages).  
6. If reboot is required, use [Reboot](#reboot) after apply finishes.  
7. Only after a few manual cycles, enable **check** schedules; enable **apply** schedules later with “only when last check found updates.”

Full journey: [Operator scenarios — Journey C](../getting-started/operator-scenarios.md#journey-c).

---

## Update checks (safe)

Configured under **Edit → Schedules**.

| Schedule | Does | Does **not** |
|----------|------|----------------|
| **OS packages (apt)** | Count ready packages, phased count, reboot-pending | Run upgrade |
| **HA updates** (`os_type=haos`) | Count Core / OS / Supervisor with `update_available` | Run `ha … update` |
| **Container images** | Pull/compare image IDs per compose project | `compose up -d` |

Results feed the dashboard, badges, and notifications.

## Patch apply (opt-in)

Off by default. Requires the matching feature flag.

| Option | Behaviour | Why |
|--------|-----------|-----|
| Enable scheduled apply | Registers APScheduler job | Automate after you trust checks |
| Only when last check found updates | Skips if last check count is `0` | Avoid empty upgrade noise |
| OS: full-upgrade | Uses `full-upgrade` instead of `upgrade` (+ update + autoremove) | Opt-in broader package moves |
| Cron | e.g. weekly Sunday `30 3 * * 0` | Quiet maintenance window |

Also skipped when a job of the same type is already **pending/running** on that server.

Scheduled work is audited as **system / scheduler**.

### Manual apply

- **Debian/Ubuntu:** update / **upgrade XOR full-upgrade** / autoremove; live apt log; Ubuntu **phased** packages counted separately.  
- **HAOS:** refresh versions + apply available components (**supervisor → core → OS**); live CLI log; no apt / no full-upgrade.  
- Container patch: `compose pull` + conditional `up -d` with live logs (not for HAOS fleet).

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

**Why bulk:** patching ten hosts one-by-one is how fleets drift. Bulk queues the **same job type** across eligible hosts; feature flags and exclusive-job rules still apply.

On **Servers** (`/servers`):

1. Tick one or more host checkboxes (or **Select all visible**).  
2. Use the bulk bar (appears when something is selected):

| Action | Requires feature on host |
|--------|---------------------------|
| **Check OS** | OS patch enabled |
| **Upgrade OS** | OS patch enabled |
| **Check containers** | Docker / containers enabled |
| **Patch containers** | Docker / containers enabled |
| **Backup** | Backups enabled |

Per-host **⋯** menu: open host, backups, OS/container patch, Docker, settings (feature-gated). Status pills (OS packages, images, reboot, backup, optional Kuma/LAN chips) come from the **last stored check results** — the list does **not** open live SSH on every paint.

Hosts without the matching feature flag are **skipped** (not failed). Confirm dialog shows which hosts will run. Progress is on **Jobs**; a banner summarises started / skipped / failed.

Bulk does not bypass exclusive-job rules: if a host already has that job type running, it is skipped as already active.

## Reboot

Least-priv sudoers may allow `/usr/sbin/reboot` (and common alternate paths). PiHerder:

1. Schedules reboot in the background (`sleep 1` then reboot) so the SSH command returns quickly.  
2. Closes SSH with a short timeout (hosts dying mid-session no longer hang the request).  
3. Clears local `reboot_pending` after a successful send so the UI does not stick.

**Why this design:** rebooting the **same host that runs PiHerder** takes the stack down moments later; the HTTP response and audit row should already be finished.

## Related

- [Jobs, audit & notifications](jobs-audit-notifications.md)  
- [Docker overview](../docker/overview.md)  
- [Troubleshooting](../troubleshooting/index.md)  
