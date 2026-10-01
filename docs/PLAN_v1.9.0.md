# PiHerder v1.9.0 — MCP one-service jobs

**Status:** **In progress** 2026-10-01. Decision locked. Hosted `MCP_JOB_TYPES` includes the four one-service jobs. Package stays **`1.8.0`** until freeze.  
**Date opened:** 2026-10-01  
**Git branch:** implementation off `main`. Package stays **`1.8.0`** until freeze.  
**Package / image version:** stays **`1.8.0`** until the tag. Image tags at freeze: `1.9.0` / `1.9` / `latest`. Pins `1.8.0` / `1.8` stay valid.  
**Theme:** **MCP-svc** — the four one-service container jobs on `trigger_job`  
**Baseline:** `v1.8.0` (tagged 2026-10-01)  
**Mode:** One committed item. No other scope in this plan.  
**Related:** [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) · [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §14 · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [RELEASE_v1.8.0.md](RELEASE_v1.8.0.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

> Production stays **v1.8.0** until the tag. This train adds the four types to hosted MCP. The stdio adapter is a companion pull request on [piherder-mcp](https://github.com/bjorngluck/piherder-mcp). Release notes (`RELEASE_v1.9.0.md`) are written at freeze. There is no changelog file.

---

## 0. Intent

Implement the locked **MCP-svc** decision. Hosted MCP `trigger_job` accepts `container_start`, `container_stop`, `container_restart`, and `container_redeploy`. Each call requires `service` and `source_filter`, matching `JobCreateBody`. The stdio adapter in [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) accepts the same four types in that same train.

Wanted:

1. Add the four types to `MCP_JOB_TYPES` in `app/services/mcp_hosted.py` — **herder side in this train**
2. Add the same four types to `JOB_TYPES` in the piherder-mcp companion, tagged with this train — **separate repository**
3. `trigger_job` takes `service` (compose service name) and `source_filter` (compose project directory)
4. Token gates unchanged: `jobs` scope, `feature:docker`, server docker flag
5. Update the operator page and the test that today asserts the four types are absent

---

## 1. Decision lock

| Choice | Value |
|--------|--------|
| Product decision | **Option A**, locked 2026-10-01. [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) |
| In-scope | **MCP-svc** only |
| Clients | Herder and piherder-mcp in one train. Adapter never ships first |
| Confirm | None on MCP. Accepted |
| Version bump | **1.9.0** at freeze, not in this change |
| Adapter today | **0.2.0** until the joint tag. Hosted `/mcp` accepts the four types before that tag |

---

## 2. Out of scope

`docker_stack_down`, `docker_stack_remove`, Move, undo, dest-up recover, nmap, console, token admin, stale-data cleanup. No new `/api/v1` route. No MCP OAuth.

---

## 3. Ship bar

| Priority | Item | Bar |
|----------|------|-----|
| **Must** | **MCP-svc** | All four types on hosted `trigger_job` and on the stdio adapter. `service` and `source_filter` required. Same-train adapter tag. The absence test in `tests/test_container_service_job.py` flips with the allowlist |

Tag honesty: **v1.9.0** tags only with **MCP-svc** on both clients. The herder allowlist can land first. The adapter tag waits until that herder accepts the types.
