# PiHerder v1.7.0 — token-API MCP, then one job runtime

**Status:** **Active** (train opened 2026-09-25). No product code yet.  
**Date opened:** 2026-09-25 (inbox parked 2026-09-18)  
**Git branch:** `v1.7.0-dev` → `main` · tag `v1.7.0` at freeze  
**Package / image version:** **`1.6.0`** until freeze  
**Theme:** **MCP-1** first (read/write client of the existing token API, separate repo), then **Jr-1** (remaining exclusive jobs onto Celery)  
**Baseline:** `v1.6.0` (tagged 2026-09-25; Hub digest `sha256:cdf88c70099f78830943e6529f05eff1b83bb5565e7b12f71ebf0877c7b018a8`)  
**Mode:** **Must → Should → Discover.** Must **MCP-1** + **Jr-1**. Should **Q** (fail-under **75 → 80**) + **Brand-1** + **Brand-2** + **Jr-2**. Discover **AC-fg** · HA Slice 2 · Undo-2 · Mux-2 · **Bak-alt**.  
**QA:** [QA_v1.7.0.md](QA_v1.7.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.6.0.md](PLAN_v1.6.0.md) · [RELEASE_v1.6.0.md](RELEASE_v1.6.0.md) · [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 J-runtime · §4 Brand · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [API.md](API.md) · [SPEC.md](../SPEC.md) · wiki [multi-worker](../wiki/operations/multi-worker.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md)

> **Train open 2026-09-25.** Production stays **v1.6.0** on `main`. Package stays **`1.6.0`** until freeze. Kill switch `PIHERDER_SERVICE_MIGRATE` stays **false**. Do not redeploy the public demo onto this branch.

---

## 0. Intent

Agents that already hold a PiHerder token still have to call HTTP themselves. The first slice is an MCP adapter, in its **own repo**, that wraps the existing bearer API: read, and the writes that token is allowed to make (`jobs`, `edit`, `files`). It does not ship inside this image and it does not add herder routes.

Move, undo, backup, and nmap already run on Celery. OS patch, container patch, update checks, compose stack jobs, and template jobs still run in the **web** process. Recycling **web** fails those rows (`cleanup_orphan_web_jobs`). **Jr-1** moves those remaining exclusive types in one go. It is Must, and it starts after MCP-1.

Instance chrome (wordmark, one accent, hide Catalog) was discovered in 1.5 and held out of 1.6. It is Should: it can slip without blocking the tag. So can the coverage step from 75% to 80%.

Wanted:

1. An MCP process an operator runs on the agent machine, configured with `PIHERDER_URL` and a bearer token. Read tools always. Write tools only for scopes the token already has  
2. Exclusive patch, check, stack, and template jobs survive a **web** recycle because they run on the default Celery queue  
3. A host that is down **waits** (pending + backoff) instead of failing the job immediately  
4. A worker kill of a **running** apt or compose stays **fail honest**  
5. Optional instance name + one accent, and a nav hide for Catalog, without a theme engine  
6. CI fail-under raised **75 → 80** if the suite can get there without lowering the floor  

**Out of 1.7 product code until promoted:** fine-grained grants, HA backup-from-HA, Undo-2, Mux-2, alternate backup destinations (**Bak-alt**), HA Slice 3, Brand-3, turning Move on by default.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.7.0-dev`** |
| Production line | **`main` @ `v1.6.0`** — hotfixes → **`v1.6.x`**, port here |
| Git tag (freeze) | **`v1.7.0`** (RCs: `1.7.0-rc.N` if needed) |
| Image tags (freeze) | `1.7.0` · `1.7` · `latest` (multi-arch); keep `1.6` / `1.6.x` pins valid |
| In-scope streams | **MCP-1** Must (first) · **Jr-1** Must · **Q** Should · **Brand-1** Should · **Brand-2** Should · **Jr-2** Should |
| Discover (no code until promoted) | **AC-fg** · HA Slice 2 · Undo-2 · Mux-2 · **Bak-alt** |
| Out-of-focus | HA Slice 3 · Brand-3 · theme engine · M-flag C (stay false) · plugin-in-image · MCP-in-image · remote HTTP MCP · CSP Slice 2 (`onclick` rewrite) · ACME · NPM CRUD · richer Files API · N3c · M-live · multi-tenant · Swarm/k8s |
| Mode | Must → freeze; Should may slip; Discover only if Must is green |
| Coverage | Floor stays **75**. Should raises CI fail-under **75 → 80** (1.x ceiling). Do not lower 75. The step may slip |
| E2E | Wizard chrome still loads. No live SSH / apt / two-host copy in CI |
| Semver | Additive minor. MCP-1 adds no herder routes. Jr-1 keeps the same job types and moves execution to Celery |
| Version bump | `1.7.0` **at freeze only** |
| Kill switch | **`PIHERDER_SERVICE_MIGRATE=false`**. Demo never copies, never mux, never live-runs patch/stack jobs |

```text
main @ v1.6.0 (+ v1.6.x patches)
  └─ v1.7.0-dev → merge → main → tag v1.7.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | Do not start Out items while Must is open. Build **MCP-1** before **Jr-1** |
| Should may slip | Brand-1 / Brand-2 / Jr-2 / **Q (80%)** do **not** block the tag if MCP-1 and Jr-1 are green |
| Prod critical bugs | **main** as **1.6.x** first, then port here |
| Demo never grows teeth | Real migrate off · real Files SFTP off · mux off · no live apt/compose from the demo |
| Tag honesty | **v1.7.0 tags only with MCP-1 + Jr-1**. Fail-under must not drop below **75**. Reaching **80** may slip |

---

## 1a. Kickoff leans (locked 2026-09-25)

| # | Question | Decision |
|---|----------|----------|
| 1 | Theme / Must | **MCP-1** first, then **Jr-1**. Both Must. Jr-1 is still all remaining **exclusive** types in one go, default Celery queue. |
| 2 | Which types | `os_patch`, `container_patch`, `os_update_check`, `container_update_check`, `docker_stack_check`, `docker_stack_deploy` / `_stop` / `_start` / `_restart` / `_down` / `_remove`, `template_deploy`, `template_redeploy`, `template_drift_check`. |
| 3 | Left on web | `retention`, `herder_backup` (not exclusive). nmap stays **`-Q nmap`**. `backup`, `service_migrate`, `service_migrate_undo` stay as they are. |
| 4 | Host down | Job stays **pending**, backoff until SSH works or **max wait**. Exclusive slot held. SSH probe is source of truth. Not “replay a stored compose on wake.” |
| 5 | Running mutate + worker kill | **Fail honest.** Pending wait-for-host **redelivers**. |
| 6 | **Jr-2** | **Should.** Settings for max wait. Kuma / `last_seen` may **signal** only. |
| 7 | **Brand-1** | **Should.** Instance name + one accent. Official mark and primary red stay. No logo upload. Demo ignores it. |
| 8 | **Brand-2** | **Should.** Hide Catalog in the nav. `/catalog` still works. Default **show**. |
| 9 | **Brand-3** | **Out.** Own-docs MkDocs skin later. |
| 10 | **MCP-1** | **Must. First slice.** Separate repo (same shape as `piherder-ha`). stdio. Read and write of today’s bearer API only. Not in this image. No new herder routes. |
| 11 | **AC-fg** | **Discover.** Three global roles stay. No schema until a spike is promoted. Not multi-tenant. |
| 12 | HA Slice 2 / Undo-2 / Mux-2 / **Bak-alt** | **Discover.** Carried from 1.6, plus alternate backup destinations (under consideration). Not this freeze unless promoted. |
| 13 | HA Slice 3 / Move-from-HA / start-stop | **Out** |
| 14 | M-flag / default-on Move | **Stay false** |
| 15 | Coverage | **Should.** Raise fail-under **75 → 80**. May slip. Do not lower **75**. |
| 16 | Version bump | `1.7.0` at freeze only |
| 17 | Public demo | Stays on the **1.6** image. Do not redeploy it onto `v1.7.0-dev`. |

---

## 1b. Recommended delivery order

```text
Phase 0   Open train + docs lock              done 2026-09-25 (22d3a04)
Phase 0b  Lock retune                         done 2026-09-25 (85d0ec4)
Phase 0c  MCP-1 read/write contract           done 2026-09-26 (3f63d16)
Phase 0d  Operator wiki + remaining pointers  ← this commit
Phase 1   MCP-1 stdio adapter                 Must — first build, separate repo
Phase 2   Jr-1 exclusive types → Celery       Must
Phase 3   Q fail-under 75 → 80                Should (may slip)
Phase 4   Brand-1 + Brand-2                   Should (may slip)
Phase 5   Jr-2 Settings max wait              Should (may slip)
Phase 6   Discover spikes                     only if Must is green and you promote
Phase 7   Wiki + QA_v1.7.0                    operator sign-off
Phase 8   Freeze                              1.7.0 bump · RELEASE · PR · tag · Hub — only when asked
```

Must before Should product. MCP-1 before Jr-1. Discover only if Must is green and you promote one row.

---

## 2. Stream **MCP-1** — read/write token-API adapter (Must, first slice)

Not product code in the PiHerder image. Own repo, same shape as [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). This host cannot create that GitHub repo. Lean name when you create it: `bjorngluck/piherder-mcp`. This train’s herder tree does not gain a scaffold for it. The process runs on the **agent machine** (the laptop running Cursor, Grok, Codex, or Claude), not in the herder container.

**Locks:**

1. **stdio only.** One command everywhere: `uvx piherder-mcp`. Config is `PIHERDER_URL` plus `PIHERDER_TOKEN`. The token is never committed. Remote Streamable HTTP stays out.
2. Hand-written tools over existing `/api/v1` only. Do not generate a tool per OpenAPI path. No new herder routes.
3. Startup calls `GET /api/v1`. Tools register only for scopes on that token. Missing `jobs`, `edit`, or `files` means those tools are absent. A token without `read` fails closed (stderr, never stdout).
4. Write is the bearer writes that already exist: trigger the six job types, patch `backup` / `os_patch` / `docker`, and fleet-jail files (list, read, write, mkdir, rename, delete a file or empty directory). Feature flags and `feature:*` scopes stay the API’s job.
5. Not tools, and not new routes: SSH, console, Move, undo, compose stack actions, template deploy, nmap, DNS, certificates, settings, token admin. Richer Files (chmod, zip, recursive delete, privileged paths) stay UI-only.
6. `trigger_job` returns the API body on **202** and on **409**. On 409 the agent polls `get_job` and does not fire again.
7. Read tools set `readOnlyHint`. `set_features`, `trigger_job`, `write_file`, `rename_file`, and `delete_file` set `destructiveHint`.
8. Server name `piherder`. Tool names are the short names below (no second `__`). File bodies capped around **256 KiB** in the MCP result, with a note when cut. Logs go to stderr.
9. Official MCP Python SDK. Do not hand-roll JSON-RPC.
10. Demo is not a target. Nothing from this slice is copied into `app/` or the image.

| Tool | HTTP | Scope |
|------|------|-------|
| `health` | `GET /api/v1/health` | `read` |
| `summary` | `GET /api/v1/summary` | `read` |
| `list_servers` | `GET /api/v1/servers` | `read` |
| `get_server` | `GET /api/v1/servers/{id}` | `read` |
| `inventory` | `GET /api/v1/inventory` or `.../servers/{id}/inventory` | `read` |
| `services` | `GET /api/v1/services` | `read` |
| `list_jobs` | `GET /api/v1/jobs` or `.../servers/{id}/jobs` | `read` |
| `get_job` | `GET /api/v1/jobs/{id}` | `read` |
| `set_features` | `PATCH /api/v1/servers/{id}/features` | `edit` |
| `trigger_job` | `POST /api/v1/servers/{id}/jobs` | `jobs` |
| `list_files` | `GET /api/v1/servers/{id}/files` | `files` |
| `read_file` | `GET .../files/download` | `files` |
| `write_file` | `POST .../files` | `files` |
| `mkdir` | `POST .../files/mkdir` | `files` |
| `rename_file` | `POST .../files/rename` | `files` |
| `delete_file` | `DELETE .../files` | `files` |

`trigger_job` accepts only `backup`, `retention`, `os_patch`, `container_patch`, `os_update_check`, `container_update_check`.

**Clients.** Same stdio process. Samples live in the adapter repo and use `${PIHERDER_TOKEN}`, not a real secret. Grok’s defaults already import Cursor and Claude MCP configs; Codex does not, so Codex gets its own snippet.

| Client | Where the sample goes |
|--------|------------------------|
| Cursor | `.cursor/mcp.json` → `mcpServers.piherder` |
| Grok Build | `.grok/config.toml` → `[mcp_servers.piherder]`. Also loads Cursor’s `mcp.json` when `[compat.cursor] mcps` is on (the default) |
| Claude Desktop / Claude Code | `mcpServers.piherder`, or a project `.mcp.json` |
| Codex | `~/.codex/config.toml` → `[mcp_servers.piherder]` |

**Instruction template.** One body, two wrappers Grok already reads from a Cursor checkout: a Cursor rule `.cursor/rules/piherder.mdc`, and the same text as a Grok skill `skills/piherder/SKILL.md`. Short copies for Claude (`CLAUDE.md`) and Codex (`AGENTS.md`). The body says: call `summary` before mutating; `trigger_job` only for the six types; on 409 poll the existing job; files stay in the fleet jail; do not invent SSH, Move, or console; a token without `jobs` / `edit` / `files` has no such tool.

**Success (Must):**

1. The adapter runs as its own process against a PiHerder base URL.
2. Read tools return the same facts as `curl` with that token.
3. A `read`-only token exposes no write tool. A token with `jobs`, `edit`, or `files` can perform those bearer writes and no others.
4. `trigger_job` surfaces 202 and 409. It does not start a second job when one is already active.
5. Cursor, Grok, Claude, and Codex can each launch the same stdio command from the sample for that client.
6. Nothing from this slice is copied into the PiHerder image or `app/`.

---

## 3. Stream **J-runtime** — rest of the exclusive jobs

Owning notes: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 J-runtime. Operator page to update when it lands: [multi-worker](../wiki/operations/multi-worker.md).

**Today — Celery:** `backup`, `service_migrate`, `service_migrate_undo`, `nmap_*`, stale-data cleanup. Web recycle does not fail them.

**Today — web** (`BackgroundTasks` / thread pools; recycle web → startup `cleanup_orphan_web_jobs` fails the row):

| Family | Types | Lane |
|--------|-------|------|
| Patch | `os_patch`, `container_patch` | One active job of that type per host |
| Checks | `os_update_check`, `container_update_check`, `docker_stack_check` | One active check of that type per host |
| Stack mutate | `docker_stack_deploy` / `_stop` / `_start` / `_restart` / `_down` / `_remove` | One active stack mutation per host (shared lane) |
| Templates | `template_deploy`, `template_redeploy`, `template_drift_check` | Stack-mutating lane where they already share it |

**Locks (Jr-1):**

1. One slice onto the **default Celery queue**. nmap stays `-Q nmap`. Keep the DB exclusive rules and the stack-mutating lane. Do not take Move’s dual-host backup mutex for a single-host apt or stack job.  
2. Host unreachable: job stays **pending**, backoff until an SSH probe works or **max wait** elapses. Exclusive slot held the whole time. Kuma / `last_seen` are not the source of truth (that is Jr-2’s signal).  
3. Worker recycle of a **running** mutate: fail honest. A job still **pending** on host-down **redelivers**.  
4. After the move, `cleanup_orphan_web_jobs` must not fail these types. Recycling **web** leaves them running. Recycling **celery-worker** fails a running one.  
5. Demo never live-runs them.  
6. Token API job triggers that already exist keep their scopes. Jr-1 does not add routes and does not add `service_migrate` or undo to the token API.

**Success (Must):**

1. Each exclusive type above is enqueued on Celery, not `BackgroundTasks`.  
2. Recycle **web** during an `os_patch` or a stack deploy: the Job continues (or stays pending), it does not flip to failed because the web process died.  
3. Recycle **celery-worker** during a **running** patch or stack mutate: the Job fails honest.  
4. Host SSH down at start: Job stays pending until SSH returns or max wait; it does not fail on the first refused connect.  
5. Two stack mutates on the same host still cannot run together. A backup on that host still cannot overlap a Move. A single-host patch does not take the other host’s Move lock.  
6. nmap still uses the nmap queue. `retention` and `herder_backup` still run where they run today.

### Jr-2 (Should)

Settings control for **max wait**. Kuma down or stale `last_seen` may mark the job “waiting on host” in the UI. SSH probe remains the only resume condition. Missing this slice: Jr-1 ships with a documented default max wait and no Settings field.

---

## 4. Stream **Brand** — wordmark + accent (Should)

Owning notes: [PLAN_v1.5.0.md](PLAN_v1.5.0.md) §4 Brand. Operator leans from 2026-09-18 still hold.

**Today:** `--color-primary: #e60012` · `--color-accent: #00a651` · header PNG pair · `ph_brand()` Pi+Herder · Catalog always in the nav · PWA title “PiHerder”. Demo is official chrome.

**Locks:**

1. Settings → General: instance name (empty = `ph_brand()`). One accent hex → `--color-accent` and `--accent-subtle-bg` only. **Do not** recolour primary red or the mark.  
2. **No** header logo upload. Avatars and service logos stay those pipelines.  
3. PWA title follows the instance name when set. Optional env `PIHERDER_INSTANCE_NAME` / `PIHERDER_ACCENT` lock (blank = unlocked).  
4. Hide Catalog is instance-wide nav only. Do not 404 `/catalog`. Default **show**. Not auto-hide on first host.  
5. **Demo** ignores name, accent, and hide (always official PiHerder). Light/dark stay the built-in pair.

| Slice | What | Bar |
|-------|------|-----|
| **Brand-1** | Instance name + one accent; PWA title; demo ignored | Should |
| **Brand-2** | Hide Catalog in the nav; URL still works | Should |
| **Brand-3** | Own-docs MkDocs skin | **Out** |
| **Out** | Theme engine; replacing primary red; header logo upload; white-label; per-user skins | |

---

## 5. Discover (no product code until a row is promoted)

| ID | Item | Notes |
|----|------|--------|
| **Bak-alt** | Alternate backup destinations | **Under consideration.** Today per-server backups rsync onto a directory on the herder host (`PIHERDER_BACKUP_HOST_PATH`, default `./backups`, mounted at `/backups`). Examples to write up, not to build: Google Drive, a LAN NAS, and similar. Not a vendor choice. Not OAuth, not a NAS client, not a replacement for that rsync directory. PiHerder’s own Settings backup stays a separate DR path. |
| **AC-fg** | Per-host / per-feature grants | No discover spike yet. Three global roles stay. Not multi-tenant SaaS. Promote only by writing the grant model first; no schema in this train until that promotion. |
| **HA Slice 2** | Backup-from-HA | Confirm + `piherder.backup`. Token `jobs` + `feature:backup`. Optional OS **check** (not apply). Bus event `piherder_job_completed` stays here. Plugin repo, not this image. |
| **Undo-2** | Beyond fail-path undo | Undo-1 shipped in 1.6. Do not reverse a green Move. No dest `down -v`. |
| **Mux-2** | Leftover mux sessions | List/kill `ph-u*` after web recycle, from the SSH-access UI. Leftover sessions after Remove server stay wiki-only until this is promoted. |

---

## 6. Ship bar

| Priority | Item | Bar | Status |
|----------|------|-----|--------|
| **Must** | **MCP-1** | stdio adapter in its own repo; read plus bearer writes (`jobs`, `edit`, `files`); four client samples; not in this image | Not started — first build, after the repo exists |
| **Must** | **Jr-1** | Exclusive types on the default Celery queue; host-down waits; running mutate fails honest; web recycle does not fail them | Not started — after MCP-1 |
| **Should** | **Q** | CI fail-under **75 → 80**. Floor stays 75 if this slips | Not started |
| **Should** | **Brand-1** | Instance name + one accent; demo ignored | Not started |
| **Should** | **Brand-2** | Hide Catalog in nav; `/catalog` still works | Not started |
| **Should** | **Jr-2** | Settings max wait; Kuma/`last_seen` is a signal | Not started |
| **Discover** | Bak-alt · AC-fg · HA Slice 2 · Undo-2 · Mux-2 | Notes only unless promoted | Parked |
| **Out** | HA Slice 3 · Brand-3 · M-flag C · plugin-in-image · MCP-in-image · remote HTTP MCP · CSP Slice 2 | Stay out | Locked 2026-09-26 |

---

## 7. Quality bar

| Gate | Target |
|------|--------|
| Unit | Floor **75**. Should raises `--cov-fail-under` to **80** on `app`. If that slips, the tag still requires the floor. Jr-1 tests cover enqueue-on-Celery, web-recycle does not fail, worker-recycle fails a running mutate, host-down stays pending, exclusive lane still blocks a second stack mutate. No live SSH. MCP-1 tests live in the adapter repo and mock HTTP: scope-filtered tools, `trigger_job` 202 and 409, no call to SSH, Move, or console |
| E2E | Wizard chrome. No live apt, compose, or two-host copy in CI |
| Docs | Wiki multi-worker + Jobs when Jr-1 lands; `mkdocs build --strict` at freeze |
| Security | Same exclusive lanes. No new token scope. Move and undo stay off the token API. MCP write tools use `jobs`, `edit`, and `files` only. Demo never live-runs the moved types and is not an MCP target. Brand env lock cannot be overridden from the UI when set |

---

## 8. Out of scope (stay honest)

- **HA Slice 3** — start/stop from HA, webhooks, alerts API, Move-from-HA, Files from HA  
- **Brand-3** — own-docs MkDocs skin; theme engine; header logo upload; recolouring primary red  
- **M-flag C** — `PIHERDER_SERVICE_MIGRATE` stays **false**. Do not turn Move on by default  
- Plugin, add-on, or **MCP adapter inside** the PiHerder image  
- **Remote HTTP / Streamable HTTP MCP** on the herder. Clients launch a local stdio process  
- **CSP Slice 2** — rewriting `onclick` to drop `script-src-attr 'unsafe-inline'`  
- Reverse a **green** Move · dest `down -v`  
- ACME-in-herder · full NPM CRUD · richer Files token API · **N3c** · **M-live**  
- Multi-tenant SaaS · k8s/bare  
- Building Google Drive, NAS, or any other backup destination (**Bak-alt** is notes only)  
- Lowering CI fail-under below **75**  

---

## 9. Capture log

| Date | Note |
|------|------|
| 2026-09-18 | Candidate file created. Inbox: **J-runtime**. Train not opened. |
| 2026-09-19 | **v1.6 opened.** Inbox grew: **AC-fg** and **Brand-1/2** (both out of 1.6). |
| 2026-09-25 | Inbox grew: **MCP-1** (read-only agent adapter, separate repo, `/api/v1` read token). Not started. |
| 2026-09-25 | **v1.6.0 tagged.** Package **1.6.0**. Hub `1.6.0` / `1.6` / `latest`. |
| 2026-09-25 | **Train opened** on `v1.7.0-dev`. Must **Jr-1**. Should **Brand-1** + **Brand-2** + **Jr-2**. Discover **MCP-1** · **AC-fg** · HA Slice 2 · Undo-2 · Mux-2. Package stays `1.6.0` until freeze. `main` patchable as **v1.6.x**. Fail-under stays **75**. Public demo stays on the 1.6 image. |
| 2026-09-25 | **Lock retune.** **MCP-1** is Must and the first slice (separate repo, read-only). **Jr-1** stays Must, second. **Q** is Should: fail-under **75 → 80**, may slip, floor stays 75. **Bak-alt** added as Discover (Google Drive, LAN NAS, and similar — notes only; rsync directory stays). |
| 2026-09-26 | **MCP-1 contract.** Read and write of the existing bearer API (`read`, `jobs`, `edit`, `files`). stdio only, so Cursor, Grok, Claude, and Codex share one process. Separate repo. No new herder routes. Remote HTTP MCP stays out. Adapter code waits on the repo. |
| 2026-09-26 | Operator page [wiki/operations/mcp.md](../wiki/operations/mcp.md). Nav, API tokens, Jobs, Host Files, demo, architecture, and the maintainer pointers name that page. |

---

## 10. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.7.0-dev`** + lock Must/Should | **Done** 2026-09-25 (`22d3a04`) |
| 2 | Retune: MCP-1 first, Q is Should, Bak-alt noted | **Done** 2026-09-25 (`85d0ec4`) |
| 3 | MCP-1 read/write contract in this plan | **Done** 2026-09-26 (`3f63d16`) |
| 3b | Operator wiki [Agents (MCP)](../wiki/operations/mcp.md) and the remaining pointers | **This commit** |
| 4 | **MCP-1** after you create the adapter repo | Not started — first build |
| 5 | **Jr-1** exclusive types → default Celery queue | Not started |
| 6 | Operator walk | [QA_v1.7.0.md](QA_v1.7.0.md). Boxes stay empty until walked |
| 7 | Q / Brand-1 / Brand-2 / Jr-2 as capacity after Must | Not started |
| 8 | Freeze · `1.7.0` · tag · Hub | Only when asked |

---

*Production remains [RELEASE_v1.6.0.md](RELEASE_v1.6.0.md) until this train freezes.*
