# PiHerder v1.8.0 — MCP job types, then backup destinations, then Home Assistant cards

**Status:** **Active** (lock retuned 2026-09-30). **MCP-jobs** landed. **Bak-alt discovery** is §3.1. **Google Drive**, **Mux-2**, **Undo-2**, and **Jr-web** are built on this branch; walks are still open and those Should rows may slip. **HA-vis** is built in plugin **0.4.0** (one card, host strip, click-through history); the walk is still open. Plugin **0.4.1** starts or stops one compose service from that card. **HA bus** is built in the same plugin; the walk is still open and it may slip. Package stays `1.7.0`.  
**Date opened:** 2026-09-28  
**Git branch:** `v1.8.0-dev` → `main` · tag `v1.8.0` at freeze  
**Package / image version:** stays **`1.7.0`** until freeze  
**Theme:** **MCP-jobs** first, then **Bak-alt** (discovery, then one Google Drive destination), then **HA-vis**  
**Baseline:** `v1.7.0` (tagged 2026-09-28; Hub digest `sha256:174cb1313f6717d323211c8c899b30240e97f5097bd35770f7a6c4555de95270`)  
**Mode:** **Must → Should → parked.** Must **MCP-jobs** + **Bak-alt discovery** + **HA-vis**. Should **Google Drive** + **HA bus** + **Mux-2** + **Undo-2** + **Jr-web**.  
**QA:** [QA_v1.8.0.md](QA_v1.8.0.md) (maintainer stub — **not** the operator wiki)  
**Related:** [PLAN_v1.7.0.md](PLAN_v1.7.0.md) · [RELEASE_v1.7.0.md](RELEASE_v1.7.0.md) · [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md) · wiki [Home Assistant](../wiki/integrations/home-assistant.md)

> **Train open 2026-09-28.** Production stays **v1.7.0** on `main`. Package stays **`1.7.0`** until freeze. Do not redeploy the public demo onto this branch.

---

## 0. Intent

Hosted MCP used to start six job types. **MCP-jobs** widened `trigger_job` to the bearer jobs POST list (`host_reboot`, the compose stack actions on that list, template deploy and redeploy). No new herder route. Move, undo, and nmap stay off the tool.

Per-server backups still rsync onto a directory on the herder host. §3.1 is the destination model: a second hop from that tree (path A). The build lean is **Google Drive**, one rclone copy, and it may slip. **OneDrive** and a **LAN NAS / SMB share** are named for later releases and are not built here. A client on each Pi that writes straight to cloud or NAS (path C) stays parked. The rsync directory stays. PiHerder’s own Settings DR backup stays that separate path.

Plugin **0.3.0** shipped with v1.7.0. Plugin **[v0.4.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.0)** is the visual pass: one card, a host strip, and a click that opens Home Assistant history of the snapshot sensors (about every 15 minutes, not a live SSH chart). Plugin **[v0.4.1](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.1)** adds Start and Stop for one compose service. The walks are still open. Screenshot recapture is [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v180--pack-status).

Wanted:

1. `trigger_job` enqueues every job type `POST /api/v1/servers/{id}/jobs` already accepts. Move, undo, nmap, console, and token admin stay off the tool  
2. A written destination model for the per-server backup: Google Drive this train, OneDrive and SMB later  
3. One Google Drive destination if the Should lands. The local rsync directory remains available  
4. Cards that are easier to read at a glance, and a 24-hour series that actually draws from HA history  
5. Optional after that: the job-finished bus event, leftover mux list/kill, a `dest_up` Move helper, and the three web-process jobs onto Celery  

