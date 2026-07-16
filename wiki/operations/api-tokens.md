# API tokens (`/api/v1`)

Automation API for n8n, Home Assistant, scripts. Browser UI uses session cookies separately.

**Where:** Settings → **API management** (`/herder-backups?tab=api`) — **admin only**.  
**Interactive OpenAPI:** on your instance at `/docs` (tag `api-v1`).  
**Long reference:** [docs/API.md](https://github.com/bjorngluck/piherder/blob/main/docs/API.md) in the repo.

## Auth

```http
Authorization: Bearer ph_<secret>
```

- Secret shown **once** at create or rotate; only a hash is stored.  
- **Rotate** issues a new secret immediately.  
- **Revoke** soft-disables; row kept for audit.

## Scopes

| Scope | Allows |
|-------|--------|
| `read` | Catalog, health, servers, jobs GET |
| `jobs` | `POST …/servers/{id}/jobs` (start work units) |
| `edit` | `PATCH …/features` (feature flags) |
| `feature:backup` | Restrict `jobs` to backup-related types when any `feature:*` is set |
| `feature:os` | OS patch / OS update-check jobs |
| `feature:docker` | Container patch / container update-check / stack check-deploy jobs |

If **no** `feature:*` scopes are set, any job type allowed by `jobs` may run (still subject to server feature flags). Prefer least privilege: e.g. n8n backups = `read` + `jobs` + `feature:backup`.

**409 / exclusive jobs:** a second start of the same exclusive type on a host returns **HTTP 409** with the existing `job_id` (same rule as the UI).

## IP allowlist

Optional IPs/CIDRs per token. Enforced using Caddy-forwarded client IP — call via ports **8888/8443**.

## CORS

Off by default. Server-side n8n/HA/curl do **not** need CORS. Set `CORS_ORIGINS` only for browser apps on other origins (exact origins; never `*`).

## Examples

```bash
export PH_TOKEN='ph_…'
export PH_URL='https://piherder.example.com'

curl -sS -H "Authorization: Bearer $PH_TOKEN" "$PH_URL/api/v1" | jq .
curl -sS -H "Authorization: Bearer $PH_TOKEN" "$PH_URL/api/v1/servers" | jq .

curl -sS -X POST -H "Authorization: Bearer $PH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_type":"backup"}' \
  "$PH_URL/api/v1/servers/1/jobs"
```

Prefer least privilege: e.g. n8n backup token = `read` + `jobs` + `feature:backup` + n8n host IP.
