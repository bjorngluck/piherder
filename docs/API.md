# PiHerder Automation API (`/api/v1`)

**Status:** v1 (admin-managed tokens)  
**Related:** [ADMIN.md](ADMIN.md) § API tokens · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · interactive OpenAPI at **`/docs`** and **`/openapi.json`**

This API is for **automation** (n8n, Home Assistant, scripts). Browser UI uses session cookies and is separate.

---

## Ownership model

| Question | Answer |
|----------|--------|
| Who creates tokens? | **Admins only** (Settings → **API management** → Tokens) |
| Per-user or shared? | **Instance-wide** (service / automation credentials) |
| Personal PATs? | Not in v1 — use service tokens named per integration |
| Audit | Jobs attribute to the **admin who created** the token when available |

---

## Authentication

```http
Authorization: Bearer ph_<secret>
```

- Secret is shown **once** at **create** or **rotate** (Settings UI has **Copy token**); only a hash is stored.
- Admins can **edit** name, scopes, and IP allowlist without rotating the secret.
- **Rotate** issues a new secret; the previous value stops working immediately.
- **Revoke** immediately if leaked (Settings or `DELETE /api/v1/tokens/{id}` with admin session).

### CORS (optional — browser clients only)

| Client | Needs CORS? |
|--------|-------------|
| n8n / Home Assistant / cron / curl (server-side) | **No** |
| PiHerder UI (same origin) | **No** |
| Browser app on another origin calling `/api/v1` | **Yes** — set env `CORS_ORIGINS` |

`CORS_ORIGINS` is a comma-separated **exact** allowlist (e.g. `https://n8n.example.com`). Empty (default) = CORS disabled. Wildcards (`*`) are rejected.

**CORS is not security for the API.** Every request is still validated on the backend:

1. Valid non-revoked Bearer token  
2. Required **scopes** / feature allowlist  
3. Optional **IP/CIDR** allowlist on the token  

Do not open CORS “to make it work” without also tightening token scopes and IP allowlists.

### IP / CIDR allowlist (optional)

Each token may list **allowed IPs or CIDRs**. Empty = any client IP.

Examples:

- `10.0.0.0/8`
- `192.168.1.50`
- `2001:db8::/32`

Client IP resolution is enforced in the **backend** on every authenticated API request:

1. `X-Forwarded-For` (first hop only)  
2. `X-Real-IP`  
3. TCP peer address  

**Proxy:** Bundled Caddy **overwrites** `X-Forwarded-For` / `X-Real-IP` with the true client IP (`{remote_host}`) so allowlists work for traffic on ports 8888/8443. Prefer that path over hitting web `:8000` directly if you use IP restrictions. If another proxy sits in front of Caddy, that edge must pass the real client IP (or configure trusted proxies / `client_ip`). Mismatch → **403** `Client IP not allowed for this API token`.

---

## Scopes

### Capability scopes

| Scope | Allows |
|-------|--------|
| `read` | `GET` catalog, health, servers, jobs |
| `jobs` | `POST /api/v1/servers/{id}/jobs` |
| `edit` | `PATCH /api/v1/servers/{id}/features` |

### Feature allowlist scopes (optional)

If **no** `feature:*` scopes are present, the token may act on **all** features (still limited by capability scopes and the server’s feature flags).

If **any** `feature:*` scope is set, only those features are allowed for jobs and feature edits:

| Scope | Feature key | Jobs | Edit flags |
|-------|-------------|------|------------|
| `feature:backup` | `backup` | `backup`, `retention` | `backup` |
| `feature:os` | `os` | `os_patch`, `os_update_check` | `os_patch` |
| `feature:docker` | `docker` | `container_patch`, `container_update_check` | `docker` |

**Example least-privilege tokens**

| Use case | Scopes | IP allowlist |
|----------|--------|--------------|
| Grafana / status poller | `read` | monitoring subnet |
| n8n nightly backup only | `read`, `jobs`, `feature:backup` | n8n host |
| HA enable/disable docker ops | `read`, `edit`, `jobs`, `feature:docker` | HA host |
| Full automation (lab) | `read`, `jobs`, `edit` | private LAN CIDR |

---

## Endpoints

Base path: **`/api/v1`**

### Catalog & health

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `GET` | `/api/v1` | `read` | Machine-readable scope/endpoint catalog + **this token’s** scopes |
| `GET` | `/api/v1/health` | `read` | `{ ok, scopes, allowed_features, client_ip }` |

### Servers

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `GET` | `/api/v1/servers` | `read` | List servers (includes `features` object) |
| `GET` | `/api/v1/servers/{id}` | `read` | One server |
| `PATCH` | `/api/v1/servers/{id}/features` | `edit` | Toggle feature flags |

**Server object (summary)**

```json
{
  "id": 1,
  "name": "pi-media",
  "hostname": "pi-media.local",
  "features": {
    "backup": true,
    "os_patch": true,
    "docker": true
  },
  "os_updates_count": 0,
  "container_updates_count": 2,
  "reboot_pending": false,
  "last_backup_at": "2026-07-10T02:00:00"
}
```

