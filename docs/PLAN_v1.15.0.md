# PiHerder v1.15.0: draft

**Status:** **DRAFT** on `v1.15.0-dev`. Scope is still to be confirmed by the maintainer. Not voted. Package stays `1.13.0`. No tag.  
**Date opened:** 2026-10-31.  
**Git branch:** `v1.15.0-dev`  
**Package / image version:** package stays **`1.13.0`**. About / footer stay **1.13.0**. This draft does not bump the package.  
**Theme:** leftovers that did not land in v1.14. Scope is not locked.  
**Baseline:** `v1.14.0-dev` at `adf968c`. **v1.14.0** is not tagged. Production image stays **1.13.0**.  
**Milestone:** **v1.15.0** is still to be created by the maintainer. These issues are not on a milestone.  
**Mode:** not voted. Do not treat the rows below as Must or Should.  
**Related:** [PLAN_v1.14.0.md](PLAN_v1.14.0.md) · draft [RELEASE_v1.14.0.md](RELEASE_v1.14.0.md)

> Cut 2026-10-31 from `v1.14.0-dev` at `adf968c`. Nothing on this train is built. [#61](https://github.com/bjorngluck/piherder/issues/61), [#62](https://github.com/bjorngluck/piherder/issues/62), [#63](https://github.com/bjorngluck/piherder/issues/63), and [#64](https://github.com/bjorngluck/piherder/issues/64) stay on the v1.14.0 milestone until that release. They are not refiled here. Volume prune `dry_run` already shipped in v1.14. Placeholders only (`host-a`, `example.com`).

---

## 0. Intent

v1.14 records the missed-slot catch-up, Drive OAuth health, the fleet summary, mute rules, restore safety, the compose drift badge, and volume prune on the API. This draft lists four leftovers. The maintainer confirms the scope before a vote. Must and Should are not assigned.

Volume prune `dry_run` already shipped in v1.14. `POST /api/v1/servers/{id}/docker/volumes` and hosted `prune_volumes` list unused volumes and sizes when `dry_run` is true. That path deletes nothing and writes no audit row. It is not rebuilt on this train.

---

## 1. Decision lock

Not voted. The maintainer confirms scope. Build order is not set.

### Tokens

| Tier | Lock |
|------|------|
| Draft | **Separate opt-in destructive token scope.** Issue [#71](https://github.com/bjorngluck/piherder/issues/71). Not built. Hosted `prune_volumes` with `confirm` true can permanently delete unused Docker volumes. v1.14 accepts that for an `edit` token. This item adds a separate scope so an `edit` token does not delete unless the operator opts in. `dry_run` stays on `edit`. Existing `edit` tokens do not gain the new scope |

### Backups

| Tier | Lock |
|------|------|
| Draft | **Restore preview of the chosen copy.** Issue [#72](https://github.com/bjorngluck/piherder/issues/72). Not built. `GET /api/v1/servers/{id}/backup/restore-preview` diffs the local backup and does not show permission or owner-only changes. A `destination_id` still diffs that local tree. This item diffs the chosen copy and lists permission and owner changes. The preview still does not write |

### Docker

| Tier | Lock |
|------|------|
| Draft | **Git drift SSH cost.** Issue [#73](https://github.com/bjorngluck/piherder/issues/73). Not built. The compose git drift badge opens one SSH session per project. Batch the checks or cache the results. The badge still compares a checkout with the last fetched origin and does not fetch or deploy |

### Migrations

| Tier | Lock |
|------|------|
| Draft | **Explicit 051 and 052 downgrades.** Issue [#74](https://github.com/bjorngluck/piherder/issues/74). Not built. Alembic `051` drops `docker_prune_schedule` inside a bare `except` that ignores every error. Alembic `052` drops `backup_catchup_enabled` and `container_check_catchup_enabled` the same way. Name the missing-column case, or drop a column only when it exists. Upgrade behavior stays as it is. This draft does not apply either migration to a live database |

### Already shipped

| Topic | Lock |
|-------|------|
| Volume prune `dry_run` | Shipped in v1.14. Lists unused volumes and sizes. Deletes nothing. Not this train |
| Issues #61 to #64 | In the v1.14 tree. They stay on the v1.14.0 milestone until that release |
| Milestone v1.15.0 | Still to be created by the maintainer |
| Version bump | Package stays **1.13.0** until the maintainer says otherwise. About stays **1.13.0** |
| Demo | The public demo is not pointed at this branch |

---

## 2. Capture log

| Date | What changed |
|------|----------------|
| 2026-10-31 | **Draft opened** from `v1.14.0-dev` at `adf968c`. Scope is still to be confirmed by the maintainer. Issues: [#71](https://github.com/bjorngluck/piherder/issues/71) destructive token scope, [#72](https://github.com/bjorngluck/piherder/issues/72) restore preview of the chosen copy, [#73](https://github.com/bjorngluck/piherder/issues/73) git drift SSH cost, [#74](https://github.com/bjorngluck/piherder/issues/74) explicit 051 and 052 downgrades. Milestone **v1.15.0** is still to be created by the maintainer. Volume prune `dry_run` already shipped in v1.14. Nothing is built. |

---

## 3. Next

| # | Step | Status |
|---|------|--------|
| 1 | Maintainer creates milestone **v1.15.0** | Not created |
| 2 | Maintainer confirms this scope | Not confirmed |
| 3 | Vote Must, Should, Discover, Later, or Out | Not voted |
| 4 | Build | Nothing is built |

---

*DRAFT on `v1.15.0-dev`. Scope is still to be confirmed by the maintainer. Package stays 1.13.0. Not tagged. Milestone v1.15.0 is still to be created by the maintainer.*
