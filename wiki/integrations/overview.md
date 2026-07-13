# Integrations

Optional **integration hub**. Top-nav **Catalog** opens a shared Catalog page with **Templates** and **Integrations** tabs (`/templates` · `/integrations`). Click **Integrations** on that strip if you land on service templates first.

Core fleet ops work **without** any integration.

| Product | Role |
|---------|------|
| [Uptime Kuma](uptime-kuma.md) | Status, TLS days, deep links, Services pages |
| [Grafana](grafana.md) | Dashboard inventory, deep links with query templates |

Credentials are Fernet-encrypted and included in [self-backup](../operations/self-backup.md).

## Design principle

- PiHerder owns **fleet truth**  
- External tools enrich via adapters and deep links  
- Prefer **n8n + token REST** over embedding every vendor API  
