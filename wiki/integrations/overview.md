# Catalog & integrations

Top-nav **Catalog** (`/catalog`) is the hub for optional products and topology. Settings-style tabs:

| Tab | Path | Purpose |
|-----|------|---------|
| **Integrations** | `/integrations` (default) | Connect Kuma, Grafana, Pi-hole, NPM |
| **Certificates** | `/certificates` | TLS vault + service maps + SSH deploy |
| **Templates** | `/templates` | Service template catalog & deploy |
| **Network** | `/dns` | Host DNS, service paths, Hosts/Path maps |

Core fleet ops (SSH, backups, patch, Docker) work **without** any Catalog entry.

## Integrations (products)

| Product | Role |
|---------|------|
| [Uptime Kuma](uptime-kuma.md) | Status, TLS days, deep links, [fleet Services](../day-to-day/dashboard-and-services.md) |
| [Grafana](grafana.md) | Dashboard inventory, deep links, preferred names (Inventory tab) |
| [Pi-hole](pihole.md) | v6 multi-instance stats, local DNS/CNAME fan-out, gravity/actions |
| [Nginx Proxy Manager](npm.md) | Proxy hosts (read-only) + certificate pull into vault |
| [Certificates](certificates.md) | Encrypted store, PEM upload, deploy maps, NPM renew |
| [Network maps](dns-fabric.md) | Host A + service paths; Pi-hole adopt; LAN/cloud/Internet Hosts map + Path map |

Credentials and cert PEMs are Fernet-encrypted and included in [self-backup](../operations/self-backup.md).

## Design principle

- PiHerder owns **fleet truth**  
- External tools enrich via adapters and deep links  
- Prefer **n8n + token REST** over embedding every vendor API  

## Related

- [Operator scenarios](../getting-started/operator-scenarios.md) — task index  
- [Templates](../service-templates/overview.md)  
