# PR: v1.9.0-dev → main

**Title:** `v1.9.0: LAN share, Move on by default, HA 0.5.0, CSP clicks`

**Base:** `main` · **Head:** `v1.9.0-dev`

**State:** Code freeze 2026-10-02. Draft pull request [#29](https://github.com/bjorngluck/piherder/pull/29) is already open. Package stays **1.8.1**. Tag not cut. End-user notes: [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md).

---

## Summary

Ninth minor after production **v1.8.1**. A LAN share can take its own copy of `/backups` beside Google Drive, and one destination can be removed without clearing the other. Move is on unless `PIHERDER_SERVICE_MIGRATE=false`. Home Assistant plugin **0.5.0** can stop a project, move one, and use fleet-jail Files. Product clicks no longer use inline event handlers.

The four one-service MCP jobs are already on **v1.8.1** and on adapter **0.3.1**. This branch does not retag the adapter.

Design: [PLAN_v1.9.0.md](PLAN_v1.9.0.md). Maintainer ticks: [QA_v1.9.0.md](QA_v1.9.0.md). End-user notes: [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md).

| Stream | Highlights |
|--------|------------|
| **MCP-svc** (Must) | Signed. Hosted `trigger_job` accepts `container_start`, `container_stop`, `container_restart`, and `container_redeploy`. `service` and `source_filter` are required. No confirm dialog. Down, remove, Move, undo, nmap, console, and token admin stay refused. Adapter **0.3.1** sends the same four. `uvx` at **0.2.0** does not |
| **LAN NAS / SMB** (Must) | Path A rclone `smb` from `/backups`. Drive and SMB can both be saved, each on its own row. Guest share reads **no login**. **Test** does not copy. The password is Fernet and stays out of the job log. **Copy now** is deferred to **v1.10** |
| **Move default-on** (Must) | Flag defaults true. `false` hides it and `POST /moves` is **404**. `POST /moves` with `confirm: true` is stop-first. A finished Move has no Undo. `POST /jobs` with `service_migrate` stays **400**. The demo does not copy |
| **Remove one dest** (Must) | Admin **Remove** with `confirm=remove` deletes that row and its secret. The other row stays. Remote files stay. The scheduler job for the wiped dest is gone. Demo refuses |
| **HA Slice 3** (Must, separate repo) | Plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)**. Poll-only. Stop project, Move, fleet-jail Files. Not inside this image |
| **CSP Slice 2** (Should) | `data-ph-*` plus `/static/js/csp-events.js`. `script-src-attr 'none'`. `/docs` and `/redoc` still allow `'unsafe-inline'` |

Not in this pull request as product: OneDrive, Path C, MCP OAuth, **Copy now** sign-off, AC-fg, Brand-3, ACME, NPM CRUD, a richer Files API, N3c, M-live.

## Migrations

None. Alembic **047** (`backup_destination`) already shipped in **v1.8.0**. Recreate **web** and **celery-worker** after pull so Move’s new default and the share copy are in the running image.

## Test plan

Walk [QA_v1.9.0.md](QA_v1.9.0.md). Signed 2026-10-02. Code freeze is set. Screenshot pack captured the same day. Package stays **1.8.1** until the version bump.

- [x] MCP-svc: four one-service types, required `service` and compose directory, no agent confirm, refused types stay refused
- [x] LAN share beside Drive, OneDrive unselectable, guest share, failed Save keeps the form, Test does not copy, password at rest
- [x] Remove one destination, including the scheduler job for that dest only
- [x] Move on with the flag unset, hidden when `false`, stop-first `POST /moves`, no MCP Move
- [x] Health `service_migrate` true when the flag is unset
- [x] CSP: Console, Move, and Settings clicks still work
- [x] Plugin **0.5.0**: Stop project, Files in the fleet jail, card Move, no webhook
- [x] 1.8 regression: About **1.8.1**, Drive still beside SMB, adapter **0.3.1**, Move default, plugin stays HACS
- [x] Screenshot pack in [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v190--pack-status)
- [x] `.venv-docs/bin/mkdocs build --strict` (2026-10-02)
- [ ] **Copy now** onto a dedicated NAS, and OneDrive. **v1.10**. Not a merge gate

## Out of scope

- Version bump, tag **v1.9.0**, and Hub publish. Those follow this pull request
- Public demo redeploy
- OneDrive client, Path C, MCP OAuth
- `docker_stack_down`, `docker_stack_remove`, undo, nmap, console, token admin

## Merge checklist

- [x] Operator walks signed ([QA_v1.9.0.md](QA_v1.9.0.md))
- [x] End-user notes drafted ([RELEASE_v1.9.0.md](RELEASE_v1.9.0.md))
- [x] Screenshot pack captured ([v1.9 pack](../wiki/assets/screenshots/README.md#v190--pack-status))
- [ ] Version bump `app/version_info.py` + `pyproject.toml` → **1.9.0**
- [ ] Wiki banner and current-release row point at **1.9.0**
- [ ] Pull request marked ready for review
- [ ] Merge `v1.9.0-dev` → `main`
- [ ] Tag **`v1.9.0`** · Hub `1.9.0` / `1.9` / `latest`
- [ ] Keep `1.8.1` / `1.8` and `1.8.0` pins valid
- [ ] Public demo stays the **1.7.0** image

## After merge

Hub publish per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md). GitHub Release body is `docs/RELEASE_v1.9.0.md`. Plugin **0.5.0** and adapter **0.3.1** are already tagged in their own repos.
