# PiHerder v1.7.0 — operator QA / sign-off

**Branch:** `v1.7.0-dev` → `main` · tag **`v1.7.0`** (cut after merge)  
**Code freeze:** *open*  
**Package:** **`1.6.0`** until freeze  
**Operator QA:** *not started*

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the operator pages while ticking boxes.

Plan: [PLAN_v1.7.0.md](PLAN_v1.7.0.md).

1.6 production sign-off stays [QA_v1.6.0.md](QA_v1.6.0.md) (historical). Do **not** re-open 1.6 boxes here.

**Tag honesty:** freeze only with **MCP-1** and **Jr-1**. CI fail-under is **80** (compose **81.01%**, kept as headroom). Do not lower it below **75**. Do not bump version, merge, tag, or Hub until asked. Do not redeploy the public demo onto this branch.

**This pass is sign-off, not new features.** Fix only a feature or regression bug you hit while walking. Discover (Bak-alt, AC-fg, HA Slice 2, Undo-2, Mux-2) stays parked until a row is promoted.

---

## Operator pages to walk

| Stream | Wiki |
|--------|------|
| MCP-1 | [Agents (MCP)](../wiki/operations/mcp.md) (operator). Herder side is [API tokens](../wiki/operations/api-tokens.md). The process is not in this image |
| Jr-1 / Jr-2 | [Multi-worker](../wiki/operations/multi-worker.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Troubleshooting](../wiki/troubleshooting/index.md) |
| Brand-1 / Brand-2 | [Appearance](../wiki/getting-started/appearance.md) · [Settings](../wiki/operations/settings.md) → General → Instance |
| HA-cards | [Home Assistant](../wiki/integrations/home-assistant.md). Plugin **0.3.0**. Walk after HACS updates |
| Regression | [Move a service](../wiki/docker/service-migration.md) · [Web SSH](../wiki/day-to-day/web-ssh-console.md) · [Home Assistant](../wiki/integrations/home-assistant.md) |

---

## How to run this

| | |
|--|--|
| **Instance** | On `v1.7.0-dev`: `docker compose build web celery-worker && docker compose up -d web celery-worker`. App code is **not** bind-mounted. About / footer still **1.6.0** until freeze |
| **Workers** | Jr-1 needs **celery-worker** up. nmap stays on `celery-worker-nmap`. MCP-1 is a separate process, not a compose service in this repo |
| **Browsers** | Desktop Chrome or Firefox **and** one phone (Brand) |
| **Accounts** | One **admin**, one **operator**, one **viewer** |
| **Hosts** | One real SSH host you can patch or deploy a stack on, plus one host you can make unreachable (SSH down) for the wait row. Do not use the public demo |
| **Flags** | `PIHERDER_SERVICE_MIGRATE` stays **false** except a regression spot-check you explicitly turn on, then off. Demo never live-runs patch or stack jobs |
| **Where to look** | Local herder: `http://127.0.0.1:8000` (Caddy `:8888` / `:8443`) |

**Do not test (Out or Discover):** Google Drive or NAS backup destinations (Bak-alt), per-host grants, the HA job-finished bus event, Undo-2, Mux-2 leftover list, container start/stop from HA, theme engine, logo upload, default-on Move, rewriting `onclick` for CSP. Do not look for the MCP adapter or the Home Assistant plugin inside the PiHerder image. Walk **HA-cards** on plugin **0.3.0**, not on **0.2.4**.

### Suggested order

1. **MCP-1** against a local herder. Start with a scope-`read` token, then a token that also has `jobs`, `edit`, and `files`. About / footer on the herder still **1.6.0**.  
2. Rebuild local **web** + **celery-worker** (Jr-1 is in this branch; the running containers do not see it until that rebuild).  
3. **Jr-1** on one real host (patch or stack), including a web recycle and a worker recycle.  
4. Host-down wait on a host whose SSH you can refuse.  
5. **Coverage** has landed at fail-under **80** (compose **81.01%**, 39638/48927). The extra point is headroom.  
6. **Brand** if the slice has landed.  
7. **1.6 regression** with Move still off.  
8. Leave **Freeze gates** empty. Tick a box only after you have walked it.

---

