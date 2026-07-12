# Jobs, audit & notifications

Three related systems — do not confuse them.

| System | Purpose |
|--------|---------|
| **Jobs** | Queue + live progress of work units |
| **Audit** | Immutable history (who / what / when / snippet) |
| **Notifications** | Dismissible inbox (updates pending, failed backup, …) |

<figure class="ph-figure" markdown>
  ![Jobs page](../assets/screenshots/jobs-page.svg)
  <figcaption>Fleet Jobs with filters and detail modal. <span class="ph-wireframe-badge">wireframe</span></figcaption>
</figure>

## Jobs

**Where:** nav **Jobs** (`/jobs`) · compact panel on each server detail.

### Job types (examples)

| Type | Typical trigger |
|------|-----------------|
| `backup` | Manual or backup cron → Celery |
| `os_patch` / `container_patch` | Manual or apply schedule |
| `os_update_check` / `container_update_check` | Manual or check schedule |
| `retention` | Retention cleanup |
| `herder_backup` | PiHerder self-backup |

Statuses: `pending` → `running` → `success` / `failed`.

### Fleet Jobs UI

- Filters: server, status, type, date range, per-page  
- **Active only** — pending + running  
- Row → detail modal (summary, log tail, scheduled flag)  
- **Cancel** works from list and modal (where applicable)  
- Link to **Audit** for historical trail  

### Live progress

JobHold / progress modals poll status and log lines for OS/container patch and similar work.

## Audit

Actors may be:

- Session user (display name + email)  
- **API token name + id** (automation)  
- **system / scheduler** for cron jobs  

Filter by user, server, token, etc.

## Notifications

- Bell icon → open / dismiss  
- Deep links into the relevant server or page  
- Optional **Web Push** for new open notifications — [PWA & Web Push](../account-security/pwa-push.md)  
- Dismiss is **idempotent** if already closed  

## API

Automation can list/trigger jobs with Bearer tokens — [API tokens](../operations/api-tokens.md).
