# Settings

**Where:** top nav **Settings** → `/herder-backups` (tabs on one page).

Settings is **admin-oriented** for stack and policy; operators still use Account for self-service.

## Tabs (overview)

| Tab | Purpose |
|-----|---------|
| **Self-backup** | Schedule, run, download, restore herder config ([Self-backup & DR](self-backup.md)) |
| **Security policy** | Force 2FA; require 2FA for template deploy & secrets |
| **Status** | Stack health: web, DB, Redis, Celery, scheduler, disk ([Status](status.md)) |
| **Timezone** | Display timezone for Audit / Jobs / Notifications / fleet times (storage stays UTC) |
| **Update checks** | Global defaults related to scheduled check behaviour (host schedules still on each server) |
| **API tokens** | Create / rotate / revoke instance Bearer tokens ([API tokens](api-tokens.md)) — admin only |

Exact tab labels may vary slightly by version; the URL is always **Settings** in the main nav.

## Common tasks

| Goal | Path |
|------|------|
| Is Redis/Celery healthy? | Settings → **Status** → Check now |
| Nightly herder backup | Settings → Self-backup → schedule + path |
| Force everyone onto 2FA | Settings → Security policy |
| Times show SAST / local | Settings → Timezone |
| n8n / HA automation | Settings → API tokens · [API](api-tokens.md) |

## Not under Settings

| Feature | Where |
|---------|--------|
| Catalog (integrations, certs, templates, network) | Nav **Catalog** |
| Users | Avatar → **Users** (admin) |
| Account / 2FA / push | Avatar → **Account** |
| Fleet services grid | Dashboard tile or `/services` |

## Related

- [Environment reference](env-reference.md) — secrets that stay in `.env`  
- [Volumes](volumes.md)  
- [Upgrades](upgrades.md)  
