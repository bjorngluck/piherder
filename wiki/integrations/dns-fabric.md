# DNS fabric (hosts ↔ apps ↔ proxy)

End-to-end **LAN DNS** and service topology: Pi-hole records, fleet hosts, NPM, and Docker/Kuma links in one model.

**UI:** **Catalog → DNS** (`/dns`) · full mesh: [`/dns/physical`](../integrations/dns-fabric.md#topology-pages) · [`/dns/logical`](../integrations/dns-fabric.md#topology-pages)

## Mental model — entities & relationships

One published **name** maps through optional layers:

```text
name  →  [NPM]  →  host  →  [service/project]  →  [container]
```

| Entity | Example | Notes |
|--------|---------|--------|
| **Name** | `grafana.hacknow.info` | CNAME, or **host identity A** when name = host FQDN |
| **NPM** | RPI5-3 | Only when proxied (edge host) |
| **Host** | RPI5-6 | Fleet server + A record |
| **Service** | `grafana` | Compose project (Kuma / NPM / deploy) |
| **Container** | `grafana` | Runtime container |

### Path kinds

| Kind | Meaning |
|------|---------|
| **host_identity** | Name **is** the host A record (e.g. `3dprint.hacknow.info`) — **no CNAME** |
| **app** | CNAME → host → Docker project/container (e.g. Grafana) |
| **npm_host** | CNAME → NPM edge → host |
| **npm_app** | CNAME → NPM → host → project/container (e.g. qBittorrent) |

## Topology pages

| Page | URL | Shows |
|------|-----|--------|
| **DNS hub** | `/dns` | Per-service **paths** (one card each) · adopt/import · host A table · external checklist |
| **Physical mesh** | `/dns/physical` | Full fleet SVG: all hosts + apps + NPM edge links · rack cards |
| **Logical mesh** | `/dns/logical` | Full SVG: URL → NPM hub / direct → destination · flow list |

## Setup

1. **Base domain** (optional) on Catalog → DNS (e.g. `hacknow.info`) for name suggestions.  
2. **Host DNS** — each server **Edit → General**: FQDN + IP; tick **Manage A on all Pi-holes** (creates/updates A; duplicates treated as success).  
3. **Import existing names** — Catalog → DNS → **Import all from Pi-hole** (or Adopt per row). Existing CNAMEs are mapped; Pi-hole is **not** recreated when the record already exists.  
4. **Host identity** — when the app name equals the host A name (Kuma host-level service, no Docker), use **Map host identity** (A only).  
5. **Template deployments** — Service DNS card attaches an inferred plan (one FQDN field when needed).  
6. **External DNS** — checklist on the hub for Cloudflare/etc. (not automated in 0.5.0).

## Pi-hole behaviour

| Action | Behaviour |
|--------|-----------|
| Host A / service CNAME create | Fans out to **all enabled** Pi-holes |
| Record already present | Treated as **success** (adopt / re-sync safe) |
| Remove service **CNAME** mapping | Deletes CNAME on Pi-holes when managed |
| Remove **host identity** mapping | Does **not** delete host A (owned by server Host DNS) |

Audit actions include `dns_host_*`, `dns_service_cname_sync`, `dns_service_a_sync`, `dns_service_delete`.

## Data model (summary)

- **Server:** `dns_name`, `dns_manage_a`, `dns_ip_override` (+ `ip_address`)  
- **ServiceDnsRecord:** FQDN, `record_type` (`cname` \| `a`), target/backend servers, project, NPM hint, sync status  
- Resolution: Pi-hole inventory, NPM poll cache + proxy_host binds, Kuma service binds, stack deployments  

## Related

- [Pi-hole](pihole.md)  
- [NPM](npm.md)  
- [Certificates](certificates.md)  
- Roadmap H2.5: container dependency graph (DB, Redis, …) — [ROADMAP_ECOSYSTEM.md](https://github.com/bjorngluck/piherder/blob/main/docs/ROADMAP_ECOSYSTEM.md)  
