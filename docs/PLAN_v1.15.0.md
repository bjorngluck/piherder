# PiHerder v1.15.0: hardening

**Status:** **Built** on `v1.15.0-dev`. Not frozen. [QA_v1.15.0.md](QA_v1.15.0.md) exists. Walks are empty. Package stays `1.14.0`. No v1.15 tag.
**Date opened:** 2026-10-31. **Rewritten:** 2026-11-03. **Merged shipped main:** 2026-11-08.  
**Git branch:** `v1.15.0-dev`  
**Package / image version:** package stays **`1.14.0`**. About / footer stay **1.14.0**. This plan does not bump the package to `1.15.0`.  
**Theme:** architecture plus bug fixes. No new features. Features, themes, and breaking changes go to 2.0. A draft [PLAN_v2.0.0.md](PLAN_v2.0.0.md) is on this branch. It is not this train's scope.
**Baseline:** shipped `main` at `84dd50c`. Tag **v1.14.0** is `553379d`. Production image is **1.14.0**. Manifest `sha256:681ba3384f235d3e354c1320223a65c359c33c2478bd34dc3598723fae029334`. Pin `1.13.0` stays that release.  
**Milestone:** **v1.15.0** is still to be created by the maintainer. These issues are not on a milestone.  
**Mode:** **Must, then Should.** Must blocks the tag. Should can slip.  
**Related:** [PLAN_v1.14.0.md](PLAN_v1.14.0.md) · [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md) · [QA_v1.15.0.md](QA_v1.15.0.md)

> Merged shipped `main` (`84dd50c`) on 2026-11-08. Tag **v1.14.0** is `553379d`. Package stays **1.14.0**. [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) shipped in v1.14.0 and are not refiled here. Placeholders only (`host-a`, `example.com`).

---

## 0. Intent

v1.14 records the missed-slot catch-up, Drive OAuth health, the fleet summary, mute rules, restore safety, the compose drift badge, and volume prune on the API. v1.15 hardens that tree. It fixes boot, logging, NameErrors, locks, migrations, and the security findings from the November 2026 reviews. It does not add a feature theme.

