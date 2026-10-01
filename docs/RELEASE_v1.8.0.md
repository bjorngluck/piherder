# PiHerder v1.8.0

**1 October 2026.** Package **1.8.0**. Tag **[v1.8.0](https://github.com/bjorngluck/piherder/releases/tag/v1.8.0)** is cut on the merge commit. Image tags `1.8.0`, `1.8`, and `latest` publish with that tag. Pins `1.7.0` / `1.7` stay valid. Pull request [#19](https://github.com/bjorngluck/piherder/pull/19) is ready for review.

An agent can start the jobs the herder already runs: host reboot, compose stack actions, and template deploy. Backups can copy on to Google Drive after they land on the herder. Home Assistant plugin **0.4.4** is one card, and it can start, stop, restart, or update one container. Leftover console sessions can be listed and killed. A Move that dies while the destination is starting can be put back. Retention, the herder’s own backup, and host facts keep running if the web process restarts.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.8.0` · `1.8` · `latest` (amd64 + arm64) publishes with tag **v1.8.0**. Pins `1.7.0` / `1.7` stay valid. The public demo stays on **1.7.0** until a redeploy is asked for.

Operator how-to: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/) · [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/) · [PiHerder backup](https://piherder-docs.hacknow.info/operations/self-backup/) · [Jobs](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/) · [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/). Technical record: [PLAN_v1.8.0](https://github.com/bjorngluck/piherder/blob/v1.8.0-dev/docs/PLAN_v1.8.0.md). Maintainer QA: [QA_v1.8.0](https://github.com/bjorngluck/piherder/blob/v1.8.0-dev/docs/QA_v1.8.0.md).

---

## What’s new

### Agents can run the jobs you already run by hand

Hosted `POST /mcp` accepts the job types the bearer API already accepted in 1.7, plus host reboot, compose check, deploy, stop, start, and restart, and template deploy and redeploy. The air-gapped adapter is [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) **0.2.0** (`uvx piherder-mcp`). A client that already points at hosted `/mcp` does not install it.

A stack or template call uses the compose project path the jobs page already takes. Restart host names the machine. It is refused while an OS patch, a container patch, or a backup is already running. If a job is already active, the tool returns **409**. Poll that job. Do not start another.

Still refused: Move, undo, nmap, the console, `docker compose down`, remove, and the four one-service buttons on the Home Assistant card (`container_start`, `container_stop`, `container_restart`, `container_redeploy`). Whether those four join the agent tool is a question for the next release.

Wiki: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/)

### A backup can copy on to Google Drive

Host backups still rsync on to a directory on the herder. That directory stays the default, and restore still uses it. Settings → PiHerder backup can then copy the whole backup drive to a folder in your Google account.

The account is a Google sign-in from this PiHerder, not a service-account key. The dialog lists the Cloud steps: Branding, Audience (Testing, plus you as a test user), the Drive scope, and the redirect URL. The client secret is not shown again. A blank secret keeps a saved one. **Test** checks that the folder is in that account’s My Drive. It does not copy files.

Folders are a tree. Tick a folder and the children stay ticked. Open it and untick a child to leave that child out. There is no typed exclude list.

**Copy now**, the schedule, and the optional follow-up after a host backup enqueue a **Drive copy** job. A failed copy does not change the host backup time. A copy that runs for hours stays one job. It is not cut off at two hours. It can run for up to seven days. If the worker stops cleanly, the row fails and **Copy now** can run again. Files already on Drive stay. A hard kill of the worker can leave the row **running**, and **Copy now** then returns that row until someone marks it failed.

OneDrive and a LAN NAS are in the service list and cannot be selected. The public demo does not upload. The token API and MCP cannot start this copy.

Wiki: [Backups — copy to Google Drive](https://piherder-docs.hacknow.info/day-to-day/backups/#copy-to-google-drive-v18-train). That page has the Cloud steps and the pictures of the card. The public site shows this page after the pull request merges.

### Home Assistant is one card

Install [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **[0.4.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.4)** with HACS, then restart Home Assistant. It is not inside the PiHerder image. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.4.4` as a JavaScript module, and hard-refresh. The **Plugin** sensor reads **0.4.4**. Delete an older `?v=0.3.0`, `?v=0.4.0`, `?v=0.4.1`, `?v=0.4.2`, or `?v=0.4.3` resource if it is still there. The wiki pictures were captured on **0.4.3**. The card is the same.

One card, three tabs: **Fleet**, **Host**, **Updates**. A host strip picks the machine, with a Raspberry Pi mark and an OS icon. Click memory, disk, or CPU load to open that sensor’s Home Assistant history. A thin sparkline draws when the recorder has points. The card keeps the tab, the selected host, and an open **Containers** section when it redraws.

**Updates** writes the counts out: **OS updates**, **container updates**, and reboot pending. They are gold when something is due. The row opens that host.

**Containers** lists the last inventory. A running service has **Stop** and **Restart**. A stopped service has **Start**. A container that needs an image update has a gold name and **Update**. Each one asks first, then runs `docker compose` for that service only. **Update** pulls the image and recreates that service. The other containers stay as they are. A container with no compose directory or service name has no button. A **read** token shows no job buttons.

A job the plugin was watching fires `piherder_job_completed` on the Home Assistant bus when it leaves the active set. The first poll after startup does not fire it. There is no new herder route and no webhook.

A 1.7 herder answers **400** for start, stop, restart, and update. Those four need this release’s web and worker.

Wiki: [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/)

### Leftover console sessions

On a host’s SSH access page, PiHerder lists leftover `ph-u*` tmux or screen sessions for that host and can kill one. Hide still detaches. Closing the console still kills that session. There is no automatic reattach. Removing the server does not kill every Unix user’s sessions. Console mux stays off unless you turn it on, and it stays off on HAOS and the public demo. Kill ends any `ph-u*` session on that host, including another operator’s tab.

### A Move that dies while the destination is starting

If a Move dies during destination start, you can inspect the destination, then stop it and start the source again. DNS and Nginx Proxy Manager stay as they were. A Move that finished still has no Undo. Destination `down -v` is not offered. Move stays **off** until you set `PIHERDER_SERVICE_MIGRATE`.

### Retention, self-backup, and host facts survive a web restart

`retention`, the herder’s own backup, and host facts run on the Celery worker. Recycling **web** does not fail them. Recycling the worker while one is **running** fails it. It is not resumed mid-flight. Host facts stays one job per host. The herder backup and retention do not take a host slot.

---

## Defaults (opt-in stays off)

| | Default |
|--|---------|
| Move a service | **off** (`PIHERDER_SERVICE_MIGRATE`) |
| Console mux | **off** per host (and never on HAOS or the demo) |
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Host Files (real SFTP) | **off** (`PIHERDER_HOST_FILES`) |
| Google Drive copy | off until you connect an account |
| MCP | off until you create a token. No second container |
| Catalog in the nav | **on** |

---

## Upgrade from 1.7

Alembic **047** (`backup_destination`) runs when **web** starts. Last shipped revision on **1.7.0** is **046**.

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. Pull `bjorngluck/piherder:1.8.0` (or `git checkout v1.8.0`).
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**. App code is not bind-mounted.
4. Confirm the database revision includes `047`.
5. Move stays **off**.
6. HACS: update [piherder-ha](https://github.com/bjorngluck/piherder-ha) to **0.4.4**, restart Home Assistant, and set the card resource query to `v=0.4.4`.
7. If you use `uvx piherder-mcp`, that package is already **0.2.0**. Hosted `/mcp` updates with this image. It does not gain the four one-service jobs.

---

## Honest limits

| | |
|--|--|
| MCP | Bearer token only. No OAuth. The four one-service container jobs stay on the Home Assistant card. Down, remove, Move, undo, nmap, and the console stay off the tool. |
| Google Drive | The scope is full Drive, because a folder you created in the Drive UI is invisible to the narrower scope. A hard kill of the copy worker can leave the job **running**. |
| Home Assistant | No Move, Files, console, webhooks, or whole-project stop. The card pictures were captured on plugin 0.4.3. HACS current is **0.4.4**. The Google Drive card is in the wiki pictures. |
| Move | Stays off. A finished Move still has no Undo. |
| Not this release | OneDrive, a LAN NAS, per-host grants, and putting the one-service jobs on MCP. |
