# PiHerder v1.7.0 — operator QA / sign-off

**Branch:** `v1.7.0-dev` → `main` · tag **`v1.7.0`** (cut after merge)  
**Code freeze:** *open*  
**Package:** **`1.6.0`** until freeze  
**Operator QA:** *not started*

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the operator pages while ticking boxes.

Plan: [PLAN_v1.7.0.md](PLAN_v1.7.0.md).

1.6 production sign-off stays [QA_v1.6.0.md](QA_v1.6.0.md) (historical). Do **not** re-open 1.6 boxes here.

**Tag honesty:** freeze only with **MCP-1** and **Jr-1**. CI fail-under must not drop below **75**. The step to **80** is Should and may slip. Do not bump version, merge, tag, or Hub until asked. Do not redeploy the public demo onto this branch.

**This pass is sign-off, not new features.** Fix only a feature or regression bug you hit while walking. Discover (Bak-alt, AC-fg, HA Slice 2, Undo-2, Mux-2) stays parked until a row is promoted.

---

## Operator pages to walk

| Stream | Wiki |
|--------|------|
| MCP-1 | [Agents (MCP)](../wiki/operations/mcp.md) (operator). Herder side is [API tokens](../wiki/operations/api-tokens.md). The process is not in this image |
| Jr-1 / Jr-2 | [Multi-worker](../wiki/operations/multi-worker.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Troubleshooting](../wiki/troubleshooting/index.md) |
| Brand-1 / Brand-2 | Settings → General (page lands with the slice) · [Catalog](../wiki/index.md) |
| Regression | [Move a service](../wiki/docker/service-migration.md) · [Web SSH](../wiki/day-to-day/web-ssh-console.md) · [Home Assistant](../wiki/integrations/home-assistant.md) |

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.7.0-dev`** (`docker compose build web celery-worker && docker compose up -d`). App code is **not** bind-mounted. About / footer still **1.6.0** until freeze |
| **Workers** | Jr-1 needs **celery-worker** up. nmap stays on `celery-worker-nmap`. MCP-1 is a separate process, not a compose service in this repo |
| **Browsers** | Desktop Chrome or Firefox **and** one phone (Brand) |
| **Accounts** | One **admin**, one **operator**, one **viewer** |
| **Hosts** | One real SSH host you can patch or deploy a stack on, plus one host you can make unreachable (SSH down) for the wait row. Do not use the public demo |
| **Flags** | `PIHERDER_SERVICE_MIGRATE` stays **false** except a regression spot-check you explicitly turn on, then off. Demo never live-runs patch or stack jobs |
| **Where to look** | Local herder: `http://127.0.0.1:8000` (Caddy `:8888` / `:8443`) |

**Do not test (Out or Discover):** Google Drive or NAS backup destinations (Bak-alt), per-host grants, backup-from-HA, Undo-2, Mux-2 leftover list, HA start/stop, theme engine, logo upload, default-on Move, rewriting `onclick` for CSP. Do not look for the MCP adapter inside the PiHerder image.

### Suggested order

1. **MCP-1** against a local herder (adapter repo, once it exists). Start with a scope-`read` token, then a token that also has `jobs`, `edit`, and `files`. About / footer on the herder still **1.6.0**.  
2. Rebuild local **web** + **celery-worker** when Jr-1 has landed.  
3. **Jr-1** on one real host (patch or stack), including a web recycle and a worker recycle.  
4. Host-down wait on a host whose SSH you can refuse.  
5. **Coverage** if the 80% step has landed. The tag is still allowed at fail-under **75**.  
6. **Brand** if the slice has landed.  
7. **1.6 regression** with Move still off.  
8. Leave **Freeze gates** empty.

---

## MCP-1 — read/write token-API adapter (Must, first)

Walk this in the adapter repo, against this herder. The adapter is **not** in the PiHerder image. Do not point it at the public demo.

- [ ] Process starts with `PIHERDER_URL` and `PIHERDER_TOKEN` over stdio (`uvx piherder-mcp`)  
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

- [ ] `os_patch` and `container_patch` enqueue on **celery-worker** (not the web process)  
- [ ] Update checks (`os_update_check`, `container_update_check`, `docker_stack_check`) enqueue on **celery-worker**  
- [ ] Stack mutate (`deploy` / `stop` / `start` / `restart` / `down` / `remove`) enqueues on **celery-worker**  
- [ ] Template deploy / redeploy / drift check enqueue on **celery-worker**  
- [ ] Recycle **web** during a running patch or stack job: the Job does **not** fail because web died  
- [ ] Recycle **celery-worker** during a **running** patch or stack mutate: the Job **fails honest**  
- [ ] SSH down at start: Job stays **pending** until SSH works or max wait; it does not fail on the first refused connect  
- [ ] A second stack mutate on the same host is refused while the first holds the lane  
- [ ] A single-host patch does **not** take Move’s dual-host backup mutex  
- [ ] nmap still runs on the nmap queue  
- [ ] `retention` and `herder_backup` behave as before  
- [ ] `backup`, Move, and Undo still run on Celery as in 1.6  
- [ ] Demo does not live-run these types  
- [ ] Token API does not gain `service_migrate` or undo  

## Brand-1 — instance name + accent (Should; may slip)

- [ ] Empty instance name keeps PiHerder wording and the official mark  
- [ ] A set name shows in the header and the PWA title  
- [ ] One accent recolours `--color-accent` only; primary red stays `#e60012`  
- [ ] No header logo upload  
- [ ] Env `PIHERDER_INSTANCE_NAME` / `PIHERDER_ACCENT` when set cannot be overridden in the UI  
- [ ] Public demo still shows official PiHerder chrome  

## Brand-2 — hide Catalog (Should; may slip)

- [ ] Default: Catalog still in the nav  
- [ ] Hide removes the nav item and does **not** 404 `/catalog`  
- [ ] Viewer and operator both follow the instance setting  

## Jr-2 — max wait in Settings (Should; may slip)

- [ ] Settings shows max wait; SSH probe is still what resumes the job  
- [ ] Kuma down or stale `last_seen` can show “waiting on host” and does **not** by itself resume or fail the job  

## Q — fail-under 75 → 80 (Should; may slip)

- [ ] CI `--cov-fail-under` is **80** on `app`, or this Should is explicitly slipped and the floor is still **75**  
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
