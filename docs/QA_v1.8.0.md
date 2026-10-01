# PiHerder v1.8.0 — operator QA / sign-off

**Branch:** `v1.8.0-dev` merged to `main` · tag **`v1.8.0`**  
**Code freeze:** **set** 2026-09-30. Shipped 2026-10-01.  
**Package:** **`1.8.0`**  
**Operator QA:** walks signed 2026-09-30: **MCP-jobs**, **Bak-alt discovery**, **Google Drive**, **HA-vis**, **Containers**, **HA bus**, **Mux-2**, **Undo-2**, **Jr-web**, and the **1.7 regression**  
**Release notes:** [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md). Tag **v1.8.0**.  
**Screenshots:** **captured** 2026-10-01. [v1.8 pack](../wiki/assets/screenshots/README.md#v180--pack-status)  
**Pull request:** [#19](https://github.com/bjorngluck/piherder/pull/19) merged. Body is [PR_v1.8.0.md](PR_v1.8.0.md).

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.8.0.md](PLAN_v1.8.0.md). 1.7 sign-off stays [QA_v1.7.0.md](QA_v1.7.0.md). Do not re-open those boxes here. Screenshot list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v180--pack-status).

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Do not redeploy the public demo onto this branch.

Boxes stay empty until the slice has landed and you walk it. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick a parked row (AC-fg, OneDrive, SMB, Slice 3).

---

## MCP-jobs (Must)

Hosted `POST /mcp` and, if you use it, the stdio adapter. Token masked. Same bearer token as 1.7.

- [x] `trigger_job` accepts `host_reboot`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, and `template_redeploy`, plus the six types from 1.7  
- [x] A stack or template call uses the project path the jobs POST already takes. The job row appears on the herder  
- [x] Restart host names the machine. It is refused (**409**) while an OS patch, a container patch, or a backup is already running. The agent polls `get_job` and does not start another  
- [x] `service_migrate`, undo, nmap, and console are still refused by the tool  
- [x] A token without `jobs` has no `trigger_job`. Feature flags and `feature:*` still gate the call  

## Bak-alt discovery (Must)

