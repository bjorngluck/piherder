# Decision: one-service container jobs on MCP

**Document:** `docs/DECISION_MCP_SVC.md`  
**Status:** Locked — 2026-10-01 (Björn, architecture review)  
**Target release:** **v1.9.0** — [PLAN_v1.9.0.md](PLAN_v1.9.0.md)  
**Related:** [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §14 · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [API.md](API.md) · wiki [Agents (MCP)](../wiki/operations/mcp.md)

---

## Context

**v1.8.0** shipped. The Home Assistant card already starts, stops, restarts, and updates one compose service. Hosted MCP and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) **0.2.0** refuse `container_start`, `container_stop`, `container_restart`, and `container_redeploy`. `tests/test_container_service_job.py` asserts those four are not in `MCP_JOB_TYPES`. [PLAN_v1.8.0.md](PLAN_v1.8.0.md) §14 left the choice open. An agent has no confirm dialog. The card does.

## Decision

**Option A.** Add all four one-service jobs to MCP `trigger_job`:

- `container_start`
- `container_stop`
- `container_restart`
- `container_redeploy`

Required arguments match API `JobCreateBody`: `service` (compose service name) and `source_filter` (compose project directory).

Hosted `MCP_JOB_TYPES` (`app/services/mcp_hosted.py`) and the stdio adapter `JOB_TYPES` (piherder-mcp) move in the same release train. Never ship the adapter ahead of the herder. A type the herder refuses returns **400**.

No confirm dialog on MCP is an accepted tradeoff against keeping these four Home Assistant-only.

Token gates stay as they are: `jobs` scope, `feature:docker` when the token is feature-restricted, and the server docker flag.

## Consequences

- `trigger_job` grows four `job_type` values. The tool count stays the same.
- The **v1.9.0** train tags a new piherder-mcp release in the same turn as the herder allowlist, so `uvx` and the MCP registry pick it up.
- Until that train ships, adapter **0.2.0** stays and these types stay refused.
- An agent with a `jobs` token and docker can start, stop, restart, or redeploy one service without a second click.

## Out of scope

Still off MCP: `docker_stack_down`, `docker_stack_remove`, Move (`service_migrate`), undo (`service_migrate_undo`), dest-up recover (`service_migrate_dest_recover`), nmap, the console, token admin, and stale-data cleanup.

## Follow-up

Implement on **v1.9.0** ([PLAN_v1.9.0.md](PLAN_v1.9.0.md)). Herder allowlist and the piherder-mcp companion ship together. This note does not change `MCP_JOB_TYPES`.
