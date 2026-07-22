# Catalog & integrations

## What this is

Top-nav **Catalog** (`/catalog` → 303 to Integrations) is the hub for **optional** products and topology that sit **around** the fleet — not the fleet itself.

Each section shares:

- An **ops-hero** (title + dual-line pulse for the section)  
- A **full-width tab bar** under the hero: Integrations · Certificates · Templates · Network  

| Tab | Path | Purpose | Why it is separate |
|-----|------|---------|---------------------|
| **Integrations** | `/integrations` (default) | Connect Kuma, Grafana, Pi-hole, NPM, **LAN Discovery** | Vendor adapters + optional nmap |
| **Certificates** | `/certificates` | TLS vault + service maps + SSH deploy | PEMs are sensitive and multi-consumer |
| **Templates** | `/templates` | Service template catalog & deploy | Stack recipes, not product logins |
| **Network** | `/dns` | Host DNS, service paths (**By path type** stats), Hosts/Path maps | Topology view of names and edges |

## Why Catalog exists

Core fleet ops (SSH, backups, patch, Docker) work **without** any Catalog entry. Catalog is for operators who also run the usual homelab edge stack and want:

- Status and deep links (Kuma, Grafana)  
- DNS fan-out and maps (Pi-hole + Network)  
- Proxy inventory and cert pull (NPM + Certificates)  
- Repeatable stack deploy (Templates)  

PiHerder stays the **fleet truth**; external tools enrich via adapters and deep links rather than embedding every vendor feature.

---

## End-to-end: first useful Catalog week

1. Deploy or already run Kuma / Pi-hole / NPM (templates or existing).  
2. Connect each product under **Integrations** (test / poll).  
3. Bind Kuma monitors → [Fleet Services](../day-to-day/dashboard-and-services.md).  
4. Set host FQDNs + Network map LAN settings → [Network maps](dns-fabric.md).  
5. Pull or upload certs → map deploy targets → [Certificates](certificates.md).  

Journey: [Operator scenarios — Journey E](../getting-started/operator-scenarios.md#journey-e).

---

## Integrations (products)

| Product | Role | Start here when you… |
|---------|------|----------------------|
| [Uptime Kuma](uptime-kuma.md) | Status, TLS days, deep links, fleet Services | Want up/down and TLS in PiHerder |
| [Grafana](grafana.md) | Dashboard inventory, deep links, preferred names | Want one-click metrics from server/Docker |
| [Pi-hole](pihole.md) | v6 multi-instance stats, local DNS/CNAME fan-out | Manage LAN DNS from the herder |
| [LAN Discovery (nmap)](lan-discovery.md) | Opt-in CIDR scans, devices, map names/kinds, Hosts map overlay, schedules | Want an end-to-end LAN view without linking every host |
| [Nginx Proxy Manager](npm.md) | Proxy hosts (read-only) + certificate pull | Inventory edge hosts and cert material |
| [Certificates](certificates.md) | Encrypted store, PEM upload, deploy maps, NPM renew | Push TLS files to many consumers |
| [Network maps](dns-fabric.md) | Host A + service paths; Hosts/Path maps; **runtime stack** expand + order | Visualise name → host → app → containers |

!!! note "LAN Discovery is opt-in"
    The default compose stack does **not** start the nmap worker. Enable profile `nmap`, build `Dockerfile.nmap`, and configure CIDRs before scanning. Compose fences web with `PIHERDER_NMAP_WORKER=0` (nmap worker `=1`) — [LAN Discovery](lan-discovery.md) · [env reference](../operations/env-reference.md#lan-discovery-nmap--opt-in).

Credentials and cert PEMs are Fernet-encrypted and included in [self-backup](../operations/self-backup.md).

## Design principle

- PiHerder owns **fleet truth**  
- External tools enrich via adapters and deep links  
- Prefer **n8n + token REST** over embedding every vendor API  

## Related

- [Operator scenarios](../getting-started/operator-scenarios.md) — task index  
- [Templates](../service-templates/overview.md)  
