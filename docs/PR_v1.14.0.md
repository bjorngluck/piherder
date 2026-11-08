# PR: v1.14.0-dev → main

**Title:** `v1.14.0: catch up a missed slot, warn on Drive OAuth, summarise the fleet, and fix the mobile console`

**Base:** `main` · **Head:** `v1.14.0-dev` · [QA_v1.14.0.md](QA_v1.14.0.md): signed 2026-11-08, including screenshots and the 1.13 regression.

**State:** [#70](https://github.com/bjorngluck/piherder/pull/70). Code freeze is set 2026-11-08. Notes are [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md). Package **1.14.0**. The published image stays **1.13.0**. Not tagged.

---

## Summary

Train after production **v1.13.0**. A missed backup or image check can be caught up once, off by default. Google Drive OAuth failure and a testing token older than 5 days raise alerts that cannot be muted. `GET /api/v1/fleet/summary` is a read-only snapshot beside the existing summary. The phone console sheet stays up above the keyboard. Samsung Keyboard remains a known issue.

Should work on the same branch: mute rules, a pre-restore snapshot, manifest file and byte checks, a dry-run restore preview, a compose **Git drift** pill, and volume prune on the API and hosted `/mcp`. Plugin **[0.5.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.4)** and adapter **[0.4.4](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.4)** are already tagged. Adapter **0.4.4** does not list `prune_volumes`. Secret `PIHERDER_PUSH_DENYLIST` is set. Actions run 215 printed `push denylist clean`.

Design: [PLAN_v1.14.0.md](PLAN_v1.14.0.md). Maintainer ticks: [QA_v1.14.0.md](QA_v1.14.0.md). Notes: [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md). Screenshot list: [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status). Wired 2026-11-08.

| Stream | Highlights |
|--------|------------|
| **Schedules** | Opt-in catch-up for [#62](https://github.com/bjorngluck/piherder/issues/62). The issue stays open |
| **Drive** | Refresh failure and testing-token age for [#63](https://github.com/bjorngluck/piherder/issues/63). Neither can be muted |
| **Fleet** | `GET /api/v1/fleet/summary` for [#61](https://github.com/bjorngluck/piherder/issues/61). Does not replace `summary` |
| **Console** | Phone sheet stays above the keyboard. [KI-console-samsung-keyboard](../wiki/day-to-day/web-ssh-console.md#ki-console-samsung-keyboard) |
| **Alerts** | Mute rules for [#64](https://github.com/bjorngluck/piherder/issues/64). Delivery only |
| **Backups** | Pre-restore snapshot, file and byte counts, `restore-preview` |
| **Docker** | **Git drift** on the stack list. `POST /api/v1/servers/{id}/docker/volumes` with `dry_run` or `confirm` true |
| **Companions** | Plugin **0.5.4**. Adapter **0.4.4**. No new companion tag on this pull request |

## Migrations

Alembic `052_schedule_catchup` and `053_alert_mute` are in the tree. Do not apply them while the **1.13.0** image is still serving the database. Recreate **web** and **celery-worker** when this ships. About says **1.14.0**.

## Test plan

Walk [QA_v1.14.0.md](QA_v1.14.0.md). Signed 2026-11-08. Do not retick the 1.13 boxes.

- [x] Missed-slot catch-up for backups and image checks. Other kinds stay off
- [x] Drive OAuth refresh failure and the testing-token warning. No token in the alert
- [x] Fleet summary counts, `since`, and no addresses or paths
- [x] Phone sheet, and the Samsung Keyboard note
- [x] Mute rules. Backup and OAuth types stay unmutable
- [x] Pre-restore snapshot, manifest counts, and the restore preview
- [x] Git drift pill, and volume prune only with `confirm` true
- [x] Screenshot files named in the [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status)
- [x] `.venv-docs/bin/mkdocs build --strict`

## Out of scope

- Tag and Hub publish. The published image stays **1.13.0**
- Public demo pointed at this branch
- A new plugin or adapter tag. Published plugin stays **0.5.4**. Published adapter stays **0.4.4**
- Closing [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), or [#64](https://github.com/bjorngluck/piherder/issues/64)
- Applying Alembic `052` or `053` to a live database

## Ship steps

- [ ] Full test suite green on the final tip
- [x] QA boxes signed in [QA_v1.14.0.md](QA_v1.14.0.md)
- [ ] Run the RELEASE_SYNC.md check against origin/main of all three repos
- [x] End-user notes (`RELEASE_v1.14.0.md`) at code freeze. The tagged release is still open
- [x] Screenshot files dropped in `wiki/assets/screenshots/`
- [x] `.venv-docs/bin/mkdocs build --strict`
- [x] Freeze. Package is **1.14.0**
- [ ] Merge this PR to main
- [ ] Tag v1.14.0 and publish the release notes
- [ ] Push Docker Hub image tags for that release
- [ ] Record the multi-arch image digest in the GitHub release notes
- [ ] Update the demo environment, only when asked
- [ ] Hand the release notes to Growth for the release post
