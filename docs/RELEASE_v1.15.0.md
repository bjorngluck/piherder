# PiHerder v1.15.0

**Status:** Draft. Code freeze is set 2026-11-08.

**Date:** 2026-11-08

**Git tag:** not cut

**Package:** `1.15.0`. About and the footer say **1.15.0** after web is recreated from this tree.

**Published image:** stays **1.14.0**. Tags `1.14.0`, `1.14`, and `latest`. Manifest `sha256:681ba3384f235d3e354c1320223a65c359c33c2478bd34dc3598723fae029334`. Pin `1.13.0` stays that release.

**Baseline:** `v1.14.0` (2026-11-08)

This is a hardening release. It does not add a feature theme. Maintainer walks in [QA_v1.15.0](QA_v1.15.0.md) are empty. The [v1.15 pack](../wiki/assets/screenshots/README.md#v115--pack-status) is reserved and not captured. There is no version pull request. Do not point a fleet at `v1.15.0-dev`. The public demo stays on the published image.

Home Assistant plugin **[0.5.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.4)** stays the card. Published adapter **[0.4.4](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.4)** stays the package. Adapter **0.4.5** is [piherder-mcp#10](https://github.com/bjorngluck/piherder-mcp/pull/10) and is not merged, not tagged, and not on PyPI. No new plugin tag and no new adapter tag in this draft.

Operator how-to stays on the wiki: [Upgrades](https://piherder-docs.hacknow.info/operations/upgrades/) · [Alerts](https://piherder-docs.hacknow.info/operations/alerts-email-webhooks/) · [Install](https://piherder-docs.hacknow.info/getting-started/install/). Technical record: [PLAN_v1.15.0](PLAN_v1.15.0.md). Prior notes: [RELEASE_v1.14.0](RELEASE_v1.14.0.md).

The issues below stay open until the release close.

---

## What's new

### The app stays up when a secret is still weak

Issue [#93](https://github.com/bjorngluck/piherder/issues/93). A default database password, a Redis URL with no password, or a Redis password the server rejects does not stop the process. Each one logs a line with no URL and no password, and shows **Action needed** on the login page and on every later page. The notice has no dismiss control. Demo mode hides only the default database password notice. A Redis connection error does not add the notice.

Compose starts Redis with no password when `REDIS_PASSWORD` is unset. The Redis process still drops to the redis user. The nmap worker reuses `DATABASE_URL` and rewrites host `db` to `127.0.0.1` unless `PIHERDER_NMAP_DATABASE_URL` is set. pyasn1 is **0.6.5**.

### A quiet host is a host that stopped answering

Issue [#89](https://github.com/bjorngluck/piherder/issues/89). `host_unreachable` uses the latest answer. An answer is last seen, a facts check, a backup, an OS check, an image check, a Docker inventory refresh, or a host-tool check. The date the host was added is not an answer. A host with no answer yet does not alert.

Settings → Alerts → **Host unreachable** sets the quiet time in hours or days. The default is 24 hours. The longest wait is 90 days. Unchecking **Raise this alert** and saving clears open cards. A mute rule is separate: it skips webhook, email, and push, and the inbox row stays.

Two missed backup slots open one `backup_missed_repeat` alert. A repeat does not push again. `host_status` is `healthy`, `degraded`, or `unreachable`, with `host_status_seconds`. Degraded means the latest answer is at least 15 minutes old, or at least one backup slot was missed. The **1.14.0** image does not return those fields.

### Readiness, worker health, and an unhealthy container

Issue [#86](https://github.com/bjorngluck/piherder/issues/86). `GET /health` is process liveness. `GET /ready` checks the database, every configured Redis URL (broker, result backend, `REDIS_URL`, and `PIHERDER_NMAP_REDIS_URL` when set), and the migration head. It returns 503 when one check is not ok. The body is `ok` or `error` and does not include a URL. The Celery healthcheck is a real worker ping. A container that stays unhealthy opens one warning, `container_unhealthy`. That type is an Inventory catalog row, so Settings can mute it. A mute rule stops webhook, email, and push. The inbox row is still written.

### Locks fail closed

Issue [#78](https://github.com/bjorngluck/piherder/issues/78). Celery, host locks, and `/ready` use the same broker and result-backend resolvers. An empty or unset broker variable is still `redis://localhost:6379/0`. A Redis error while taking a host lock does not fall back to a process-local lock. That includes a failed first ping. The failure is not cached, so the next attempt opens a new connection. A connection error retries later and reports `lock_unavailable`. The log is a warning. `piherder_lock_acquire_failures_total` increases with `reason` `redis_error`. A held lock uses `reason` `busy`. A rejected password fails the host job once with `Redis authentication failed.` and does not retry. The log line has no URL and no password. Move's inline path does the same: the password failure closes the job, and a Redis outage leaves the job pending with `lock_unavailable`. Process-local locks remain only for tests and for `PIHERDER_SINGLE_PROCESS=1`.

Issue [#79](https://github.com/bjorngluck/piherder/issues/79). Patch, stack, reboot, and prune take the same per-host lock as a backup, and each waits while a backup holds it.

### Files, webhooks, templates, and the host key

Issue [#92](https://github.com/bjorngluck/piherder/issues/92). The files API and hosted `read_file` refuse names that look like secrets (`.env`, `*.key`, `id_*`, `*.pem`, `secrets`, and similar). The tool text says the tool is sensitive.

Issue [#94](https://github.com/bjorngluck/piherder/issues/94). A webhook URL that resolves to a private, loopback, or link-local address is rejected unless `PIHERDER_WEBHOOK_ALLOW_CIDRS` lists that address. Stock compose gives the default network subnet `192.0.2.0/24` and pins Caddy at `192.0.2.10`. `PIHERDER_TRUSTED_PROXY_CIDRS` defaults to that one address plus loopback (`192.0.2.10/32`, `127.0.0.1/32`, `::1/128`). An existing `.env` value wins. Do not list the whole subnet. Another container on that subnet cannot supply forwarded headers. If `192.0.2.0/24` is already used by another Docker network, set `PIHERDER_COMPOSE_SUBNET`, set `PIHERDER_CADDY_IP` inside it, and set the trusted-proxy variable to that address as a `/32` plus loopback.

Issue [#84](https://github.com/bjorngluck/piherder/issues/84) and [#97](https://github.com/bjorngluck/piherder/issues/97). `tojson` stays inside a script tag. Templates set the CSP nonce on the script tags they write. Rendered HTML is not scanned for script tags.

Issue [#85](https://github.com/bjorngluck/piherder/issues/85). rsync uses the pinned host key, `StrictHostKeyChecking=yes`, and a quoted identity path. A host whose key changed is refused.

Issue [#95](https://github.com/bjorngluck/piherder/issues/95). An empty leak-guard denylist fails closed. A `jobs` token cannot queue audit retention. That action needs `admin`.

Issue [#96](https://github.com/bjorngluck/piherder/issues/96). Base images in this repo are pinned by digest. GitHub Actions are pinned by commit SHA. CI fails when a `uses:` or a `FROM` is not pinned. The adapter half is pull request 10 and is not in this draft.

### Boot, pages, and job state

Issue [#75](https://github.com/bjorngluck/piherder/issues/75). Alembic no longer turns off existing loggers, so scheduler, alert, and auth lines still emit after migrations. A failed `alembic upgrade head` stops boot.

Issue [#76](https://github.com/bjorngluck/piherder/issues/76). Creating an Nginx Proxy Manager integration reports success after the row is stored.

Issue [#77](https://github.com/bjorngluck/piherder/issues/77), from pull request #99. The backups page shows the last backup for a host that has one. A failed audit lookup is logged and the page still renders.

Issue [#82](https://github.com/bjorngluck/piherder/issues/82). A certificate preset keeps the wizard category and the unix group as two fields.

Issue [#87](https://github.com/bjorngluck/piherder/issues/87). `host_facts` on a host that does not answer waits out the probe window before another queue. A failed facts job names the host and does not keep a success summary. Pending jobs are not counted as running.

Issue [#88](https://github.com/bjorngluck/piherder/issues/88). A raised Celery task writes one ERROR record with the traceback attached.

### Restore preview and git drift

Issue [#72](https://github.com/bjorngluck/piherder/issues/72). Restore preview with `destination_id` diffs that chosen copy from a temporary folder that is removed. `changes` includes `permission` and `owner`. The preview does not write to the host. The v1.14 local-backup preview is unchanged: it still does not list permission-only or owner-only changes.

Issue [#73](https://github.com/bjorngluck/piherder/issues/73). The compose list runs one drift script for every project on the open SSH session. The badge still compares a checkout with the last fetched origin. It does not fetch or deploy.

### Metrics starter and the coverage floor

Issue [#98](https://github.com/bjorngluck/piherder/issues/98). `/metrics` exposes a gauge for the age of the last stack check and a counter for alert delivery failures. `contrib/` includes a starter Grafana dashboard and a Prometheus rule file. `promtool check rules` runs on that file in CI.

Issue [#80](https://github.com/bjorngluck/piherder/issues/80). Test CI runs on pushes to `v*-dev`. A Postgres job upgrades to head and downgrades to base. A ruff lint gate runs. CI `--cov-fail-under` is **90** on `app`. The unit run measured **90%** (54249/60276).

Issue [#83](https://github.com/bjorngluck/piherder/issues/83). The image installs `requirements.runtime.lock.txt` only. Dev packages are not in the image.

Issue [#81](https://github.com/bjorngluck/piherder/issues/81) and [#74](https://github.com/bjorngluck/piherder/issues/74). Alembic `054_job_audit_indexes` adds `job (server_id, status, created_at)` and `auditlog (server_id, action, started_at)`. Revisions `051` and `052` drop their columns only when those columns exist. A real drop error is not swallowed.

Issue [#91](https://github.com/bjorngluck/piherder/issues/91). The docs describe a self-backup and restore drill. A test writes a self-backup, restores it onto an empty database, starts the app, and reads the host back.

Issue [#90](https://github.com/bjorngluck/piherder/issues/90). `tests/fixtures/mcp_public_tools.json` is the hosted tool list. CI fails when the adapter name list or the Home Assistant service list drifts. The recorded adapter gap is `prune_volumes` only.

---

## Not in this release

- A separate token scope for volume prune. Issue [#71](https://github.com/bjorngluck/piherder/issues/71) is design only, in [docs/design/71-token-scopes.md](design/71-token-scopes.md). An `edit` token can still confirm volume prune. `dry_run` stays on `edit`.
- Adapter **0.4.5**. Pull request 10 sends the caller's `confirm` and installs CI from `uv.lock`. It does not add `prune_volumes`.
- The ops track: a Grafana contact point for a fleet, scraping `/metrics`, node exporters, a tagged Tailscale key, and publishing the Drive OAuth app.
- Features, themes, and breaking changes. Those stay in [PLAN_v2.0.0](PLAN_v2.0.0.md).
- borg, kopia, rclone crypt, and putting the Home Assistant plugin in the image. The removed set from 2026-10-27 stays off future ballots. The list is in [PLAN_v1.14.0](PLAN_v1.14.0.md).

---

## Upgrade

Not an upgrade path while this draft is untagged. The image to run is still **1.14.0**.

When the tag is cut, stop **celery-worker** and **celery-worker-nmap** first so they do not write job rows while the indexes change. Recreate **web** and let it apply Alembic `054_job_audit_indexes`. Start **celery-worker** after `/ready` reports migrations ok. Start **celery-worker-nmap** only with `--profile nmap`. About should say **1.15.0**. Do not apply that revision by hand before this image is running. A plain `docker compose up` does not start the nmap worker. It is a second image (`Dockerfile.nmap`), it runs as root on the host network, and it adds `NET_RAW` and `NET_ADMIN`.

Before that recreate:

- Set a unique database password on the Postgres role and in `DATABASE_URL`. An existing volume keeps the role password it was created with. `POSTGRES_PASSWORD` in compose applies to a new volume.
- Set `REDIS_PASSWORD` and include it in the Redis URLs. Compose still starts Redis when the variable is unset, and the banner stays until the password is set.
- Stock compose pins Caddy at `192.0.2.10` on subnet `192.0.2.0/24` (TEST-NET-1, picked on purpose). The trusted-proxy default is that `/32` plus loopback. An existing `.env` value wins. Do not list the whole subnet. If the subnet conflicts with another Docker network, set `PIHERDER_COMPOSE_SUBNET`, `PIHERDER_CADDY_IP`, and the trusted-proxy `/32` together. The default Compose network name changes from `<project>_default` to `piherder_net`. Reattach containers that used the old network, for example an external Prometheus scraper. `PIHERDER_COMPOSE_NETWORK` sets another name. The default Caddyfile drops `CF-Connecting-IP` and `True-Client-IP` and sets `X-Real-IP` and `X-Forwarded-For` from the TCP peer. Cloudflare is opt-in: uncomment the `servers` block, paste the current ranges from https://www.cloudflare.com/ips/, and forward `{client_ip}`.
- A webhook aimed at a private address needs `PIHERDER_WEBHOOK_ALLOW_CIDRS`.
- `GET /health` is liveness. `GET /ready` reports database, Redis, and migration failure.
- Rollback to image `1.14.0` uses the same `PIHERDER_MASTER_KEY`. Stop the workers first. While the 1.15 web container can still see the database, downgrade Alembic to `053_alert_mute` (that drops the two `054` indexes only), then pin the image and start web before the workers. Upgrade-to-head CI is not this rehearsal.
- The weak-secret banner is a compatibility choice. It is not credential enforcement. Issue [#93](https://github.com/bjorngluck/piherder/issues/93) stays open. A rejected Redis password still fails the host job.

Plugin **0.5.4** stays the card. Adapter **0.4.4** stays the package on PyPI. The public demo is updated only when asked.

## Known issues

**KI-console-samsung-keyboard.** Samsung Keyboard on Android Chrome can change the word while you type, hide letters until Enter, and scramble soft Tab. Gboard did not reproduce those three. The page cannot hide that suggestion row. Unchanged from v1.14.0.

Maintainer walks are not signed. The three v1.15 screenshots are not captured.