## MCP-1 — read/write token-API adapter (Must, first)

Walk this in the adapter repo, against this herder. The adapter is **not** in the PiHerder image. Do not point it at the public demo.

- [ ] Process starts with `PIHERDER_URL` and `PIHERDER_TOKEN` over stdio (`uvx --from git+https://github.com/bjorngluck/piherder-mcp.git piherder-mcp`)  
- [ ] A scope-`read` token exposes health, summary, servers, inventory, services, and jobs (list and detail), and those match `curl`  
- [ ] That `read` token has no `set_features`, `trigger_job`, or files tool  
- [ ] A token without `read` fails closed (message on stderr)  
- [ ] `trigger_job` with `jobs` accepts only `backup`, `retention`, `os_patch`, `container_patch`, `os_update_check`, `container_update_check`  
- [ ] `trigger_job` returns **202**, and **409** when one is already active, without starting a second job  
- [ ] `set_features` with `edit` changes only `backup`, `os_patch`, and `docker`  
- [ ] Files tools with `files` stay in the fleet jail: list, read, write, mkdir, rename, delete a file or empty directory  
- [ ] Read tools are marked read-only. `set_features`, `trigger_job`, `write_file`, `rename_file`, and `delete_file` are marked destructive  
- [ ] No tool opens SSH, a console, Move, undo, a compose stack action, or token admin  
- [ ] Cursor, Grok, Claude, and Codex samples each launch that same stdio command  
- [ ] One instruction template: Cursor rule and Grok skill share a body; Claude and Codex get the same short copy  
- [ ] Nothing from this slice is baked into the PiHerder image  

## Jr-1 — exclusive jobs on Celery (Must)

Code is on `v1.7.0-dev`. Task name `app.tasks.exclusive_job`, default queue, container **`piherder-celery`**. Not Move’s backup mutex. Default host wait **30 minutes** (`PIHERDER_EXCLUSIVE_HOST_WAIT_SEC`, probe every 30s). Wiki: [Multi-worker](../wiki/operations/multi-worker.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md).

Boxes stay empty until you walk them. Do this on the **local** herder. Do not redeploy or exercise this on the public demo.

### Setup

```bash
docker compose build web celery-worker && docker compose up -d web celery-worker
docker logs --tail 30 piherder-celery
```

The worker log should show it ready, consuming the default queue (no `-Q nmap` on this container). About / footer still **1.6.0**.

While a job is in flight:

```bash
docker logs --since 5m piherder-web 2>&1 | grep 'Enqueued'
docker logs --since 5m piherder-celery 2>&1 | grep exclusive_job
```

Web should log `Enqueued <type> job #<id> … on the default Celery queue`. The worker should log `Received task: app.tasks.exclusive_job`. A hit only in the web log, with no `exclusive_job` on `piherder-celery`, fails the row.

### Enqueue

Use one real SSH host. You do not need every stack action if one mutate and one check are honest; tick only the rows you actually ran.

- [ ] `os_patch` and `container_patch` enqueue on **celery-worker** (not the web process). Server → check for updates, then apply. JobHold leaves **pending** and reaches **running** / **success** or a real apt/compose failure. Worker log shows `exclusive_job`  
- [ ] Update checks (`os_update_check`, `container_update_check`, `docker_stack_check`) enqueue on **celery-worker**. Dashboard or server **Check**, and one stack **Check updates**. Same log pair as above  
- [ ] Stack mutate (`deploy` / `stop` / `start` / `restart` / `down` / `remove`) enqueues on **celery-worker**. One disposable project is enough: **Deploy** or **Restart**, and note the job id  
- [ ] Template deploy / redeploy / drift check enqueue on **celery-worker**. Catalog → deploy a small template, or an existing deployment → **Check drift** / **Save & redeploy**  

### Recycle and host-down

