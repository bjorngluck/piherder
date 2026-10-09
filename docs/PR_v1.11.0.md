# PR: v1.11.0-dev → main

**Title:** `v1.11.0: passkey 2FA, direct backup, MCP, git repository`

**Base:** `main` · **Head:** `v1.11.0-dev` · [QA_v1.11.0.md](QA_v1.11.0.md) boxes are empty. Screenshot names are reserved. Frames are not in the tree.

**State:** open as [#35](https://github.com/bjorngluck/piherder/pull/35). Notes are [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md). Package stays **1.10.0**. Tag and Hub publish stay for the ship step.

---

## Summary

Eleventh minor after production **v1.10.0**. A passkey counts as 2FA when Force 2FA is on. A host can send its files straight to a saved Drive, OneDrive, or LAN share. Hosted `/mcp` can start a Move, read or start a LAN scan, and tidy discovery devices. Docker → Repository shows a newer git tag on a project that is already on the host.

Operator walks are not signed. Package stays **1.10.0**.

Design: [PLAN_v1.11.0.md](PLAN_v1.11.0.md). Maintainer ticks: [QA_v1.11.0.md](QA_v1.11.0.md). Screenshot list: [v1.11 pack](../wiki/assets/screenshots/README.md#v111--pack-status). Next notes: [PLAN_v1.12.0.md](PLAN_v1.12.0.md).

| Stream | Highlights |
|--------|------------|
| **Passkey** | Signup with Force 2FA can register a passkey. That passkey satisfies the enroll wall. The account and the Users list treat it as 2FA |
| **Path C** | Opt in per host. None of the destinations start selected. Save refuses an empty tick. Original files go straight out. Nothing new under `/backups`. Jobs label **Direct copy**. The finished backup names the destination |
| **MCP** | `start_move`, `read_discovery`, `start_discovery`, and the device tools. Undo stays out. `trigger_job` still refuses `service_migrate`. Adapter **0.4.1** |
| **Repository** | Existing project. Branch or tag. **Update available** names a newer tag. A local tracked edit stops the update. No deploy |
| **Demo** | Stays the production image. Not this branch |

## Migrations

Alembic **049** (`049_backup_direct`) and **050** (`050_backup_direct_targets`). Recreate **web** and **celery-worker** after merge so the direct-copy and repository code are in the running image.

## Test plan

Walk [QA_v1.11.0.md](QA_v1.11.0.md). Do not retick the 1.10 boxes.

- [ ] Passkey versus Force 2FA
- [ ] Direct backup on LAN NAS, Google Drive, and OneDrive. Each finished Backup row and audit row names the place, with no sign-in, and **Copy now** shows **Direct copy**
- [ ] MCP Move, a LAN Discovery read, a saved-range scan, and device rename, link, and purge
- [ ] Repository on an existing checkout: newer tag visible, local edit stops the update, no deploy
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [ ] Screenshot files named in the [v1.11 pack](../wiki/assets/screenshots/README.md#v111--pack-status)

## Out of scope

- NPM CRUD and remote restore. Notes on v1.12
- Optional audit of API and MCP reads
- Hiding routine `host_facts` rows from Jobs and Audit
- A repo check inside the Docker **Check updates** job
- Public demo pointed at this branch
- Version bump, tag, and Hub publish

## Merge checklist

- [ ] Operator walks signed ([QA_v1.11.0.md](QA_v1.11.0.md))
- [ ] End-user notes (`RELEASE_v1.11.0.md`)
- [ ] Screenshot files dropped in `wiki/assets/screenshots/`
- [ ] `.venv-docs/bin/mkdocs build --strict`
