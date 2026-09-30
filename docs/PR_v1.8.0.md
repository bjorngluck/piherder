# PR: v1.8.0-dev → main

**Title:** `v1.8.0: MCP jobs, Drive copy, HA card, leftover consoles, Move recover`

**Base:** `main` · **Head:** `v1.8.0-dev` · **Tag:** `v1.8.0` (only after this PR is no longer a draft)

**State:** **Draft** [#19](https://github.com/bjorngluck/piherder/pull/19). Operator QA is in progress (2026-09-30) and is not signed. Package stays **`1.7.0`**. No release notes file yet. Do not undraft, merge, tag, or publish until asked.

---

## Summary

Eighth minor after production **v1.7.0**. Hosted MCP can enqueue the same jobs the bearer API already accepts. A Google Drive copy can follow the local `/backups` tree. Home Assistant plugin **0.4.1** is one card, and it can start or stop one compose service. Leftover console sessions can be listed and killed. A Move that dies during dest start can be recovered. Retention, the herder’s own backup, and host facts run on Celery.

Design: [PLAN_v1.8.0.md](PLAN_v1.8.0.md). Maintainer ticks: [QA_v1.8.0.md](QA_v1.8.0.md). User-facing notes are not written yet (freeze).

| Stream | Highlights |
|--------|------------|
| **MCP-jobs** (Must) | `trigger_job` adds `host_reboot`, the compose stack actions on the jobs POST, `template_deploy`, and `template_redeploy`. Hosted `/mcp` and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) **0.2.0** match. Move, undo, nmap, and console stay refused. `container_start` and `container_stop` are Home Assistant only |
| **Bak-alt** (Must) | Destination model in the plan. Local rsync stays the default. Path A is a second hop from `/backups`. OneDrive and SMB are named and not built |
| **Google Drive** (Should) | Settings → PiHerder backup. Google sign-in, folder tree, job `backup_replicate`. A failed upload fails the copy, not the rsync. The copy may run up to 7 days. Demo does not upload |
| **HA-vis** (Must) | Plugin **[v0.4.1](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.1)** in [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha). One card, host strip, history click. Host **Containers** starts or stops one service. Not in this image. A 1.7 herder answers **400** for those two types |
| **HA bus** (Should) | `piherder_job_completed` from the plugin poll. No herder webhook |
| **Mux-2** (Should) | SSH access lists and kills leftover `ph-u*` sessions. No reattach. HAOS and demo stay off |
| **Undo-2** (Should) | A Move that dies during `dest_up` can inspect, then stop dest and start source. No DNS revert. A green Move still has no Undo |
| **Jr-web** (Should) | `retention`, `herder_backup`, and `host_facts` leave the web process. `host_facts` stays exclusive. The other two do not take a host slot |

Parked, and not in this diff as product: AC-fg, HA webhooks, Move-from-HA, Files, whole-project stop, Brand-3, CSP Slice 2, OneDrive, SMB.

## Migrations (apply on deploy)

| Rev | What |
|-----|------|
| **047** | `backup_destination` (Google Drive copy of `/backups`) |

Last shipped rev on **v1.7.0** is **046**. Recreate **web** and **celery-worker** after pull. App code is not bind-mounted. Until that recreate, Start and Stop from the card answer **400**.

## Test plan

Walk [QA_v1.8.0.md](QA_v1.8.0.md). Boxes stay empty until that walk finishes.

- [ ] MCP-jobs: wider `trigger_job`, **409** on a busy host, Move and console still refused
- [ ] Bak-alt write-up matches the built Drive path
- [ ] Google Drive copy on a real folder, including a long copy that outlives the old 2-hour limit
- [ ] HA plugin **0.4.1**: one card, history click, Containers Start/Stop for one service, read token has no buttons
- [ ] `piherder_job_completed` with no new herder route
- [ ] Mux-2 leftover list and kill
- [ ] Undo-2 on a Move that dies during dest start
- [ ] Jr-web: web recycle does not fail retention, herder backup, or host facts
- [ ] Screenshot pack in [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v180--pack-status)
- [ ] `.venv-docs/bin/mkdocs build --strict` at freeze
- [ ] Unit suite still meets fail-under **80**

## Out of scope

- Version bump off **1.7.0** (freeze only)
- Undraft, merge, tag `v1.8.0`, GitHub Release, or Hub publish
- Public demo redeploy
- OneDrive, SMB, per-host grants, webhooks, Move or Files from Home Assistant, whole-project stop
- Restart, down, or remove of a container from the card
- MCP `container_start` / `container_stop`

## Merge checklist

- [ ] Operator QA signed ([QA_v1.8.0.md](QA_v1.8.0.md))
- [ ] Version bump `app/version_info.py` + `pyproject.toml` → **1.8.0**
- [ ] `docs/RELEASE_v1.8.0.md` + wiki Home current-release row
- [ ] Screenshot pack captured
- [ ] Draft undrafted when asked
- [ ] Merge `v1.8.0-dev` → `main`
- [ ] Tag **`v1.8.0`** · Hub `1.8.0` / `1.8` / `latest`
- [ ] Keep `1.7` / `1.7.0` pins valid
- [ ] `PIHERDER_SERVICE_MIGRATE` stays **false**

## After merge

Hub publish per [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md). GitHub Release body = `docs/RELEASE_v1.8.0.md` when that file exists. The public demo stays on the **1.7.0** image until a redeploy is asked for. Plugin **0.4.1** is already tagged in piherder-ha and is not part of this merge.