**Out until a later train:** AC-fg, HA Slice 3, Brand-3, CSP Slice 2, ACME, plugin-in-image, Move default-on, MCP OAuth, an OneDrive client, an SMB/NAS client.

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.8.0-dev`** |
| Production line | **`main` @ `v1.7.0`** — hotfixes → **`v1.7.x`**, port here |
| Git tag (freeze) | **`v1.8.0`** |
| Image tags (freeze) | `1.8.0` · `1.8` · `latest` (multi-arch); keep `1.7` / `1.7.x` pins valid |
| In-scope | **MCP-jobs** Must (first) · **Bak-alt discovery** Must · **Google Drive** Should · **HA-vis** Must · **HA bus** Should · **Mux-2** Should · **Undo-2** Should · **Jr-web** Should |
| Parked (no code) | **AC-fg** · HA Slice 3 · Brand-3 · CSP Slice 2 · ACME · plugin-in-image · M-flag C · MCP OAuth · OneDrive client · SMB/NAS client |
| Version bump | Freeze only. About / footer stay **1.7.0** until then |
| Demo | Stays the published **1.7.0** image. Do not redeploy this branch |
| Coverage | Fail-under stays **80**. Do not lower it |

```text
main @ v1.7.0 (+ v1.7.x patches)
  └─ v1.8.0-dev → merge → main → tag v1.8.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → then freeze | **MCP-jobs** before the backup write-up. The write-up before **HA-vis**. Do not start parked items |
| Should may slip | Google Drive, the HA bus event, Mux-2, Undo-2, and Jr-web do **not** block the tag |
| Tag honesty | **v1.8.0 tags only with MCP-jobs + Bak-alt discovery + HA-vis** |
| Prod critical bugs | **main** as **1.7.x** first, then port here |
| Demo never grows teeth | No live Drive upload, apt, compose, or mux from the demo |

---

## 1a. Kickoff leans (locked 2026-09-28)

| # | Question | Decision |
|---|----------|----------|
| 1 | First slice | **MCP-jobs** is **Must** and first. Widen `trigger_job` to the existing jobs POST. No new route. |
| 2 | Which extra types | `host_reboot`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, `template_redeploy`. The six types from 1.7 stay. |
| 3 | Still refused | `service_migrate`, `service_migrate_undo`, nmap, console, Files beyond today’s tools, token admin, `docker_stack_down`, `docker_stack_remove`, `template_drift_check`. Those last three are not on `JobCreateBody`. |
| 4 | Backup discovery | **Must**, second. Model for the per-server rsync backup. Local directory stays. Settings DR backup stays its own path. |
| 5 | One built destination | **Should.** **Google Drive** only. May slip. |
| 6 | Later destinations | **OneDrive** and **LAN NAS / SMB** are in the write-up for a later release. No client this train. |
| 7 | HA-vis | **Must**, after MCP and the backup write-up. Plugin **0.4.0**. One card with Fleet / Host / Updates. Host strip, device and OS icons, click a stat for Home Assistant history. |
| 8 | HA bus | **Should.** Built in plugin 0.4.0. Poll-diff `piherder_job_completed`. No herder webhook. May slip. |
| 9 | Mux-2 | **Should.** List and kill leftover `ph-u*` on SSH access. No automatic reattach. Remove-server still does not `kill-server`. |
| 10 | Undo-2 | **Should.** `dest_up` worker-restart helper: inspect dest, optional stop-dest then start-source. No DNS revert. No green-Move undo. |
| 11 | Jr-web | **Should.** Built on this branch. `retention`, `herder_backup`, and `host_facts` leave web `BackgroundTasks`. `host_facts` uses the exclusive lane. The other two use the default queue and do not take a host slot. Walk still open. |
| 12 | AC-fg | **Parked.** No grant write-up. Three global roles stay. |
| 13 | Version bump | `1.8.0` at freeze only |
| 14 | Public demo | Stays on the **1.7** image |

---

## 1b. Recommended delivery order

```text
Phase 0   Lock this plan + QA + pointers     this docs pass
Phase 1   MCP-jobs                           Must, first
Phase 2   Bak-alt discovery                  Must (Drive lean; OneDrive + SMB later)
Phase 3   Google Drive destination          Should, one client
Phase 4   HA-vis (plugin 0.4.0)              Must
Phase 5   HA bus event                       Should, same plugin
Phase 6   Mux-2                              Should
Phase 7   Undo-2                             Should
Phase 8   Jr-web                             Should
Phase 9   Freeze                             only when asked
```

Must before Should product. MCP-jobs before the backup write-up. The write-up before HA-vis. Parked rows stay notes in section 8.

---

## 2. Stream **MCP-jobs** — wider `trigger_job` (Must, first)

