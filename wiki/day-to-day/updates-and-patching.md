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

### Docker: Check updates vs Deploy

| UI action | Meaning |
|-----------|---------|
| **Check updates** | Pull / compare only |
| **Deploy** | Pull + `up -d` — surfaces pull/up results (not silent success) |

Successful Deploy clears pending stack badges and resolves `container_updates` when none remain.

## Reboot

Least-priv sudoers may allow `/usr/sbin/reboot`. After a successful reboot command, PiHerder clears `reboot_pending` so the UI does not stick.
