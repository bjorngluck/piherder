# PR: v1.10.0-dev → main

**Title:** `v1.10.0: OneDrive, self-backup copy, MCP sign-in`

**Base:** `main` · **Head:** `v1.10.0-dev` · **Draft** until [QA_v1.10.0.md](QA_v1.10.0.md) is signed

**State:** [#30](https://github.com/bjorngluck/piherder/pull/30) is open and not a draft. It is not ready to merge until [QA_v1.10.0.md](QA_v1.10.0.md) is signed. No code freeze. Package stays **1.9.0**. Tag not cut. End-user notes are not written yet.

---

## Summary

Tenth minor after production **v1.9.0**. OneDrive is a selectable copy destination beside Google Drive and a LAN share. A saved destination can also receive the herder self-backup. An agent can sign in to hosted `POST /mcp` in the browser. A pasted `ph_` token still works.

**Copy now**, the herder self-backup copy, and the 30-minute pending self-backup are signed 2026-10-04. OneDrive, MCP OAuth, and the other walks are not signed. Path C is discovered and parked for **v1.11.0**.

Design: [PLAN_v1.10.0.md](PLAN_v1.10.0.md). Maintainer ticks: [QA_v1.10.0.md](QA_v1.10.0.md). Screenshot list: [v1.10 pack](../wiki/assets/screenshots/README.md#v110--pack-status).

| Stream | Highlights |
|--------|------------|
| **OneDrive** | Own row, Fernet client secret and refresh token, Microsoft sign-in. **Test** does not copy. **Copy now**, the schedule, and the host-backup follow-up can use the row |
| **DR copy** | Opt-in **Also copy each new self-backup**. One `.tar.gz` goes to `herder/<filename>`. The local archive stays. Jobs labels that hop **Self-backup copy**. It does not share the **Copy now** slot. The demo does not upload |
| **Pending self-backup** | A `herder_backup` still pending after 30 minutes is failed and raises the critical alert. A running archive is left alone |
| **MCP OAuth** | Browser sign-in for hosted `POST /mcp`. A `ph_oa_` token is rejected on `/api/v1`. `uvx piherder-mcp` still uses `PIHERDER_TOKEN`. The tool list still refuses Move, undo, nmap, the console, token admin, down, and remove. OneDrive consent is `User.Read`, `Files.ReadWrite`, and `offline_access` |
| **Dependabot** | `.github/dependabot.yml`. Security updates on. Version-update pull requests stay off. PyJWT **2.15.1** and urllib3 **2.8.0** alerts were already fixed |
| **Reports** | **Cards** row on `/reports` for the six history cards |
| **Template fleet** | A template page lists hosts and stacks recorded from it |
| **Demo** | Reports **1.9.0** from `main`. Not this branch |
| **Sibling repos** | `piherder-ha` and `piherder-mcp` have Dependabot, `SECURITY.md`, and `main` protection. Not in this diff |

## Migrations

Alembic **048** (`mcp_oauth`). Recreate **web** and **celery-worker** after merge so the new copy and sign-in code are in the running image.

## Test plan

Walk [QA_v1.10.0.md](QA_v1.10.0.md). **Copy now**, the herder self-backup copy, and the 30-minute pending watch are signed 2026-10-04. The other boxes are empty. Do not retick the 1.9 SMB **Test** or password-at-rest checks.

- [ ] OneDrive row beside Drive and SMB. **Test** does not copy. **Remove** deletes that row only
- [x] Self-backup copy is opt-in. A failed copy leaves the local archive. The demo does not upload
- [x] A running self-backup copy does not block **Copy now**. Jobs labels that hop **Self-backup copy**
- [x] A self-backup pending 30 minutes fails and raises the critical alert. A running one does not
- [ ] Hosted MCP browser sign-in. A pasted `ph_` token still works. Stdio still uses `PIHERDER_TOKEN`
- [ ] Refused MCP tools stay refused
- [ ] Reports **Cards** row. Pin, hide, and reorder still work
- [ ] Template **On the fleet** lists only that template’s stacks
- [ ] `.venv-docs/bin/mkdocs build --strict`
- [ ] Screenshot pack in [v1.10 pack](../wiki/assets/screenshots/README.md#v110--pack-status)
- [x] NAS **Copy now** walk. Signed 2026-10-04. The rest of QA is still open, so this does not mark the pull request ready

## Out of scope

- Version bump, tag **v1.10.0**, Hub publish, and `RELEASE_v1.10.0.md`. Those follow a signed QA
- Path C. Decision and any build are **v1.11.0**
- Public demo pointed at this branch
- Restore from Drive, SMB, or OneDrive

## Merge checklist

- [ ] Operator walks signed ([QA_v1.10.0.md](QA_v1.10.0.md))
- [ ] End-user notes drafted (`RELEASE_v1.10.0.md`)
- [ ] Screenshot pack captured
- [ ] Version bump `app/version_info.py` + `pyproject.toml` → **1.10.0**
- [ ] Wiki banner and current-release row point at **1.10.0**
- [ ] Pull request marked ready for review
- [ ] Merge `v1.10.0-dev` → `main`
- [ ] Tag **`v1.10.0`** · Hub `1.10.0` / `1.10` / `latest`
- [ ] Keep `1.9.0` / `1.9` pins valid
