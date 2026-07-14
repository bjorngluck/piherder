# Network (hosts ↔ apps ↔ proxy ↔ internet)

End-to-end **network maps**: Pi-hole records, fleet hosts, NPM, LAN/gateway/public IP, Uptime Kuma, and Docker links — path cards and topology maps (not a “service mesh”).

**UI label:** **Catalog → Network** (URL slug remains `/dns` for compatibility).

**Pages:** [Network hub](#map-pages) · [Hosts map](#map-pages) · [Path map](#map-pages)

---

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

---

## Network map settings

On the **Network hub** (`/dns`), configure home topology used by the Hosts map:

| Field | Example | Used for |
|-------|---------|----------|
| **LAN subnet** | `192.168.86.0/24` | Home host ring; IPs **outside** CIDR → **cloud** |
| **Gateway / router IP** | `192.168.86.1` | Router node between Internet and LAN |
| **Public (WAN) IP** | looked up or manual | Shown on Internet / Public IP nodes |
| **Lookup public IP now** | — | Outbound check from the PiHerder host |
| **Router Kuma monitor** | optional | Status chip + deep link on **Router** |
| **Public IP Kuma monitor** | optional | Status on **Public IP** / Internet |

### Hosts map layout

```text
Internet (☁) ── WAN ── Router ── LAN ── home hosts (RFC1918 / in subnet)
     │
     └── WAN ── cloud hosts (public IP / outside subnet, e.g. Nomad VPS)
```

- **Spine always drawn** when fleet hosts exist (even if LAN/gateway settings are empty).
- Without a LAN CIDR: **RFC1918 / CGNAT** addresses stay on LAN; other addresses are **cloud**.
- LAN hosts sit on a ring with a **clear top gap** so nothing covers the Router → LAN link.
- Cloud hosts sit beside the Internet cloud (not on the LAN ring).

---

## Map pages

| Page | URL | Shows |
|------|-----|--------|
| **Network hub** | `/dns` | Path cards · filters · network settings · adopt/import · host A table · external checklist |
| **Hosts map** | `/dns/physical` | Rack cards (mobile-first) + fleet SVG (Internet → Router → LAN → hosts + apps) |
| **Path map** | `/dns/logical` | Flow list (mobile-first) + SVG (URL → NPM hub → destination) |

### Focus, zoom & mobile

- **Tap or hover** any **host** (including Nomad with no mapped services), **Router**, **LAN**, **Internet**, **Public IP**, or **app path** to highlight and show a callout.
- Hosts **without** mapped services are still selectable (node focus). App satellites focus the service **path**.
- **Open host** / **Open in Kuma** appears when the focused node has a link (same-tab for fleet hosts; new tab for external Kuma).
- **Copy path** copies the callout route string.
- **Clear focus** / tap the same node again to clear.
- Maps: **pinch** / scroll-wheel zoom up to **500%** (SVG **viewBox** — stays sharp), **drag** to pan, **+/− / 1:1**, double-click reset. Hover preview is mouse/stylus only; finger tap locks focus without navigating.
- Status dots: **green** = last Pi-hole sync ok · **amber** partial · **red** error · small amber ring = managed cert linked · Kuma **up/down** on Router / Public IP when bound.
- Deep links: `/dns/physical?focus=<service_id>` and `/dns/logical?focus=<service_id>` (also from each path card).
- On **narrow screens**, maps default to the **list** (racks / flows). Use **View full map** for the SVG.
- Hub and path map support **search** and path-type filters (All / Via NPM / Direct / Host identity).
- **Adopt candidates** load after the hub paints (HTMX → `/dns/candidates`) so a slow or down Pi-hole does not block path cards / host DNS.
- Hosts map caps app satellites per host (then a **+N more** marker); full app list stays on rack cards.

### Light / dark theme

Infrastructure nodes (Internet cloud, Router, LAN, NPM hub) use theme-aware fills (no default black SVG fill). Zoom chrome stays readable in light mode.

---

## Setup

1. **Base domain** (optional) on Catalog → Network (e.g. `hacknow.info`) for name suggestions.  
2. **Network map** — set LAN CIDR, gateway, public IP (or **Lookup**); optionally bind Router / WAN Kuma monitors (poll Kuma first so the dropdown is populated).  
3. **Host DNS** — each server **Edit → General**: FQDN + IP; tick **Manage A on all Pi-holes** (creates/updates A; duplicates treated as success).  
4. **Import existing names** — Catalog → Network → **Import all from Pi-hole** (or Adopt per row after candidates load). Existing CNAMEs are mapped; Pi-hole is **not** recreated when the record already exists.  
5. **Host identity** — when the app name equals the host A name (Kuma host-level service, no Docker), use **Map host identity** (A only).  
6. **Template deployments** — Service DNS card attaches an inferred plan (one FQDN field when needed).  
7. **External DNS** — checklist on the hub for Cloudflare/etc. (not automated in 0.5.0).

---

## Pi-hole behaviour

| Action | Behaviour |
|--------|-----------|
| Host A / service CNAME create | Fans out to **all enabled** Pi-holes |
| Record already present | Treated as **success** (adopt / re-sync safe) |
| Remove service **CNAME** mapping | Deletes CNAME on Pi-holes when managed |
| Remove **host identity** mapping | Does **not** delete host A (owned by server Host DNS) |

Audit actions include `dns_host_*`, `dns_service_cname_sync`, `dns_service_a_sync`, `dns_service_delete`.

---

## Data model (summary)

| Setting / field | Storage | Notes |
|-----------------|---------|--------|
| `network_lan_subnet` | App settings | CIDR |
| `network_gateway_ip` | App settings | Router internal IP |
| `network_public_ip` (+ checked_at) | App settings | WAN IP |
| `network_gateway_kuma_external_id` | App settings | Kuma monitor id/name |
| `network_public_kuma_external_id` | App settings | Optional WAN monitor |
| `network_kuma_integration_id` | App settings | Empty = first enabled Kuma |
| **Server** | `dns_name`, `dns_manage_a`, `dns_ip_override` | Host A |
| **ServiceDnsRecord** | FQDN, `record_type` (`cname` \| `a`), servers, project, NPM, sync | Service path |

Resolution also uses Pi-hole inventory, NPM poll cache + proxy_host binds, Kuma service binds, and stack deployments.

**Code:** `app/services/dns_fabric.py` · `app/routers/dns.py` · `app/static/js/fabric-mesh.js` · templates `dns_*.html`  
**Tests:** `tests/test_dns_fabric.py`

---

## Related

- [Pi-hole](pihole.md)  
- [NPM](npm.md)  
- [Uptime Kuma](uptime-kuma.md)  
- [Certificates](certificates.md)  
- [v0.5.0 plan § F.1](https://github.com/bjorngluck/piherder/blob/main/docs/PLAN_v0.5.0.md)  
- Roadmap H2.5: container dependency graph (DB, Redis, …) — [ROADMAP_ECOSYSTEM.md](https://github.com/bjorngluck/piherder/blob/main/docs/ROADMAP_ECOSYSTEM.md)
