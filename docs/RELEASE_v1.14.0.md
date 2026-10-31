# PiHerder v1.14.0

**Status:** Draft

**Date:** not tagged

**Git tag:** none

**Package / image version:** package stays `1.13.0`. About / footer stay **1.13.0**. This note is not an upgrade path.

**Baseline:** `v1.13.0` (2026-10-26)

Maintainer walks in [QA_v1.14.0](QA_v1.14.0.md) are written and are not signed. Screenshot names are reserved in the [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status). They are not captured. The public demo is not pointed at this branch.

**Image:** production stays [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) **1.13.0**. This draft does not move `1.13`, `latest`, or any pin.

Home Assistant plugin **[0.5.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.4)** is already the card (Home Assistant **2024.7** or newer). **0.5.3** stays the previous tag. Published adapter **[0.4.4](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.4)** adds `fleet_summary`, `list_alerts`, `prune_build_cache`, and `replace_stale_mac`. **0.4.3** stays the previous package. Hosted `/mcp` also lists `prune_volumes`. Adapter **0.4.4** does not. A later adapter tag carries that tool if it lands. No new plugin tag and no new adapter tag in this draft.

Operator how-to stays on the wiki: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Docker](https://piherder-docs.hacknow.info/docker/overview/) · [Jobs and audit](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/) · [Alerts](https://piherder-docs.hacknow.info/operations/alerts-email-webhooks/) · [Web SSH](https://piherder-docs.hacknow.info/day-to-day/web-ssh-console/) · [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/). Technical record: [PLAN_v1.14.0](PLAN_v1.14.0.md). Maintainer QA: [QA_v1.14.0](QA_v1.14.0.md). Prior notes: [RELEASE_v1.13.0](RELEASE_v1.13.0.md).

---

## What's new

### Missed slot catch-up

Issue [#62](https://github.com/bjorngluck/piherder/issues/62). A host can opt in, off by default, to catch up one missed backup or one missed image check. The labels are **Catch up one missed backup** and **Catch up one missed image check**. OS update checks, OS patch, redeploy, and `docker_prune` are not eligible.

One catch-up per missed slot. It does not overlap a run that is already active, and it skips the slot when a newer run of that schedule has already finished. The catch-up is the normal exclusive job, so it appears in Job history. The job records why the slot was missed: `scheduler_down`, `overlap`, `clock`, `error`, or `unknown`. A successful run at or after the slot resolves the matching `backup_schedule_missed` or `container_check_missed` alert. A failed catch-up is not retried. The alert stays open until a success or the next slot. A catch-up already pending, running, or failed for the slot is skipped with no new metric and no new audit row. When the host already has an exclusive job, that slot gets at most one audit row. `/metrics` exposes `piherder_schedule_catchup_total` with `kind` of `backup` or `container_check` and `result` of `queued`, `skipped`, or `failed`. The counter is per process and resets when the process restarts. Sum it across workers and use increase() or rate(). `clock` scales with the schedule: 3 hours when the hour is every hour, 26 hours for a daily slot, 8 days for a weekly slot, and 32 days for a monthly slot. The issue stays open until the release.

### Google Drive OAuth health

Issue [#63](https://github.com/bjorngluck/piherder/issues/63). No new credential store. PiHerder records when the refresh token was saved. That timestamp is not the token.

A failed refresh opens `drive_oauth_refresh_failed` at critical. A destination checkbox, **Google app is in Testing**, defaults off. When it is on and the saved token is older than 5 days, `drive_oauth_token_aging` opens at warning. A missing save time does not open that warning. The alert carries presence and age only. It never logs or shows the token. Neither type can be muted. Either resolves on a successful refresh or a new token. The issue stays open until the release.

### Fleet summary

Issue [#61](https://github.com/bjorngluck/piherder/issues/61). `GET /api/v1/fleet/summary?since=` (`read`) and hosted `fleet_summary` return counts and states for hosts, open alerts, failed jobs, backups, Drive replication, and pending updates. Host ids are allowed. Addresses, tokens, webhook URLs, and filesystem paths are not. `since` omitted is the current snapshot. A usable ISO-8601 `since` returns only areas that changed after that time. An unusable `since` is HTTP 400. This does not replace `GET /api/v1/summary` or hosted `summary`. A read writes `api_read` when read audit is on. Adapter **0.4.4** lists `fleet_summary`. A herder on the **1.13.0** image returns 404 for that route. The issue stays open until the release.

### Mobile console

The suggestion bar, a word hidden until Enter, and scrambled Tab were reproduced with Samsung Keyboard on Android Chrome. The same console with Gboard did not reproduce them. A live preview and two later input changes were tried and reverted the same day. They made phone typing worse. Shell input is the committed console again.

The short phone sheet no longer hides when the dimmed area is tapped, and it moves above the keyboard. The known issue is [KI-console-samsung-keyboard](../wiki/day-to-day/web-ssh-console.md#ki-console-samsung-keyboard). Workaround: use Gboard for the console, or turn off predictive text in Samsung Keyboard. Desktop keyboards and a physical keyboard are unchanged.

### Mute rules

Issue [#64](https://github.com/bjorngluck/piherder/issues/64). Settings → Alerts → **Mute rules** is separate from category mute. One rule is one host or one alert type. A second rule for the same host or the same type is rejected. A window is optional. A start with no end is rejected. An end that is not after the start is rejected.

Mute skips webhook, email, and push. The inbox row is still written. Schedules still run. These types cannot be muted, including by a host rule: `backup_failed`, `herder_backup_failed`, `backup_destination_auth_failed`, `drive_oauth_refresh_failed`, `drive_oauth_token_aging`, and `admin_change`. Create, edit, and delete need scope `edit` and write `alert_mute_created`, `alert_mute_updated`, and `alert_mute_deleted`. A `read` token can list rules and cannot change one. Demo cannot save one. `GET /api/v1/alerts` items include `muted`. Gauge `piherder_alerts_muted` is the number of active rules. Its help text says number of active mute rules. Sensor-level ignores stay in Prometheus. The issue stays open until the release.

### Restore safety

Before a real restore, PiHerder copies the current host folder aside, with owners. Free space on that disk is checked with statvfs against the local source size, and a short disk fails before the host is copied. A retry keeps the first snapshot until the restore succeeds or Undo runs. A failed snapshot does not start the restore. Dry-run does not take it. **Undo last restore** writes that copy back with rsync `--delete`, which removes files created on the host after the restore.

A sidecar from this train also stores `file_count` and `byte_count` for regular files. A real restore is good only when the host matches both. An older sidecar without those counts still restores. Dry-run does not check. The v1.13 ownership step still applies numeric `uid`, `gid`, and mode with `setuid` and `setgid` cleared.

`GET /api/v1/servers/{id}/backup/restore-preview` (`read`) is an SSH rsync dry-run of the local backup. It diffs that local backup and does not show permission or owner-only changes. It times out after 120 seconds. One preview at a time per host. A busy host returns 409. It does not write. It is not a hosted MCP tool.

### Compose git drift

The Docker stack list shows **Git drift** when a tracked checkout is behind the last fetched origin branch, ahead of it, or has tracked edits. Untracked files do not count. The inventory probe does not fetch and does not deploy. The pill is stored on the inventory snapshot, so it appears after the next inventory refresh.

### Volume prune on the API

`POST /api/v1/servers/{id}/docker/volumes` (`edit`) and hosted `prune_volumes` prune unused volumes when `confirm` is true. `dry_run` true is a best-effort preview. It lists dangling volumes and sizes and deletes nothing. That set can differ slightly from what `docker volume prune -f` removes on newer Docker versions, and there is a small time gap between the dry run and the delete. It is not an exact match. confirm is not required on that path, and it writes no audit row. Agents should dry-run first and get a human's OK. A real prune writes one audit row, `docker_volume_prune`, with `source` (`ui`, `rest`, or `mcp`), the deleted names, and `bytes_reclaimed`. The page job stores the same fields with `source` `ui`. The call is not a `docker_prune` job and it is not scheduled. A missing confirm does not prune. A `read` token cannot call the route. Demo mode returns 403. The host page queues a separate `docker_prune` job. A saved `docker_prune_schedule` still starts dangling images and exited containers only. Adapter **0.4.4** does not list `prune_volumes`.

### Companions and the coverage gate

[piherder-ha#6](https://github.com/bjorngluck/piherder-ha/issues/6) is merged. The four integration-root icon and logo copies are removed. `brand/` stays. Tag **v0.5.4** is the card. Install wording is HACS, Integrations, PiHerder. It no longer says to add a custom repository. No new plugin tag.

CI `--cov-fail-under` is **85** on `app`. Adapter **v0.4.4** is already tagged on merge `30bfa2f`. Volume prune is not in that package.

---

## Not in this release

- Secret `PIHERDER_PUSH_DENYLIST` is not set. The workflow is in. Until it has at least one pattern, the job prints that the manual grep remains and exits 0. It does not report a clean scan. Patterns stay off git.
- A new adapter tag for `prune_volumes`.
- Fine-grained roles, stricter command-audit redaction, per-host Prometheus sensor ignore, and per-target scrape status.
- borg, kopia, rclone crypt, and putting the Home Assistant plugin in the image. The removed set from 2026-10-27 stays off future ballots. The list is in [PLAN_v1.14.0](PLAN_v1.14.0.md).
- The package bump, the tag, and the Hub publish. About stays **1.13.0**.
- Closing [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), or [#64](https://github.com/bjorngluck/piherder/issues/64). [#50](https://github.com/bjorngluck/piherder/issues/50) shipped in v1.13.0 and is not rebuilt.

---

## Upgrade

This draft is not an upgrade path. Production stays **1.13.0**. Do not apply Alembic `052_schedule_catchup` or `053_alert_mute` ahead of the tag.

When this ships, those two revisions run on web start. Recreate **web** and **celery-worker**. About should say the package that ships. Plugin **0.5.4** is already the card. Adapter **0.4.4** is already on PyPI. A cached **0.4.3** needs `uvx --refresh piherder-mcp`, then a restart of the MCP client. Hosted `prune_volumes` is ahead of that package. The public demo is updated only when asked.

## Known issues

**KI-console-samsung-keyboard.** Samsung Keyboard on Android Chrome can change the word while you type, hide letters until Enter, and scramble soft Tab. Gboard did not reproduce those three. The page cannot hide that suggestion row.
