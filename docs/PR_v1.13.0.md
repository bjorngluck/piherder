# PR: v1.13.0-dev → main

**Title:** `v1.13.0: ownership on restore, and a hint when a MAC changes`

**Base:** `main` · **Head:** `v1.13.0-dev` · [QA_v1.13.0.md](QA_v1.13.0.md): Host tools and Internals signed 2026-10-25. The other walks are not signed. Screenshots are reserved.

**State:** draft [#60](https://github.com/bjorngluck/piherder/pull/60). Notes are [RELEASE_v1.13.0.md](RELEASE_v1.13.0.md). Package stays **1.12.1**. Not tagged. Not an upgrade path.

---

## Summary

Train after production **v1.12.1**. A backup writes `.piherder-ownership.json` and a real restore puts the numeric owner, group, and mode back. A scan that inserts a MAC beside a stale row with the same hostname and IP shows a possible NIC change. Replace stale MAC is one audited step. Device identity stays the MAC.

Should work on the same branch: backup minutes spread across the day, Docker build-cache prune, a per-host prune schedule that leaves volumes alone, dangling-image prune after a successful update, a stuck-queue warning, read-only alerts, an OAuth token-name suffix, orphaned running jobs, and `get_engine()`. Host tool bootstrap installs missing `rsync` or official Docker only for features that are already on. A new API token secret stays off the redirect query string.

Plugin **[0.5.3](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.3)** and adapter **[0.4.3](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.3)** stay. Hosted `/mcp` lists `list_alerts`, `prune_build_cache`, and `replace_stale_mac` ahead of that adapter. Those three are not in the **1.12.1** image.

Host tools and Internals are signed 2026-10-25. The other walks are not signed. Screenshot names are reserved. Package stays **1.12.1**.

Design: [PLAN_v1.13.0.md](PLAN_v1.13.0.md). Maintainer ticks: [QA_v1.13.0.md](QA_v1.13.0.md). Screenshot list: [v1.13 pack](../wiki/assets/screenshots/README.md#v113--pack-status).

| Stream | Highlights |
|--------|------------|
| **Backups** | Ownership sidecar. Stagger across 1,440 minutes |
| **Discovery** | NIC notice for [#50](https://github.com/bjorngluck/piherder/issues/50). Replace stale MAC. The issue stays open |
| **Docker** | Build cache, scheduled prune without volumes, dangling images after a successful update, inventory `update_available` |
| **Alerts** | `stuck_queue` and `GET /api/v1/alerts` |
| **Hosts** | `POST /servers/{id}/host-deps/bootstrap`. Not on `/api/v1` or `/mcp` |
| **Tokens** | HttpOnly cookie `api_token_once`. OAuth client name plus a 6-hex suffix |
| **Companions** | Plugin **0.5.3**. Adapter **0.4.3**. No new companion tag |

## Migrations

Alembic **051** adds `docker_prune_schedule`. Recreate **web** and **celery-worker** when this ships. About stays **1.12.1** until freeze.

## Test plan

Walk [QA_v1.13.0.md](QA_v1.13.0.md). Host tools and Internals are signed 2026-10-25. The other walks are not signed. Screenshots are not captured. Do not retick the 1.12 boxes.

- [ ] Restore ownership and the backup stagger ceiling
- [ ] NIC change notice and Replace stale MAC. [#50](https://github.com/bjorngluck/piherder/issues/50) stays open
- [ ] Build cache, scheduled prune, and prune after an update. Volumes stay off the schedule and off the API
- [ ] Stuck queue, read-only alerts, orphaned running jobs
- [ ] Host tool bootstrap. Refused in demo. Not on `/api/v1` or hosted `/mcp`
- [ ] Token secret stays off the query string. OAuth names stay distinct
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [ ] Screenshot files named in the [v1.13 pack](../wiki/assets/screenshots/README.md#v113--pack-status)

## Out of scope

- Package bump, tag, and Hub publish
- Public demo pointed at this branch
- A new plugin or adapter tag. Published plugin stays **0.5.3**. Published adapter stays **0.4.3**
- Closing [#50](https://github.com/bjorngluck/piherder/issues/50)
- Volume prune on the API or hosted `/mcp`
- Setting the push-denylist secret

## Ship steps

- [ ] Full test suite green on the final tip
- [ ] QA boxes signed in [QA_v1.13.0.md](QA_v1.13.0.md)
- [ ] Run the RELEASE_SYNC.md check against origin/main of all three repos
- [ ] End-user notes (`RELEASE_v1.13.0.md`) moved from draft to the shipped release
- [ ] Screenshot files dropped in `wiki/assets/screenshots/`
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [ ] Freeze. Package bump is a separate ask
- [ ] Merge this PR to main
- [ ] Tag v1.13.0 and publish the release notes
- [ ] Push Docker Hub image tags for that release
- [ ] Record the multi-arch image digest in the GitHub release notes
- [ ] Update the demo environment, only when asked
- [ ] Hand the release notes to Growth for the release post
