# PiHerder Automation API (`/api/v1`)

**Status:** v1 (admin-managed tokens)  
**Related:** [ADMIN.md](ADMIN.md) § API tokens · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · interactive OpenAPI at **`/docs`** and **`/openapi.json`**

This API is for **automation** (n8n, Home Assistant, scripts). Browser UI uses session cookies and is separate.

Home Assistant can consume these endpoints with a bearer token today. Managing an **HAOS host from PiHerder** (SSH / `ha` CLI) is separate path-1 product work — see [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) and wiki [HAOS hosts](../wiki/day-to-day/haos-hosts.md).

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

- Secret is shown **once** at **create** or **rotate** (Settings UI has **Copy token** + a one-time **MCP / agent client config** snippet); only a hash is stored.
- Admins can **edit** name, scopes, and IP allowlist without rotating the secret.
- Optional **`expires_at`** (never / 30d / 90d / custom). Expired tokens fail Bearer lookup like revoked ones.
- **Rotate** issues a new secret; the previous value stops working immediately. Expiry is preserved.
- **Revoke** immediately if leaked (Settings or `DELETE /api/v1/tokens/{id}` with admin session).
- **MCP agent** mint preset in Settings suggests a `mcp-…` name and default scope `read` (add `jobs` / `edit` / `files` as needed). Hosted MCP is **`POST /mcp`** on this origin with the same Bearer token (not a query parameter). The one-time snippet is that URL plus `Authorization`. Set `PIHERDER_PUBLIC_URL` or the snippet host is `https://piherder.example.com` and the banner says so. An `http`/`https` `Origin` must match the request `Host` or that public URL; a Bearer token does not bypass it. Clients that omit `Origin` are unchanged. Optional stdio `uvx piherder-mcp` is the air-gapped fallback. See [wiki/operations/mcp.md](../wiki/operations/mcp.md).

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

**Proxy:** Bundled Caddy **overwrites** `X-Forwarded-For` / `X-Real-IP` with the true client IP (`{remote_host}`) so allowlists work for traffic on ports 8888/8443. Compose publishes web as **`127.0.0.1:8000` only** (not the LAN). Forwarded headers are honoured only when the TCP peer is in `PIHERDER_TRUSTED_PROXY_CIDRS` (Compose default: RFC1918 + loopback so Caddy is trusted). Hitting the app port from an untrusted peer ignores spoofed `X-Forwarded-For`. Mismatch → **403** `Client IP not allowed for this API token`.

---

## Scopes

### Capability scopes

| Scope | Allows |
|-------|--------|
| `read` | `GET` catalog, health, servers, jobs |
| `jobs` | `POST /api/v1/servers/{id}/jobs` |
| `edit` | `PATCH /api/v1/servers/{id}/features` |
| `files` | Host Files (fleet identity only): list / download / upload / mkdir / rename / delete-empty. Not in default token scopes. Privileged Files, zip, edit, chmod, recursive delete stay **UI + 2FA**. Richer API Files is **under consideration for v1.4+**. |

### Feature allowlist scopes (optional)

If **no** `feature:*` scopes are present, the token may act on **all** features (still limited by capability scopes and the server’s feature flags).

If **any** `feature:*` scope is set, only those features are allowed for jobs and feature edits:

| Scope | Feature key | Jobs | Edit flags |
|-------|-------------|------|------------|
| `feature:backup` | `backup` | `backup`, `retention` | `backup` |
| `feature:os` | `os` | `os_patch`, `os_update_check`, `host_reboot` | `os_patch` |
| `feature:docker` | `docker` | `container_patch`, `container_update_check`, `container_start`, `container_stop`, `container_restart`, `container_redeploy`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, `template_redeploy` | `docker` |

**Example least-privilege tokens**

| Use case | Scopes | IP allowlist |
|----------|--------|--------------|
| Grafana / status poller | `read` | monitoring subnet |
| MCP agent (read-only) | `read` | empty (roaming) or laptop LAN CIDR |
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
| `GET` | `/api/v1/health` | `read` | `{ ok, scopes, allowed_features, client_ip, service_migrate }` |
| `GET` | `/api/v1/summary` | `read` | Fleet heartbeat: hosts, updates, jobs, alerts, plus resource **sums** `cpu_cores`, `memory_*_bytes`, `disk_*_bytes`, `containers`. DB snapshots only. |

