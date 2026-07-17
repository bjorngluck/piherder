# Uptime Kuma

## What this is

The **Uptime Kuma integration** connects PiHerder to an existing (or template-deployed) Kuma instance so monitor status, TLS days, and bindings show up on servers, Docker, Dashboard tiles, and the fleet **Services** grid.

## Why it exists

Kuma is excellent at HTTP/TCP checks; PiHerder is excellent at fleet actions. Connecting them avoids a second mental model: when something is down or TLS is expiring, you see it next to the host that owns the service — and can jump into Kuma or the Docker page in one click.

You can **deploy** Kuma via [Templates](../service-templates/overview.md), then connect the integration for status and bindings.

<figure class="ph-figure" markdown>
  ![Kuma integration](../assets/screenshots/integrations-kuma.svg)
  <figcaption>Connect + bind SSH/services/Docker. <span class="ph-wireframe-badge">wireframe</span></figcaption>
</figure>

---

## End-to-end: Kuma → fleet Services

1. Run Kuma (template or existing) and create an **API key**.  
2. From a host that can reach Kuma, probe metrics (optional sanity check).  
3. **Catalog → Integrations → + Uptime Kuma** — URL + key → Test / Poll.  
4. Bind monitors (SSH, host service, or Docker) or **Suggest matches**.  
5. Open `/services` and filter **Down** / **TLS issue**.  
6. Optional: enable Web Push for **Integration monitor down**.

---

## Connect

1. In Kuma: **Settings → API Keys** — create a key (copy once).  
2. From a host that can reach Kuma:

   ```bash
   curl -sS -u ":$KUMA_API_KEY" "https://uptime.example.com/metrics" | head
   ```

3. PiHerder → **Catalog → Integrations → + Uptime Kuma** — base URL + API key → Save.  
4. **Optional (recommended on Kuma 1.23):** username/password for dashboard ID map (`/dashboard/{id}`). Metrics often omit `monitor_id`.  
5. Poll interval default **60s**; **Test** / **Poll now**.

## Binding scopes

| Scope | Where you see it | Why bind that way |
|-------|------------------|-------------------|
| **SSH** reachability | Server chips, detail, Services | Host alive vs app alive |
| **Host service** (no Docker) | Server detail, Services — e.g. HAOS | Non-compose apps |
| **Docker** project/container | Docker chips + Services | Stack-level status |

- **Suggest matches** maps unbound servers to TCP/SSH monitors.  
- HTTP monitors expose **TLS valid** + **days remaining**.  
- Down transitions → in-app notifications (+ optional Web Push: **Integration monitor down**).

## Services UI

| Path | Purpose |
|------|---------|
| `/integrations` | Connect + bind |
| `/servers/{id}/services` | Per-host services |
| `/services` | Fleet icon grid — filter **All / Up / Down / TLS issue**, search by name/host/location, App / Kuma / Host / Docker links |
| Dashboard | Services count tile → `/services` |

Empty fleet grid explains how to bind Kuma monitors. Prefer fixing **down** or **TLS** filters first when something looks wrong.

### Logos

Auto favicon fetch + manual upload under `DATA_ROOT/service_logos/` (`./piherder_data`).

## Related

- [Dashboard & Services](../day-to-day/dashboard-and-services.md)  
- [PWA & Web Push](../account-security/pwa-push.md)  
