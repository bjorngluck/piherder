# Metrics & webhooks

## What this is

Optional **Prometheus metrics** scrape endpoint and outbound **webhooks** so external observability stacks can watch PiHerder health and events without scraping the full UI.

## Why it exists

Homelabs already run Prometheus/Grafana or notification bridges. Exposing gauges and hooks avoids reinventing alerting inside every consumer.

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

Useful series: `piherder_up`, `piherder_db_up`, `piherder_info`, `piherder_redis_up`, `piherder_celery_up`, `piherder_celery_workers`, `piherder_celery_pool_slots`, `piherder_servers`, `piherder_servers_attention`, `piherder_servers_reboot_pending`, `piherder_os_update_hosts`, `piherder_container_update_hosts`, `piherder_os_packages_pending`, `piherder_container_projects_pending`, `piherder_servers_never_checked_os`, `piherder_servers_never_checked_containers`, `piherder_jobs`, `piherder_jobs_active`, `piherder_jobs_failed_24h`, `piherder_notifications_open`, `piherder_notifications_open_by_type`, `piherder_servers_backup_enabled`, `piherder_servers_backup_stale`, `piherder_schedule_missed`, `piherder_queue_oldest_pending_seconds`, `piherder_stuck_queue`.

`piherder_schedule_missed{kind="backup",server_id="1"}` is 1 when that host's latest due backup slot did not start within 15 minutes and has not succeeded since. `kind="container_check"` is the same gauge for a container image check. Schedules that are off are omitted.

`piherder_queue_oldest_pending_seconds` is the age of the oldest `pending` job, or 0 when none are pending. `piherder_stuck_queue` is 1 when that age is over 15 minutes and no Celery worker answered. A failed worker check leaves the gauge at 0. Both are on the v1.13 train and are not in the 1.12.1 image.

If `METRICS_TOKEN` is empty, treat `/metrics` like private-network only.

## Webhooks → Signal (or similar)

**Preferred (v1.1+):** configure under **Settings → Alerts** — URL, secret, event filters, test ping. See [Alerts (email & webhooks)](alerts-email-webhooks.md).

**Env fallback** (when Settings URL empty):

```bash
WEBHOOK_URL=https://your-n8n-or-bridge/...
WEBHOOK_NUMBER=+1...
# WEBHOOK_RECIPIENTS=["+1..."]
```

Typical pattern: PiHerder → n8n webhook → Signal CLI. In-app notifications and Web Push work without webhooks.
