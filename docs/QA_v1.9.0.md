# PiHerder v1.9.0 — operator QA / sign-off

**Branch:** `v1.9.0-dev` (includes Backup Settings UX #28) → `main` · tag **`v1.9.0`** (cut after freeze)  
**Code freeze:** not set  
**Package:** stays **`1.8.1`** until freeze. About / footer stay **1.8.1**  
**Operator QA:** signed 2026-10-02 by Björn: **MCP-svc**, **Remove one backup dest** (including observability), **Move default-on** (including health), **CSP Slice 2**, **HA Slice 3**, **HA Move**, and **LAN NAS / SMB** (including **Test** and the password at rest). **Copy now** is deferred to **v1.10**, with a full copy test that includes OneDrive, after a separate NAS is set up. The **1.8 regression** block is not part of this sign-off  
**Screenshots:** **captured** 2026-10-02. [v1.9 pack](../wiki/assets/screenshots/README.md#v190--pack-status)  
**Release notes:** not written. `RELEASE_v1.9.0.md` is freeze

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.9.0.md](PLAN_v1.9.0.md). 1.8 sign-off stays [QA_v1.8.0.md](QA_v1.8.0.md). Do not re-open those boxes here. Do not retick one-service start, stop, restart, and update on the card. Those shipped on plugin **0.4.4**.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.5.0**. It is not inside the PiHerder image. The test host already has **0.5.0**. Do not redeploy the public demo onto this branch. The demo stays the **1.7.0** image.

Walk the herder slices on a roll of **`v1.9.0-dev`**. Home Assistant Stop project, Files, Move, and the Plugin line were walked on the test host with plugin **0.5.0**. Health `service_migrate` is true on that roll.

Do not tick a freeze-only row. Do not tick OneDrive. Do not tick **Copy now**. That copy, and a full OneDrive copy test, wait for **v1.10**.

---

## MCP-svc (Must)

Hosted `POST /mcp` on the rolled `v1.9.0-dev` herder. Token masked. An agent has no confirm dialog. Do not tick this from the Home Assistant card. The card already sends these jobs.

Signed 2026-10-02. Published adapter **[0.3.1](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.3.1)** sends the same four types. Herder **v1.8.1** already accepts them. `uvx piherder-mcp` at **0.2.0** still refuses them.

- [x] `trigger_job` accepts `container_start`, `container_stop`, `container_restart`, and `container_redeploy`
- [x] `service` is required. `source_filter` is the compose project directory. The rest of the project stays up. The job row appears on the herder
- [x] The call starts the job. There is no confirm step on the agent
- [x] `docker_stack_down`, `docker_stack_remove`, `service_migrate`, undo, nmap, the console, and token admin are still refused
- [x] A token without `jobs` has no `trigger_job`. Feature flags and `feature:*` still gate the call

## LAN NAS / SMB and Google Drive (Must)

Settings → PiHerder backup → **Copy the backup drive**. The local rsync directory stays the default. This walk is path A (rclone from `/backups`). A host CIFS mount as the dest root is not this slice. Demo is not the target.

Each saved destination is its own row (#28). A Google Drive row and a LAN NAS / SMB row can both be on the card. Each row has **Test** (when the account or guest share can be tested), **Folders**, **Edit**, and **Remove**. **Add Google Drive** and **Add LAN share** show only for a destination that is not saved yet.

Drive and SMB are two hops. Each has its own destination row and its own Fernet secret. Passwords and the client secret stay out of the frame and out of the job log.

Signed 2026-10-02. **Test** and the password check were confirmed the same day. **Copy now** is not a 1.9 box.

- [x] Saving **Google Drive** and **LAN NAS / SMB** leaves both rows on the card. Removing one does not clear the other
- [x] **OneDrive (more to follow)** stays in the service list and cannot be selected. Saving Drive or SMB does not turn it into a destination
- [x] A share with no login saves when username and password are both empty. That row reads **no login** and can be tested. A blank password on an existing account keeps the saved password. Username without a password, or a password without a username, is refused
- [x] A failed Save stays on the edit sheet with what was typed. The sheet does not close onto fields reloaded from the database
- [x] SMB **Test** checks the share and does not copy the tree. Drive **Test** still checks the folder and does not copy
- [x] The SMB password and the Drive token are Fernet ciphertext. Neither is written to the job log

**Copy now** (the schedule, or the follow-up after a host backup) is deferred to **v1.10**. A separate NAS is required before that copy. The same pass includes OneDrive. Do not treat an unticked copy as a 1.9 failure.

Picture: `settings-backup-multi-dest.png` (captured 2026-10-02). See [Screenshot pack](#screenshot-pack).

## Remove one backup dest (Must)

Settings → PiHerder backup. Admin. **Remove** is on that destination's row and posts `confirm=remove` for that provider only. Not a job. Not a token route. Not an MCP tool.

Signed 2026-10-02.

- [x] Confirm **Remove** on the Google Drive row deletes that row and its Fernet secret. The SMB row is still there, still tests, and still copies. Files already in Drive stay in Drive
- [x] Confirm **Remove** on the LAN NAS / SMB row deletes that row and its Fernet secret. The Drive row is still there. Files already on the share stay on the share
- [x] After the wipe, **Copy now** on the removed service does not start rclone. The page says that destination is not saved, so nothing was copied
- [x] The public demo refuses the wipe
- [x] Cancelling the confirm leaves both rows in place

### Observability (Remove)

The copy schedule is an APScheduler job on the web process, id `backup_copy_{destination id}`, only while that dest is enabled, has a schedule, and still has credentials. Settings → Status shows the scheduler job count, not the ids. The Jobs page shows `backup_replicate` rows.

Signed 2026-10-02.

- [x] Wipe one dest that had a schedule. `backup_copy_{that id}` is gone. The scheduler job count drops by that one cron
- [x] The other dest's `backup_copy_{id}` is still registered when that dest still has credentials and a schedule
- [x] Existing `backup_replicate` rows for the kept dest are unchanged (same job id, status, and `destination_id`). The wiped dest does not gain a new copy job

Picture: `settings-backup-remove-confirm.png` (captured 2026-10-02).

## Move default-on (Must)

Unset `PIHERDER_SERVICE_MIGRATE` enables Move. `false` still hides it. Operator+ in the UI. A finished Move still has no Undo. The copy is still stop-first.

Surfaces that start a Move when the flag is on: the Docker UI (confirm in the wizard), and `POST /api/v1/servers/{id}/moves` with `confirm: true`. The card uses that route only when health says the surface is on. See [HA Move](#ha-move-deferred-until-the-19-roll).

Still refused: an MCP Move tool, and `POST /api/v1/servers/{id}/jobs` with `service_migrate` (**400**).

Signed 2026-10-02.

- [x] On a `v1.9.0-dev` herder, Move is available without setting the flag. The wizard asks before it starts
- [x] An install that sets the flag to false hides Move in the UI. `POST /moves` is **404**. Health `service_migrate` is false
- [x] `POST /api/v1/servers/{id}/moves` with `confirm: true` starts one stop-first Move and leaves the source stopped. A finished Move has no Undo
- [x] `POST /jobs` with `service_migrate` is **400**. Hosted MCP has no Move tool
- [x] The public demo is still the **1.7.0** image, or its compose still forces the flag off. Demo never copies

Pictures: `docker-move-default.png` (project menu, **Move to another host…**) and `ha-move-card.png` (herder Move wizard, stop-first, **Leave stopped**). Both captured 2026-10-02. The card Move panel is in `ha-stop-project.png`.

### Observability (health)

`GET /api/v1/health` returns `service_migrate`. The Home Assistant card draws **Move** only when that field is `true`.

Signed 2026-10-02.

- [x] After the `v1.9.0-dev` roll, health includes `service_migrate`. With the flag unset, the value is `true`
- [x] Live health before that roll does not expose `service_migrate`. Do not treat a hidden card Move as a failure of plugin **0.5.0**

## CSP Slice 2 (Should)

Built on this branch. Product templates use `data-ph-*` and `/static/js/csp-events.js`. App CSP is `script-src-attr 'none'`. Slice 1 script nonces stay. `/docs` and `/redoc` still use `script-src 'unsafe-inline'`.

There is no new screen. No screenshot.

Signed 2026-10-02.

- [x] A click that worked before the rewrite still works. Console, Move, and Settings included

## HA Slice 3 (Must, plugin 0.5.0)

Walk on Home Assistant with plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)**. The test host already has this plugin. Restart Home Assistant after a HACS update. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.5.0` as a **JavaScript module**, then hard-refresh. The **Plugin** sensor reads **0.5.0**. Token masked in every screenshot.

Poll-only. There is no herder webhook. Job-finished events still come from the plugin poll (`piherder_job_completed`). One-service start, stop, restart, and update already shipped. Do not retick those.

Files are the fleet jail (same `files` scope as the token API): list, read, write, mkdir, rename, and delete of a file or an empty directory. The card does not install HAOS `/config` and does not open privileged paths. Do not sign a walk that claims `/config`.

Signed 2026-10-02.

- [x] HACS shows **0.5.0**. The **Plugin** sensor reads **0.5.0**
- [x] The card does not register or call a herder webhook
- [x] **Stop project** asks first, then runs `docker compose stop` (`docker_stack_stop`). It does not remove containers or volumes
- [x] **Files** stays inside the fleet jail. Delete asks first and removes one file or an empty directory. A path outside the jail is not offered as a real file
- [x] A `read` token shows no job buttons. Docker feature off hides Stop project and Move

Pictures: `ha-stop-project.png`, `ha-plugin-050.png` (captured 2026-10-02). The Plugin sensor reads **0.5.0**. The Version sensor reads **1.8.1**. Device info Firmware on that frame still reads **1.8.0**.

### HA Move

The card's Move panel is empty unless health `service_migrate` is `true` (#24 on the rolled herder). Signed 2026-10-02 on the rolled herder.

- [x] After the roll, with health `service_migrate` true, the card shows **Move**, asks first, and posts `POST /moves`. The source is left stopped. A finished Move has no Undo on the card
- [x] With the flag false, health `service_migrate` is false and the card shows no Move

The card Move panel is in `ha-stop-project.png`. `ha-move-card.png` is the herder wizard, filed under [Move default-on](#move-default-on-must).

## 1.8 regression

Not part of the 2026-10-02 sign-off. Leave these empty until that pass.

- [ ] About / footer still **1.8.1**
- [ ] Google Drive copy still runs beside a saved LAN share. OneDrive stays unselectable. Each saved destination stays on its own row
- [ ] Hosted `trigger_job` on the rolled herder accepts the four one-service types. Published adapter **0.3.1** sends them. `uvx` at **0.2.0** still refuses them
- [ ] Unset `PIHERDER_SERVICE_MIGRATE` leaves Move on. Explicit `false` still hides it
- [ ] Plugin install is still the separate HACS repo, not a file in this image

---

## Out of this operator QA

Do not tick these here. They are not walks of this train.

| Item | Where it stands |
|------|-----------------|
| Herder tag **1.9.0**, image tags, Hub publish | Freeze-only. Package stays **1.8.1** |
| Adapter tag **0.3.1** | Published 2026-10-02. Not a 1.9.0 herder tag |
| **OneDrive** | Discovery only on this train. Listed, unselectable. No rclone hop. A real copy test is **v1.10**, with **Copy now**, after a separate NAS is set up |
| **Copy now** (SMB and Drive hop) | Deferred to **v1.10**. Test and the password-at-rest check are signed on this train. A separate NAS is required before the copy |
| **Path C** | Discover write-up in the plan. No client. `/backups` stays the default |
| **MCP OAuth** | Discover write-up. Bearer stays the only path |
| Supply-chain locks | PyJWT **2.15.1** and urllib3 **2.8.0** are pinned on this branch. Not an operator screen. Dependabot alerts wait until the locks are on `main`. [PLAN §4](PLAN_v1.9.0.md#4-supply-chain-locks-must-planned) |
| AC-fg, Brand-3, ACME, NPM CRUD, a richer Files API, N3c, M-live | Out |

---

## Screenshot pack

Captured 2026-10-02. Files are in [wiki/assets/screenshots/](../wiki/assets/screenshots/README.md#v190--pack-status). Tokens, the SMB password, and the Google client secret stay out of the frame. Do not photograph the public demo. Light theme, desktop width, except the Home Assistant card (dark desktop is fine, same as 1.8).

### Captured

| File | Where | What the frame shows |
|------|--------|----------------------|
| `settings-backup-multi-dest.png` | Settings → PiHerder backup, **Copy the backup drive** | Google Drive and LAN NAS / SMB each on their own row, with **Test**, **Folders**, **Edit**, and **Remove**. The SMB row reads **no login**. Folder tree and **Copy now** are under the Drive row |
| `settings-backup-remove-confirm.png` | **Remove** on the LAN share row | Confirm for that share only. The saved account and schedule are deleted in PiHerder. Files already on the share stay. **Cancel** and **Remove** |
| `ha-stop-project.png` | Lovelace card, Host → **Containers**, plugin **0.5.0** | **Stop project** on compose projects, plus the card **Move** panel (project, destination, **Move**). Token not visible |
| `ha-plugin-050.png` | Home Assistant Fleet device | **Plugin** sensor **0.5.0**. **Version** sensor **1.8.1**. Device info Firmware reads **1.8.0** |
| `docker-move-default.png` | Docker project menu on the `v1.9.0-dev` herder | **Move to another host…** in the project menu. Flag unset |
| `ha-move-card.png` | Herder Move wizard | Stop-first cutover for one project. **Leave stopped** selected. **Move service**. This is the herder wizard, not the Home Assistant card |

### No frame

| Slice | Why |
|-------|-----|
| CSP Slice 2 | No new screen. The click walk is the check |
| MCP-svc | Token and tool call. No new herder chrome beyond a job row |
| Failed Save | The walk is the check. The edit sheet stays open with what was typed |
| HA Files | Walk is in the card section. A frame is optional and must show the fleet jail, not HAOS `/config` |
| OneDrive, freeze tags | Not this pack |
