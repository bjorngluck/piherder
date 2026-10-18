# PR: v1.11.0-dev → main

**Title:** `v1.11.0: passkey 2FA, direct backup, MCP, git repository`

**Base:** `main` · **Head:** `v1.11.0-dev` · [QA_v1.11.0.md](QA_v1.11.0.md) walks signed 2026-10-18. Screenshots are wired. Tag and Hub follow approval.

**State:** open as [#35](https://github.com/bjorngluck/piherder/pull/35). Notes are [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md). Package **1.11.0**. Tag and Hub publish stay for the ship step.

---

## Summary

Eleventh minor after production **v1.10.0**. A passkey counts as 2FA when Force 2FA is on. A host can send its files straight to a saved Drive, OneDrive, or LAN share. Hosted `/mcp` can start a Move, read or start a LAN scan, and tidy discovery devices. Docker → Repository shows a newer git tag on a project that is already on the host.

Operator walks signed 2026-10-18. Package **1.11.0**. Release notes are written as the shipped release. Image tags are `1.11.0` / `1.11` / `latest`. Tag and Hub follow approval.

Design: [PLAN_v1.11.0.md](PLAN_v1.11.0.md). Maintainer ticks: [QA_v1.11.0.md](QA_v1.11.0.md). Screenshot list: [v1.11 pack](../wiki/assets/screenshots/README.md#v111--pack-status). Next notes: [PLAN_v1.12.0.md](PLAN_v1.12.0.md).

| Stream | Highlights |
|--------|------------|
| **Passkey** | Signup with Force 2FA can register a passkey. That passkey satisfies the enroll wall. The account and the Users list treat it as 2FA |
| **Path C** | Opt in per host. None of the destinations start selected. Save refuses an empty tick. Original files go straight out. Nothing new under `/backups`. Jobs label **Direct copy**. The finished backup names the destination |
| **MCP** | `start_move`, `read_discovery`, `start_discovery`, and the device tools. Undo stays out. `trigger_job` still refuses `service_migrate`. Adapter **0.4.2** |
| **Repository** | Existing project. Branch or tag. **Update available** names a newer tag. A local tracked edit stops the update. No deploy |
| **Bug fixes** | Listed in [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md). Schedule clock, fleet backup time, host facts, backup alerts, Docker off, apt `E:` lines, and the Settings tab label |
| **Demo** | Stays the production image. Not this branch |

## Migrations

Alembic **049** (`049_backup_direct`) and **050** (`050_backup_direct_targets`). Recreate **web** and **celery-worker** after merge so the direct-copy and repository code are in the running image.

## Test plan

Walk [QA_v1.11.0.md](QA_v1.11.0.md). Do not retick the 1.10 boxes.

- [x] Passkey versus Force 2FA
- [x] Direct backup on LAN NAS, Google Drive, and OneDrive. Each finished Backup row and audit row names the place, with no sign-in, and **Copy now** shows **Direct copy**
- [x] MCP Move, a LAN Discovery read, a saved-range scan, and device rename, link, and purge
- [x] Repository on an existing checkout: newer tag visible, local edit stops the update, no deploy
- [x] Bug-fix walk in [QA_v1.11.0.md](QA_v1.11.0.md): schedule hour matches the label, fleet backups give every host an effective schedule, and the Fleet health boxes
- [x] `.venv-docs/bin/mkdocs build --strict`
- [x] Screenshot files named in the [v1.11 pack](../wiki/assets/screenshots/README.md#v111--pack-status)

## Out of scope

- NPM CRUD and remote restore. Notes on v1.12
- Optional audit of API and MCP reads
- Hiding routine `host_facts` rows from Jobs and Audit
- A repo check inside the Docker **Check updates** job
- Public demo pointed at this branch
- Tag and Hub publish. The package is already **1.11.0**

## Ship steps

- [x] Full test suite green on the final tip
- [ ] QA boxes done: LAN, Drive, OneDrive direct walks; fleet backups on gives every host a schedule; direct copy on a low-RAM Pi; arch mismatch error
- [x] Git-rich onboard: QA box ticked, or marked "can ship if the walk slips" like MCP Move and nmap
- [x] Run the RELEASE_SYNC.md check against origin/main of all three repos
- [x] Operator walks signed ([QA_v1.11.0.md](QA_v1.11.0.md))
- [x] End-user notes (`RELEASE_v1.11.0.md`)
- [x] Screenshot files dropped in `wiki/assets/screenshots/`
- [x] `.venv-docs/bin/mkdocs build --strict`
- [x] Merge PR #35 to main
- [x] Tag v1.11.0 and publish the release notes
- [x] Push Docker Hub image tags 1.11.0, 1.11 and latest (amd64 + arm64)
- [x] Record the multi-arch image digest (sha256:5b1a0ec8cfa5e2da26a486c5bc0f6d400ec86a8285e6eb9c2bbe64cfd30671b7) in the GitHub release notes
- [ ] Update the demo environment
- [ ] Post-deploy check on the herder: About shows 1.11.0; every host with backups on returns backup_scheduled true with a next_backup_run, or both null (this process cannot see the scheduler; then confirm from the first scheduled Backup job instead); the first scheduled run after deploy lands as a Backup job with an audit row
- [ ] Hand the release notes to Growth for the release post
