# PR: v1.14.0-dev → main

**Title:** `v1.14.0: catch up a missed slot, warn on Drive OAuth, summarise the fleet, and fix the mobile console`

**Base:** `main` · **Head:** `v1.14.0-dev` · [QA_v1.14.0.md](QA_v1.14.0.md): walks are written and are not signed. Screenshots are reserved and not captured.

**State:** draft [#70](https://github.com/bjorngluck/piherder/pull/70). Code freeze is not set. Notes are [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md). Package stays **1.13.0**. Not tagged. Not an upgrade path.

---

## Summary

Train after production **v1.13.0**. A missed backup or image check can be caught up once, off by default. Google Drive OAuth failure and a testing token older than 5 days raise alerts that cannot be muted. `GET /api/v1/fleet/summary` is a read-only snapshot beside the existing summary. The phone console sheet stays up above the keyboard. Samsung Keyboard remains a known issue.

Should work on the same branch: mute rules, a pre-restore snapshot, manifest file and byte checks, a dry-run restore preview, a compose **Git drift** pill, and volume prune on the API and hosted `/mcp`. Plugin **[0.5.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.4)** and adapter **[0.4.4](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.4)** are already tagged. Adapter **0.4.4** does not list `prune_volumes`. Secret `PIHERDER_PUSH_DENYLIST` is not set.

Design: [PLAN_v1.14.0.md](PLAN_v1.14.0.md). Maintainer ticks: [QA_v1.14.0.md](QA_v1.14.0.md). Draft notes: [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md). Screenshot list: [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status).

| Stream | Highlights |
|--------|------------|
| **Schedules** | Opt-in catch-up for [#62](https://github.com/bjorngluck/piherder/issues/62). The issue stays open |
| **Drive** | Refresh failure and testing-token age for [#63](https://github.com/bjorngluck/piherder/issues/63). Neither can be muted |
| **Fleet** | `GET /api/v1/fleet/summary` for [#61](https://github.com/bjorngluck/piherder/issues/61). Does not replace `summary` |
| **Console** | Phone sheet stays above the keyboard. [KI-console-samsung-keyboard](../wiki/day-to-day/web-ssh-console.md#ki-console-samsung-keyboard) |
| **Alerts** | Mute rules for [#64](https://github.com/bjorngluck/piherder/issues/64). Delivery only |
| **Backups** | Pre-restore snapshot, file and byte counts, `restore-preview` |
| **Docker** | **Git drift** on the stack list. `POST /api/v1/servers/{id}/docker/volumes` with `confirm` true |
| **Companions** | Plugin **0.5.4**. Adapter **0.4.4**. No new companion tag on this pull request |

## Migrations

Alembic `052_schedule_catchup` and `053_alert_mute` are in the tree. Do not apply them to a live database ahead of the tag. Recreate **web** and **celery-worker** when this ships. About stays **1.13.0** until the package bump.

## Test plan

Walk [QA_v1.14.0.md](QA_v1.14.0.md). Boxes are open. Do not retick the 1.13 boxes.

- [ ] Missed-slot catch-up for backups and image checks. Other kinds stay off
- [ ] Drive OAuth refresh failure and the testing-token warning. No token in the alert
- [ ] Fleet summary counts, `since`, and no addresses or paths
- [ ] Phone sheet, and the Samsung Keyboard note
- [ ] Mute rules. Backup and OAuth types stay unmutable
- [ ] Pre-restore snapshot, manifest counts, and the restore preview
- [ ] Git drift pill, and volume prune only with `confirm` true
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [ ] Screenshot files named in the [v1.14 pack](../wiki/assets/screenshots/README.md#v114--pack-status)

## Out of scope

- Package bump, tag, and Hub publish
- Public demo pointed at this branch
- A new plugin or adapter tag. Published plugin stays **0.5.4**. Published adapter stays **0.4.4**
- Closing [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), or [#64](https://github.com/bjorngluck/piherder/issues/64)
- Setting secret `PIHERDER_PUSH_DENYLIST`
- Applying Alembic `052` or `053` to a live database

## Ship steps

- [ ] Full test suite green on the final tip
- [ ] QA boxes signed in [QA_v1.14.0.md](QA_v1.14.0.md)
- [ ] Run the RELEASE_SYNC.md check against origin/main of all three repos
- [ ] End-user notes (`RELEASE_v1.14.0.md`) moved from draft to the shipped release
- [ ] Screenshot files dropped in `wiki/assets/screenshots/`
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [ ] Freeze. Package bump is a separate ask
- [ ] Merge this PR to main
- [ ] Tag v1.14.0 and publish the release notes
- [ ] Push Docker Hub image tags for that release
- [ ] Record the multi-arch image digest in the GitHub release notes
- [ ] Update the demo environment, only when asked
- [ ] Hand the release notes to Growth for the release post
