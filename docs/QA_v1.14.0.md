# PiHerder v1.14.0 (operator QA / sign-off)

**Branch:** `v1.14.0-dev`

**Code freeze:** not set. Package bump, merge, tag, and Hub stay a separate ask.

**Package:** stays **`1.13.0`**. About / footer stay **1.13.0**.

**Operator QA:** walks are written and are not signed.

**Release notes:** not written. Not tagged.

**Screenshots:** reserved in [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status). Not captured.

**Pull request:** not opened.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.14.0.md](PLAN_v1.14.0.md). v1.13 sign-off stays [QA_v1.13.0.md](QA_v1.13.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Adapter work is [bjorngluck/piherder-mcp](https://github.com/bjorngluck/piherder-mcp). Do not point the public demo at this branch. Do not photograph the public demo. Mask names, paths, addresses, and secrets.

Must and Should code is on `v1.14.0-dev`. These boxes are the operator walk. They are not signed. Alembic `052_schedule_catchup` and `053_alert_mute` are in the tree and are not applied to a live database. This walk does not apply them. Issues [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) stay open until the release.

---

## Schedules (Must)

Issue [#62](https://github.com/bjorngluck/piherder/issues/62). Opt-in per schedule, off by default. A catch-up is one exclusive job in Job history.

Not signed.

- [ ] Host Backups shows **Catch up one missed backup. Off unless you turn this on.** A new host leaves it unticked
- [ ] The container-check form shows **Catch up one missed image check. Off unless you turn this on.** A new host leaves it unticked
- [ ] With the backup tick on, one missed backup slot enqueues one `backup` job. A second pass for that same slot does not enqueue another
- [ ] With the image-check tick on, one missed image-check slot enqueues one `container_update_check` job
- [ ] OS update checks, OS patch, redeploy, and `docker_prune` do not catch up, even if a caller opts in
- [ ] A catch-up does not start while a run of that kind is already active on the host
- [ ] A slot is skipped when a newer run of that schedule has already finished
- [ ] The job records why the slot was missed: `scheduler_down`, `overlap`, `clock`, `error`, or `unknown`
- [ ] A successful catch-up resolves the matching `backup_schedule_missed` or `container_check_missed` alert
- [ ] `/metrics` exposes `piherder_schedule_catchup_total` with `kind` of `backup` or `container_check` and `result` of `queued`, `skipped`, or `failed`. The counter is per process and resets on restart

## Drive (Must)

Issue [#63](https://github.com/bjorngluck/piherder/issues/63). No new credential store. The saved time is not the token.

Not signed.

- [ ] The Drive destination has **Google app is in Testing**. It is off unless the operator turns it on
- [ ] A failed refresh opens `drive_oauth_refresh_failed` at critical
- [ ] With the testing tick on, a saved token older than 5 days opens `drive_oauth_token_aging` at warning
- [ ] A missing save time does not open the ageing warning
- [ ] The alert text has presence and age only. It does not show the token, the client secret, or the rclone config
- [ ] Neither type can be muted, including by a host mute rule
- [ ] A successful refresh or a new token resolves the open row

## Fleet read (Must)

Issue [#61](https://github.com/bjorngluck/piherder/issues/61). This does not replace `GET /api/v1/summary` or hosted `summary`.

Not signed.

- [ ] `GET /api/v1/fleet/summary` (`read`) returns counts and states for hosts, open alerts, failed jobs, backups, Drive replication, and pending updates
- [ ] The body has host ids. It has no addresses, tokens, webhook URLs, or filesystem paths
- [ ] `since=` omitted is the current snapshot. A usable ISO-8601 `since` returns only areas that changed after that time
- [ ] An unusable `since` is HTTP 400
- [ ] With **API and MCP read audit** on, the call writes one `api_read` row and the row has no token value. With it off, the call writes no audit row
- [ ] Hosted `/mcp` `fleet_summary` (`read`) calls that route. Adapter **0.4.4** lists the tool. Adapter **0.4.3** does not
- [ ] `GET /api/v1/summary` and hosted `summary` are unchanged

## Console (Must)

The suggestion bar, a word hidden until Enter, and scrambled Tab were reproduced with Samsung Keyboard on Android Chrome. Gboard did not reproduce them. A live preview and two later input changes were tried and reverted.

Not signed.

- [ ] The short phone sheet stays open when the dimmed area is tapped
- [ ] The sheet sits above the keyboard
- [ ] Shell input is the committed console. There is no live PTY preview and no whole-phrase Tab flush
- [ ] [KI-console-samsung-keyboard](../wiki/day-to-day/web-ssh-console.md#ki-console-samsung-keyboard) names the three Samsung Keyboard symptoms and the Gboard workaround
- [ ] Desktop keyboards and a physical keyboard are unchanged

## Backups (Should)

A real restore copies the current host folder aside first. Undo writes that copy back. A new sidecar records `file_count` and `byte_count` for regular files.

Not signed.

- [ ] A real restore copies the current host folder aside before it writes. The copy keeps owners. A retry keeps that first copy until the restore succeeds or Undo runs
- [ ] Free space is checked with statvfs against the local source size. A short disk fails before the host is copied
- [ ] A snapshot that fails does not start the restore
- [ ] Dry-run does not take the snapshot
- [ ] **Undo last restore** writes that copy back with rsync `--delete`, which removes files created after the restore
- [ ] A sidecar from this train stores `file_count` and `byte_count` for regular files only
- [ ] A real restore is good only when the host matches both counts. Extra files already on the host are a warning and the restore still succeeds
- [ ] An older sidecar without those counts still restores
- [ ] Dry-run does not check the counts
- [ ] `GET /api/v1/servers/{id}/backup/restore-preview` (`read`) is an SSH rsync dry-run of the local backup with a 120 second timeout. It does not show permission or owner-only changes. A second preview while the host backup lock is held returns 409. It does not write
- [ ] That preview is not a hosted MCP tool
- [ ] The v1.13 ownership step still applies numeric `uid`, `gid`, and mode with `setuid` and `setgid` cleared

## Docker (Should)

**Git drift** is a pill on the stack list. Volume prune on the API is a separate call from the Docker page confirm and from scheduled `docker_prune`.

Not signed.

- [ ] The stack list shows **Git drift** when a tracked checkout is behind the last fetched origin branch, ahead of it, or has tracked edits
- [ ] Untracked files do not show the pill
- [ ] The inventory probe does not fetch and does not deploy. The pill appears after the next inventory refresh
- [ ] The Docker page still prunes unused volumes only after the operator ticks the confirm. `docker-prune.png` and `docker-build-cache-prune.png` stay
- [ ] `POST /api/v1/servers/{id}/docker/volumes` (`edit`) with `dry_run` true lists unused volumes and deletes nothing. `confirm` true prunes them in the request and writes `source`, names, and bytes reclaimed. It is not a `docker_prune` job and not scheduled
- [ ] A body without `confirm` true does not prune
- [ ] A `read` token cannot call that route
- [ ] Hosted `prune_volumes` (`edit`) does the same prune when `confirm` is true. A missing confirm returns an error and does not call the API
- [ ] Each of those writes one audit row, `docker_volume_prune`
- [ ] A saved `docker_prune_schedule` still starts `both` only. Volumes stay off that schedule
- [ ] Adapter **0.4.4** does not list `prune_volumes`. **0.4.3** was not moved

## Alerts (Should)

Issue [#64](https://github.com/bjorngluck/piherder/issues/64). Mute rules are separate from category mute. Alembic `053_alert_mute` is not applied to a live database.

Not signed.

- [ ] Settings → Alerts → **Mute rules** can save one host or one alert type
- [ ] A second rule for the same host or the same type is rejected. Kind and target stay fixed after create
- [ ] A window is optional. A start with no end is rejected. An end that is not after the start is rejected
- [ ] An active rule skips webhook, email, and push. The inbox row is still written. The schedule still runs
- [ ] These types cannot be muted, including by a host rule: `backup_failed`, `herder_backup_failed`, `backup_destination_auth_failed`, `drive_oauth_refresh_failed`, `drive_oauth_token_aging`, and `admin_change`
- [ ] Create, edit, and delete write `alert_mute_created`, `alert_mute_updated`, and `alert_mute_deleted`. Scope `edit` is required. A `read` token can list rules and cannot change one
- [ ] Demo cannot save a rule
- [ ] `GET /api/v1/alerts` items include `muted` from the rules that are active now
- [ ] `/metrics` gauge `piherder_alerts_muted` is the number of active rules. Help text says number of active mute rules. An expired rule stays in the table and does not count
- [ ] Sensor-level ignores stay in Prometheus

## Plugin and adapter (Must & Should)

Plugin **0.5.4** and adapter **0.4.4** are already tagged. No new plugin tag and no new adapter tag on this walk.

Not signed.

- [ ] The integration root has no `icon.png`, `icon@2x.png`, `logo.png`, or `logo@2x.png`. `brand/` stays. Tag **v0.5.4** is the card. **v0.5.3** was not moved
- [ ] The herder wiki and the plugin README say HACS, Integrations, PiHerder. They do not say to add a custom repository. Plugin version stays **0.5.4**
- [ ] Adapter tag **v0.4.4** lists `fleet_summary`, `list_alerts`, `prune_build_cache`, and `replace_stale_mac`. It does not list `prune_volumes`. **v0.4.3** was not moved
- [ ] CI `--cov-fail-under` is **85** on `app`

## Push denylist (Should)

The workflow is in. Secret `PIHERDER_PUSH_DENYLIST` is not set. This box stays open until the secret has at least one pattern.

Not signed.

- [ ] Secret `PIHERDER_PUSH_DENYLIST` has at least one literal of 4 or more characters. Comments and blank lines do not count. An empty secret prints that the manual grep remains and exits 0. That is not a pass. The job does not print a pattern or the matched line. Patterns stay off git

## Later

Not a walk on this train.

- Fine-grained roles
- Stricter command-audit redaction
- Per-host Prometheus sensor ignore
- Per-target scrape status

borg, kopia, rclone crypt, and putting the Home Assistant plugin in the image stay off this train. The removed set from 2026-10-27 stays off future ballots. The list is in [PLAN_v1.14.0.md](PLAN_v1.14.0.md).

## Screenshots

Reserved 2026-10-28. Not captured. About and the footer still say **1.13.0**. Mask names, paths, addresses, and secrets. Do not photograph the public demo. The file list is [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status).

- [ ] `docker-git-drift.png`
- [ ] `settings-mute-rules.png`
- [ ] `alerts-drive-oauth.png`

## 1.13 regression

Not signed. Do not retick [QA_v1.13.0.md](QA_v1.13.0.md).

- [ ] A backup still writes `.piherder-ownership.json`, and a restore still applies numeric owner, group, and mode
- [ ] Possible NIC change and **Replace stale MAC** still behave as signed on v1.13.0
- [ ] Docker build-cache prune, the prune schedule, and dangling-image prune after a successful update still leave volumes alone
- [ ] Host tool bootstrap still creates a `host_bootstrap` job and does not show a password in the job log
- [ ] A token with `admin` can still GET users, tokens, and the audit log. A `read` token cannot
