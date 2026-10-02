# PiHerder v1.9.0

**2 October 2026.** Code freeze. Not tagged. Package stays **1.8.1** until the version bump. Do not treat this page as a shipped release.

Move is on unless you turn it off. A LAN share can sit beside Google Drive as its own copy of `/backups`. You can remove one of those destinations without clearing the other. Home Assistant plugin **0.5.0** can stop a whole project, move one, and use the fleet-jail Files browser. Clicks in the product UI no longer use inline `onclick` handlers.

The four one-service agent jobs (`container_start`, `container_stop`, `container_restart`, `container_redeploy`) are already on the **1.8.1** image and on adapter **0.3.1**. This train keeps them.

**Image:** not published for **1.9.0**. Current Hub tags stay `1.8.1` · `1.8` · `latest`. Pins `1.8.0` and `1.7.0` / `1.7` stay valid. The public demo stays the **1.7.0** image.

Operator how-to: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/) · [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/) · [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/). Technical record: [PLAN_v1.9.0](PLAN_v1.9.0.md). Maintainer QA: [QA_v1.9.0](QA_v1.9.0.md).

---

## What’s new

### A LAN share can take a copy of the backup drive

Host backups still rsync into `/backups` on this PiHerder. That directory stays the default, and restore still uses it. Settings → PiHerder backup → **Copy the backup drive** can also send the ticked folders to one SMB share. Google Drive and the share are two hops. Both can be saved. Each has its own row, with **Test**, **Folders**, **Edit**, and **Remove**.

**Add destination** asks which kind you are adding, then opens that form. There is no service dropdown on the add sheet. OneDrive is listed as more to follow and cannot be selected.

A share with no login saves when the username and password are both empty. That row reads **no login**. A blank password on an existing account keeps the saved password. A username without a password, or a password without a username, is refused. **Test** checks the share and does not copy the tree. The password is stored with the instance master key and is not written to the job log.

**Remove** deletes that one saved row and its secret. The other row stays. Files already on Drive or on the share stay where they are. The public demo refuses the wipe. This is not a job, not a token route, and not an MCP tool.

**Copy now**, the schedule, and the follow-up after a host backup are in the product. The live copy onto a dedicated NAS was not walked on this train. That walk, including OneDrive, is **v1.10**.

Wiki: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/).

### Move is on unless you turn it off

`PIHERDER_SERVICE_MIGRATE` defaults to on. Set it to `false` and recreate **web** to hide Move. The wizard still asks before it starts. The copy is still stop-first. A finished Move still has no Undo.

`POST /api/v1/servers/{id}/moves` with `confirm: true` starts one Move. `POST /jobs` with `service_migrate` is still **400**. Hosted MCP has no Move tool. The public demo does not copy.

Wiki: [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/).

### Home Assistant plugin 0.5.0

Install [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)** with HACS, then restart Home Assistant. It is not inside the PiHerder image. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.5.0` as a JavaScript module, and hard-refresh. The **Plugin** sensor reads **0.5.0**.

The card stays poll-only. There is no herder webhook. **Stop project** asks first, then runs `docker compose stop`. It does not remove containers or volumes. **Move** shows when health `service_migrate` is true, asks first, and posts `POST /moves`. The source is left stopped. A finished Move has no Undo on the card. **Files** stays in the fleet jail. Delete asks first. The card does not open Home Assistant `/config`.

One-service start, stop, restart, and update already shipped on **0.4.4**. A `read` token shows no job buttons.

Wiki: [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/).

### Clicks no longer use inline handlers

Product pages use `data-ph-*` and `/static/js/csp-events.js`. The app Content-Security-Policy sets `script-src-attr 'none'`. Script nonces from 1.6 stay. `/docs` and `/redoc` still allow `'unsafe-inline'` scripts. There is no new screen.

---

## Defaults

| | Default |
|--|---------|
| Move a service | **on** (`PIHERDER_SERVICE_MIGRATE`). Set `false` to hide it |
| Console mux | **off** per host (and never on HAOS or the demo) |
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Host Files (real SFTP) | **off** (`PIHERDER_HOST_FILES`) |
| Google Drive copy | off until you connect an account |
| LAN share copy | off until you save a share |
| MCP | off until you create a token. No second container |
| Catalog in the nav | **on** |

---

## Upgrade from 1.8.1

No new database revision. **1.8.0** already applied Alembic **047** (`backup_destination`). **1.8.1** already pins PyJWT **2.15.1** and urllib3 **2.8.0**.

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. After the tag exists, pull `bjorngluck/piherder:1.9.0` (or `1.9` / `latest`).
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**.
4. Confirm About / footer says **1.9.0** after the version bump. Until that bump it still says **1.8.1**.
5. Move is **on** unless `PIHERDER_SERVICE_MIGRATE=false`. An install that must not copy should set `false` before recreate.
6. HACS: update [piherder-ha](https://github.com/bjorngluck/piherder-ha) to **0.5.0**, restart Home Assistant, and set the card resource query to `v=0.5.0`.
7. If you use `uvx piherder-mcp`, install **0.3.1**. Hosted `/mcp` on **1.8.1** already accepts the four one-service jobs. **0.2.0** does not send them.

---

## Honest limits

| | |
|--|--|
| Copy now | In the product. Not walked on a dedicated NAS. That test, and OneDrive, are **v1.10**. |
| OneDrive | Listed. Cannot be selected. No rclone hop. |
| MCP | Bearer token only. No OAuth. Move, undo, nmap, the console, token admin, `docker compose down`, and remove stay off the tool. |
| Home Assistant | The plugin is a separate HACS repo. No webhook. Files stay in the fleet jail. Stop project is not `docker compose down`. |
| Move | On by default. A finished Move has no Undo. The public demo does not copy. |
| Not this release | Path C (a host writing straight to Drive or the NAS), MCP OAuth, and the **Copy now** sign-off. |