Features, themes, and breaking changes belong on 2.0. The draft is [PLAN_v2.0.0.md](PLAN_v2.0.0.md). Do not add those changes to this train.

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
| Must | **Backups page last backup.** Issue [#77](https://github.com/bjorngluck/piherder/issues/77). In the tree from [#99](https://github.com/bjorngluck/piherder/pull/99). The backups page imports `AuditLog`. A lookup failure is logged with a traceback and the page still renders. The page shows the last backup |

### Locks

| Tier | Lock |
|------|------|
| Must | **Host lock fails closed.** Issue [#78](https://github.com/bjorngluck/piherder/issues/78). In the tree. A Redis error on acquire must not fall back to a process-local lock. Two workers must not run `rsync --delete` on one host. The acquire fails and the job retries later. A Redis error reports `lock_unavailable` instead of a busy host. That error logs a warning and increments `piherder_lock_acquire_failures_total` with `reason` `redis_error`. A held lock increments the same counter with `reason` `busy` |
| Must | **Patch, stack, reboot, and prune take the per-host lock.** Issue [#79](https://github.com/bjorngluck/piherder/issues/79). In the tree. Those jobs acquire the same per-host lock as a backup. Each defers while a backup holds the lock |

### CI and migrations

| Tier | Lock |
|------|------|
| Must | **CI on dev branches, a Postgres migration job, lint, coverage.** Issue [#80](https://github.com/bjorngluck/piherder/issues/80). In the tree. Test CI runs on pushes to `v*-dev`, not only `main` and pull requests. A Postgres job upgrades to head and downgrades to base, and a downgrade error fails that job. The shared test engine runs Alembic on Postgres. SQLite still uses `create_all` because the migration SQL is Postgres. A ruff lint gate is added. Coverage fail-under is **90** |
| Must | **Coverage 90.** Before freeze. CI `--cov-fail-under` is **90** on `app`. Unit run **90%** (54249/60276). No new GitHub issue. The v1.14 floor was 85 |
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
| Must | **Redis password, default DB password, pyasn1.** Issue [#93](https://github.com/bjorngluck/piherder/issues/93). In the tree. The web process and the worker ping Redis at startup. A missing Redis password, a rejected Redis password, or the default database password does not stop the process. Each one logs a line with no URL and no password, and shows a notice on every page, including login. Demo mode hides the default database password notice. A connection error does not add a notice. The job defer path stays for transient Redis errors only. The nmap worker reuses `DATABASE_URL` and rewrites host `db` to `127.0.0.1` unless `PIHERDER_NMAP_DATABASE_URL` is set. Compose starts Redis with no password when `REDIS_PASSWORD` is unset. pyasn1 is **0.6.5** |
| Must | **Webhook targets and trusted proxies.** Issue [#94](https://github.com/bjorngluck/piherder/issues/94). In the tree. Webhook URLs that resolve to private, loopback, or link-local ranges are rejected unless `PIHERDER_WEBHOOK_ALLOW_CIDRS` opts that address in. The compose trusted-proxy default is loopback only. Forwarded headers from an untrusted source are ignored |
| Must | **Leak-guard and audit retention.** Issue [#95](https://github.com/bjorngluck/piherder/issues/95). In the tree. An empty leak-guard denylist fails closed. A `jobs` token cannot queue audit retention. That action needs `admin` and returns 403 for `jobs` |
| Must | **Pin images and Actions, quote the rsync key path.** Issue [#96](https://github.com/bjorngluck/piherder/issues/96). In the tree for this repo. Base images are pinned by digest. GitHub Actions are pinned by commit SHA. CI fails when a `uses:` or a `FROM` is not pinned. The rsync key path is quoted, including a path that contains spaces. The adapter half is [piherder-mcp#10](https://github.com/bjorngluck/piherder-mcp/pull/10) on `fix/96-lockfile-confirm` (package **0.4.5**, not merged, not tagged). That branch sends the caller's `confirm` and installs CI from `uv.lock` |
| Must | **CSP nonces only on known scripts.** Issue [#97](https://github.com/bjorngluck/piherder/issues/97). In the tree. Templates set the nonce on the script tags they write. Rendered HTML is not scanned for script tags |

### Should

| Tier | Lock |
|------|------|
| Should | **Separate opt-in destructive token scope.** Issue [#71](https://github.com/bjorngluck/piherder/issues/71). Design only, in `docs/design/71-token-scopes.md`. No token code on this train yet. Hosted `prune_volumes` with `confirm` true can permanently delete unused Docker volumes. v1.14 accepts that for an `edit` token. The design adds a separate scope so an `edit` token does not delete unless the operator opts in. `dry_run` stays on `edit`. Existing `edit` tokens do not gain the new scope |
| Should | **Restore preview of the chosen copy.** Issue [#72](https://github.com/bjorngluck/piherder/issues/72). In the tree. `destination_id` diffs that chosen copy from a temporary folder that is removed. `changes` includes `permission` and `owner`. `ownership_step` is true when the tree has a manifest. The preview does not write to the host |
| Should | **Git drift SSH cost.** Issue [#73](https://github.com/bjorngluck/piherder/issues/73). In the tree. The compose list runs one drift script for every project on the open SSH session. The badge still compares a checkout with the last fetched origin and does not fetch or deploy |
| Should | **Readiness, worker health, unhealthy containers.** Issue [#86](https://github.com/bjorngluck/piherder/issues/86). In the tree. `/ready` checks the database, Redis, and migration head. The Celery healthcheck is a real worker ping. A container that stays unhealthy raises one warning, `container_unhealthy`. That type is an Inventory catalog row, so Settings can mute it. `/health` stays the process-alive check |
| Should | **One log record per Celery exception.** Issue [#88](https://github.com/bjorngluck/piherder/issues/88). In the tree. A raised task exception writes one ERROR record with the traceback attached. The worker does not redirect stderr into one ERROR per traceback line |
| Should | **Unreachable hosts and missed backups.** Issue [#89](https://github.com/bjorngluck/piherder/issues/89). In the tree. A host that has not answered for the quiet time raises one `host_unreachable` alert. The default quiet time is 24 hours. Settings → Alerts sets hours or days, and turning the alert off clears open cards. An answer is a backup, facts check, image check, or Docker inventory refresh. The date the host was added is not an answer. A host with no answer yet does not raise the alert. Two missed backup slots raise one critical alert. A repeat does not push again. `host_status` is healthy, degraded, or unreachable, with `host_status_seconds` |
| Should | **One source for MCP tool definitions.** Issue [#90](https://github.com/bjorngluck/piherder/issues/90). In the tree. `tests/fixtures/mcp_public_tools.json` is the hosted name, description, and input schema. The adapter name list and the Home Assistant service list are checked against it. CI fails when a name or schema differs from those files. The recorded adapter gap is `prune_volumes` only. The plugin services are a subset |
| Should | **Self-backup and restore drill.** Issue [#91](https://github.com/bjorngluck/piherder/issues/91). In the tree. The docs site describes the drill. A test writes a self-backup of the database and settings, restores it onto an empty database, starts the app, and reads the host back |
| Should | **Stack-check age, delivery failures, starter rules.** Issue [#98](https://github.com/bjorngluck/piherder/issues/98). In the tree. A gauge for the age of the last stack check and a counter for alert delivery failures appear on `/metrics`. A starter Grafana dashboard and a Prometheus rule file ship under `contrib/`. The rule file passes `promtool check rules` in CI |

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
| Issues #61 to #64 | Shipped in v1.14.0. Not this train |
| Milestone v1.15.0 | Still to be created by the maintainer |
| Version bump | Package stays **1.14.0** until the v1.15 freeze. About stays **1.14.0** |
| 2.0 | Features, themes, and breaking changes. Draft is [PLAN_v2.0.0.md](PLAN_v2.0.0.md). Do not add them here |
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
| 2026-11-03 | **MCP parity.** Hosted tool schemas, the adapter name list, and the Home Assistant service list are fixtures. A new name or schema fails CI. The adapter fixture is still missing `prune_volumes`. |
| 2026-11-03 | **Rewritten as a hardening release** after the merge of `4e7d7fb` (`78990487`). No new features. Features, themes, and breaking changes go to 2.0. Architect owns `PLAN_v2.0.0.md`. That file is not added. Must correctness: [#75](https://github.com/bjorngluck/piherder/issues/75), [#76](https://github.com/bjorngluck/piherder/issues/76), [#77](https://github.com/bjorngluck/piherder/issues/77), [#78](https://github.com/bjorngluck/piherder/issues/78), [#79](https://github.com/bjorngluck/piherder/issues/79), [#80](https://github.com/bjorngluck/piherder/issues/80), [#74](https://github.com/bjorngluck/piherder/issues/74), [#81](https://github.com/bjorngluck/piherder/issues/81), [#82](https://github.com/bjorngluck/piherder/issues/82), [#83](https://github.com/bjorngluck/piherder/issues/83), [#87](https://github.com/bjorngluck/piherder/issues/87). Must security: [#84](https://github.com/bjorngluck/piherder/issues/84), [#85](https://github.com/bjorngluck/piherder/issues/85), [#92](https://github.com/bjorngluck/piherder/issues/92), [#93](https://github.com/bjorngluck/piherder/issues/93), [#94](https://github.com/bjorngluck/piherder/issues/94), [#95](https://github.com/bjorngluck/piherder/issues/95), [#96](https://github.com/bjorngluck/piherder/issues/96), [#97](https://github.com/bjorngluck/piherder/issues/97). Should: [#71](https://github.com/bjorngluck/piherder/issues/71), [#72](https://github.com/bjorngluck/piherder/issues/72), [#73](https://github.com/bjorngluck/piherder/issues/73), [#86](https://github.com/bjorngluck/piherder/issues/86), [#88](https://github.com/bjorngluck/piherder/issues/88), [#89](https://github.com/bjorngluck/piherder/issues/89), [#90](https://github.com/bjorngluck/piherder/issues/90), [#91](https://github.com/bjorngluck/piherder/issues/91), [#98](https://github.com/bjorngluck/piherder/issues/98). Ops track stays off the repo: Grafana contact point and rules, scraping `/metrics`, node exporters on all hosts, a tagged Tailscale key, publishing the Drive OAuth app. Nothing is built. |
| 2026-11-08 | **Merged shipped `main`** at `84dd50c`. Tag **v1.14.0** is `553379d`. Package on this branch is **1.14.0**. Image `1.14.0` / `1.14` / `latest`. Pin `1.13.0` stays that release. No v1.15 tag. |
| 2026-11-08 | **[#99](https://github.com/bjorngluck/piherder/pull/99) is on this train** as `9bf46da`, authored by the submitter. The backups page logs a failed audit lookup and still renders. The extra cases cover failure, a legacy success with failed rsync output, other hosts and actions, no history, and the logged error. `docs/RELEASE_UNRELEASED.md` is not on this branch. pyasn1 **0.6.5** from [#100](https://github.com/bjorngluck/piherder/pull/100) is already in `requirements.lock.txt` and `requirements.runtime.lock.txt`. |
| 2026-11-08 | **Doc pass.** Must and Should are in the tree except [#71](https://github.com/bjorngluck/piherder/issues/71) (design only). The 28 open issues stay open. Milestone **v1.15.0** is not created. No QA file, release notes, or version pull request. The wiki alerts page, the upgrade page, and the API server example name the open-train behavior. |
| 2026-11-08 | **[#86](https://github.com/bjorngluck/piherder/issues/86) catalog row.** `container_unhealthy` is Inventory, warning. Settings can mute that type. **[#96](https://github.com/bjorngluck/piherder/issues/96) adapter half** is [piherder-mcp#10](https://github.com/bjorngluck/piherder-mcp/pull/10), package **0.4.5**, not merged and not tagged. Both issues stay open. |
| 2026-11-08 | **[#93](https://github.com/bjorngluck/piherder/issues/93) stays a notice.** A default database password, a Redis URL with no password, or a rejected Redis password keeps the process running and shows a notice on every page, including login. Demo mode hides only the default database password notice. Compose starts Redis with no password when `REDIS_PASSWORD` is unset. The nmap worker reuses `DATABASE_URL` and rewrites host `db` to `127.0.0.1` unless `PIHERDER_NMAP_DATABASE_URL` is set. The issue stays open. |
| 2026-11-08 | **[#89](https://github.com/bjorngluck/piherder/issues/89) quiet time.** `host_unreachable` uses the latest answer, not the date the host was added. Settings → Alerts sets the quiet time in hours or days. Default 24 hours. Turning the alert off clears open cards. The issue stays open. |
| 2026-11-08 | **QA file.** [QA_v1.15.0.md](QA_v1.15.0.md) lists the walks. The boxes are empty. The v1.15 screenshot names are reserved and not captured. Release notes and the version pull request are not started. Issues stay open. |
| 2026-11-08 | **Coverage 90.** CI `--cov-fail-under` raised **85** to **90**. Unit run **90%** (54249/60276). Packs are `tests/test_coverage_v115_q90_*.py`. No new GitHub issue. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Maintainer creates milestone **v1.15.0** | Not created |
| 2 | Boot and logging ([#75](https://github.com/bjorngluck/piherder/issues/75)) | In the tree |
| 3 | NameErrors ([#76](https://github.com/bjorngluck/piherder/issues/76), [#77](https://github.com/bjorngluck/piherder/issues/77)) | In the tree |
| 4 | Locks ([#78](https://github.com/bjorngluck/piherder/issues/78), [#79](https://github.com/bjorngluck/piherder/issues/79)) | In the tree |
| 5 | CI with migrations ([#80](https://github.com/bjorngluck/piherder/issues/80), [#74](https://github.com/bjorngluck/piherder/issues/74), [#81](https://github.com/bjorngluck/piherder/issues/81)) | In the tree |
| 6 | Rest of Must correctness ([#82](https://github.com/bjorngluck/piherder/issues/82), [#83](https://github.com/bjorngluck/piherder/issues/83), [#87](https://github.com/bjorngluck/piherder/issues/87)) | In the tree |
| 7 | Security ([#84](https://github.com/bjorngluck/piherder/issues/84), [#85](https://github.com/bjorngluck/piherder/issues/85), [#92](https://github.com/bjorngluck/piherder/issues/92), [#93](https://github.com/bjorngluck/piherder/issues/93), [#94](https://github.com/bjorngluck/piherder/issues/94), [#95](https://github.com/bjorngluck/piherder/issues/95), [#96](https://github.com/bjorngluck/piherder/issues/96), [#97](https://github.com/bjorngluck/piherder/issues/97)) | In the tree |
| 8 | Shoulds | In the tree, except [#71](https://github.com/bjorngluck/piherder/issues/71), which is design only |
| 9 | Operator QA | [QA_v1.15.0.md](QA_v1.15.0.md) exists. Walks are empty. Screenshot names are reserved and not captured |
| 10 | Release notes and the version pull request | Not started |

---

*Built on `v1.15.0-dev`. Not frozen. Hardening only. Package stays 1.14.0. Not tagged. Milestone v1.15.0 is still to be created by the maintainer. Features, themes, and breaking changes stay in the 2.0 draft.*
