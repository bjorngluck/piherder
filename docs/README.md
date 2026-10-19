# Docs index

Maintainer plans and release notes. Operator wiki is `wiki/` (MkDocs).

## Live

| Kind | Where |
|------|--------|
| Current release | [RELEASE_v1.11.0.md](RELEASE_v1.11.0.md) · [PLAN_v1.11.0.md](PLAN_v1.11.0.md) · [QA_v1.11.0.md](QA_v1.11.0.md). Tag **v1.11.0**. Image `1.11.0` / `1.11` / `latest`. Pin `1.10.0` / `1.10` stays the previous image. Adapter **[0.4.2](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.2)**. Plugin **0.5.0**. Operator QA signed 2026-10-18 |
| Prior release | [RELEASE_v1.10.0.md](RELEASE_v1.10.0.md) · [PLAN_v1.10.0.md](PLAN_v1.10.0.md) · [QA_v1.10.0.md](QA_v1.10.0.md). Tag **v1.10.0**. Pin `1.10.0` / `1.10` |
| Open train | [PLAN_v1.12.0.md](PLAN_v1.12.0.md) · [QA_v1.12.0.md](QA_v1.12.0.md). Branch **`v1.12.0-dev`**. Package stays **1.11.0**. Must is in. Direct copy on a different CPU is in. Missed scheduled run is in. Copy before the backups is in. Backup time is on `list_servers` and `get_server`. Docker prune is a job. The GitHub adapter readme badges herder 1.11.0. About lists each scheduled job's last fire and next fire. An OS patch job keeps apt's error output, including when sudo drops DEBIAN_FRONTEND. The history sweep is in on this tree. The push denylist checks added lines when its secret is set. The three lockfiles pin Mako 1.4.3 and multidict 6.9.1. Actions stay on major tags. Six medium Dependabot alerts stay open on main until this branch lands. PyPI 0.4.2 still badges 1.10.0 until the adapter cut that ships with this herder. Walks are empty |
| Next notes | [PLAN_v1.13.0.md](PLAN_v1.13.0.md). Not a train. Build cache and buildx cache from the host page, the token API, and hosted MCP. Not built |
| Prior release | [RELEASE_v1.9.0.md](RELEASE_v1.9.0.md) · [PLAN_v1.9.0.md](PLAN_v1.9.0.md) · [QA_v1.9.0.md](QA_v1.9.0.md). Tag **v1.9.0**. Pin `1.9.0` / `1.9` stays valid |
| Prior release | [RELEASE_v1.8.1.md](RELEASE_v1.8.1.md) · [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md) · [PLAN_v1.8.0.md](PLAN_v1.8.0.md) · [QA_v1.8.0.md](QA_v1.8.0.md) |
| Prior production | [RELEASE_v1.7.0.md](RELEASE_v1.7.0.md) · [PLAN_v1.7.0.md](PLAN_v1.7.0.md) · [QA_v1.7.0.md](QA_v1.7.0.md) |
| Backup destinations | [FEATURE_PLAN_BACKUP_DESTINATIONS.md](FEATURE_PLAN_BACKUP_DESTINATIONS.md). Drive shipped in **1.8.0**. SMB shipped in **1.9.0**. OneDrive and the self-backup copy are in **1.10.0**. Operator: [Backups](../wiki/day-to-day/backups.md) · [Agents (MCP)](../wiki/operations/mcp.md) · [Home Assistant](../wiki/integrations/home-assistant.md) |
| API | [API.md](API.md) |
| Design | `FEATURE_PLAN_*.md`, [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md), [SPEC.md](../SPEC.md), [ADMIN.md](ADMIN.md) |

## Archived (pre-1.0)

Full text is under `docs/archive/v0/`. The old `docs/PLAN_v0.*` and `docs/RELEASE_v0.*` paths are stubs.

- [PLAN_v0.4.0.md](archive/v0/PLAN_v0.4.0.md)
- [PLAN_v0.5.0.md](archive/v0/PLAN_v0.5.0.md)
- [PLAN_v0.6.0.md](archive/v0/PLAN_v0.6.0.md)
- [PLAN_v0.7.0.md](archive/v0/PLAN_v0.7.0.md)
- [PLAN_v0.8.0.md](archive/v0/PLAN_v0.8.0.md)
- [PLAN_v0.9.0.md](archive/v0/PLAN_v0.9.0.md)
- [RELEASE_v0.2.0.md](archive/v0/RELEASE_v0.2.0.md)
- [RELEASE_v0.3.0.md](archive/v0/RELEASE_v0.3.0.md)
- [RELEASE_v0.4.0.md](archive/v0/RELEASE_v0.4.0.md)
- [RELEASE_v0.5.0.md](archive/v0/RELEASE_v0.5.0.md)
- [RELEASE_v0.6.0.md](archive/v0/RELEASE_v0.6.0.md)
- [RELEASE_v0.7.0.md](archive/v0/RELEASE_v0.7.0.md)
- [RELEASE_v0.8.0.md](archive/v0/RELEASE_v0.8.0.md)
- [RELEASE_v0.9.0.md](archive/v0/RELEASE_v0.9.0.md)
