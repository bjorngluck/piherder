# Integrations

Optional **integration hub**. Top-nav **Catalog** (`/catalog`) always opens **Integrations**, with the same Settings-style tab buttons for **Integrations** | **Templates**. Grafana preferred names are set on the integration **Inventory** tab; see [Grafana](grafana.md).

Core fleet ops work **without** any integration.

| Product | Role |
|---------|------|
| [Uptime Kuma](uptime-kuma.md) | Status, TLS days, deep links, Services pages |
| [Grafana](grafana.md) | Dashboard inventory, deep links with query templates |
| [Pi-hole](pihole.md) | v6 multi-instance stats, local DNS/CNAME fan-out, gravity/actions |
| [Nginx Proxy Manager](npm.md) | Proxy hosts (read-only) + certificate pull |
| [Certificates](certificates.md) | Encrypted store, PEM upload, deploy targets, NPM renew |

Credentials and cert PEMs are Fernet-encrypted and included in [self-backup](../operations/self-backup.md).

## Design principle

- PiHerder owns **fleet truth**  
- External tools enrich via adapters and deep links  
- Prefer **n8n + token REST** over embedding every vendor API  
