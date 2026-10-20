# PR: v1.12.0-dev → main

**Title:** `v1.12.0: quieter lists, honest backup and image signals`

**Base:** `main` · **Head:** `v1.12.0-dev` · [QA_v1.12.0.md](QA_v1.12.0.md) walks are signed 2026-10-20. Screenshots are reserved and not captured. Tag and Hub follow approval.

**State:** draft [#54](https://github.com/bjorngluck/piherder/pull/54). Code freeze is set 2026-10-20. Notes are [RELEASE_v1.12.0.md](RELEASE_v1.12.0.md). Package stays **1.11.0** until the ship bump. Tag and Hub publish stay for the ship step.

---

## Summary

Twelfth minor after production **v1.11.0**. The default Jobs and Audit lists leave a successful host snapshot out. A rejected Drive, OneDrive, or LAN sign-in stays one critical alert. An image stays flagged while a running container is still on the previous id. An unsupported OS is named and is not upgraded. Restore can pull one selected folder from a copy destination. An admin can create, edit, and delete an NPM proxy host. A token with `admin` can read users, tokens, and the audit log.

Operator walks are signed 2026-10-20. Screenshots are not captured. Package stays **1.11.0**. Release notes are a draft. Image tags stay `1.11.0` / `1.11` / `latest` until the ship step.

Design: [PLAN_v1.12.0.md](PLAN_v1.12.0.md). Maintainer ticks: [QA_v1.12.0.md](QA_v1.12.0.md). Screenshot list: [v1.12 pack](../wiki/assets/screenshots/README.md#v112--pack-status).

| Stream | Highlights |
|--------|------------|
| **Lists** | Optional API and MCP read audit. Successful `host_facts` stays off the default Jobs and Audit lists |
| **Signals** | Critical backup-credential alert, self-backup last success, admin alert, image update flag ([#47](https://github.com/bjorngluck/piherder/issues/47)), OS end of life ([#49](https://github.com/bjorngluck/piherder/issues/49)), update-check gap, checkout ownership |
| **Backup** | rclone for amd64 and arm64, missed schedule, copy before the backups, backup time on `list_servers` and `get_server`, remote restore, direct hop no longer waits on a stuck folder row |
| **Docker** | `docker_prune` from the page, the token API, and hosted MCP ([#48](https://github.com/bjorngluck/piherder/issues/48)). Volume prune stays on the page |
| **NPM and admin API** | Admin create, edit, and delete of a proxy host. GET `/api/v1/admin` with scope `admin` |
| **Demo** | Stays the production image. Not this branch |

## Migrations

No new Alembic revision. Recreate **web** and **celery-worker** after merge so this code is in the running image. About still says **1.11.0** until the ship bump.

## Test plan

Walk [QA_v1.12.0.md](QA_v1.12.0.md). Signed 2026-10-20. Do not retick the 1.11 boxes. Screenshot files stay open.

- [x] Jobs and audit walks
- [x] Alert walks, including the critical backup credential and the admin alert
- [x] Image update flag, OS end of life, update-check gap, and checkout ownership
- [x] Direct copy CPU, missed schedule, copy before the backups, remote restore, and the direct hop
- [x] Docker prune, About last run and next fire, and OS patch error output
- [x] NPM proxy create, edit, and delete. Operator cannot. No MCP tool
- [x] Admin API: `admin` can GET, `read` cannot, no token prefix, MCP sign-in cannot receive `admin`
- [x] `.venv-docs/bin/mkdocs build --strict`
- [ ] Screenshot files named in the [v1.12 pack](../wiki/assets/screenshots/README.md#v112--pack-status)

## Out of scope

- Package bump, tag, and Hub publish
- Public demo pointed at this branch
- Adapter publish ahead of this herder. Adapter stays **0.4.2**
- Closing [#47](https://github.com/bjorngluck/piherder/issues/47), [#48](https://github.com/bjorngluck/piherder/issues/48), [#49](https://github.com/bjorngluck/piherder/issues/49), or [#50](https://github.com/bjorngluck/piherder/issues/50)
- Build cache prune and restore ownership. Notes in [PLAN_v1.13.0.md](PLAN_v1.13.0.md)
- Setting the push denylist secret

## Ship steps

- [ ] Full test suite green on the final tip
- [x] QA boxes signed in [QA_v1.12.0.md](QA_v1.12.0.md)
- [ ] Run the RELEASE_SYNC.md check against origin/main of all three repos
- [ ] End-user notes (`RELEASE_v1.12.0.md`) moved from draft to the shipped release
- [ ] Screenshot files dropped in `wiki/assets/screenshots/`
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [x] Freeze. Package bump is a separate ask
- [ ] Merge this PR to main
- [ ] Tag v1.12.0 and publish the release notes
- [ ] Push Docker Hub image tags 1.12.0, 1.12, and latest (amd64 + arm64)
- [ ] Record the multi-arch image digest in the GitHub release notes
- [ ] Update the demo environment, only when asked
- [ ] Hand the release notes to Growth for the release post
