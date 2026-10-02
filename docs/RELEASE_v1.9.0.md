# PiHerder v1.9.0

**2 October 2026.** Tag **[v1.9.0](https://github.com/bjorngluck/piherder/releases/tag/v1.9.0)**. Package **1.9.0**. Shipped.

Move is on unless you turn it off. Backups can copy on to a NAS share as well as Google Drive, and you can remove one of those copies without touching the other. Home Assistant plugin **0.5.0** can stop a whole project, move one, and browse files in the fleet jail. An agent can start, stop, restart, or update one container.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.9.0` · `1.9` · `latest` (amd64 + arm64). Manifest `sha256:8519ad53e7d0ad0636164966bb876c4774f76b10101bb3c1a74dc6b2d0945472`. Pin `1.8.1` / `1.8` stays the previous image. Pin `1.8.0` stays valid. Pins `1.7.0` / `1.7` stay valid. The public demo stays the **1.7.0** image.

Operator how-to: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/) · [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/) · [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/). Technical record: [PLAN_v1.9.0](PLAN_v1.9.0.md). Maintainer QA: [QA_v1.9.0](QA_v1.9.0.md).

---

## What’s new

### A backup can also copy on to a NAS

Host backups still land in a folder on the PiHerder machine. That folder stays the default, and restore still uses it. Settings → PiHerder backup → **Copy the backup drive** can then send the ticked folders to a share on your network, as well as to Google Drive.

Drive and the share are separate. You can keep both. Each one is its own row, with **Test**, **Folders**, **Edit**, and **Remove**. **Add destination** asks whether you are adding Google Drive or a LAN share, then opens the form for that one. OneDrive is in the list as more to follow. You cannot select it yet.

A share that needs no login saves when you leave the username and password empty. The row then reads **no login**. If an account is already saved, leaving the password blank keeps that password. A username without a password, or a password without a username, is refused. **Test** checks that PiHerder can see the share. It does not copy files. The password is not shown again and is not written into the job log.

**Remove** deletes that one saved destination. The other one stays. Files already in Drive, or already on the share, stay where they are. The public demo will not remove a destination, and it will not upload.

**Copy now**, a schedule, and a copy after a host backup are in this release. The full copy test on a dedicated NAS is not done yet. That test, and OneDrive, are the next release.

Wiki: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/).

### Move is on unless you turn it off

On 1.8, Move stayed hidden until you set a switch. On 1.9 it is available as soon as you upgrade. The project menu shows **Move to another host…**. The wizard still asks before it starts. The project stops on the source, copies, and starts on the destination. A Move that finished still has no Undo. Run a new Move the other way if you need it back.

To keep Move hidden, set `PIHERDER_SERVICE_MIGRATE=false` and recreate **web** before you rely on the new image. The public demo does not copy a project.

The Home Assistant card can start the same Move when the herder says Move is on. It asks first. The source is left stopped. The card does not offer Undo.

An agent still cannot start a Move.

Wiki: [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/).

### Home Assistant can stop a project, move one, and open files

Install [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)** with HACS, then restart Home Assistant. The plugin is not inside the PiHerder image. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.5.0` as a JavaScript module, and hard-refresh. The **Plugin** sensor reads **0.5.0**. Delete an older `?v=0.4.4` resource if it is still there.

**Stop project** asks first, then stops the containers in that compose project. It does not delete containers or volumes.

**Move** is on the Host tab when this herder has Move turned on. Pick the project and the destination. It asks first.

**Files** lists the fleet jail, the same files the token is allowed to see. Delete asks first and removes one file or an empty folder. It does not open Home Assistant’s own `/config`.

Start, stop, restart, and update for one container already shipped on plugin **0.4.4**. A token that can only read shows no buttons. The card still checks in on a timer. PiHerder does not call Home Assistant when a job finishes.

Wiki: [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/).

### An agent can start, stop, restart, or update one container

If you already run the **1.8.1** image, hosted agents can do this today. This release keeps it. `container_start`, `container_stop`, `container_restart`, and `container_redeploy` need the compose project directory and the one service name. The rest of the project stays up. There is no confirm dialog on the agent. The Home Assistant card still asks first.

The installable adapter is [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) **0.3.1** (`uvx piherder-mcp`). A machine that still has **0.2.0** will refuse those four. A client that already points at hosted `/mcp` does not install the adapter.

Still refused: Move, undo, nmap, the console, `docker compose down`, and remove.

Wiki: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/).

### The web UI blocks inline click scripts

Buttons and forms look the same. PiHerder no longer puts click code inside the page HTML. The API documentation pages are unchanged. There is nothing new to configure.

---

## Defaults

| | Default |
|--|---------|
| Move a service | **on**. Set `PIHERDER_SERVICE_MIGRATE=false` to hide it |
| Console mux | **off** per host (and never on HAOS or the demo) |
| Web SSH console | **off** |
| Host Files | **off** |
| Google Drive copy | off until you connect an account |
| LAN share copy | off until you save a share |
| Agents (MCP) | off until you create a token. No second container |
| Catalog in the nav | **on** |

---

## Upgrade from 1.8.1

There is no new database step. **1.8.0** already added the backup-destination table. **1.8.1** already updated the security libraries.

1. Take a full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. Pull `bjorngluck/piherder:1.9.0` (or `1.9` / `latest`).
3. `docker compose pull && docker compose up -d`. Recreate **web** and **celery-worker**. The app code is not a folder on the host.
4. Confirm About / footer says **1.9.0**.
5. Move is **on**. If this install must not copy a project to another host, set `PIHERDER_SERVICE_MIGRATE=false` before you recreate **web**.
6. In HACS, update [piherder-ha](https://github.com/bjorngluck/piherder-ha) to **0.5.0**, restart Home Assistant, and set the card address to `?v=0.5.0`.
7. If you use `uvx piherder-mcp`, install **0.3.1**. **0.2.0** does not start, stop, restart, or update one container.

---

## Honest limits

| | |
|--|--|
| NAS copy | Saving a share and **Test** are signed off. A full **Copy now** onto a dedicated NAS is the next release, together with OneDrive. |
| OneDrive | Listed under **Add destination**. You cannot select it. |
| Google Drive | Unchanged from 1.8. A folder you created in the Drive UI needs the full Drive permission. A hard stop of the copy worker can leave the job **running**. |
| Move | On by default. A finished Move has no Undo. The public demo does not copy. |
| Home Assistant | The plugin is a separate install. Stop project does not delete containers or volumes. Files stay in the fleet jail. |
| Agents | A token only. No separate sign-in for the agent. Move, undo, nmap, the console, down, and remove stay off the tool. |
| Not this release | A copy that never lands on the herder first. OneDrive. The full NAS copy test. |