- [ ] Recycle **web** during a running patch or stack job: the Job does **not** fail because web died. Start a slow apply or deploy, then `docker compose up -d --force-recreate --no-deps web`. Job stays **pending** or **running** and still finishes or fails for a real reason. It must not say `Web process restarted — this job was no longer running`  
- [ ] Recycle **celery-worker** during a **running** patch or stack mutate: the Job **fails honest**. Wait until status is **running** (not still waiting on SSH), then `docker compose up -d --force-recreate --no-deps celery-worker`. Job becomes **failed**. Details include `Worker restarted while this job was running. It was not resumed.` Do not expect apt or compose to continue  
- [ ] SSH down at start: Job stays **pending** until SSH works or max wait; it does not fail on the first refused connect. On a lab host only, point SSH at a closed port (or stop `sshd`) and start an OS check or patch. JobHold stays **pending** and the log says `Host SSH unreachable — waiting`. Restore SSH. The same job should proceed without a second click. Leaving it down for 30 minutes fails it with `Host stayed unreachable` — that is the limit, not the first probe  

### Lanes that must stay put

- [ ] A second stack mutate on the same host is refused while the first holds the lane. During the deploy/restart above, start Stop or Deploy again on that host. The UI follows the existing job. It does not start a second compose. `POST /api/v1/servers/{id}/jobs` for that type returns **409** with `already_active`  
- [ ] A single-host patch does **not** take Move’s dual-host backup mutex. With a patch **running** on host A, a backup on host B still starts. Worker log for the patch has no backup-lock wait. Move stays off (`PIHERDER_SERVICE_MIGRATE=false`)  
- [ ] nmap still runs on the nmap queue. If `celery-worker-nmap` is up, a LAN discover shows on `piherder-celery-nmap`, not as `exclusive_job` on `piherder-celery`. If the profile is not running, confirm the main worker command has no `-Q nmap` and skip the live scan  
- [ ] `retention` and `herder_backup` behave as before. Run one. It is **not** `exclusive_job`. Recreate **web** while it is still running and that row **fails** with the web-restart message. Patch/stack rows from the recycle test above do not  
- [ ] `backup`, Move, and Undo still run on Celery as in 1.6. One backup: web log enqueues backup, worker runs `backup_server`, recreate **web** does not fail it. Move wizard stays **404** while the flag is false. Undo is not offered on a green or absent Move  
- [ ] Demo does not live-run these types. This walk stays on the local herder. Do not open the public demo and do not redeploy it onto `v1.7.0-dev`  
- [ ] Token API does not gain `service_migrate` or undo. With a `jobs` token, `POST /api/v1/servers/{id}/jobs` body `{"job_type":"service_migrate"}` is **400** `Unsupported job_type`. The allowed list is backup, retention, os_patch, container_patch, os_update_check, container_update_check, and `host_reboot`. `service_migrate_undo` is the same **400**  

## Brand-1 — instance name + accent (Should; may slip)

Settings → **General** → **Instance**. Boxes stay empty until you walk them. Rebuild **web** first. The header image stays the official mark.

- [ ] Empty instance name keeps PiHerder wording and the official mark. Clear the name, save, and reload. The header still reads **Pi** / **Herder**. The mark image is unchanged  
- [ ] A set name shows in the header and the PWA title. Save a short name such as **Homelab**. The header text, the footer, and the sign-in wordmark use it. View source: `apple-mobile-web-app-title` is that name. `GET /manifest.webmanifest` has `"name": "Homelab"` and `"theme_color": "#e60012"`  
- [ ] One accent recolours `--color-accent` only; primary red stays `#e60012`. Pick a blue, save, and reload. Links and accent chips follow it. The mark and the red buttons stay red. Putting the accent back on `#00a651` restores the official green  
- [ ] No header logo upload. The Instance card has a name and a color. It has no file field  
- [ ] Env `PIHERDER_INSTANCE_NAME` / `PIHERDER_ACCENT` when set cannot be overridden in the UI. With either variable set in `.env` and **web** recreated, that field is disabled and a save does not change it  
- [ ] Public demo still shows official PiHerder chrome. Do not redeploy the demo. This walk is the local herder  

## Brand-2 — hide Catalog (Should; may slip)

Settings → **General** → **Instance** → **Show Catalog in the navigation**. Default is on. Boxes stay empty until you walk them. Rebuild **web** first.

- [ ] Default: Catalog still in the nav. Desktop links and the phone menu both include Catalog  
- [ ] Hide removes the nav item and does **not** 404 `/catalog`. Uncheck the box, save, reload. Catalog is gone from the header and the phone menu. Open `/catalog` directly. The page still loads, including its own tabs  
- [ ] Viewer and operator both follow the instance setting. Sign in as a non-admin after the hide. Catalog is gone for that account too. Check the box again and save. Catalog returns for both  

