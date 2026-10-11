# PiHerder v1.11.0

**11 October 2026.** Draft notes for this train. About, `/api/v1`, and hosted MCP report **1.11.0-dev**. Hub tags stay `1.10.0` · `1.10` · `latest`.

A passkey counts as a second factor when Force 2FA is on. A host can send its own files straight to Google Drive, OneDrive, or a LAN share. An agent can start a Move, read or start a LAN Discovery scan, and tidy the devices that scan found. Docker can show a newer git tag on a project that is already on the host.

About and `/api/v1` report **1.11.0-dev**. The straight-out tick and **Repository…** are how you tell this train is running.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.10.0` · `1.10` · `latest` (amd64 + arm64). Pin `1.9.0` / `1.9` stays the previous image. Home Assistant plugin stays **0.5.0**. Adapter **[0.4.2](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.2)** is on PyPI. `uvx piherder-mcp` installs it. The public demo stays the production image.

Operator how-to: [Account security](https://piherder-docs.hacknow.info/account-security/two-factor/) · [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/) · [Docker](https://piherder-docs.hacknow.info/docker/overview/) · [LAN Discovery](https://piherder-docs.hacknow.info/integrations/lan-discovery/). Technical record: [PLAN_v1.11.0](PLAN_v1.11.0.md). Maintainer QA: [QA_v1.11.0](QA_v1.11.0.md).

---

## What’s new

### A passkey counts as 2FA

Force 2FA no longer demands an authenticator app when a passkey is registered. Signup with Force 2FA on can register a passkey, and that passkey satisfies the enroll wall. The account page and the Users list treat the passkey as a second factor. App-based 2FA stays available.

### A host can send files straight to the copy

On a host, Backups → Configure can turn on **Send this host's files straight to the copy**. None of the saved destinations start selected. Save refuses the tick when none are selected. With no destination saved in Settings, the tick stays unavailable.

**Backup now** and the host schedule send the original files to each ticked destination. Nothing from that host is written under `/backups` first, and the host does not keep a second copy. rclone reads those files in place. When the backup user is not root, the herder binary is installed for that run at `/var/lib/piherder/rclone` and removed when the run ends, along with the temp config. Mode, owner, and modification time stay. A file that grows during the copy, such as a live log, is sent at the size first seen and does not fail the run.

The host must use the same CPU architecture as this PiHerder. This PiHerder copies its own rclone onto the host. On a mixed fleet, an armv7 Pi or an x86_64 machine fails that copy when this PiHerder is aarch64. Leave the tick off on those hosts and keep the `/backups` path.

Hosts that leave the tick off still rsync onto `/backups`. The herder copy of that mirror is unchanged. **Copy now** on one destination starts that destination only. Jobs labels a straight-out hop **Direct copy**. A folder hop stays **Backup copy**. **Backup now** on the host stays a **Backup** job, and the finished row names the destination. The sign-in is not in the job or the audit row. When a destination ticks both a folder on `/backups` and a direct host, a failed folder hop still starts the direct hop. If that folder row stays **running**, that tick waits for the next schedule.

Restore of that host still reads a tree on this PiHerder. Drive, OneDrive, and the NAS are not a restore source.

Any host whose SSH user is not root needs the sudoers script copied again from SSH access and applied, so the backup user may run `/var/lib/piherder/rclone`.

### An agent can start a Move and a LAN scan

Hosted `POST /mcp` can start a stop-first Move with `start_move`. `confirm` must be true. The source stack is left stopped. There is no undo. `trigger_job` still refuses `service_migrate`.

`read_discovery` reads saved ranges and recent scans. The short device list stops at 50 rows and includes the total. Page the rest with `list_discovery_devices`. `start_discovery` scans those saved ranges. The agent does not choose the targets. Vulnerability scripts stay off. A token that sets any `feature:*` scope cannot start a scan. A `jobs` token with no `feature:*` scope can.

`list_discovery_devices` pages devices. An agent with `edit` can rename a device, mark it known, new, or ignored, link it to a fleet server, and purge one device or the offline rows. A linked device cannot be purged. `scan_discovery_device` scans one device that already sits inside the saved ranges. Vulnerability scripts stay off.

Adapter **0.4.2** lists these tools and sends `confirm=true` on the two purge calls. **0.4.1** lists the tools and omits that query, so purge returns 400 on this train. A machine on **0.4.0** does not send the device tools. Run `uvx --refresh piherder-mcp`, then restart the MCP client so it lists 27 tools. A client that is still running keeps the old tool list. If uv does not see **0.4.2** yet, run `uvx --no-cache --refresh piherder-mcp` and restart the client again. `read_discovery`, `start_discovery`, and the device tools need this train's herder. `start_move` also works on a 1.10.0 herder.

### Docker can see a newer git tag

On a project that is already on the host, Docker → ⋯ → **Repository…** reads the checkout. The herder can read a folder it does not own. The page says **Update available** when the remote branch has commits this checkout does not, and it names any tag ahead of the checkout.

You pick the branch or a tag. The remote default, `main` or `master`, starts selected. **Update checkout** moves the files. If a tracked file differs, or the checkout has commits the remote does not, the update stops until you **Keep the local files** or **Take the remote copies**. Untracked files stay. The stack is not deployed. Deploy is still the separate action.

A project that is not a git checkout yet can be attached to a remote from the same page. New Docker Service can still clone a URL once and then write the pasted compose over it.

### Backups on for the fleet now gives every host a schedule

Hosts could previously be enabled with no schedule and never back up. Settings → Fleet has one base backup time, daily at 02:00 unless you change it. **Apply to all servers**, with **Enable Backups** ticked, turns backups on. It does not write a schedule onto the host. Adding a host does not turn backups on and does not write a schedule.

A host with backups on and no saved schedule inherits the fleet time. The Servers list and the Backups page mark that time **fleet default**. A saved schedule on the host always wins. Backups off means no job. Saving a new base clock moves the inheriting hosts, whether or not Apply is ticked. A schedule that was already exactly the fleet minute for that host is cleared once at startup, so the host inherits the same minute. Any other saved schedule is left alone.

The fleet clock is one time. On a daily base, each host gets its own minute from its id. Ids below 60 stay on that hour. Later ids use the following hours and stop at 23:00. They do not wrap to the morning of the same day. A 02:00 base therefore has 1320 distinct minutes, which covers a fleet of hundreds or a thousand hosts. Past that window, a few hosts share a minute. The worker queues them. One host still does not run two backups at once. A list such as `2,14`, a step such as `*/6`, or a weekday base only changes the minute, so hosts 60 apart can share that minute. Placing one host does not read the rest of the fleet, and adding a host does not move anyone else's time.

The server API adds `backup_schedule` (the saved value, or null), `effective_backup_schedule`, `backup_schedule_source` (`host` or `fleet`), `next_backup_run`, and `backup_scheduled`. `next_backup_run` is the live job's next run. When backups are on and no job is registered, `next_backup_run` is null and `backup_scheduled` is false. Older clients can ignore the new keys.

A weekday range such as `1-5`, or a day list such as `1,15`, stays as typed. Configure switches that expression to Custom instead of saving a different cron. Saving Fleet defaults records the old and new base clock and flags, plus the applied counts. The fleet source-list save records the old and new source list and destination root.

---

## Bug fixes

| What you saw | What it does now |
|---|---|
| A passkey did not count as 2FA when Force 2FA was on | A registered passkey satisfies Force 2FA. Signup can register one. App-based 2FA stays available |
| A backup set for hour 6 ran two hours later than the label | Host backups and destination copies use the Settings timezone, the same clock as the label |
| Enable Backups on the fleet turned the flag on and left the schedule empty, so the host never backed up | Apply turns backups on and does not write a schedule. A host with no saved schedule runs on the fleet time. The list marks it **fleet default**. A saved schedule stays |
| Saving Configure changed a minute such as 2 to 0, a weekday range, or a day list | A simple minute is kept. A range or a list stays as typed. Opening Configure on an empty schedule does not fill one in |
| A cron backup was not marked scheduled, and Backup now said it was scheduled | The cron run is marked scheduled. Backup now is not |
| A backup configuration change did not say which field moved | The audit row records the old and new values, including the schedule |
| Changing the backup flag through the token API left the old cron in place | That change re-registers the host cron after the save. The server payload includes the effective schedule, its source, whether a job is registered, and that job's next run |
| A failed host-facts refresh cleared the OS and kernel lines | The previous OS and kernel stay |
| A host-facts job left running blocked the next snapshot | That job fails after 30 minutes, and the next snapshot can queue |
| Drive or OneDrive rejecting the sign-in was only a failed job | One alert opens on Settings → PiHerder backup. Severity follows the backup category. A later success resolves it |
| A backup-enabled host could go quiet with no warning | One warning opens when the last backup is outside the stale window. A later success resolves it. If the host has no schedule, the warning says nothing is queued |
| Docker off still contacted the host on refresh | The Docker page says off and does not SSH |
| A direct host was sent on every destination schedule | A destination schedule includes that host only when that destination is ticked. An empty push is a skip, not a failed job |
| Deploy failed because a stopped container from the same project still held the name | That stopped container is removed and the deploy retries once. A container from another project is left in place |
| An apt update that exited 100 stored no `E:` line | The job log keeps the `E:` line |
| The Settings tab read PiHerderbackup | The tab reads PiHerder backup |
| Status did not say where hosted MCP runs | The Web card says hosted MCP is this same process, at POST /mcp |
| Repository did not mark a newer tag | The page names a tag ahead of the checkout |
| A failed folder hop did not start the direct hop | The direct hop still starts. A folder row left running waits for the next schedule |
| A token limited to one feature could start a LAN scan | A scan needs a jobs token with no `feature:*` limit |
| An https git URL with a user or token in it was accepted | That URL is refused. Use a deploy key or a credential helper |

---

## Defaults

| | Default |
|--|---------|
| Force 2FA | unchanged. A passkey counts when one is registered |
| Send this host's files straight to the copy | off |
| Direct destinations | none selected until you tick them |
| Move a service | **on**. Set `PIHERDER_SERVICE_MIGRATE=false` to hide it |
| Agents (MCP) | off until you paste a token or approve a browser sign-in |
| Repository update | does not deploy |
| Fleet backup time | daily at 02:00. A host with no saved schedule inherits it. Ids below 60 stay on that hour. Later ids use the following hours through 23:00. A list, a step, or a weekday base only changes the minute. Apply does not copy the time onto the host |

---

## Upgrade from 1.10.0

Two database revisions add the straight-out tick and the chosen destinations. The image applies them on startup.

1. Take a full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. Pull the image for this train when it is published. Hub tags today are `1.10.0` · `1.10` · `latest`.
3. `docker compose pull && docker compose up -d`. Recreate **web** and **celery-worker**. The app code is not a folder on the host.
4. On any host whose SSH user is not root and that will send files straight out, copy the sudoers script from SSH access and apply it again. It lets the backup user run `/var/lib/piherder/rclone`. No destination sign-in is stored on the host. The temp config lives only for the run.
5. `uvx --refresh piherder-mcp` if an agent should see the device tools, then restart the MCP client so it lists 27 tools. **0.4.2** sends `confirm=true` on purge. If uv does not see **0.4.2** yet, run `uvx --no-cache --refresh piherder-mcp` and restart the client again.

Rolling back means restoring the DR self-backup from step 1. Pinning the image at `1.10.0` does not undo the two database revisions.

The public demo is not this train.

---

## Not in this release

- Creating or editing Nginx Proxy Manager proxy hosts. Move can still retarget a backend.
- Restore from Drive, OneDrive, or a LAN share.
- An audit row for every API or MCP read.
- Hiding the 15-minute host-facts snapshot from the default Jobs and Audit lists.
- A repo check inside the Docker **Check updates** job. The Repository page is the check.
- Greying out the straight-out tick when the host CPU does not match this PiHerder. The run names that mismatch. A note on the v1.12 plan.
- An end-of-life badge on the host OS line.
- Merging two LAN Discovery rows when a laptop changes MAC.
- A Docker prune job from the host page, the token API, and MCP.
- An image update check that says up to date after a pull, while the container still runs the old image.