### Servers

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `GET` | `/api/v1/servers` | `read` | List servers (`features` object). Optional `q`, `limit` (default **100**, max 100), `offset`. Response includes `total`, `limit`, `offset`. Previously unbounded. |
| `GET` | `/api/v1/servers/{id}` | `read` | One server |
| `GET` | `/api/v1/inventory` | `read` | Last Docker inventory for every host (DB snapshot, never SSH). Slim containers: `name`, `running`, `state`, `image`, `status` (uptime text), `project` |
| `GET` | `/api/v1/servers/{id}/inventory` | `read` | Same snapshot for one host, plus `os_pretty`, `hardware`, `disk_*_bytes` |
| `GET` | `/api/v1/services` | `read` | Fleet service chips (`state` up/down) from stored monitor rows. Does not poll Kuma or NPM |
| `PATCH` | `/api/v1/servers/{id}/features` | `edit` | Toggle feature flags |
| `GET` | `/api/v1/servers/{id}/files?p=` | `files` | List jail-relative directory (fleet) |
| `GET` | `/api/v1/servers/{id}/files/download?p=` | `files` | Download one file |
| `POST` | `/api/v1/servers/{id}/files` | `files` | Multipart upload (`file`, `p`) |
| `POST` | `/api/v1/servers/{id}/files/mkdir` | `files` | JSON `{p, name}` |
| `POST` | `/api/v1/servers/{id}/files/rename` | `files` | JSON `{p, src, dest}` |
| `DELETE` | `/api/v1/servers/{id}/files?p=` | `files` | Delete file or empty directory (recursive delete is UI-only) |

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
  "os_type": "ubuntu",
  "os_id": "ubuntu",
  "os_pretty": "Ubuntu 24.04.3 LTS",
  "os_display": "Ubuntu 24.04.3 LTS",
  "hardware": "Raspberry Pi 5 Model B Rev 1.0",
  "arch": "aarch64",
  "os_updates_count": 0,
  "container_updates_count": 2,
  "reboot_pending": false,
  "last_backup_at": "2026-07-10T02:00:00Z",
  "alerts_open": 0,
  "alert_title": null,
  "alerts": []
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
| `host_reboot` | os_patch | `feature:os` |
| `container_patch` | docker | `feature:docker` |
| `container_update_check` | docker | `feature:docker` |
| `container_start` | docker | `feature:docker` |
| `container_stop` | docker | `feature:docker` |
| `container_restart` | docker | `feature:docker` |
| `container_redeploy` | docker | `feature:docker` |
| `docker_stack_check` | docker | `feature:docker` |
| `docker_stack_deploy` | docker | `feature:docker` |
| `docker_stack_stop` | docker | `feature:docker` |
| `docker_stack_start` | docker | `feature:docker` |
| `docker_stack_restart` | docker | `feature:docker` |
| `template_deploy` | docker | `feature:docker` |
| `template_redeploy` | docker | `feature:docker` |

That table is the allowlist (`JOB_FEATURE_KEY`). Anything else, including `docker_stack_down`, `docker_stack_remove`, `template_drift_check`, `service_migrate`, `service_migrate_undo`, and `service_migrate_dest_recover`, is **400** `Unsupported job_type`. Move is a separate route below. It is not this POST.

