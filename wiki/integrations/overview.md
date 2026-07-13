# Integrations

Optional **integration hub**. Top-nav **Catalog** opens **Integrations** by default (`/integrations`), with Settings-style buttons for **Integrations** | **Templates**. Preferred Grafana chip labels live on the integration (by dashboard UID); see [Grafana](grafana.md).

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
