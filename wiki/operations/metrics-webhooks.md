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

Useful series, and the full set exported by `app/services/metrics.py`:

| Series | What it counts |
|--------|----------------|
| `piherder_up` | Process is up (always 1) |
| `piherder_db_up` | Database connectivity (1 = ok). Later series are omitted when this is 0 |
| `piherder_info` | Build info. Label `version` |
| `piherder_redis_up` | Redis from the last stack health check (1 = ok). Not a live probe on every scrape |
| `piherder_celery_up` | Celery from that same check (1 = ok) |
| `piherder_celery_workers` | Celery worker nodes from that check |
| `piherder_celery_pool_slots` | Prefork pool slots from that check |
| `piherder_servers` | Managed servers |
| `piherder_servers_attention` | Servers with a reboot or pending updates |
| `piherder_servers_reboot_pending` | Servers with reboot pending |
| `piherder_os_update_hosts` | Servers with installable OS packages |
| `piherder_container_update_hosts` | Servers with container project updates |
| `piherder_os_packages_pending` | Installable OS packages across the fleet |
| `piherder_container_projects_pending` | Container projects with updates |
| `piherder_servers_never_checked_os` | OS-patch servers never checked |
| `piherder_servers_never_checked_containers` | Container-patch servers never checked |
| `piherder_jobs` | Job rows. Label `status` |
| `piherder_jobs_active` | Pending plus running jobs |
| `piherder_jobs_failed_24h` | Jobs that failed in the last 24 hours |
| `piherder_notifications_open` | Open in-app notifications |
| `piherder_notifications_open_by_type` | Open notifications. Label `type` |
| `piherder_servers_backup_enabled` | Servers with backup enabled |
| `piherder_servers_backup_stale` | Backup-enabled servers with no success inside the stale window |
| `piherder_schedule_missed` | 1 while a due slot was missed. Labels `kind` (`backup`, `container_check`, `docker_prune`) and `server_id` |
| `piherder_queue_oldest_pending_seconds` | Age in seconds of the oldest `pending` job, or 0 when none are pending |
| `piherder_stuck_queue` | 1 when that age is over 15 minutes and no Celery worker answered. An unknown heartbeat leaves this at 0 |

`piherder_schedule_missed{kind="backup",server_id="1"}` is 1 when that host's latest due backup slot did not start within 15 minutes and has not succeeded since. `kind="container_check"` is the same gauge for a container image check. `kind="docker_prune"` is the same gauge for a saved per-host Docker prune. Schedules that are off are omitted. The prune kind ships in **1.13.0**.

`piherder_queue_oldest_pending_seconds` is the age of the oldest `pending` job, or 0 when none are pending. `piherder_stuck_queue` is 1 when that age is over 15 minutes and no Celery worker answered. A failed worker check leaves the gauge at 0. Both ship in **1.13.0**.

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
