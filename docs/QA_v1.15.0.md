# PiHerder v1.15.0 (operator QA / sign-off)

**Branch:** `v1.15.0-dev`

**Code freeze:** set 2026-11-08. No new product work on this branch.

**Package:** **`1.15.0`**. About and the footer say **1.15.0** after web is recreated from this tree. No v1.15 tag. Image `1.14.0` / `1.14` / `latest` stays the v1.14.0 image. Pin `1.13.0` stays that release.

**Operator QA:** not signed. The boxes below are empty.

**Release notes:** draft [RELEASE_v1.15.0.md](RELEASE_v1.15.0.md). Do not tag or publish from this file.

**Screenshots:** reserved, not captured. Names are in the [v1.15 pack](../wiki/assets/screenshots/README.md#v115--pack-status). Names and paths in any frame must be examples. About on this tree says **1.15.0**. The frames do not show the footer.

**Pull request:** draft [#101](https://github.com/bjorngluck/piherder/pull/101). The checklist is [PR_v1.15.0.md](PR_v1.15.0.md). It stays a draft until these boxes are signed.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.15.0.md](PLAN_v1.15.0.md). v1.14 sign-off stays [QA_v1.14.0.md](QA_v1.14.0.md). Do not re-open those boxes.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Adapter work is [bjorngluck/piherder-mcp](https://github.com/bjorngluck/piherder-mcp). Do not point the public demo at this branch. Do not photograph the public demo. Mask names, paths, addresses, and secrets.

Must and Should code is on `v1.15.0-dev`, except [#71](https://github.com/bjorngluck/piherder/issues/71), which is design only. These boxes are the operator walk. Issues stay open until a release close. Alembic revisions already shipped in v1.14 are not re-applied by this walk.

---

## Boot and logging (Must)

Issue [#75](https://github.com/bjorngluck/piherder/issues/75).

- [ ] After web start, scheduler, alert, and auth log lines still emit. Alembic does not turn those loggers off
- [ ] A failed `alembic upgrade head` stops boot. The process does not log the error and keep serving

## NameErrors (Must)

Issue [#76](https://github.com/bjorngluck/piherder/issues/76).

- [ ] Creating an Nginx Proxy Manager integration saves the row and the page says success. It does not report a save failure after the row is stored

Issue [#77](https://github.com/bjorngluck/piherder/issues/77). From pull request #99. The GitHub pull request stays closed.

- [ ] The backups page shows the last backup for a host that has one
- [ ] A failed audit lookup is logged and the page still renders

## Locks (Must)

Issue [#78](https://github.com/bjorngluck/piherder/issues/78).

- [ ] Celery, host locks, and `/ready` use the same broker resolver. An unset broker URL is still `redis://localhost:6379/0`. Process-local locks run only with `PIHERDER_SINGLE_PROCESS=1`
- [ ] When Redis errors on lock acquire, including the first ping, the job does not run on a process-local lock. It retries later. The failure is not cached
- [ ] Two workers on that broker cannot both hold one host. After the holder releases, the other worker takes the lock. The busy counter and the Redis-error counter both move
- [ ] That failure reports `lock_unavailable`. The log is a warning and `piherder_lock_acquire_failures_total` increases with `reason` `redis_error`
- [ ] A rejected Redis password fails the host job once with `Redis authentication failed.` and does not retry. The log has no URL and no password
- [ ] A held lock increases the same counter with `reason` `busy`

Issue [#79](https://github.com/bjorngluck/piherder/issues/79).

- [ ] Patch, stack, reboot, and prune take the same per-host lock as a backup
- [ ] Each of those waits while a backup holds the lock

## CI and migrations (Must)

Issue [#80](https://github.com/bjorngluck/piherder/issues/80).

- [ ] Test CI runs on a push to `v1.15.0-dev`, not only on `main` and pull requests
- [ ] The Postgres job upgrades to head and downgrades to base. A downgrade error fails that job
- [ ] The ruff lint gate runs
- [ ] CI coverage fail-under is **90**

Issue [#74](https://github.com/bjorngluck/piherder/issues/74). Do not apply `051` or `052` to a live database for this box.

- [ ] The CI migration job shows `051` drops `docker_prune_schedule` only when that column exists
- [ ] The same job shows `052` drops `backup_catchup_enabled` and `container_check_catchup_enabled` only when those columns exist
- [ ] A real drop error is not swallowed

Issue [#81](https://github.com/bjorngluck/piherder/issues/81).

- [ ] The migration adds `job (server_id, status, created_at)` and `auditlog (server_id, action, started_at)`
- [ ] Upgrade and downgrade of that revision both pass in the CI migration job

## Other correctness (Must)

Issue [#82](https://github.com/bjorngluck/piherder/issues/82).

- [ ] A certificate preset keeps the wizard `category` and the unix `group` as two different fields

Issue [#83](https://github.com/bjorngluck/piherder/issues/83).

- [ ] The image install uses `requirements.runtime.lock.txt` only. Dev packages are not in the image

Issue [#87](https://github.com/bjorngluck/piherder/issues/87).

- [ ] `host_facts` on a host that does not answer waits out the probe window before another queue
- [ ] A failed facts job names the host
- [ ] A failed job does not keep a success summary
- [ ] Pending jobs are not counted as running

## Security (Must)

Issue [#84](https://github.com/bjorngluck/piherder/issues/84).

- [ ] A template value passed through `tojson` does not break out of a script tag

Issue [#85](https://github.com/bjorngluck/piherder/issues/85).

- [ ] rsync uses the pinned host key, `StrictHostKeyChecking=yes`, and a quoted identity path
- [ ] A host whose key changed is refused

Issue [#92](https://github.com/bjorngluck/piherder/issues/92).

- [ ] The files API and hosted `read_file` refuse names that look like secrets (`.env`, `*.key`, `id_*`, `*.pem`, `secrets`, and similar)
- [ ] The `read_file` tool text says the tool is sensitive. It does not say the tool is read-only

Issue [#93](https://github.com/bjorngluck/piherder/issues/93). The process stays up. The notice is the banner, not a crash.

- [ ] With the default database password and demo mode off, the login page and every later page show **Action needed**. The notice has no dismiss control. The app still serves
- [ ] Demo mode hides that database notice
- [ ] A Redis URL with no password shows the same banner, including in demo mode
- [ ] A Redis password the server rejects shows the banner. The log line has no URL and no password
- [ ] A Redis connection error does not add the banner
- [ ] With `REDIS_PASSWORD` unset, `docker compose up` still starts Redis
- [ ] The nmap worker uses `DATABASE_URL`. Host `db` is rewritten to `127.0.0.1` unless `PIHERDER_NMAP_DATABASE_URL` is set
- [ ] pyasn1 is **0.6.5** in both lockfiles

Issue [#94](https://github.com/bjorngluck/piherder/issues/94).

- [ ] A webhook URL that resolves to a private, loopback, or link-local address is rejected
- [ ] `PIHERDER_WEBHOOK_ALLOW_CIDRS` can opt one of those addresses in
- [ ] Stock compose trusts `192.0.2.10/32` plus loopback. An existing `.env` value wins. A neighbor on `192.0.2.0/24` cannot spoof forwarded headers. Two clients through Caddy keep separate login rate-limit buckets
- [ ] If `192.0.2.0/24` is already a Docker network, the install documents `PIHERDER_COMPOSE_SUBNET`, `PIHERDER_CADDY_IP`, and the matching trusted-proxy `/32`
- [ ] `celery-worker-nmap` stays off unless `--profile nmap` is set. The notes name the extra image build, root, host network, `NET_RAW`, and `NET_ADMIN`

Issue [#95](https://github.com/bjorngluck/piherder/issues/95).

- [ ] An empty leak-guard denylist fails closed
- [ ] A `jobs` token cannot queue audit retention. That action needs `admin` and returns 403 for `jobs`

Issue [#96](https://github.com/bjorngluck/piherder/issues/96). The herder half is on this branch. The adapter half is [piherder-mcp#10](https://github.com/bjorngluck/piherder-mcp/pull/10), not merged and not tagged.

- [ ] Base images in this repo are pinned by digest. GitHub Actions are pinned by commit SHA. CI fails when a `uses:` or a `FROM` is not pinned
- [ ] The rsync identity path is quoted, including a path that contains spaces
- [ ] Adapter pull request 10 sends the caller's `confirm` and installs CI from `uv.lock`. Package **0.4.5** is not published. Volume prune is not added

Issue [#97](https://github.com/bjorngluck/piherder/issues/97).

- [ ] Templates set the CSP nonce on the script tags they write. Rendered HTML is not scanned for script tags

## Should

Issue [#71](https://github.com/bjorngluck/piherder/issues/71). Design only, in [docs/design/71-token-scopes.md](design/71-token-scopes.md). No token code on this train. Do not tick a product walk.

- [ ] The design still says an `edit` token does not delete volumes unless the operator opts into a separate scope. `dry_run` stays on `edit`

Issue [#72](https://github.com/bjorngluck/piherder/issues/72).

- [ ] Restore preview with `destination_id` diffs that chosen copy from a temporary folder that is removed
- [ ] `changes` includes `permission` and `owner`. `ownership_step` is true when the tree has a manifest
- [ ] The preview does not write to the host

Issue [#73](https://github.com/bjorngluck/piherder/issues/73).

- [ ] The compose list runs one drift script for every project on the open SSH session
- [ ] The badge still compares a checkout with the last fetched origin. It does not fetch or deploy

Issue [#86](https://github.com/bjorngluck/piherder/issues/86).

- [ ] `GET /health` is process liveness. `GET /ready` checks the database, every configured Redis URL, and the migration head. It returns 503 when one check is not ok. The body does not include a URL
- [ ] The Celery healthcheck is a real worker ping
- [ ] A container that stays unhealthy opens one warning, `container_unhealthy`
- [ ] Settings can mute that type. A mute rule stops webhook, email, and push. The inbox row is still written

Issue [#88](https://github.com/bjorngluck/piherder/issues/88).

- [ ] A raised Celery task writes one ERROR record with the traceback attached. The worker does not write one ERROR per traceback line

Issue [#89](https://github.com/bjorngluck/piherder/issues/89).

- [ ] A host that answered recently does not open `host_unreachable`. An answer is a backup, a facts check, an image check, or a Docker inventory refresh
- [ ] The date the host was added does not open the alert. A host with no answer yet does not open it
- [ ] Settings → Alerts → **Host unreachable** sets the quiet time in hours or days. The default is 24 hours
- [ ] Unchecking **Raise this alert** and saving clears open cards and stops new ones
- [ ] Two missed backup slots open one `backup_missed_repeat` alert. A repeat does not push again
- [ ] `host_status` is `healthy`, `degraded`, or `unreachable`, with `host_status_seconds`

Issue [#90](https://github.com/bjorngluck/piherder/issues/90).

- [ ] `tests/fixtures/mcp_public_tools.json` is the hosted name, description, and input schema
- [ ] CI fails when the adapter name list or the Home Assistant service list drifts from that file
- [ ] The recorded adapter gap is `prune_volumes` only

Issue [#91](https://github.com/bjorngluck/piherder/issues/91).

- [ ] The docs site describes the self-backup and restore drill
- [ ] The drill test writes a self-backup, restores it onto an empty database, starts the app, and reads the host back

Issue [#98](https://github.com/bjorngluck/piherder/issues/98).

- [ ] `/metrics` exposes a gauge for the age of the last stack check and a counter for alert delivery failures
- [ ] `contrib/` has a starter Grafana dashboard and a Prometheus rule file
- [ ] `promtool check rules` passes on that rule file in CI
- [ ] The rule file warns on lock acquire failures and on `up{job="piherder-worker"} == 0`. `promtool test rules` shows each alert fire and clear. No live notification receiver is used

## Companions

Plugin **0.5.4** and adapter **0.4.4** stay the published packages. No new plugin tag on this walk. Adapter **0.4.5** waits on pull request 10.

- [ ] The herder README and the wiki still say adapter **0.4.4** until that pull request is merged and tagged
- [ ] CI `--cov-fail-under` is **90** on `app`. The unit run is **90%** (54249/60276)
- [ ] Published plugin **0.5.4** and adapter **0.4.4** work against this build with an existing token. A destructive adapter call sends `confirm`
- [ ] A copy of a 1.14 database moves `053` to `054` and back to `053` with the same master key. Data and settings stay. Workers are stopped before the change and started after web is ready. Record the time. Upgrade-to-head CI is not this rehearsal

## Screenshots

Not captured. Reserve the names below. Do not photograph the public demo. Mask names, paths, addresses, and secrets. About on this tree says **1.15.0**. The frames do not show the footer. The v1.14 pack stays. Do not retake it.

- [ ] `login-boot-secrets.png`
- [ ] `settings-host-unreachable.png`
- [ ] `alerts-host-unreachable.png`

## 1.14 regression

Do not retick [QA_v1.14.0.md](QA_v1.14.0.md).

- [ ] A missed backup slot can still catch up one job when that tick is on, and a second pass does not enqueue another
- [ ] Drive refresh failure and token ageing still open the signed alerts, and neither can be muted
- [ ] `GET /api/v1/fleet/summary` still returns counts and states and does not return addresses or tokens
- [ ] Mute rules still stop webhook, email, and push, and the inbox row is still written
- [ ] Volume prune on the API still needs `confirm` true and still writes one audit row

## Later

Not a walk on this train.

- The ops track: Grafana contact point, scraping `/metrics`, node exporters, a tagged Tailscale key, and publishing the Drive OAuth app
- Milestone **v1.15.0**, opening the version pull request, the tag, and the Hub publish. Draft notes are [RELEASE_v1.15.0.md](RELEASE_v1.15.0.md)
- Features, themes, and breaking changes. Those stay in [PLAN_v2.0.0.md](PLAN_v2.0.0.md)

borg, kopia, rclone crypt, and putting the Home Assistant plugin in the image stay off this train. The removed set from 2026-10-27 stays off future ballots. The list is in [PLAN_v1.14.0.md](PLAN_v1.14.0.md).
