# PiHerder v1.7.0

**28 September 2026.** Code freeze on `v1.7.0-dev`. The package and the Hub image stay **1.6.0** until the version bump, tag, and publish.

An agent can talk to this herder over `POST /mcp`. Patch, check, stack, and template jobs run on the Celery worker, and a down host waits instead of failing on the first refused connection. You can name the instance, pick one accent, and hide Catalog in the nav. Home Assistant plugin **0.3.0** adds a host card, an updates card, a resources card, and confirm actions, including restart.

**Image:** not published yet. When it ships, [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.7.0` · `1.7` · `latest` (amd64 + arm64). Pins `1.6.0` / `1.6` stay valid until then.

Operator how-to: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/) · [Settings](https://piherder-docs.hacknow.info/operations/settings/) · [Appearance](https://piherder-docs.hacknow.info/getting-started/appearance/) · [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/) · [Jobs](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/). Those public pages update when this branch merges to `main`. Technical record: [PLAN_v1.7.0](https://github.com/bjorngluck/piherder/blob/v1.7.0-dev/docs/PLAN_v1.7.0.md). Maintainer QA: [QA_v1.7.0](https://github.com/bjorngluck/piherder/blob/v1.7.0-dev/docs/QA_v1.7.0.md).

---

## What’s new

### Agents on this herder

Settings → API management → **Create new token** → **MCP agent**. That fills a name starting with `mcp-` and leaves only **read** checked. Add **jobs**, **edit**, or **files** if the agent should do more than look. After you create the token, copy the hosted `/mcp` URL and the `Authorization: Bearer` header once. There is no browser login and no MCP OAuth.

Cursor, Claude, and the same style of client paste that JSON. A read token can see health, the fleet summary, servers, inventory, services, and jobs. It cannot change features, start jobs, or touch Files. A token with no **read** scope gets no tools.

The optional `uvx piherder-mcp` process is only for a machine that cannot reach the herder. It is not a second Compose service. Do not point an agent at the public demo.

Wiki: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/) · [API tokens](https://piherder-docs.hacknow.info/operations/api-tokens/)

### Jobs wait for a down host, and you can see which worker took them

OS patch, container patch, update checks, stack actions, template deploy, and host reboot run on the default Celery queue (`piherder-celery`). The Jobs row names that worker. A job nobody has taken yet says **not claimed**.

If SSH is refused, the job stays **pending** and the worker probes every 30 seconds. Settings → General → **Jobs** is how long it will wait (1–1440 minutes, default **30**). Restoring SSH lets the same job continue. Recycling **web** does not fail one of these jobs. Recycling the worker while one is **running** fails it and says it was not resumed.

Uptime Kuma or an old `last_seen` can label a row **waiting on host**. That label does not start the job and does not fail it.

Backup, Move, and Undo stay on Celery as in 1.6. Retention, the herder’s own backup, and host facts stay on the web process. nmap stays on its own worker. Move stays **off**.

Wiki: [Jobs](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/) · [Multi-worker](https://piherder-docs.hacknow.info/operations/multi-worker/) · [Settings](https://piherder-docs.hacknow.info/operations/settings/)

### Name the instance

Settings → General → **Instance**. A name up to 40 characters replaces the Pi/Herder wordmark in the header, footer, and sign-in page. Empty keeps PiHerder. One accent recolours links and accent chips. The mark and the red buttons stay red. There is no logo upload.

**Show Catalog in the navigation** is on by default. Uncheck it to drop Catalog from the desktop nav and the phone menu. `/catalog` still opens. The public demo ignores this card.

Wiki: [Appearance](https://piherder-docs.hacknow.info/getting-started/appearance/)

### Home Assistant cards can confirm an action

Install [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.3.0** with HACS. It is not inside the PiHerder image. Set the card resource to `/local/piherder-dashboard-card.js?v=0.3.0`.

The fleet card stays. New cards: one host (gauges and confirm buttons), updates (OS and container counts, reboot pending), and resources (memory, disk, and CPU). Confirm can run backup, retention, the update checks, OS patch, container patch, and restart. Restart is refused while a patch or a backup is already running. Feature toggles need an **edit** token. A **read** token still shows sensors and no buttons.

The cards are plain, and the 24-hour chart does not draw. The next release focuses on Home Assistant and replaces these with richer cards and that chart.

Wiki: [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/)

---

## Fixed

These were open GitHub issues. The fixes are on this branch. The issues stay open until the version pull request merges.

| Issue | What you see now |
|--|--|
| [#10](https://github.com/bjorngluck/piherder/issues/10) | Queued and running backup phase rows no longer fill the Audit “running” pulse. **Hide incomplete runs** still hides them. A finished backup stays in the feed. |
| [#9](https://github.com/bjorngluck/piherder/issues/9) | An OS patch that fails in apt still fails, and the job summary can include the apt `E:` lines so the reason is not only `rc=100`. |
| [#16](https://github.com/bjorngluck/piherder/issues/16) | Registering the first admin writes a `user_registered` audit event, not only the later login. |

Still open, and not part of this release: [#8](https://github.com/bjorngluck/piherder/issues/8) daily stale-data cleanup hitting a nmap foreign key, [#11](https://github.com/bjorngluck/piherder/issues/11) auditing API writes, [#12](https://github.com/bjorngluck/piherder/issues/12) LAN device rename audit on the API path.

---

## Defaults (opt-in stays off)

| | Default |
|--|---------|
| Move a service | **off** (`PIHERDER_SERVICE_MIGRATE`) |
| Console mux | **off** per host (and never on HAOS or the demo) |
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Host Files (real SFTP) | **off** (`PIHERDER_HOST_FILES`) |
| Command audit | **off** (Settings) |
| Catalog in the nav | **on** |
| Host wait | **30 minutes** |
| MCP | off until you create a token. No second container |
| CSP on the public demo | **Report-Only** |

---

## Upgrade from 1.6

Do this after the `v1.7.0` tag exists. It does not exist at freeze.

Alembic **046** (worker name on the job row) runs when **web** starts. The instance name, accent, and Catalog hide are settings, not a migration.

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. Pull `bjorngluck/piherder:1.7.0` (or `git checkout v1.7.0`).
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**.
4. Confirm the database revision includes `046_job_worker_hostname`.
5. Move stays **off**.
6. HACS: update [piherder-ha](https://github.com/bjorngluck/piherder-ha) to **0.3.0** and set the card resource query to `v=0.3.0`. Add **jobs** and **edit** only if you want the confirm buttons and toggles.

---

## Honest limits

| | |
|--|--|
| MCP | Bearer token only. No OAuth. No SSH, console, Move, or token admin through the tool list. `trigger_job` is six types (no host reboot, stack, or template). |
| Jobs | A running exclusive job dies if the worker is recreated. It is not resumed. |
| Home Assistant | No container start/stop, Move, Files, or console. The 24-hour chart does not draw. |
| Branding | One name and one accent. No logo upload. The public demo stays official PiHerder. |
| Not this release | Richer HA cards (v1.8.0), per-host grants, alternate backup destinations, Undo beyond a failed Move, leftover mux cleanup, and the HA job-finished event. |
