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
| **Network maps** panel | Named hosts / paths pulse → Catalog **Network**, Hosts map, Path map ([Network maps](../integrations/dns-fabric.md)) |
| **Quick links** | Servers, notifications, audit, Settings, Pi-hole, Certificates, Catalog |

Status comes from **last check jobs** (and related caches) — not a continuous SSH poll on every open. The Network maps panel uses a **cheap pulse** (counts only), not a full SVG build.

## Fleet Services (`/services`)

Icon grid of **Uptime Kuma service bindings** across the fleet (host service or Docker project/container).

| Feature | Use |
|---------|-----|
| **Filter** | All · Up · Down · TLS issue |
| **Search** | Name, host, location |
| **App** | Open service URL (if set) |
| **Kuma** | Open monitor in Kuma |
| **Host** | Per-server services page |
| **Docker** | Host Docker page when binding is a stack |
| **Logo…** | Upload, fetch favicon, or remove (operator+) |

### Empty grid

If you see “No fleet services yet”:

1. [Connect Uptime Kuma](../integrations/uptime-kuma.md).  
2. Bind HTTP/HTTPS monitors as **service** (or use Suggest matches).  
3. Return to `/services` or the Dashboard **Services** tile.

Per-host view: **Server → Services** (`/servers/{id}/services`).

## Related

- [Updates & patching](updates-and-patching.md) — what “attention” means  
- [Jobs, audit & notifications](jobs-audit-notifications.md)  
- [Uptime Kuma](../integrations/uptime-kuma.md)  