Reading pass against [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §3.1. No client required for this box.

- [x] The write-up keeps the local rsync directory as the default and leaves the Settings DR backup on its own path  
- [x] Path A is a second hop from `/backups` (rclone on the herder). Path B is a later SMB mount used as the dest root. Path C (the Pi writes straight to the alternate store) stays parked  
- [x] Google Drive is the only destination this train may build, and it is that rclone copy. A failed upload fails the copy job, not the rsync job  
- [x] OneDrive and a LAN NAS / SMB share are in the service list and cannot be selected. restic, borg, and kopia are not this train  

## Google Drive (Should)

Skip this section if the destination slips the tag. Demo is not the target.

- [x] Settings → PiHerder backup, under the self-backup cards, copies the whole backup drive. The service list saves Google Drive only. OneDrive and LAN NAS / SMB are visible and cannot be selected  
- [x] The account is a Google sign-in. The dialog lists the Cloud steps, including Branding, Audience (Testing, test user), the Drive scope, and the redirect URL. The client secret is not shown again. A blank secret keeps a saved one. Setup steps show only while Google Drive is selected. **Test** checks the folder in that account’s My Drive and does not copy  
- [x] Folders are a tree on the left and the open folder on the right. Tick a folder, open it, and the children stay ticked. Untick a child to leave it out. No typed excludes. Restore still uses the local tree  
- [x] Copy now, the schedule, and the optional follow-up after a host backup enqueue a **Drive copy** job. A failed copy does not change the host backup time  
- [x] A copy that runs longer than an hour stays one job. It is not killed at 2 hours. If the worker stops, the row fails and **Copy now** can run again. Files already on Drive stay  
- [x] The credential is not written to the job log. The public demo does not upload. The token API and MCP cannot start this job  

Pictures: `settings-drive-copy.png` and `settings-drive-setup.png`, wired 2026-10-01. The client secret is not readable in the setup frame.  

## HA-vis (Must)

Walk on Home Assistant with plugin **[v0.4.3](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.3)**. Restart Home Assistant after the HACS update. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.4.3` as a **JavaScript module**, then hard-refresh. The **Plugin** sensor reads **0.4.3**. Token masked in every screenshot. The six Home Assistant frames landed 2026-10-01 and are wired. [v1.8 pack](../wiki/assets/screenshots/README.md#v180--pack-status). Do not retick these boxes from the pictures.

- [x] HACS shows **0.4.3**. After a Home Assistant restart the **Plugin** sensor reads **0.4.3**  
- [x] One card. The host strip picks a machine, with a Raspberry Pi model mark and an OS icon. `server_id` still pins one host  
- [x] The selected host and tab stay put after an action and after the card redraws. Open **Containers** stays open  
- [x] Clicking memory, disk, or CPU load opens that sensor’s Home Assistant history. A thin sparkline draws when the recorder has points, and stays empty when it does not. It does not SSH. Reboot and last backup open the same way when those sensors exist  
- [x] Backup is a button. The other confirms are under Actions. Features are collapsed. A `read` token shows no job buttons  
- [x] Updates is one row per host. The counts are written out (**OS updates**, **container updates**, reboot pending) and are gold when something is due. The row opens that host and does not repeat the buttons  
- [x] A dashboard that already had the fleet, host, updates, or resources card still loads those elements  

## Containers (plugin 0.4.3)

Needs the v1.8 herder. A 1.7 herder answers 400 for these job types. Resource `/local/piherder-dashboard-card.js?v=0.4.3`. Plugin sensor **0.4.3**.

- [x] HACS shows **0.4.3** after the release tag. Home Assistant was restarted. The resource query is `?v=0.4.3`
- [x] Host tab **Containers** lists the inventory. **Stop** on a running service and **Start** on a stopped one each ask first, then run `docker compose` for that service only
- [x] A container with no compose directory or service name has no button. A `read` token has no buttons. Docker feature off hides them
- [x] Updates tab says **OS updates** and **container updates**, not a short letter. The host shows those counts. A container that needs an image update has a gold name and an **Update** button. A running service has **Stop** and **Restart**. A stopped service has **Start**
- [x] MCP `trigger_job` still refuses `container_start`, `container_stop`, `container_restart`, and `container_redeploy`

## HA bus (Should)

- [x] A finished job the plugin was watching fires `piherder_job_completed` on the HA bus. The first poll after startup does not fire it. The payload is the last-seen job id, server, type, and status  
- [x] No new herder route and no webhook  

## Mux-2 (Should)

- [x] SSH access lists leftover `ph-u*` sessions for that host and can kill one  
- [x] Hide still detaches. Closing the console still kills that session. There is no automatic reattach  
- [x] Removing the server does not kill every Unix user’s mux  

## Undo-2 (Should)

- [x] A Move that dies during `dest_up` offers inspect, then stop dest and start source, without reverting DNS or NPM  
- [x] A green Move still has no Undo. Dest `down -v` is not offered  

## Jr-web (Should)

- [x] Recycle **web** while `retention`, `herder_backup`, or `host_facts` is running. The job continues or stays pending. It is not failed because the web process restarted  
- [x] Recycle the worker while one of those is **running**. The job fails honest and is not resumed mid-flight  
- [x] `herder_backup` does not take a host exclusive slot  

## 1.7 regression

- [x] Hosted `POST /mcp` still answers with the same bearer token. The original six job types still enqueue  
- [x] Exclusive jobs from 1.7 still run on Celery. The regression walk saw About / footer at **1.7.0**. The package bump sets them to **1.8.0**  
- [x] Move stays off. Console mux stays opt-in and off on HAOS and the demo  
- [x] Plugin **0.3.0** cards still load until you switch the resource URL to **0.4.3**  
