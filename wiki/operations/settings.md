# Settings

**Where:** top nav **Settings** → `/herder-backups` (tabs on one page; legacy path kept for bookmarks).

Settings is **admin-oriented** for stack and policy; operators still use Account for self-service. The page uses the shared **ops-hero** (tab-aware title + pulse) plus Settings-style tabs under the hero. Switching tabs is **client-side** (URL `?tab=` updates without a full reload); the hero title, caption, and viz follow the active tab.

## Tabs (overview)

| Tab | Purpose |
|-----|---------|
| **General** | App timezone (display + date presets; storage stays UTC) and security policy (force 2FA) |
| **Fleet defaults** | Global OS / container update-check defaults (optional apply to all hosts) |
| **PiHerder backup** | Schedule, run, download, restore herder config ([Self-backup & DR](self-backup.md)) |
| **Status** | Stack health: web, DB, Redis, Celery, scheduler, disk ([Status](status.md)) — admin |
| **API** | Create / rotate / revoke instance Bearer tokens, docs, catalog ([API tokens](api-tokens.md)) — admin |

### General tab — timezone card

The hero shows a **timezone identity card** (not a city name jammed into the orb): continent badge, city, `UTC±offset`, local clock, and full IANA id (e.g. `Africa/Johannesburg`).

## Common tasks

| Goal | Path |
|------|------|
| Is Redis/Celery healthy? | Settings → **Status** → Check now |
| Nightly herder backup | Settings → **PiHerder backup** → schedule + path |
| Force everyone onto 2FA | Settings → **General** → security policy |
| Times show SAST / local | Settings → **General** → timezone |
| n8n / HA automation | Settings → **API** · [API](api-tokens.md) |

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
