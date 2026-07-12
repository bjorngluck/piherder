# Metrics & webhooks

## Prometheus (`GET /metrics`)

Scrape-time gauges (DB only, no SSH). Not behind login cookies.

| Env | Purpose |
|-----|---------|
| `METRICS_TOKEN` | If set, require `Authorization: Bearer` or `X-Metrics-Token` |
| `METRICS_BACKUP_STALE_HOURS` | Hours without successful backup → stale (default **36**) |

```yaml
scrape_configs:
  - job_name: piherder
    metrics_path: /metrics
    static_configs:
      - targets: ["web:8000"]   # compose service name
    authorization:
      type: Bearer
      credentials: "<METRICS_TOKEN>"
```

Useful series: `piherder_up`, `piherder_db_up`, `piherder_servers*`, `piherder_jobs*`, `piherder_notifications_open*`, `piherder_servers_backup_stale`.

If `METRICS_TOKEN` is empty, treat `/metrics` like private-network only.

## Webhooks → Signal (or similar)

```bash
WEBHOOK_URL=https://your-n8n-or-bridge/...
WEBHOOK_NUMBER=+1...
# WEBHOOK_RECIPIENTS=["+1..."]
```

Typical pattern: PiHerder → n8n webhook → Signal CLI. In-app notifications and Web Push work without webhooks.