Hosted path stays `POST /mcp` on this herder. The stdio adapter in [bjorngluck/piherder-mcp](https://github.com/bjorngluck/piherder-mcp) gets the same allowlist. No second Compose service. No OAuth.

**Locks:**

1. Add to `MCP_JOB_TYPES` in `app/services/mcp_hosted.py`, and to the stdio allowlist: `host_reboot`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, `template_redeploy`.
2. The tool still calls the existing `POST /api/v1/servers/{id}/jobs`. Feature flags and `feature:*` scopes stay the API’s job.
3. Stack and template calls may pass the body fields that POST already takes (`source_filter` for the compose project). Do not invent new fields.
4. `host_reboot` keeps today’s **409** while an OS patch, a container patch, or a backup is active, and the reverse.
5. On **409** the agent polls `get_job` and does not fire again. `trigger_job` stays `destructiveHint`.
6. Operator page [wiki/operations/mcp.md](../wiki/operations/mcp.md) lists every tool and job type. The stdio adapter is [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) **0.2.0** on PyPI ([notes](https://github.com/bjorngluck/piherder-mcp/blob/main/docs/RELEASE_v0.2.0.md)).

---

## 3. Stream **Bak-alt** — destination model, then Google Drive

### 3.1 Discovery (Must)

Today `run_backup` in [app/services/backup.py](../app/services/backup.py) pulls chosen directories over SSH. The worker runs `rsync -aHz --delete --numeric-ids` with `--rsync-path` `sudo -n rsync` (plain `rsync` on root or HAOS) into `{backup_dest_root}/{hostname}/{dest_name}`. The default root is `PIHERDER_BACKUP_HOST_PATH` (`./backups`, mounted at `/backups`). Path policy in [app/services/backup_path_policy.py](../app/services/backup_path_policy.py) denies `/` and the OS roots, so a whole-host copy is an explicit allow. The remote only needs SSH and `rsync`. Restore rsyncs that tree back. PiHerder’s own Settings backup (instance DR, `/herder_backups`) is not this stream.

rsync speaks a local path, SSH, or an `rsync://` daemon. It does not speak Google Drive, OneDrive, or SMB. Do not FUSE-mount Drive or OneDrive and point `rsync --delete` at that mount.

| Path | Data | This train |
|------|------|------------|
| **A. Second hop** | Remote → `/backups` (today’s rsync) → herder copies checked paths onward | **The model.** The Google Drive copy on this branch is this hop. |
| **B. NAS is the dest root** | Remote → a share mounted on the Docker host and bound in. One copy. | Named for later. No mount helper this train. |
| **C. Remote writes the alternate** | The Pi pushes to Drive, OneDrive, or the NAS. Data never lands on `/backups`. | **Parked.** That needs `rclone` or CIFS on every host. |

Path A keeps the no-agent rule. Selection is checked paths under `/backups`, with skipped children left behind. The local tree stays the restore source. The herder disk still holds the fleet. Bandwidth is remote to herder, then herder to the alternate.

Path B is LAN only. Mount the share on the Docker host and bind it in, then set that host’s `backup_dest_root` to the mount. A host mount is safer than mounting CIFS inside the app container. CIFS is a poor POSIX disk for `--numeric-ids`. This shape does not help Drive or OneDrive.

Path C stays parked. Every Pi would need a route to the store and a client installed. Credentials would sit on the Pi or pass through SSH. The sudo rsync path, path policy, and vanished-file retry would not apply. rsync on the Pi still cannot speak Drive or OneDrive.

| Destination | How | This train |
|-------------|-----|------------|
| Local rsync directory | Path today. Default. | Stays. |
| **Google Drive** | Path A. **rclone** on the herder, after the pull, from checked paths under `/backups`. | Built on this branch. Walk open. May slip. |
| **OneDrive** | Same rclone binary, a different remote, later. | In the service list. Cannot be selected. |
| **LAN NAS / SMB** | Path B (mounted dest root) when the share should be the only copy. rclone `smb` as path A when it is a second copy. | In the service list. Cannot be selected. |

No vendor-neutral plugin framework. No restic, borg, or kopia: those replace the browsable mirror and the restore wizard. One rclone binary is how OneDrive and SMB can be added later. It is not a framework. Those two services are visible in the list and cannot be saved.

The Drive copy uses a job-scoped temp rclone config. The form stores a Google web client ID, client secret, and refresh token with Fernet. PiHerder runs the redirect. A service account cannot own files on a personal Drive, so it is not the upload account. There is no Graph client, no device-code login, and no SMB client.

### 3.2 Google Drive (Should)

Built on this branch. Detail is [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md). One destination row over the whole `/backups` drive, not a setting on one host. **Settings → PiHerder backup** has the section under the instance self-backup. The service list is Google Drive, plus OneDrive and LAN NAS / SMB disabled. The account is a Google sign-in from this PiHerder. The schedule uses the same presets as the rest of Settings. The browser is a read-only folder tree: tick a folder, open it, untick a child to leave it behind. No typed excludes. rclone runs on the herder as job `backup_replicate` (**Drive copy**) on the existing worker. The global Celery hard limit stays 2 hours for host backups. This copy may run for up to 7 days, and it is acknowledged when the worker receives it so Redis does not start a second rclone an hour later. If the worker process dies, the job is marked failed and **Copy now** can run again. Files already uploaded stay. The client secret and refresh token are Fernet-encrypted and not written to the job log. A failed upload fails the **copy** job. The rsync job and `last_backup_at` stay as they were. Demo never uploads. `backup_replicate` is not on the token API. If this slips, the tag still ships with the local directory only and §3.1. Rebuild the image before a real upload; rclone **1.68.2** is pinned in the Dockerfile. Apply Alembic **047**.

---

## 4. Stream **HA-vis** — one interactive card (Must)

**Built in plugin 0.4.0. Walk still open.** Lands in [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha), not in this image.

**Locks:**

1. One renderer. Tabs are Fleet, Host, and Updates. The old four elements stay registered so a 0.3.0 dashboard still loads: fleet opens on Fleet, host on Host, updates on Updates, resources on Host at the stats.
2. A host strip picks the machine. `server_id` still pins one host and hides the strip. You do not add a Lovelace card per host.
3. Each host shows a device icon and a short model (`5`, `4`, `400`, `Zero`, `CM4`, `CM5` from the stored hardware string) plus an OS icon (Ubuntu, Home Assistant, Debian / Raspberry Pi OS, otherwise Linux). No new herder field.
4. Memory, disk, and CPU load are fleet-style bars (absolute memory and disk, CPU load over core count) with a thin 24-hour sparkline. Clicking a stat, reboot, or last backup opens Home Assistant more-info for that sensor. Empty sparkline only when the recorder has no points.
5. The history call reads the websocket map keyed by entity id. `rows[0]` is only the older list shape. Points stay HA history of the snapshot sensors (`server_id` + `piherder_metric`), about every 15 minutes. No new herder route.
6. Backup is the one face button. Everything else is an Actions menu. Features (the three toggles) are collapsed. Open host is the one link; Docker, Backups, Alerts, and Audit are behind Also. Token rules stay (`read` has no job buttons; `jobs` confirms; `edit` toggles).
7. Updates is one row per host: model icon, name, OS count, container count, reboot. The row opens that host. It does not repeat the action buttons.
8. Plugin **0.4.1** starts or stops one compose service from the Host tab (**Containers**). It does not stop the whole project. Move, Files, and console stay out. MCP does not accept `container_start` or `container_stop`.

---

## 5. Stream **HA bus** — `piherder_job_completed` (Should)

**Built in plugin 0.4.0. Walk still open. May slip.**

Poll-diff on the plugin coordinator fires `piherder_job_completed` on the Home Assistant bus when a watched job leaves the active set. The first poll does not fire. The payload is the last-seen job: id, server, type, and the status it had while active. No herder webhook. No new herder route.

---

## 6. Stream **Mux-2** — leftover sessions (Should)

**Built on this branch. Walk still open.**

On the host SSH-access page, **List sessions** shows leftover `ph-u*` tmux or screen sessions for **that** host, and **Kill** ends one. Names stay `ph-u{user}-s{server}-n{tab}-{f|p}` (`app/services/ssh_console.py`). This is the list an operator needs after a web recycle, because idle timeout does not kill a detached mux session. The list does not attach.

No automatic reattach. Removing a server still does not `kill-server` for every Unix user. After remove, the wiki kill remains. HAOS and the demo never mux. May slip.

---

## 7. Stream **Undo-2** — `dest_up` helper (Should)

**Built on this branch. Walk still open.**

Undo-1 already reverts a Move that failed at cutover, rebind, or validate. This slice is only the hole left when the worker dies during `dest_up`: dest may already be up, so Start source would dual-run.

JobHold and the job detail offer **Inspect destination**, then **Stop dest and start source**. That is `compose stop` on dest and `compose start` on source. DNS and NPM stay. No `down -v`. A green Move is still a new Move in the other direction, not an undo. `WORKER_RESTART_RECOVER_STEPS` stays `stop` and `copy` only. A pipeline failure at dest up (the up command returned) still offers **Start source stack**. May slip.

---

## 8. Stream **Jr-web** — three jobs off the web process (Should)

**Built on this branch. Walk still open.**

`retention`, `herder_backup`, and `host_facts` run on Celery. A web recycle does not fail the row. `host_facts` uses the existing exclusive lane (one per host, SSH-down wait). `retention` and `herder_backup` use `housekeeping_job` on the default queue and do not take a host exclusive slot. `herder_backup` has no host. One self-backup runs at a time. Settings → Run and the schedule queue that job. A worker kill of a **running** job stays fail-honest. The worker cap for these two is 2 hours, under the 3-hour Redis visibility window. nmap stays `-Q nmap`. May slip.

---

## 9. Parked (no product code)

| ID | Item | Why it stays out |
|----|------|------------------|
| **AC-fg** | Per-host / per-feature grants | No grant write-up this train. Three global roles stay. Not multi-tenant. |
| HA Slice 3 | Webhooks, Move-from-HA, Files, whole-project stop | Start and stop of **one** compose service is plugin **0.4.1**. The rest stays out. |
| Brand-3 | Own-docs MkDocs skin | Out. No theme engine. |
| CSP Slice 2 | Rewrite `onclick` | Out. Large, easy to regress. |
| OneDrive / SMB | Clients | Named in §3.1. SMB later is path B or rclone `smb`. OneDrive is rclone later. Path C stays parked. |
| Also out | ACME · NPM CRUD · richer Files API · N3c · M-live · plugin-in-image · MCP OAuth · Move default-on | Unchanged from 1.7 |

---

## 10. Ship bar

| Priority | Item | Bar | Status |
|----------|------|-----|--------|
| **Must** | **MCP-jobs** | `trigger_job` accepts the jobs POST list. Move and undo stay refused. Hosted and stdio match | Landed on branch. Operator walk still open |
| **Must** | **Bak-alt discovery** | §3.1 names path A (rclone second hop), path B (SMB mount later), path C parked. Local rsync stays the default | Written in §3.1 |
| **Should** | **Google Drive** | Path A rclone copy after the local rsync. A failed upload fails the copy job. Demo never uploads | Built on branch. Walk still open. May slip |
| **Must** | **HA-vis** | Plugin **0.4.1**. One card, host strip, click a stat for Home Assistant history. Start or stop one compose service. Same token rules | Built in the plugin. Walk still open |
| **Should** | **HA bus** | `piherder_job_completed` from the plugin poll | Built in the plugin. Walk still open. May slip |
| **Should** | **Mux-2** | List/kill `ph-u*` on SSH access | Built on branch. Walk still open. May slip |
| **Should** | **Undo-2** | `dest_up` inspect helper. No green-Move undo | Built on branch. Walk still open. May slip |
| **Should** | **Jr-web** | Three web jobs survive a web recycle | Built on branch. Walk still open. May slip |
| **Parked** | AC-fg · Slice 3 · OneDrive · SMB · the rest of §9 | No code | Parked |

---

## 11. Quality bar

| Gate | Target |
|------|--------|
| Unit | Fail-under stays **80**. New herder branches (MCP allowlist, and any Should that lands in `app/`) get tests. No live SSH, apt, Drive, or HA in CI |
| Plugin | Card and history parsing tested in piherder-ha with mocked hass. No live Home Assistant |
| E2E | Wizard chrome still loads. No live two-host copy |
| Docs | Operator wiki updates when a slice lands, not in this lock. `mkdocs build --strict` at freeze |
| Security | No new token scope. MCP still cannot Move. Drive credentials encrypted at rest. Demo is not a target |

---

## 12. Capture log

| Date | Note |
|------|------|
| 2026-09-28 | Train opened from `main` after **v1.7.0** shipped. First theme was the visual pass and the 24-hour chart. Package stays `1.7.0`. |
| 2026-09-28 | **Lock retune.** **MCP-jobs** is Must and first. **Bak-alt discovery** is Must (Google Drive lean; OneDrive and LAN NAS/SMB named for later). **Google Drive** is the one Should destination. **HA-vis** stays Must, after those two. HA bus, Mux-2, Undo-2, and Jr-web are Should. AC-fg stays parked. |
| 2026-09-28 | **MCP-jobs landed.** `trigger_job` matches the jobs POST list on hosted `/mcp` and the stdio adapter. `service_migrate`, undo, nmap, `docker_stack_down`, `docker_stack_remove`, and `template_drift_check` stay refused. |
| 2026-09-28 | Adapter package set to **0.2.0** in [piherder-mcp](https://github.com/bjorngluck/piherder-mcp). Tag `v0.2.0` publishes it. `uvx` stays on **0.1.1** until then. |
| 2026-09-28 | **Bak-alt discovery written.** Path A is a rclone second hop from `/backups`. Path B (SMB as the dest root) and OneDrive are later. Path C (a client on each Pi) stays parked. No Drive client in this pass. |
| 2026-09-29 | **Google Drive copy built** on the branch. Settings → PiHerder backup. Service list shows Drive, with OneDrive and SMB not selectable. Sign-in is the operator’s Google account (web OAuth client). A service account cannot store the files on a personal Drive. Folder tree. Job `backup_replicate`. Walk still open. |
| 2026-09-29 | **Mux-2 built** on the branch. SSH access lists and kills leftover `ph-u*` sessions for that host. No reattach. Remove server still does not `kill-server`. HAOS and the demo stay off. Walk still open. |
| 2026-09-29 | **Drive copy time limit.** Job 2268 was killed at the global 2-hour Celery limit, and Redis had already started a second copy at 1 hour. This copy now allows 7 days, acks on receive, and Redis visibility for other tasks is 3 hours. A dead worker marks the job failed. |
| 2026-09-29 | **Undo-2 built** on the branch. A Move that dies during `dest_up` offers inspect, then `compose stop` dest and start source. DNS and NPM stay. No `down -v`. A green Move still has no Undo. Walk still open. |
| 2026-09-29 | **Jr-web built** on the branch. Retention, PiHerder backup, and host facts run on Celery. A web recycle does not fail them. Host facts stays one-per-host. The backup and retention do not take a host slot. Walk still open. |
| 2026-09-30 | **HA-vis and HA bus built** in plugin **0.4.0**. One card with a host strip, Raspberry Pi model and OS icons, and a click that opens Home Assistant history. Actions sit in one menu. A job that leaves the active set fires `piherder_job_completed`. No new herder route. Walks still open. Tag **v0.4.0** is the HACS release. Screenshot recapture is still open. |
| 2026-09-30 | **Container start/stop** in plugin **0.4.1**. Host tab **Containers** starts or stops one compose service (`container_start` / `container_stop`). Not the whole project. Not MCP. Walk still open. HACS lists 0.4.1 after the tag. |

---

## 13. Next

| # | Step | Status |
|---|------|--------|
| 1 | Open **`v1.8.0-dev`** | **Done** 2026-09-28 |
| 2 | Lock Must / Should in this plan | **Done** 2026-09-28 |
| 3 | **MCP-jobs** | **Landed** on branch. Walk is [QA_v1.8.0.md](QA_v1.8.0.md) |
| 4 | Bak-alt discovery is §3.1. Google Drive client | **Built** on branch (Settings → PiHerder backup). Walk still open |
| 5 | **HA-vis**, container start/stop, and the bus event | **Built** in plugin 0.4.1. Walks still open |
| 6 | Freeze · `1.8.0` · tag · Hub | Only when asked |

---

*Package stays `1.7.0` until freeze. Operator walks live in [QA_v1.8.0.md](QA_v1.8.0.md).*