`source_filter` is the backup source name for `backup`. For `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, and `docker_stack_restart` it is the compose project path. `docker_stack_stop` runs `docker compose stop` for that path. It does not remove containers or volumes. `container_start`, `container_stop`, `container_restart`, and `container_redeploy` need that same path plus `service` (one compose service). They do not change the rest of the project. Redeploy is `docker compose up -d --no-deps --pull always` for that service. `template_deploy` and `template_redeploy` are on this allowlist, but this body has no template slug or variable values, so it does not start a catalog deploy. Those jobs still run from the template UI.

**Responses**

| Code | Meaning |
|------|---------|
| 202 | Job accepted (`job_id`, `status`, `job`) |
| 400 | Bad job type / feature disabled on server / empty edit body |
| 401 | Missing/invalid token |
| 403 | Missing scope, feature not allowed, or IP not allowed |
| 404 | Server or job not found |
| 409 | Job already active for this server — body includes existing `job` / `already_active`. Applies to `backup` and exclusive types (`os_patch`, `container_patch`, `host_reboot`, `os_update_check`, `container_update_check`, `docker_stack_check`, `docker_stack_deploy`, `docker_stack_stop`, `docker_stack_start`, `docker_stack_restart`, `template_deploy`, `template_redeploy`). `host_reboot` is also **409** while `os_patch`, `container_patch`, or `backup` is pending or running, and those three are **409** while a reboot is active. Clients should poll the returned job rather than retry-create. |
| 503 | e.g. Celery unavailable for backups |

**Exclusivity:** At most one **pending/running** job of each exclusive type per server. A second trigger does not start a parallel SSH session. `host_reboot` also waits for an OS patch, a container patch, or a backup on that host.

### Move

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `POST` | `/api/v1/servers/{id}/moves` | `jobs` | Start a stop-first Move. HTTP **202** |

```json
{ "dest_server_id": 2, "project": "web", "confirm": true }
```

`project` is the compose project name, not a directory. `confirm` must be `true`. The source leftover is always **stopped**. This route does not accept source remove, port maps, bind overrides, or a destination rename. There is no undo route.

Gates: `jobs`, `feature:docker` when the token is feature-restricted, the docker flag on **both** hosts, and the herder Move surface (`PIHERDER_SERVICE_MIGRATE`, off in demo). Health field `service_migrate` is that surface. **404** when it is off. **400** when `confirm` is not true or the project name is a path. **409** when source or dest already has a stack, Move, or backup job — poll that job. The audit row stores this token and the client IP.

`POST /api/v1/servers/{id}/jobs` with `service_migrate` stays **400**. Hosted MCP `trigger_job` does not grow a Move tool.

Hosted MCP `trigger_job` uses this same list, including `container_start`, `container_stop`, `container_restart`, and `container_redeploy` (**v1.9** [DECISION_MCP_SVC.md](DECISION_MCP_SVC.md)). Those four require `service` and `source_filter`, the same body as this POST. It does not add `docker_stack_down`, `docker_stack_remove`, `template_drift_check`, Move, undo, nmap, the console, token admin, or stale-data cleanup. For a `docker_stack_*` job, `source_filter` is the compose project path. Feature flags and `feature:*` scopes still apply. Token gates stay `jobs`, `feature:docker` when the token is feature-restricted, and the server docker flag. Published adapter **0.3.1** lists the four one-service types. `uvx` at **0.2.0** does not.

### Stale data cleanup

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `POST` | `/api/v1/maintenance/stale-data-cleanup` | `jobs` | Queue fleet `stale_data_cleanup` (HTTP **202**) |

```json
{ "dry_run": false }
```

The token must have `jobs` and **no** `feature:*` scope. A feature-restricted token is **403**. This route is not an MCP tool. The queued audit and the finished audit both store that token (`api_token_id`, `api_token_name`) and the client IP. The worker copies them off the job, because the Celery task has no request.

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
  "allowed_cidrs": ["10.0.0.0/8"],
  "expires_preset": "90d"
}
```

Optional expiry fields: `expires_preset` (`none` | `30d` | `90d` | `custom`) and/or absolute `expires_at` (ISO-8601 UTC). Omit both for never.

MCP-oriented example:

```json
{
  "name": "mcp-laptop",
  "scopes": ["read"],
  "expires_preset": "30d"
}
```

Response includes `secret` **once**, plus `mcp_snippet` (hosted `/mcp` URL and Bearer header first, then the optional `uvx` block). Do not log or re-fetch the plaintext. Do not put the secret in the MCP URL.

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

