# PiHerder v1.15.0: hardening

**Status:** **Scoped** on `v1.15.0-dev`. The maintainer set Must and Should on 2026-11-03. This is a hardening release. Package stays `1.13.0`. No tag.  
**Date opened:** 2026-10-31. **Rewritten:** 2026-11-03.  
**Git branch:** `v1.15.0-dev`  
**Package / image version:** package stays **`1.13.0`**. About / footer stay **1.13.0**. This plan does not bump the package.  
**Theme:** architecture plus bug fixes. No new features. Features, themes, and breaking changes go to 2.0. Architect owns `PLAN_v2.0.0.md`. This repo does not add that file.  
**Baseline:** `v1.15.0-dev` at `78990487`, which merges `v1.14.0-dev` at `4e7d7fb`. **v1.14.0** is not tagged. Production image stays **1.13.0**.  
**Milestone:** **v1.15.0** is still to be created by the maintainer. These issues are not on a milestone.  
**Mode:** **Must, then Should.** Must blocks the tag. Should can slip.  
**Related:** [PLAN_v1.14.0.md](PLAN_v1.14.0.md) · draft [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md)

> Rewritten 2026-11-03 after the merge of `4e7d7fb`. Nothing on this train is built. [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) stay on the v1.14.0 milestone until that release. They are not refiled here. Placeholders only (`host-a`, `example.com`).

---

## 0. Intent

v1.14 records the missed-slot catch-up, Drive OAuth health, the fleet summary, mute rules, restore safety, the compose drift badge, and volume prune on the API. v1.15 hardens that tree. It fixes boot, logging, NameErrors, locks, migrations, and the security findings from the November 2026 reviews. It does not add a feature theme.

Features, themes, and breaking changes belong on 2.0. Architect writes `PLAN_v2.0.0.md`. Do not create that file on this branch.

Volume prune `dry_run` already shipped in v1.14, including the best-effort wording from `4e7d7fb`. `POST /api/v1/servers/{id}/docker/volumes` and hosted `prune_volumes` give a best-effort preview of dangling volumes when `dry_run` is true. That set can differ slightly from what `docker volume prune -f` removes on newer Docker versions, and there is a small time gap before the delete. It is not an exact match. That path deletes nothing and writes no audit row. It is not rebuilt on this train.

Suggested build order: boot and logging, the NameErrors, locks, CI with migrations, the rest of Must correctness, then security, then the Shoulds.

---

## 1. Decision lock

### Boot and logging

