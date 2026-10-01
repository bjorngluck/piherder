# PiHerder v1.9.0 — operator QA / sign-off

**Branch:** `v1.9.0-dev` (tip `c01e7ed`, docs #26) → `main` · tag **`v1.9.0`** (cut after freeze)  
**Code freeze:** not set  
**Package:** stays **`1.8.0`** until freeze. About / footer stay **1.8.0**  
**Operator QA:** walks written. Boxes stay empty until you sign them  
**Screenshots:** **not captured.** Shot list below and in [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v190--pack-status). Ops (`@PiHerder`) fills the PNGs. This file does not invent binary frames  
**Release notes:** not written. `RELEASE_v1.9.0.md` is freeze  

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki.

Plan: [PLAN_v1.9.0.md](PLAN_v1.9.0.md). 1.8 sign-off stays [QA_v1.8.0.md](QA_v1.8.0.md). Do not re-open those boxes here. Do not retick one-service start, stop, restart, and update on the card. Those shipped on plugin **0.4.4**.

Plugin work is [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.5.0**. It is not inside the PiHerder image. The test host already has **0.5.0**. Do not redeploy the public demo onto this branch. The demo stays the **1.7.0** image.

Walk the herder slices after a roll of **`v1.9.0-dev`**. Home Assistant Stop project, Files, and the Plugin line can be walked on the test host now. The Home Assistant **Move** panel stays hidden until that roll reports `service_migrate` on health.

Boxes stay empty until you walk the slice. Landed code is not a signed walk. Do not tick a freeze-only row. Do not tick OneDrive.

---

## MCP-svc (Must)

Hosted `POST /mcp` on the rolled `v1.9.0-dev` herder. Token masked. An agent has no confirm dialog. Do not tick this from the Home Assistant card. The card already sends these jobs.

Published adapter **0.2.0** and `uvx` still refuse the four types. Tag **0.3.0** is freeze-only. A herder pass does not require that tag.

- [ ] `trigger_job` accepts `container_start`, `container_stop`, `container_restart`, and `container_redeploy`
- [ ] `service` is required. `source_filter` is the compose project directory. The rest of the project stays up. The job row appears on the herder
- [ ] The call starts the job. There is no confirm step on the agent
- [ ] `docker_stack_down`, `docker_stack_remove`, `service_migrate`, undo, nmap, the console, and token admin are still refused
- [ ] A token without `jobs` has no `trigger_job`. Feature flags and `feature:*` still gate the call

## LAN NAS / SMB and Google Drive (Must)

Settings → PiHerder backup → **Copy the backup drive**. The local rsync directory stays the default. This walk is path A (rclone from `/backups`). A host CIFS mount as the dest root is not this slice. Demo is not the target.

Drive and SMB are two hops. Each has its own destination row and its own Fernet secret. Passwords and the client secret stay out of the frame and out of the job log.

- [ ] The service list saves **Google Drive** and **LAN NAS / SMB**. Both can be saved and live at the same time. Removing one does not clear the other
- [ ] **OneDrive (more to follow)** stays in the list and cannot be selected. Saving Drive or SMB does not turn it into a destination
- [ ] SMB **Test** checks the share and does not copy the tree. Drive **Test** still checks the folder and does not copy
- [ ] **Copy now**, the schedule, or the follow-up after a host backup copies to the selected hop. A failed copy does not change the host backup time
- [ ] The SMB password and the Drive token are Fernet ciphertext. Neither is written to the job log

Pictures (not in the tree): `settings-backup-multi-dest.png`. Capture now. See [Screenshot pack](#screenshot-pack).

## Remove one backup dest (Must)

Settings → PiHerder backup. Admin. The **Remove** button posts `confirm=remove`. Not a job. Not a token route. Not an MCP tool.

- [ ] Confirm **Remove** on Google Drive deletes that row and its Fernet secret. A saved SMB share is still there, still tests, and still copies. Files already in Drive stay in Drive
- [ ] Confirm **Remove** on LAN NAS / SMB deletes that row and its Fernet secret. A saved Drive destination is still there. Files already on the share stay on the share
- [ ] After the wipe, **Copy now** on the removed service does not start rclone. The page says that destination is not saved, so nothing was copied
- [ ] The public demo refuses the wipe
- [ ] Cancelling the confirm leaves both destinations in place

### Observability (Remove)

The copy schedule is an APScheduler job on the web process, id `backup_copy_{destination id}`, only while that dest is enabled, has a schedule, and still has credentials. Settings → Status shows the scheduler job count, not the ids. The Jobs page shows `backup_replicate` rows.

- [ ] Wipe one dest that had a schedule. `backup_copy_{that id}` is gone. The scheduler job count drops by that one cron
- [ ] The other dest’s `backup_copy_{id}` is still registered when that dest still has credentials and a schedule
- [ ] Existing `backup_replicate` rows for the kept dest are unchanged (same job id, status, and `destination_id`). The wiped dest does not gain a new copy job

Pictures (not in the tree): `settings-backup-remove-confirm.png`. Capture now.

## Move default-on (Must)

Unset `PIHERDER_SERVICE_MIGRATE` enables Move. `false` still hides it. Operator+ in the UI. A finished Move still has no Undo. The copy is still stop-first.

Surfaces that start a Move when the flag is on: the Docker UI (confirm in the wizard), and `POST /api/v1/servers/{id}/moves` with `confirm: true`. The card uses that route only when health says the surface is on. See [HA Move](#ha-move-deferred-until-the-19-roll).

Still refused: an MCP Move tool, and `POST /api/v1/servers/{id}/jobs` with `service_migrate` (**400**).

- [ ] On a `v1.9.0-dev` herder, Move is available without setting the flag. The wizard asks before it starts
- [ ] An install that sets the flag to false hides Move in the UI. `POST /moves` is **404**. Health `service_migrate` is false
- [ ] `POST /api/v1/servers/{id}/moves` with `confirm: true` starts one stop-first Move and leaves the source stopped. A finished Move has no Undo
- [ ] `POST /jobs` with `service_migrate` is **400**. Hosted MCP has no Move tool
- [ ] The public demo is still the **1.7.0** image, or its compose still forces the flag off. Demo never copies

Picture (not in the tree): `docker-move-default.png`. Shoot it on the train build, when Move is on screen. It is not part of the capture-now set if the host under the camera is still pre-roll.

### Observability (health)

`GET /api/v1/health` returns `service_migrate`. The Home Assistant card draws **Move** only when that field is `true`.

- [ ] After the `v1.9.0-dev` roll, health includes `service_migrate`. With the flag unset, the value is `true`
- [ ] Live health before that roll does not expose `service_migrate`. Do not treat a hidden card Move as a failure of plugin **0.5.0**

## CSP Slice 2 (Should)

Built on this branch. Product templates use `data-ph-*` and `/static/js/csp-events.js`. App CSP is `script-src-attr 'none'`. Slice 1 script nonces stay. `/docs` and `/redoc` still use `script-src 'unsafe-inline'`.

There is no new screen. No screenshot.

- [ ] A click that worked before the rewrite still works. Console, Move, and Settings included

## HA Slice 3 (Must, plugin 0.5.0)

Walk on Home Assistant with plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)**. The test host already has this plugin. Restart Home Assistant after a HACS update. Set the dashboard resource to `/local/piherder-dashboard-card.js?v=0.5.0` as a **JavaScript module**, then hard-refresh. The **Plugin** sensor reads **0.5.0**. Token masked in every screenshot.

Poll-only. There is no herder webhook. Job-finished events still come from the plugin poll (`piherder_job_completed`). One-service start, stop, restart, and update already shipped. Do not retick those.

Files are the fleet jail (same `files` scope as the token API): list, read, write, mkdir, rename, and delete of a file or an empty directory. The card does not install HAOS `/config` and does not open privileged paths. Do not sign a walk that claims `/config`.

- [ ] HACS shows **0.5.0**. The **Plugin** sensor reads **0.5.0**
- [ ] The card does not register or call a herder webhook
- [ ] **Stop project** asks first, then runs `docker compose stop` (`docker_stack_stop`). It does not remove containers or volumes
- [ ] **Files** stays inside the fleet jail. Delete asks first and removes one file or an empty directory. A path outside the jail is not offered as a real file
- [ ] A `read` token shows no job buttons. Docker feature off hides Stop project and Move

Pictures (not in the tree): `ha-stop-project.png`, `ha-plugin-050.png`. Capture now.

### HA Move (deferred until the 1.9 roll)

The card’s Move panel is empty unless health `service_migrate` is `true` (#24 on the rolled herder). Live health does not expose that field yet. Plugin **0.5.0** on the test host is not enough.

- [ ] After the roll, with health `service_migrate` true, the card shows **Move**, asks first, and posts `POST /moves`. The source is left stopped. A finished Move has no Undo on the card
- [ ] With the flag false, health `service_migrate` is false and the card shows no Move

Picture: `ha-move-card.png`. **Deferred.** Do not shoot it until the post-roll health check above passes. Leave the file out of the tree until then.

## 1.8 regression

- [ ] About / footer still **1.8.0**
- [ ] Google Drive copy still runs beside a saved LAN share. OneDrive stays unselectable
- [ ] Hosted `trigger_job` on the rolled herder accepts the four one-service types. Published adapter **0.2.0** still refuses them
- [ ] Unset `PIHERDER_SERVICE_MIGRATE` leaves Move on. Explicit `false` still hides it
- [ ] Plugin install is still the separate HACS repo, not a file in this image

---

## Out of this operator QA

Do not tick these here. They are not walks of this train.

| Item | Where it stands |
|------|-----------------|
| Herder tag **1.9.0**, image tags, Hub publish | Freeze-only. Package stays **1.8.0** |
| Adapter tag **0.3.0** | Freeze-only. Published adapter stays **0.2.0** |
| **OneDrive** | Discovery only. Listed, unselectable. No rclone hop |
| **Path C** | Discover write-up in the plan. No client. `/backups` stays the default |
| **MCP OAuth** | Discover write-up. Bearer stays the only path |
| Supply-chain locks | PyJWT **2.15.1** and urllib3 **2.8.0** are pinned on this branch. Not an operator screen. Dependabot alerts wait until the locks are on `main`. [PLAN §4](PLAN_v1.9.0.md#4-supply-chain-locks-must-planned) |
| AC-fg, Brand-3, ACME, NPM CRUD, a richer Files API, N3c, M-live | Out |

---

## Screenshot pack

PNGs are not in the repo. Ops fills [wiki/assets/screenshots/](../wiki/assets/screenshots/README.md#v190--pack-status). Mask tokens, the SMB password, and the Google client secret. Do not photograph the public demo. Light theme, desktop width, except the Home Assistant card (dark desktop is fine, same as 1.8).

Wire a `![…]` only after the file exists. `mkdocs build --strict` fails on a missing PNG.

### Capture now

| File | Where | Caption |
|------|--------|---------|
| `settings-backup-multi-dest.png` | Settings → PiHerder backup, **Copy the backup drive** | Google Drive and LAN NAS / SMB both saved. Service picker visible. OneDrive listed as more to follow. No secret, no password |
| `settings-backup-remove-confirm.png` | Same card, **Remove** on one dest | Confirm dialog. Copy says the saved account and schedule are deleted in PiHerder and files already on Drive or the share stay. The other dest is not the one in the dialog |
| `ha-stop-project.png` | Lovelace card, Host → **Containers**, plugin **0.5.0** | **Stop project** on a compose project. Confirm in frame if it is open. Token not visible |
| `ha-plugin-050.png` | Home Assistant device | **Plugin** sensor reads **0.5.0** |

### Train build (herder UI Move)

| File | Where | Caption |
|------|--------|---------|
| `docker-move-default.png` | Docker project menu on a `v1.9.0-dev` herder, flag unset | **Move to another host…** available. Wizard or confirm visible. About / footer may still read **1.8.0** |

### Deferred (post-roll)

| File | Where | Caption |
|------|--------|---------|
| `ha-move-card.png` | Card **Move** panel | Only after `GET /api/v1/health` returns `service_migrate: true`. Project select, dest select, **Move**. Do not capture while live health omits the field |

### No frame

| Slice | Why |
|-------|-----|
| CSP Slice 2 | No new screen. The click walk is the check |
| MCP-svc | Token and tool call. No new herder chrome beyond a job row |
| HA Files | Walk is in the card section. A frame is optional and must show the fleet jail, not HAOS `/config` |
| OneDrive, freeze tags | Not this pack |