**v1.6:** first-class **HACS integration** (runs on HA) — Slice 1: fleet sensors, host devices, **Visit** = `{origin}/servers/{id}`, Lovelace **PiHerder fleet** card (`custom:piherder-dashboard-card`). Heartbeat `GET /api/v1/summary` (`read`) includes fleet resource sums. Host `os_pretty` / `hardware` / cpu / memory / disk / `container_count` from the host-facts snapshot. Slice **1b** read APIs: `GET /api/v1/inventory`, `GET /api/v1/servers/{id}/inventory`, `GET /api/v1/services` (stored snapshots only). Plugin [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.4** is that read path. No start/stop. Operator: [wiki Home Assistant](../wiki/integrations/home-assistant.md). [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7 · [PLAN_v1.6.0.md](PLAN_v1.6.0.md). YAML `rest` remains possible. CORS is not required (HA Core is server-side). Prefer an IP allowlist for the HA host.

**v1.7:** Plugin **0.3.0** adds memory % and CPU load sensors, plus host, updates, and resources Lovelace cards. **v1.8:** plugin **0.4.4** is one card (Fleet, Host, Updates; the walk and pictures used **0.4.3**). Click a stat for Home Assistant history. The card keeps the selected host. **Start**, **Stop**, **Restart**, and **Update** act on one compose service (`container_start`, `container_stop`, `container_restart`, `container_redeploy`). `piherder_job_completed` is a Home Assistant bus event from the plugin poll, not a herder webhook and not a new `/api/v1` route. Writes go through HA services to the jobs and features routes above (including `host_reboot`). **v1.9 train:** plugin **0.5.0** stays poll-only. The card confirms Move (`POST /moves` above, `confirm: true`), fleet-jail Files (the `files` routes; delete confirms; not HAOS `/config`), and **Stop project** (`docker_stack_stop`, `docker compose stop`, not down or remove). Console, token admin, and nmap stay in the PiHerder UI. **Hosted MCP** is `POST /mcp` on this process (Streamable HTTP, stateless JSON, the same Bearer token). It does not add `/api/v1` routes. Tool names match [bjorngluck/piherder-mcp](https://github.com/bjorngluck/piherder-mcp): `read`, and `jobs` / `edit` / `files` when the token has them. The 1.7 tool list was those six types. On **v1.8.0**, MCP `trigger_job` matched the jobs POST list above except `container_start`, `container_stop`, `container_restart`, and `container_redeploy` (Home Assistant only). **v1.9** puts those four on hosted `trigger_job` with required `service` and `source_filter` ([DECISION_MCP_SVC.md](DECISION_MCP_SVC.md) · [PLAN_v1.9.0.md](PLAN_v1.9.0.md)). Package version stays **1.8.0** until that tag. `uvx piherder-mcp` remains an optional air-gapped client. Adapter **0.2.0** on PyPI is still the v1.8 list. The source on [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) `main` adds the four types and is not tagged. **0.1.1** was the six-type build. The tool and type tables are on [wiki/operations/mcp.md](../wiki/operations/mcp.md). Operator page: [wiki/operations/mcp.md](../wiki/operations/mcp.md). **Jr-1** moved the remaining exclusive job types onto Celery and added `host_reboot` to that set. [PLAN_v1.7.0.md](PLAN_v1.7.0.md).

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

Audit entries for API **mutations** record the **token name + id** (and the creating user when known), plus **`client_ip`** resolved from Caddy’s `X-Forwarded-For` / `X-Real-IP` (or TCP peer if hit direct). That covers job triggers, feature-flag patches, and host-file writes. Host-file list and download are audited too. Ordinary read GETs — catalog, health, summary, servers, inventory, services, and job reads — are not written to Audit. In the UI: **Settings → API tokens → Audit trail**, or **Audit → filter by API token** (list/detail show IP; search matches IP).

---

## Security notes

- Prefer **least scopes** + **feature allowlist** + **IP allowlist** for production automations.  
- Do not put tokens in public git or Discord.  
- Prefer TLS; tokens over plain HTTP on untrusted networks are stealable.  
- `/metrics` uses a separate `METRICS_TOKEN` (not the same as `ph_` tokens).  
- See [SECURITY.md](../SECURITY.md).
