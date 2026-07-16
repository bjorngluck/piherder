# Dashboard & fleet Services

## Dashboard (`/`)

After login, **Dashboard** is the home page: fleet health at a glance.

| Tile / block | Meaning |
|--------------|---------|
| **Servers** | Host count → [Servers](add-server.md) |
| **Services** | Monitored apps (Kuma bindings) → **`/services`** (this page below) |
| **Need attention** | Hosts with OS/image updates or reboot pending |
| **Reboot pending** | Count of hosts flagging reboot-required |
| **Open alerts** | Inbox count → [Notifications](jobs-audit-notifications.md) (bell) |
| OS / container summary cards | Aggregate package/image update counts |
| **Needs attention** table | Only hosts that need work; Open / Docker shortcuts |
| **Network maps** panel | Constellation graphic + **named hosts** / **mapped names** / **NPM hosts** → Catalog **Network**, Hosts map, Path map ([Network maps](../integrations/dns-fabric.md)) |
| **Quick links** | Servers, notifications, audit, Settings, Pi-hole, Certificates, Catalog |

Status comes from **last check jobs** (and related caches) — not a continuous SSH poll on every open. The Network maps panel uses a **cheap pulse** (counts only), not a full SVG build.

| Pulse field | Source |
|-------------|--------|
| **Named hosts** | Fleet servers with `dns_name` set |
| **Mapped names** | `ServiceDnsRecord` rows |
| **NPM hosts** | Sum of NPM integration poll `proxy_host_count` (matches Catalog → NPM detail; not only DNS `via_proxy` flags — the edge hostname itself is often `via_proxy=false`) |

## Fleet Services (`/services`)

Fleet-wide view of **Uptime Kuma service bindings** (host service or Docker project/container). Uses the shared **ops-hero** (up / down / TLS pulse) plus filter chips and search.

| Feature | Use |
|---------|-----|
| **Filter** | All · Up · Down · TLS issue |
| **Search** | Name, host, location |
| **App** | Open service URL (if set) — primary open-app tile |
| **Kuma** | Open monitor in Kuma |
| **Host** | Per-server services page |
| **Docker** | Host Docker page when binding is a stack |
| **Logo…** | Upload, fetch favicon, or remove (operator+) |

On **narrow viewports**, service rows stack metadata and actions vertically so controls stay tappable (no horizontal squash).

### Empty grid

If you see “No fleet services yet”:

1. [Connect Uptime Kuma](../integrations/uptime-kuma.md).  
2. Bind HTTP/HTTPS monitors as **service** (or use Suggest matches).  
3. Return to `/services` or the Dashboard **Services** tile.

Per-host view: **Server → Services** (`/servers/{id}/services`) — same card pattern and mobile stacking for that host only.

## Related

- [Updates & patching](updates-and-patching.md) — what “attention” means  
- [Jobs, audit & notifications](jobs-audit-notifications.md)  
- [Uptime Kuma](../integrations/uptime-kuma.md)  
