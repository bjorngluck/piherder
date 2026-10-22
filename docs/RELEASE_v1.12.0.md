# PiHerder v1.12.0

**22 October 2026.** Tag **[v1.12.0](https://github.com/bjorngluck/piherder/releases/tag/v1.12.0)**. Package **1.12.0**. Released.

About and `/api/v1` report **1.12.0**.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.12.0` · `1.12` · `latest` (amd64 + arm64). Manifest `sha256:3e0cf9c438b75500e64aebc5eb80fc89c640ae24bf18190ed2a950c40dd77f5e`. Pin `1.11.0` / `1.11` stays the previous image. Pins `1.10.0` / `1.10` and `1.9.0` / `1.9` stay valid. Home Assistant plugin **[0.5.2](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.2)**. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** is on PyPI. The public demo is being updated to this release.

The default Jobs and Audit lists leave routine noise out. Backup and image signals stay honest: a rejected copy sign-in stays critical, a running container keeps an image flagged until it is actually on that image, and an unsupported OS is named. A host can pull one backup folder back from Drive, OneDrive, or a LAN share. An admin can create, edit, and delete an NPM proxy host. Remote restore, NPM CRUD, and the read-only admin API were promoted from Discover.

Operator how-to stays on the wiki: [Jobs and audit](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/) · [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Alerts](https://piherder-docs.hacknow.info/operations/alerts-email-webhooks/) · [Docker](https://piherder-docs.hacknow.info/docker/overview/) · [NPM](https://piherder-docs.hacknow.info/integrations/npm/) · [API tokens](https://piherder-docs.hacknow.info/operations/api-tokens/). Technical record: [PLAN_v1.12.0](PLAN_v1.12.0.md). Maintainer QA: [QA_v1.12.0](QA_v1.12.0.md). Walks are signed 2026-10-20. Screenshots are captured and wired.

Home Assistant plugin is **[0.5.2](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.2)** (card and logo served from `/piherder/`, not copied into `config/www`). Tag `v0.5.2` is the plugin for this herder. It needs Home Assistant **2024.7** or newer. Adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** is tagged and on PyPI. It names the backup schedule fields and adds `prune_docker`. The GitHub adapter readme badges herder v1.12.0. The public demo is being updated to this release.

---

## What's new

### Quieter Jobs and Audit lists

A successful 15-minute host snapshot stays out of the default Jobs list and the default Audit list. A failed snapshot stays. **Show routine host snapshots** brings the successful rows back.

API and MCP reads write an audit row only when Settings, Security policy, **API and MCP read audit** is on. The row stores the token id and name, and the client IP. It does not store the token. Those rows stay off the default Audit list and keep their own retention (1 to 90 days, default 7). Turning the flag on or off writes its own row. Writes stay audited either way.

### Backup and image signals

Google Drive, OneDrive, and a LAN share each open one critical alert when the saved credential expires or the destination rejects it. Drive and OneDrive say reconnect. The backup category cannot lower it. The alert and the log line may carry the provider error code. They never carry the token, the rclone config, or the raw response body. A later successful copy resolves it. Publish the Google app to production. A Testing-mode app loses its token after 7 days. The copy still requests the full Drive scope, because `drive.file` cannot see a folder the operator created.

A failed self-backup alert includes the last successful self-backup time, or "never".

Creating a user, creating an API token, or changing force 2FA or template 2FA opens one warning. The body names the actor and the client IP. It does not include a password or a token.

An image stays flagged after a pull while any running container for that image is still on the previous id. The flag clears once every running container uses the id on disk. This is issue [#47](https://github.com/bjorngluck/piherder/issues/47), closed with this release.

Ubuntu, Debian, or Raspbian outside Ubuntu 22.04, 24.04, and 26.04 LTS, or Debian and Raspbian 12 and 13, shows **Unsupported** next to the OS line. HAOS is not in this check. The warning does not start an upgrade. This is issue [#49](https://github.com/bjorngluck/piherder/issues/49), closed with this release.

A saved container check cron registers while fleet container checks are on. Applying with that check off clears the cron. Scheduled container checks had stopped because Apply cleared the per-host flag and left the cron.

Repository refuses a folder or checkout the SSH user does not own, before git runs. `safe.directory` is not set.

### Direct copy, schedules, and restore

The image carries rclone for amd64 and arm64. A direct copy sends the one that matches the host. The straight-out tick is greyed out when host facts name another CPU. An unknown architecture stays available. A host the binary cannot run on keeps `/backups`.

A host backup or a container update check that does not start within 15 minutes of its slot opens one warning. The next successful run of that kind resolves it. Turning the schedule off resolves it too. `/metrics` exposes `piherder_schedule_missed`.

A destination copy scheduled earlier the same day than a ticked host backup opens one warning and names those hosts. A direct host is not included. The warning clears when the copy is no longer earlier.

`list_servers` and `get_server` return `effective_backup_schedule`, `next_backup_run`, and `backup_scheduled`. The tool text names those fields. `backup_scheduled` is false only when backups are on, the scheduler is running, and no job is registered. Null means that state is unknown.

The restore wizard can pull one selected folder from Google Drive, OneDrive, or a LAN share onto this PiHerder, then reverse-rsync it to the host. Dry-run does not write the pull. A direct host only lists destinations it sends to. The audit row names the destination and leaves out the sign-in. Those copies do not keep a Unix owner. A named volume can come back as the wrong account, and the container may then fail to start. This release does not repair ownership or mode.

A folder row left running does not hold that tick's direct hop. The direct hop starts when the folder task ends, and a later copy enqueue starts it if the folder row is still running. A second folder copy still waits. A cancel does not start the direct hop.

### Docker prune and the schedule list

The host Docker page, `POST /api/v1/servers/{id}/docker/prune`, and hosted `prune_docker` start a `docker_prune` job. The API and MCP need the `edit` scope. `both` is dangling images, then exited containers. Networks are their own type. Volume prune stays on the Docker page until the operator confirms it. Every run writes an audit row. This is issue [#48](https://github.com/bjorngluck/piherder/issues/48), closed with this release. Build cache and buildx cache are not in this prune.

About keeps a short count of scheduled jobs. View schedule opens them grouped by type, with each host's status, last run, and next fire. Last run is the latest matching job. A restart does not clear it. A missed slot is not a run.

An OS patch job keeps apt's error output when sudo drops `DEBIAN_FRONTEND`. A carriage-return rewrite no longer replaces a debconf, sudo, or `E:` line.

### Dependency patches

The three lockfiles pin aiohttp **3.14.4**, yarl **1.25.1**, Mako **1.4.3**, and multidict **6.9.1**. aiohttp 3.14.4 needs that yarl. These pins cover the Dependabot advisories that were open on `main`: the aiohttp high, medium, and low issues, the Mako path check, and the multidict items-view leak. Those pins are on this release. Pull request [#56](https://github.com/bjorngluck/piherder/pull/56) stops at aiohttp 3.14.4, Mako 1.4.2, and multidict 6.9.1, and it does not pin yarl. This branch is ahead of it.

### NPM proxy hosts and a read-only admin API

An admin can create, edit, and delete a proxy host from the NPM page. The form sets domains, forward scheme, host, and port, enabled, an existing certificate, and force SSL. It does not request a certificate. Each change writes an audit row and leaves out the NPM password. There is no MCP tool. Move still only changes `forward_host`. An operator can still bind a host. An operator cannot create, edit, or delete one.

A token with the `admin` scope can GET users, API tokens, and the audit log at `/api/v1/admin`. The scope is off unless you tick it. `read` does not include it. MCP sign-in cannot receive it. Responses omit the password hash, the TOTP secret, the token secret, the token hash, and the token prefix. These GETs write an `api_read` row when read audit is on, at most one per token and path every 5 minutes. There is no write route and no MCP tool.

---

## Not in this release

- Another adapter tag. **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** is already on PyPI.
- Issue [#50](https://github.com/bjorngluck/piherder/issues/50). Later. Device identity stays the MAC. A laptop that changes MAC can still leave two discovery rows.
- Build cache and buildx cache. [PLAN_v1.13.0](PLAN_v1.13.0.md).
- Putting the Unix owner, group, and mode back on a restore. [PLAN_v1.13.0](PLAN_v1.13.0.md).
- Backup stagger past about 1,320 hosts, a stuck-queue alert, an OAuth token name suffix, and one `get_engine()` accessor.
- The push denylist secret. The workflow is in. An empty secret does not pretend it checked.


---

## Upgrade

No new Alembic revision. Recreate **web** and **celery-worker**. About should say **1.12.0**. Pull `bjorngluck/piherder:1.12.0`. Tags `1.12` and `latest` are that same image. Plugin **0.5.2** is the card for this release. It needs Home Assistant **2024.7** or newer. Set the resource to `/piherder/piherder-dashboard-card.js?v=0.5.2`. Delete a `/local/piherder-dashboard-card.js` or `/api/piherder/…` resource. The public demo is being updated to this release.