**PATCH body** (omit fields you do not want to change):

```json
{
  "backup": true,
  "os_patch": false,
  "docker": true
}
```

Server-side feature flags still gate jobs: you cannot run a backup job if `features.backup` is false on the server (enable it first with `edit`, or in the UI).

### Jobs

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `GET` | `/api/v1/jobs` | `read` | Fleet job list (`server_id`, `status_filter`, `job_type`, `active_only`, `limit`, `offset`) |
| `GET` | `/api/v1/jobs/{id}` | `read` | Job detail (`?detail=true` for longer log tail) |
| `GET` | `/api/v1/servers/{id}/jobs` | `read` | Jobs for one server |
| `POST` | `/api/v1/servers/{id}/jobs` | `jobs` | Trigger job (HTTP **202**) |

**POST body**

```json
{
  "job_type": "backup",
  "source_filter": null,
  "os_steps": ["update", "upgrade", "autoremove"]
}
```

| `job_type` | Server feature required | Token feature scope if restricted |
|------------|-------------------------|-----------------------------------|
| `backup` | backup | `feature:backup` |
| `retention` | backup | `feature:backup` |
| `os_patch` | os_patch | `feature:os` |
| `os_update_check` | os_patch | `feature:os` |
| `container_patch` | docker | `feature:docker` |
| `container_update_check` | docker | `feature:docker` |

**Responses**

| Code | Meaning |
|------|---------|
| 202 | Job accepted (`job_id`, `status`, `job`) |
| 400 | Bad job type / feature disabled on server / empty edit body |
| 401 | Missing/invalid token |
| 403 | Missing scope, feature not allowed, or IP not allowed |
| 404 | Server or job not found |
| 409 | Backup already running |
| 503 | e.g. Celery unavailable for backups |

### Token management (admin **session**, not Bearer token)

| Method | Path | Auth |
|--------|------|------|
| `GET` | `/api/v1/tokens` | Admin cookie/JWT |
| `POST` | `/api/v1/tokens` | Admin cookie/JWT |
| `DELETE` | `/api/v1/tokens/{id}` | Admin cookie/JWT |
| `POST` | `/herder-backups/api-tokens/test` | Admin cookie/JWT — body `{"token":"ph_…"}`; Settings **Test now** after create/rotate |

`POST` body example:

```json
{
  "name": "n8n",
  "scopes": ["read", "jobs", "feature:backup"],
  "allowed_cidrs": ["10.0.0.0/8"]
}
```

Response includes `secret` **once**.

---

## Examples

```bash
export PH_TOKEN='ph_…'
export PH_URL='https://piherder.example.com'

# Catalog
curl -sS -H "Authorization: Bearer $PH_TOKEN" "$PH_URL/api/v1" | jq .

# List fleet
curl -sS -H "Authorization: Bearer $PH_TOKEN" "$PH_URL/api/v1/servers" | jq .

# Enable OS feature then check updates
curl -sS -X PATCH -H "Authorization: Bearer $PH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"os_patch": true}' \
  "$PH_URL/api/v1/servers/1/features"

curl -sS -X POST -H "Authorization: Bearer $PH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_type":"os_update_check"}' \
  "$PH_URL/api/v1/servers/1/jobs"
```

### n8n

HTTP Request node: Method GET/POST, Header `Authorization` = `Bearer ph_…`, JSON body for POST/PATCH.

### Home Assistant

Use `rest_command` / `rest` sensor against `/api/v1/servers` and job endpoints with a token that has the least scopes needed. Prefer an IP allowlist for the HA host.

---

## Interactive OpenAPI

| URL | Description |
|-----|-------------|
| `/docs` | Swagger UI (entire app; focus on **api-v1** tag) |
| `/redoc` | ReDoc |
| `/openapi.json` | OpenAPI 3 schema |

Authorize with Bearer `ph_…` for try-it-out on automation routes. Token admin routes still need a logged-in **admin** session.

---

## Scope + server feature-flag enforcement

Every job trigger checks **all three** layers:

1. Capability scope (`jobs`)
2. Token feature allowlist (if any `feature:*` scopes are set)
3. Server feature flag (`features.backup` / `os_patch` / `docker` on that host)

Missing capability or feature allowlist → **403**. Server flag off → **400** with a clear “feature is disabled for this server” message. Feature edits require `edit` plus each affected feature allowlist scope before any flag is written.

Audit entries for API-triggered jobs and feature patches record the **token name + id** (and the creating user when known). In the UI: **Settings → API tokens → Audit trail**, or **Audit → filter by API token**.

---

## Security notes

- Prefer **least scopes** + **feature allowlist** + **IP allowlist** for production automations.  
- Do not put tokens in public git or Discord.  
- Prefer TLS; tokens over plain HTTP on untrusted networks are stealable.  
- `/metrics` uses a separate `METRICS_TOKEN` (not the same as `ph_` tokens).  
- See [SECURITY.md](../SECURITY.md).
