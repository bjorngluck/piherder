# PiHerder 2.0.0 plan (draft)

Status: draft, owner sign-off pending. 1.x stays single-node first class. Breaking changes land only here.

## Principles
- Subtract before adding. No plugin framework, no Celery beat, no second scheduler, no multi-tenant, no k8s operator.
- Every breaking change has a 1.x deprecation warning one release earlier.
- Fail closed: locks, migrations, readiness.

## Architecture changes
1. **Control plane split.** Move the scheduler out of the web process into a `scheduler` service with a Redis leader lock (one active). Web becomes stateless and can run N replicas. Hosted MCP stays in web. Per-process counters go away. The scheduler exposes its own /metrics with a piherder_scheduler_leader gauge (1 on the active node); alert when the sum is not exactly 1 for 5m.
2. **One job model.** Every long or destructive operation (prune, build cache, bootstrap, restore, updates) runs as a job: pending, running, succeeded, failed, cancelled. Idempotency key per request, fail-closed host lock. API v2 returns a job id; v1 keeps its synchronous contract until removed.
3. **Server-side approval.** Destructive agent actions create a proposal that a human approves in the UI. Replaces the `confirm` flag. Tokens with the destructive scope (from #71) may skip approval only when the owner opts in per token.
4. **RBAC.** Users and roles (owner, operator, viewer). Tokens belong to a user; scopes stay as the token ceiling.
5. **API v2 and MCP v2.** Versioned `/api/v2`, OpenAPI as the contract, MCP tool schemas generated from it. v1 is frozen in 1.16 and removed or read-only in 2.0.
6. **Data.** Postgres only outside tests. New squashed baseline (no `create_all` in a revision). Every migration ships with a tested downgrade; CI runs upgrade and downgrade on Postgres.
7. **Self-backup.** Backup and restore of PiHerder itself (DB, encryption key, config) as a feature with a tested drill.
8. **Themes and layout.** CSS variables or a token file only. Strict CSP stays; no user-supplied JS, HTML or remote fonts. Per-user layout prefs (needs RBAC users).

## Owned by other lanes (tracked here, specified there)
- Observability: OpenTelemetry traces web to celery to SSH, correlation id, JSON logs, per-host status with duration, SLO burn alerts.
- Security: signed images, SBOM and provenance, tamper-evident audit, non-root nmap worker.

## Upgrade path
1.15 (hardening) then 1.16 (bridge: v2 API alongside v1, deprecation warnings, scheduler service optional) then 2.0. Upgrading to 2.0 requires the last 1.x first. piherder-ha and piherder-mcp move to /api/v2 in 1.16, with a compatibility check that must pass before v1 is frozen.

## Open questions for the owner
- Ship a 1.16 bridge release, or go straight from 1.15 to 2.0?
- Is multi-user RBAC wanted for a homelab, or is owner plus scoped tokens enough?
- Remove v1 API in 2.0, or keep it read-only for one more major?
