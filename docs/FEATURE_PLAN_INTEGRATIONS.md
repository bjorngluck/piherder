# Feature Plan: Integration Hub (Uptime Kuma first)

**Document:** `docs/FEATURE_PLAN_INTEGRATIONS.md`  
**Status:** **Shipped (H1 Kuma + Grafana)** — 2026-07-10  
**Owner:** Bjorn  
**Horizon:** H1 / v0.3  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § Horizon 1 · [SPEC.md](../SPEC.md) Phase 5 · [ADMIN.md](ADMIN.md) § Uptime Kuma / Grafana

---

## Goal (delivered)

PiHerder is an optional **integration hub** for the homelab stack:

### Uptime Kuma
- Connect an existing Kuma instance with a **Kuma API key** (primary) and optional Kuma login for dashboard IDs.
- Poll **`GET /metrics`** (Prometheus text) for monitor inventory, status, response time, and TLS cert fields.
- Bind monitors at three scopes:
  1. **Server → SSH** reachability  
  2. **Host service** (no Docker — e.g. Home Assistant on HAOS)  
  3. **Docker project / container**  
- Deep links to **`{kuma}/dashboard/{id}`** when a numeric dashboard ID is known.
- Fleet **Services** icon grid + per-server Services page; logos via favicon discovery or upload.
- Down notifications (transition-only) + push preference `integration_down`.

### Grafana
- Connect existing Grafana with optional **service account token** (Bearer).
- Poll **`GET /api/health`** (version, database) and **`GET /api/search?type=dash-db`** when token present.
- Bind **server → dashboard UID** (`role=dashboard`); chips on server detail.
- **Open in Grafana** with URL query templates (`var-host={hostname}`, etc.).
- High-level health chips on integration detail (not full metrics exploration).

Top-level **Integrations** nav (not under Settings). Later adapters (multi Pi-hole, NPM, …) reuse the same registry + binding model.

---

## Decisions (locked)

| # | Decision |
|---|----------|
| 1 | Authenticated Kuma data (not status-page-only); SSH + HTTP service bindings |
| 2 | Server bindings required; service bindings at host and Docker scopes |
| 3 | Plan first, then implement |
| 4 | Top-level **Integrations** menu |
| 5 | Auth = **API key** (primary) for `/metrics` |
| 6 | Optional Kuma **username/password** for Socket.IO name→dashboard id map (deep links on Kuma 1.23) |
| 7 | PiHerder does not ship or manage Kuma; operator points at existing instance |
| 8 | Service logos: auto favicon + operator upload under `DATA_ROOT/service_logos/` |
| 9 | Dashboard tile + fleet `/services` icon grid |

### Principles

- Integrations optional; core fleet ops work alone.  
- PiHerder owns fleet truth; Kuma owns continuous probes.  
- Read-mostly in H1; create-monitor = H2.  
- Secrets Fernet-encrypted; included in herder self-backup.  

---

## Architecture (as shipped)

```
PiHerder web + scheduler
  → HTTP GET {base}/metrics  (Basic auth: empty user + API key)
  → optional Socket.IO login  (username/password → dashboard ids)
  → Integration + IntegrationBinding in PostgreSQL
  → Server list/detail chips · Docker chips · Services pages · fleet /services
  → notifications on down transitions
```

**Kuma 1.23 note:** Prometheus labels often omit `monitor_id`. External key = monitor **name**; numeric **dashboard id** stored in meta (manual field or login sync). Deep link: `/dashboard/{id}`.

---

## Data model (shipped)

### `integration`
type, name, base_url, enabled, config_json (poll_interval, tls_verify), credentials_encrypted (`api_key`, optional `username`/`password`), last_status_json, last_polled_at, last_error.

### `integrationbinding`
- **role:** `ssh_reachability` | `service`  
- **docker_project** / **docker_container** (optional; empty = host service)  
- external_id, external_label, external_meta_json, **logo_path**, last_state, last_message, last_checked_at  
- Unique scope: integration + server + role + external_id + project + container  

---

## UI surfaces (shipped)

| Surface | Path / place |
|---------|----------------|
| Integrations list / Kuma detail | `/integrations`, `/integrations/{id}` |
| Server list SSH chip | Opens Kuma dashboard when id known |
| Server detail | SSH card + host services preview |
| Server Services | `/servers/{id}/services` |
| Docker stack | TLS/status chips on project/container |
| Fleet Services | `/services` icon grid |
| Dashboard | Services count tile → `/services` |
| Account push prefs | Integration monitor down |

---

## Polling & notifications

- APScheduler interval (default 60s) + manual Test / Poll.  
- Single-flight poll lock per integration.  
- Transition-only notifications (`integration_monitor_down`); resolve on recovery.  
- Opportunistic favicon discovery when logo missing.  

---

## Implementation phases (all done for Kuma H1)

| Phase | Status |
|-------|--------|
| 0 Plan | Done |
| 1 Registry + nav | Done |
| 2 API key + `/metrics` adapter | Done |
| 3 SSH bindings + UI | Done |
| 4 Poller + notifications | Done |
| 5 Host + Docker service scope | Done |
| 6 Per-server + fleet Services pages | Done |
| 7 Logos (discover + upload) | Done |
| 8 Docs | Done (this update) |

**Still H1 later / other adapters:** multi Pi-hole, NPM/HA generic URL entries.

---

## File map

| Area | Path |
|------|------|
| Models | `app/models.py` |
| Migrations | `012`–`015` integrations / binding scope / logos |
| Adapter | `app/services/integrations/uptime_kuma.py` |
| Registry / poll | `app/services/integrations/registry.py`, `poll.py` |
| Logos | `app/services/service_logos.py` |
| Routers | `integrations.py`, `server_services.py`, `fleet_services.py` |
| Templates | `integrations_*.html`, `server_services.html`, `fleet_services.html` |
| Tests | `tests/test_integrations_kuma.py`, `test_integration_bindings.py` |

---

## Success criteria

- [x] Top-level Integrations nav  
- [x] API key + `/metrics` poll  
- [x] SSH bindings + suggest matches  
- [x] Host + Docker service bindings + TLS from metrics  
- [x] Dashboard deep links when dashboard id set  
- [x] Down notifications + push pref  
- [x] Per-server Services page + fleet `/services` icon grid  
- [x] Logos: discover + upload  
- [x] Herder backup includes integrations/bindings  
- [x] Pytest for metrics parse / bindings / deep links  
- [x] Operator smoke against live Kuma  

---

## Grafana file map (shipped)

| Area | Path |
|------|------|
| Adapter | `app/services/integrations/grafana.py` |
| Registry / poll | `TYPE_GRAFANA`, `ROLE_DASHBOARD`, `_poll_grafana` |
| UI | `integrations_grafana_form.html`, `integrations_grafana_detail.html` |
| Tests | `tests/test_integrations_grafana.py` |

---

## H2 teaser (not in this ship)

- Create monitor in Kuma from templates (Socket.IO).  
- Auto-bind on add-server.  
- Multi Pi-hole / NPM / HA generic URL adapters.  

---

**End of Feature Plan**