| Tier | Lock |
|------|------|
| Must | **Boot keeps loggers and fails closed.** Issue [#75](https://github.com/bjorngluck/piherder/issues/75). In the tree. Alembic `fileConfig()` must not disable existing loggers, so scheduler, alert, and auth log lines still emit after migrations. A failed `alembic upgrade head` aborts boot. It does not log the error and continue |

### NameErrors

| Tier | Lock |
|------|------|
| Must | **NPM integration create.** Issue [#76](https://github.com/bjorngluck/piherder/issues/76). In the tree. Create calls an undefined `poll_svc` after the row commits, so the UI says save failed for a save that worked. Use the real poll service. The create path returns success |
| Must | **Backups page last backup.** Issue [#77](https://github.com/bjorngluck/piherder/issues/77). In the tree. The backups page uses `AuditLog` without importing it and swallows the error, so last backup status is always empty. Import `AuditLog` and do not hide that exception. The page shows the last backup |

### Locks

| Tier | Lock |
|------|------|
| Must | **Host lock fails closed.** Issue [#78](https://github.com/bjorngluck/piherder/issues/78). In the tree. A Redis error on acquire must not fall back to a process-local lock. Two workers must not run `rsync --delete` on one host. The acquire fails and the job retries later. A Redis error reports `lock_unavailable` instead of a busy host. That error logs a warning and increments `piherder_lock_acquire_failures_total` with `reason` `redis_error`. A held lock increments the same counter with `reason` `busy` |
| Must | **Patch, stack, reboot, and prune take the per-host lock.** Issue [#79](https://github.com/bjorngluck/piherder/issues/79). In the tree. Those jobs acquire the same per-host lock as a backup. Each defers while a backup holds the lock |

### CI and migrations

| Tier | Lock |
|------|------|
| Must | **CI on dev branches, a Postgres migration job, lint, coverage.** Issue [#80](https://github.com/bjorngluck/piherder/issues/80). In the tree. Test CI runs on pushes to `v*-dev`, not only `main` and pull requests. A Postgres job upgrades to head and downgrades to base, and a downgrade error fails that job. The shared test engine runs Alembic on Postgres. SQLite still uses `create_all` because the migration SQL is Postgres. A ruff lint gate is added. Coverage is back over 85% |
| Must | **Explicit 051 and 052 downgrades.** Issue [#74](https://github.com/bjorngluck/piherder/issues/74). In the tree. Alembic `051` drops `docker_prune_schedule` only when that column exists. Alembic `052` drops `backup_catchup_enabled` and `container_check_catchup_enabled` the same way. A real drop error is not swallowed. Upgrade behavior stays as it is. Revision `040` already drops its table only when the table exists. This plan does not apply either migration to a live database |
| Must | **Indexes on job and audit log filters.** Issue [#81](https://github.com/bjorngluck/piherder/issues/81). In the tree. A reversible migration adds `job (server_id, status, created_at)` and `auditlog (server_id, action, started_at)`. Upgrade and downgrade both pass in the CI migration job |

### Other correctness

| Tier | Lock |
|------|------|
| Must | **Certificate presets keep category and group.** Issue [#82](https://github.com/bjorngluck/piherder/issues/82). In the tree. The wizard category is `category`. `group` stays the unix group. A preset keeps both |
| Must | **Production lockfile in the image.** Issue [#83](https://github.com/bjorngluck/piherder/issues/83). In the tree. The image installs `requirements.runtime.lock.txt` only. Dev packages are not in the image. Image size before: 739114216 bytes. After: 700781466 bytes |
| Must | **host_facts on a down host, and job state.** Issue [#87](https://github.com/bjorngluck/piherder/issues/87). In the tree. `host_facts` on an unreachable host waits out the probe window before another queue. The probe window matches the Celery task time limit. A failed facts job names the host. A failed job does not keep a success summary. Pending jobs are not counted as running |

### Security

| Tier | Lock |
|------|------|
| Must | **Escape `tojson` in templates.** Issue [#84](https://github.com/bjorngluck/piherder/issues/84). In the tree. `tojson` and pre-serialised JSON are HTML-safe in templates. Related to [#97](https://github.com/bjorngluck/piherder/issues/97) |
| Must | **rsync checks the pinned host key.** Issue [#85](https://github.com/bjorngluck/piherder/issues/85). In the tree. rsync ssh uses the pinned known_hosts entry, `StrictHostKeyChecking=yes`, and a quoted identity path. A host whose key changed is refused |
| Must | **Block secret-looking file names.** Issue [#92](https://github.com/bjorngluck/piherder/issues/92). In the tree. The files API and hosted `read_file` refuse names that look like secrets (`.env`, `*.key`, `id_*`, `*.pem`, `secrets`, and similar). `read_file` is not described as read-only. The tool text marks it sensitive |
| Must | **Redis password, default DB password, pyasn1.** Issue [#93](https://github.com/bjorngluck/piherder/issues/93). In the tree. Boot requires a Redis password and pings Redis with that password in the web process and the worker. An authentication failure refuses to start. The log line has no URL and no password. A connection error does not refuse boot. The job defer path stays for transient Redis errors only. Boot refuses the default database password outside demo mode. pyasn1 is **0.6.5** |
| Must | **Webhook targets and trusted proxies.** Issue [#94](https://github.com/bjorngluck/piherder/issues/94). In the tree. Webhook URLs that resolve to private, loopback, or link-local ranges are rejected unless `PIHERDER_WEBHOOK_ALLOW_CIDRS` opts that address in. The compose trusted-proxy default is loopback only. Forwarded headers from an untrusted source are ignored |
| Must | **Leak-guard and audit retention.** Issue [#95](https://github.com/bjorngluck/piherder/issues/95). In the tree. An empty leak-guard denylist fails closed. A `jobs` token cannot queue audit retention. That action needs `admin` and returns 403 for `jobs` |
| Must | **Pin images and Actions, quote the rsync key path.** Issue [#96](https://github.com/bjorngluck/piherder/issues/96). Not built. Base images are pinned by digest. GitHub Actions are pinned by commit SHA. The rsync key path is quoted, including a path that contains spaces. piherder-mcp gets a lockfile and stops auto-sending `confirm: true`. The v1.14 removed list had parked pinning Actions to commit SHAs. This plan puts that pin back as supply-chain hardening |
| Must | **CSP nonces only on known scripts.** Issue [#97](https://github.com/bjorngluck/piherder/issues/97). In the tree. Templates set the nonce on the script tags they write. Rendered HTML is not scanned for script tags |

### Should

| Tier | Lock |
|------|------|
| Should | **Separate opt-in destructive token scope.** Issue [#71](https://github.com/bjorngluck/piherder/issues/71). Not built. Hosted `prune_volumes` with `confirm` true can permanently delete unused Docker volumes. v1.14 accepts that for an `edit` token. This item adds a separate scope so an `edit` token does not delete unless the operator opts in. `dry_run` stays on `edit`. Existing `edit` tokens do not gain the new scope |
| Should | **Restore preview of the chosen copy.** Issue [#72](https://github.com/bjorngluck/piherder/issues/72). Not built. `GET /api/v1/servers/{id}/backup/restore-preview` diffs the local backup and does not show permission or owner-only changes. A `destination_id` still diffs that local tree. This item diffs the chosen copy and lists permission and owner changes. The preview still does not write |
| Should | **Git drift SSH cost.** Issue [#73](https://github.com/bjorngluck/piherder/issues/73). Not built. The compose git drift badge opens one SSH session per project. Batch the checks or cache the results. The badge still compares a checkout with the last fetched origin and does not fetch or deploy |
| Should | **Readiness, worker health, unhealthy containers.** Issue [#86](https://github.com/bjorngluck/piherder/issues/86). Not built. `/ready` checks the database, Redis, and migration head. The Celery healthcheck is a real worker ping. A container that stays unhealthy raises an alert. `/health` stays the process-alive check |
| Should | **One log record per Celery exception.** Issue [#88](https://github.com/bjorngluck/piherder/issues/88). Not built. A raised task exception writes one ERROR record with the traceback attached, not one ERROR per traceback line |
| Should | **Unreachable hosts and missed backups.** Issue [#89](https://github.com/bjorngluck/piherder/issues/89). Not built. A host unreachable for more than 24 hours, or with 2 missed backups, raises one critical alert on the push channels. Each trigger fires once, not on a repeat. Per-host status is healthy, degraded, or unreachable, with how long it has been in that state |
| Should | **One source for MCP tool definitions.** Issue [#90](https://github.com/bjorngluck/piherder/issues/90). Not built. Hosted MCP, the stdio adapter, and the Home Assistant plugin are hand-copied and have drifted. Generate tool definitions from one schema, or add a parity test. CI fails when the tool lists or schemas differ |
| Should | **Self-backup and restore drill.** Issue [#91](https://github.com/bjorngluck/piherder/issues/91). Not built. Document and test backup and restore of PiHerder itself (database plus config). A test restores that backup into a fresh instance and the app boots with data intact. The drill doc is on the docs site. An optional monthly test restore of one host backup to scratch storage may follow |
| Should | **Stack-check age, delivery failures, starter rules.** Issue [#98](https://github.com/bjorngluck/piherder/issues/98). Not built. A gauge for the age of the last stack check and a counter for alert delivery failures appear on `/metrics`. A starter Grafana dashboard and a Prometheus rule file ship under `contrib/`. The rule file passes `promtool check rules` in CI |

### Ops track

These are operator tasks. They are not repo changes and they are not GitHub issues on this plan.

| Item | Lock |
|------|------|
| Grafana | A contact point and alert rules for this fleet. Separate from the starter files in [#98](https://github.com/bjorngluck/piherder/issues/98) |
| Metrics | Scrape `/metrics` |
| Hosts | Node exporters on all hosts |
| Tailscale | A tagged Tailscale key |
| Drive | Publish the Drive OAuth app |

### Already shipped or out of this train

| Topic | Lock |
|-------|------|
| Volume prune `dry_run` | Shipped in v1.14, wording at `4e7d7fb`. Best-effort preview of dangling volumes. Not an exact match for `docker volume prune -f`. Deletes nothing. Not this train |
| Issues #61 to #64 | In the v1.14 tree. They stay on the v1.14.0 milestone until that release |
| Milestone v1.15.0 | Still to be created by the maintainer |
| Version bump | Package stays **1.13.0** until the maintainer says otherwise. About stays **1.13.0** |
| 2.0 | Features, themes, and breaking changes. Architect owns `PLAN_v2.0.0.md`. Do not add that file here |
| Demo | The public demo is not pointed at this branch |

### Build order

| # | Step | Issues |
|---|------|--------|
| 1 | Boot and logging | [#75](https://github.com/bjorngluck/piherder/issues/75) |
| 2 | NameErrors | [#76](https://github.com/bjorngluck/piherder/issues/76), [#77](https://github.com/bjorngluck/piherder/issues/77) |
| 3 | Locks | [#78](https://github.com/bjorngluck/piherder/issues/78), [#79](https://github.com/bjorngluck/piherder/issues/79) |
| 4 | CI with migrations | [#80](https://github.com/bjorngluck/piherder/issues/80), [#74](https://github.com/bjorngluck/piherder/issues/74), [#81](https://github.com/bjorngluck/piherder/issues/81) |
| 5 | Rest of Must correctness | [#82](https://github.com/bjorngluck/piherder/issues/82), [#83](https://github.com/bjorngluck/piherder/issues/83), [#87](https://github.com/bjorngluck/piherder/issues/87) |
| 6 | Security | [#84](https://github.com/bjorngluck/piherder/issues/84), [#85](https://github.com/bjorngluck/piherder/issues/85), [#92](https://github.com/bjorngluck/piherder/issues/92), [#93](https://github.com/bjorngluck/piherder/issues/93), [#94](https://github.com/bjorngluck/piherder/issues/94), [#95](https://github.com/bjorngluck/piherder/issues/95), [#96](https://github.com/bjorngluck/piherder/issues/96), [#97](https://github.com/bjorngluck/piherder/issues/97) |
| 7 | Shoulds | [#71](https://github.com/bjorngluck/piherder/issues/71), [#72](https://github.com/bjorngluck/piherder/issues/72), [#73](https://github.com/bjorngluck/piherder/issues/73), [#86](https://github.com/bjorngluck/piherder/issues/86), [#88](https://github.com/bjorngluck/piherder/issues/88), [#89](https://github.com/bjorngluck/piherder/issues/89), [#90](https://github.com/bjorngluck/piherder/issues/90), [#91](https://github.com/bjorngluck/piherder/issues/91), [#98](https://github.com/bjorngluck/piherder/issues/98) |

The ops track stays beside this order. It does not land as commits.

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-31 | **Draft opened** from `v1.14.0-dev` at `adf968c`. Scope was still to be confirmed. Issues then: [#71](https://github.com/bjorngluck/piherder/issues/71), [#72](https://github.com/bjorngluck/piherder/issues/72), [#73](https://github.com/bjorngluck/piherder/issues/73), [#74](https://github.com/bjorngluck/piherder/issues/74). Milestone **v1.15.0** was still to be created. Nothing was built. |
| 2026-11-03 | **Rewritten as a hardening release** after the merge of `4e7d7fb` (`78990487`). No new features. Features, themes, and breaking changes go to 2.0. Architect owns `PLAN_v2.0.0.md`. That file is not added. Must correctness: [#75](https://github.com/bjorngluck/piherder/issues/75), [#76](https://github.com/bjorngluck/piherder/issues/76), [#77](https://github.com/bjorngluck/piherder/issues/77), [#78](https://github.com/bjorngluck/piherder/issues/78), [#79](https://github.com/bjorngluck/piherder/issues/79), [#80](https://github.com/bjorngluck/piherder/issues/80), [#74](https://github.com/bjorngluck/piherder/issues/74), [#81](https://github.com/bjorngluck/piherder/issues/81), [#82](https://github.com/bjorngluck/piherder/issues/82), [#83](https://github.com/bjorngluck/piherder/issues/83), [#87](https://github.com/bjorngluck/piherder/issues/87). Must security: [#84](https://github.com/bjorngluck/piherder/issues/84), [#85](https://github.com/bjorngluck/piherder/issues/85), [#92](https://github.com/bjorngluck/piherder/issues/92), [#93](https://github.com/bjorngluck/piherder/issues/93), [#94](https://github.com/bjorngluck/piherder/issues/94), [#95](https://github.com/bjorngluck/piherder/issues/95), [#96](https://github.com/bjorngluck/piherder/issues/96), [#97](https://github.com/bjorngluck/piherder/issues/97). Should: [#71](https://github.com/bjorngluck/piherder/issues/71), [#72](https://github.com/bjorngluck/piherder/issues/72), [#73](https://github.com/bjorngluck/piherder/issues/73), [#86](https://github.com/bjorngluck/piherder/issues/86), [#88](https://github.com/bjorngluck/piherder/issues/88), [#89](https://github.com/bjorngluck/piherder/issues/89), [#90](https://github.com/bjorngluck/piherder/issues/90), [#91](https://github.com/bjorngluck/piherder/issues/91), [#98](https://github.com/bjorngluck/piherder/issues/98). Ops track stays off the repo: Grafana contact point and rules, scraping `/metrics`, node exporters on all hosts, a tagged Tailscale key, publishing the Drive OAuth app. Nothing is built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Maintainer creates milestone **v1.15.0** | Not created |
| 2 | Boot and logging ([#75](https://github.com/bjorngluck/piherder/issues/75)) | In the tree |
| 3 | NameErrors ([#76](https://github.com/bjorngluck/piherder/issues/76), [#77](https://github.com/bjorngluck/piherder/issues/77)) | In the tree |
| 4 | Locks ([#78](https://github.com/bjorngluck/piherder/issues/78), [#79](https://github.com/bjorngluck/piherder/issues/79)) | In the tree |
| 5 | CI with migrations ([#80](https://github.com/bjorngluck/piherder/issues/80), [#74](https://github.com/bjorngluck/piherder/issues/74), [#81](https://github.com/bjorngluck/piherder/issues/81)) | In the tree |
| 6 | Rest of Must correctness ([#82](https://github.com/bjorngluck/piherder/issues/82), [#83](https://github.com/bjorngluck/piherder/issues/83), [#87](https://github.com/bjorngluck/piherder/issues/87)), then security, then Shoulds | Must correctness is in the tree |

---

*Scoped on `v1.15.0-dev`. Hardening only. Package stays 1.13.0. Not tagged. Milestone v1.15.0 is still to be created by the maintainer. Features, themes, and breaking changes go to 2.0.*
