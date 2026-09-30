# PiHerder v1.8.0 — operator QA / sign-off

**Branch:** `v1.8.0-dev` → `main` · tag **`v1.8.0`** (cut after merge)  
**Code freeze:** not set  
**Package:** stays **`1.7.0`** until freeze  
**Operator QA:** not started  
**Pull request:** draft [#19](https://github.com/bjorngluck/piherder/pull/19). Body is [PR_v1.8.0.md](PR_v1.8.0.md). Do not undraft, merge, tag, or publish until asked

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.8.0.md](PLAN_v1.8.0.md). 1.7 sign-off stays [QA_v1.7.0.md](QA_v1.7.0.md). Do not re-open those boxes here. Screenshot list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v180--pack-status).

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). It is not inside the PiHerder image. Do not redeploy the public demo onto this branch.

Boxes stay empty until the slice has landed and you walk it. A Should that slips the tag stays unchecked and is noted as slipped. Do not tick a parked row (AC-fg, OneDrive, SMB, Slice 3).

---

## MCP-jobs (Must)

Hosted `POST /mcp` and, if you use it, the stdio adapter. Token masked. Same bearer token as 1.7.

- [ ] `trigger_job` accepts `host_reboot`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, and `template_redeploy`, plus the six types from 1.7  
- [ ] A stack or template call uses the project path the jobs POST already takes. The job row appears on the herder  
- [ ] Restart host names the machine. It is refused (**409**) while an OS patch, a container patch, or a backup is already running. The agent polls `get_job` and does not start another  
- [ ] `service_migrate`, undo, nmap, and console are still refused by the tool  
- [ ] A token without `jobs` has no `trigger_job`. Feature flags and `feature:*` still gate the call  

## Bak-alt discovery (Must)

Reading pass against [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §3.1. No client required for this box.

- [ ] The write-up keeps the local rsync directory as the default and leaves the Settings DR backup on its own path  
- [ ] Path A is a second hop from `/backups` (rclone on the herder). Path B is a later SMB mount used as the dest root. Path C (the Pi writes straight to the alternate store) stays parked  
- [ ] Google Drive is the only destination this train may build, and it is that rclone copy. A failed upload fails the copy job, not the rsync job  
- [ ] OneDrive and a LAN NAS / SMB share are in the service list and cannot be selected. restic, borg, and kopia are not this train  

## Google Drive (Should)

Skip this section if the destination slips the tag. Demo is not the target.

- [ ] Settings → PiHerder backup, under the self-backup cards, copies the whole backup drive. The service list saves Google Drive only. OneDrive and LAN NAS / SMB are visible and cannot be selected  
- [ ] The account is a Google sign-in. The dialog lists the Cloud steps, including Branding, Audience (Testing, test user), the Drive scope, and the redirect URL. The client secret is not shown again. A blank secret keeps a saved one. Setup steps show only while Google Drive is selected. **Test** checks the folder in that account’s My Drive and does not copy  
- [ ] Folders are a tree on the left and the open folder on the right. Tick a folder, open it, and the children stay ticked. Untick a child to leave it out. No typed excludes. Restore still uses the local tree  
- [ ] Copy now, the schedule, and the optional follow-up after a host backup enqueue a **Drive copy** job. A failed copy does not change the host backup time  
- [ ] A copy that runs longer than an hour stays one job. It is not killed at 2 hours. If the worker stops, the row fails and **Copy now** can run again. Files already on Drive stay  
- [ ] The credential is not written to the job log. The public demo does not upload. The token API and MCP cannot start this job  

## HA-vis (Must)

Walk on Home Assistant with plugin **[v0.4.2](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.2)**. Restart Home Assistant after the HACS update. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.4.2` as a **JavaScript module**, then hard-refresh. The **Plugin** sensor reads **0.4.2**. Token masked in every screenshot. Capture list: [v1.8 pack](../wiki/assets/screenshots/README.md#v180--pack-status). Replace `ha-fleet-card.png`, `ha-host-card.png`, `ha-updates-card.png`, and `ha-resources-card.png` in place. Add `ha-more-info.png` only after that dialog is in the frame. Do not tick these boxes from the pictures; the pictures land with the captions.

- [ ] HACS shows **0.4.2**. After a Home Assistant restart the **Plugin** sensor reads **0.4.2**  
- [ ] One card. The host strip picks a machine, with a Raspberry Pi model mark and an OS icon. `server_id` still pins one host  
- [ ] The selected host and tab stay put after an action and after the card redraws. Open **Containers** stays open  
- [ ] Clicking memory, disk, or CPU load opens that sensor’s Home Assistant history. A thin sparkline draws when the recorder has points, and stays empty when it does not. It does not SSH. Reboot and last backup open the same way when those sensors exist  
- [ ] Backup is a button. The other confirms are under Actions. Features are collapsed. A `read` token shows no job buttons  
- [ ] Updates is one row per host (OS count, container count, reboot). The row opens that host and does not repeat the buttons  
- [ ] A dashboard that already had the fleet, host, updates, or resources card still loads those elements  

## Container start/stop (plugin 0.4.1)

Needs the v1.8 herder. A 1.7 herder answers 400 for these two job types. Resource `/local/piherder-dashboard-card.js?v=0.4.2`. Plugin sensor **0.4.2**. Boxes stay empty until walked.

- [ ] HACS shows **0.4.2** after the release tag. Home Assistant was restarted. The resource query is `?v=0.4.2`
- [ ] Host tab **Containers** lists the inventory. **Stop** on a running service and **Start** on a stopped one each ask first, then run `docker compose` for that service only
- [ ] A container with no compose directory or service name has no button. A `read` token has no buttons. Docker feature off hides them
- [ ] MCP `trigger_job` still refuses `container_start` and `container_stop`

## HA bus (Should)

- [ ] A finished job the plugin was watching fires `piherder_job_completed` on the HA bus. The first poll after startup does not fire it. The payload is the last-seen job id, server, type, and status  
- [ ] No new herder route and no webhook  

## Mux-2 (Should)

- [ ] SSH access lists leftover `ph-u*` sessions for that host and can kill one  
- [ ] Hide still detaches. Closing the console still kills that session. There is no automatic reattach  
- [ ] Removing the server does not kill every Unix user’s mux  

## Undo-2 (Should)

- [ ] A Move that dies during `dest_up` offers inspect, then stop dest and start source, without reverting DNS or NPM  
- [ ] A green Move still has no Undo. Dest `down -v` is not offered  

## Jr-web (Should)

- [ ] Recycle **web** while `retention`, `herder_backup`, or `host_facts` is running. The job continues or stays pending. It is not failed because the web process restarted  
- [ ] Recycle the worker while one of those is **running**. The job fails honest and is not resumed mid-flight  
- [ ] `herder_backup` does not take a host exclusive slot  

## 1.7 regression

- [ ] Hosted `POST /mcp` still answers with the same bearer token. The original six job types still enqueue  
- [ ] Exclusive jobs from 1.7 still run on Celery. About / footer still **1.7.0** until the version bump  
- [ ] Move stays off. Console mux stays opt-in and off on HAOS and the demo  
- [ ] Plugin **0.3.0** cards still load until you switch the resource URL to **0.4.2**  