## Jr-2 — max wait in Settings (Should; may slip)

Settings → **General** → **Jobs**. Default **30 minutes**. Env `PIHERDER_EXCLUSIVE_HOST_WAIT_SEC` locks the field when set. Boxes stay empty until you walk them.

- [ ] Settings shows max wait; SSH probe is still what resumes the job. Open the Jobs card. The minutes field is there (1–1440). Save a short value such as **2** on a lab host, then refuse SSH and start an OS check. The pending log should mention the new limit (about 120s, not 1800). Restore SSH. The same job proceeds. Set the field back to **30** when you are done. If the env var is set, the field is disabled and the save does not change the wait  
- [ ] Kuma down or stale `last_seen` can show “waiting on host” and does **not** by itself resume or fail the job. With a host whose Kuma SSH monitor is down, or whose `last_seen` is over 15 minutes old, start a check while SSH still works. The Jobs row or JobHold may say **waiting on host** before the probe, then the job **runs** anyway. It must not fail just because Kuma or `last_seen` looks down. With SSH actually refused, the job stays **pending** until the probe works or the wait you saved elapses  

## HA-cards — Lovelace cards and token writes (Should; may slip)

Plugin **0.3.0** in [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). Boxes stay empty until you walk them. The herder image does not contain the plugin. Update HACS to **0.3.0** and set the card resource to `/local/piherder-dashboard-card.js?v=0.3.0`. The token needs `jobs` (and `edit` for the toggles). `host_reboot` also needs the host’s OS-patch flag on.

- [ ] A `read` token still shows the fleet card and the host sensors, and shows no backup, check, patch, retention, reboot, or feature control  
- [ ] Host card is one server (`server_id`). It shows gauges, 24h sparklines, and the Host / Docker / Backups / Alerts / Audit links. It does not SSH  
- [ ] Updates card shows OS and container update counts from that snapshot, plus reboot pending  
- [ ] Resources card draws memory %, disk %, and CPU load from Home Assistant history. After a System Info refresh and one poll, a new point appears. The line steps on the snapshot (about 15 minutes). It is not a live chart  
- [ ] Confirm backup calls `POST /api/v1/servers/{id}/jobs` with `backup` and the job appears on the herder. A second confirm while it is active gets **409** and does not start another  
- [ ] OS check and container check enqueue those job types. OS patch and container patch ask for a confirm, then enqueue. Retention asks for a confirm  
- [ ] Restart host names the machine and says it reboots it. The herder job type is `host_reboot`. Start an OS patch first: the reboot confirm is refused (**409**) and does not SSH. The PiHerder Reboot button uses the same job  
- [ ] Feature toggles call `PATCH /api/v1/servers/{id}/features` for backup, OS patch, and Docker only. Turning one off asks first. A token without `edit` does not show them  
- [ ] No container start/stop, Move, Files, or console control appears on any card  
- [ ] Public demo is not the target. Do not point the plugin at it  

## Q — fail-under 75 → 80 (Should; may slip)

- [x] CI `--cov-fail-under` is **80** on `app` — compose **81.01%** (39638/48927), 1694 passed. The extra point stays as headroom. Floor was **75**  
- [ ] Fail-under is not lowered below **75**  
- [ ] No live SSH / apt / two-host copy in CI  

## 1.6 regression

- [ ] Move still off unless you turn the flag on for a spot-check; flag returns to **false**  
- [ ] Console mux still opt-in; HAOS and demo never mux  
- [ ] HACS fleet card still loads against this herder (plugin not in the image)  
- [ ] Home install CSP still enforces the script nonce; `onclick` still works  
- [ ] Expired session → Sign in (not JSON)  

## Freeze gates

- [ ] `mkdocs build --strict`  
- [ ] [RELEASE_v1.7.0.md](RELEASE_v1.7.0.md) drafted  
- [ ] Package bumped to **1.7.0** only when asked  
- [ ] Draft PR undrafted, merged, tagged, and Hub-published only when asked  
