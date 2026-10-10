# PiHerder v1.11.0

**8 October 2026.** Package stays **1.10.0** on this freeze. Hub tags stay `1.10.0` · `1.10` · `latest`.

A passkey counts as a second factor when Force 2FA is on. A host can send its own files straight to Google Drive, OneDrive, or a LAN share. An agent can start a Move, read or start a LAN Discovery scan, and tidy the devices that scan found. Docker can show a newer git tag on a project that is already on the host.

About and `/api/v1` still report package **1.10.0**. The straight-out tick and **Repository…** are how you tell this train is running.

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

### Fleet health

Host backup schedules and destination copy schedules use the Settings timezone, the same clock as the label under the field. A stored hour 6 that was firing two hours later in Johannesburg now fires at 06:00 there.

A host-facts refresh that cannot SSH keeps the previous OS and kernel. A facts row left pending or running after its task is gone is failed, and the host can refresh again. Drive or OneDrive rejecting the sign-in opens one alert on Settings → PiHerder backup. A backup-enabled host with no backup inside the stale window opens one warning. A later success resolves it.

The Docker page on a host with Docker off says off and does not SSH. A direct host is copied only to the destinations it has ticked, so a Drive schedule does not fail that host with nothing to send. A deploy retries once when a stopped container from the same project holds the name. An apt update that exits 100 keeps the `E:` line in the job log.

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
