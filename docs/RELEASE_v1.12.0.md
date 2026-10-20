# PiHerder v1.12.0

**Draft. 20 October 2026.** Code freeze is set. Not tagged. Package stays **1.11.0** until the ship bump. About and the footer still say **1.11.0**. Image tags stay `1.11.0` / `1.11` / `latest`. Pin `1.10.0` / `1.10` stays the previous image. This note is the text for the tag. It is not a ship.

The default Jobs and Audit lists leave routine noise out. Backup and image signals stay honest: a rejected copy sign-in stays critical, a running container keeps an image flagged until it is actually on that image, and an unsupported OS is named. A host can pull one backup folder back from Drive, OneDrive, or a LAN share. An admin can create, edit, and delete an NPM proxy host. Remote restore, NPM CRUD, and the read-only admin API were promoted from Discover.

Operator how-to stays on the wiki: [Jobs and audit](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/) · [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Alerts](https://piherder-docs.hacknow.info/operations/alerts-email-webhooks/) · [Docker](https://piherder-docs.hacknow.info/docker/overview/) · [NPM](https://piherder-docs.hacknow.info/integrations/npm/) · [API tokens](https://piherder-docs.hacknow.info/operations/api-tokens/). Technical record: [PLAN_v1.12.0](PLAN_v1.12.0.md). Maintainer QA: [QA_v1.12.0](QA_v1.12.0.md). Walks are signed 2026-10-20. Screenshots are not captured.

Home Assistant plugin is **[0.5.1](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.1)** (card copy off the event loop). This herder cut does not ship the plugin. Adapter stays **[0.4.2](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.2)** until the cut that ships with this herder. The GitHub adapter readme already badges herder 1.11.0. PyPI 0.4.2 still freezes the 1.10.0 readme. The public demo stays the production image.

---

## What's new

### Quieter Jobs and Audit lists

A successful 15-minute host snapshot stays out of the default Jobs list and the default Audit list. A failed snapshot stays. **Show routine host snapshots** brings the successful rows back.

API and MCP reads write an audit row only when Settings, Security policy, **API and MCP read audit** is on. The row stores the token id and name, and the client IP. It does not store the token. Those rows stay off the default Audit list and keep their own retention (1 to 90 days, default 7). Turning the flag on or off writes its own row. Writes stay audited either way.

### Backup and image signals

Google Drive, OneDrive, and a LAN share each open one critical alert when the saved credential expires or the destination rejects it. Drive and OneDrive say reconnect. The backup category cannot lower it. The alert and the log line may carry the provider error code. They never carry the token, the rclone config, or the raw response body. A later successful copy resolves it. Publish the Google app to production. A Testing-mode app loses its token after 7 days. The copy still requests the full Drive scope, because `drive.file` cannot see a folder the operator created.

A failed self-backup alert includes the last successful self-backup time, or "never".

Creating a user, creating an API token, or changing force 2FA or template 2FA opens one warning. The body names the actor and the client IP. It does not include a password or a token.

An image stays flagged after a pull while any running container for that image is still on the previous id. The flag clears once every running container uses the id on disk. This is issue [#47](https://github.com/bjorngluck/piherder/issues/47). The issue stays open.

Ubuntu, Debian, or Raspbian outside Ubuntu 22.04, 24.04, and 26.04 LTS, or Debian and Raspbian 12 and 13, shows **Unsupported** next to the OS line. HAOS is not in this check. The warning does not start an upgrade. This is issue [#49](https://github.com/bjorngluck/piherder/issues/49). The issue stays open.

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

The host Docker page, `POST /api/v1/servers/{id}/docker/prune`, and hosted `prune_docker` start a `docker_prune` job. The API and MCP need the `edit` scope. `both` is dangling images, then exited containers. Networks are their own type. Volume prune stays on the Docker page until the operator confirms it. Every run writes an audit row. This is issue [#48](https://github.com/bjorngluck/piherder/issues/48). The issue stays open. Build cache and buildx cache are not in this prune.

About keeps a short count of scheduled jobs. View schedule opens them grouped by type, with each host's status, last run, and next fire. Last run is the latest matching job. A restart does not clear it. A missed slot is not a run.

An OS patch job keeps apt's error output when sudo drops `DEBIAN_FRONTEND`. A carriage-return rewrite no longer replaces a debconf, sudo, or `E:` line.

### NPM proxy hosts and a read-only admin API

An admin can create, edit, and delete a proxy host from the NPM page. The form sets domains, forward scheme, host, and port, enabled, an existing certificate, and force SSL. It does not request a certificate. Each change writes an audit row and leaves out the NPM password. There is no MCP tool. Move still only changes `forward_host`. An operator can still bind a host. An operator cannot create, edit, or delete one.

A token with the `admin` scope can GET users, API tokens, and the audit log at `/api/v1/admin`. The scope is off unless you tick it. `read` does not include it. MCP sign-in cannot receive it. Responses omit the password hash, the TOTP secret, the token secret, the token hash, and the token prefix. These GETs write an `api_read` row when read audit is on, at most one per token and path every 5 minutes. There is no write route and no MCP tool.

---

## Not in this release

- The package bump, the `v1.12.0` tag, and the Hub push. Freeze is set. They wait for a separate ship step.
- The public demo. It stays the production image.
- Adapter **0.4.2** on PyPI. The tool text for backup time and `prune_docker` ships with this herder, not ahead of it.
- Issues [#47](https://github.com/bjorngluck/piherder/issues/47), [#48](https://github.com/bjorngluck/piherder/issues/48), [#49](https://github.com/bjorngluck/piherder/issues/49), and [#50](https://github.com/bjorngluck/piherder/issues/50). Close them with the release, only when asked. [#50](https://github.com/bjorngluck/piherder/issues/50) is Later. Device identity stays the MAC.
- Build cache and buildx cache. [PLAN_v1.13.0](PLAN_v1.13.0.md).
- Putting the Unix owner, group, and mode back on a restore. [PLAN_v1.13.0](PLAN_v1.13.0.md).
- Backup stagger past about 1,320 hosts, a stuck-queue alert, an OAuth token name suffix, and one `get_engine()` accessor.
- The push denylist secret. The workflow is in. An empty secret does not pretend it checked.
- Six medium Dependabot alerts for Mako and multidict on `main`. They close when this branch lands and is green. The locks on this branch already pin Mako **1.4.3** and multidict **6.9.1**.

---

## Upgrade

No new Alembic revision. Recreate **web** and **celery-worker** so the image includes this branch. About still says **1.11.0** until the ship bump. Plugin **0.5.1** is the current card. Set the resource to `?v=0.5.1`. Do not point the public demo at this branch.
