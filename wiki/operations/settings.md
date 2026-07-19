# Settings

## What this is

**Settings** is the admin control plane for the **instance**: timezone, security policy, fleet update-check defaults, **stale data cleanup**, PiHerder self-backup, stack Status, and API tokens.

**Where:** top nav **Settings** → `/herder-backups` (tabs on one page; legacy path kept for bookmarks).

## Why it exists

Day-to-day fleet work lives on Servers / Jobs / Catalog. Settings keeps **policy and DR** in one place so operators are not hunting for “where do I force 2FA?” or “where is the herder backup?”

Settings is **admin-oriented** for stack and policy; operators still use Account for self-service. **Timezone, security policy, fleet defaults, stale data cleanup, PiHerder self-backup/restore, Status, and API tokens** require **admin** (UI tabs and POST routes). Non-admins see a short notice on General only.

The page uses the shared **ops-hero** (tab-aware title + pulse) plus Settings-style tabs under the hero. Switching tabs is **client-side** (URL `?tab=` updates without a full reload); the hero title, caption, and viz follow the active tab.

---

## End-to-end: harden a new instance

1. **General** → set app **timezone** (Audit/Jobs clocks).  
2. **General** → enable **force 2FA** if everyone should enrol.  
3. **PiHerder backup** → run once + schedule; store archive + master key offline.  
4. **Status** → Check now until green.  
5. Optional **API** tokens for n8n/HA only if needed.  

---

## Tabs (overview)

| Tab | Purpose |
|-----|---------|
| **General** | App timezone, security policy (force 2FA), and **Stale data cleanup** |
| **Fleet defaults** | Global OS / container update-check defaults (optional apply to all hosts) |
| **PiHerder backup** | Schedule, run, download, restore herder config ([Self-backup & DR](self-backup.md)) |
| **Status** | Stack health: web, DB, Redis, Celery, scheduler, disk ([Status](status.md)) — admin |
| **API** | Create / rotate / revoke instance Bearer tokens, docs, catalog ([API tokens](api-tokens.md)) — admin |

### General tab — timezone card

The hero shows a **timezone identity card** (not a city name jammed into the orb): continent badge, city, `UTC±offset`, local clock, and full IANA id (e.g. `Africa/Johannesburg`).

### Stale data cleanup {#stale-data-cleanup}

**Opt-in** purge of old **Jobs**, **Audit**, and optionally **nmap scan runs** (plus run XML under `DATA_ROOT/nmap/…`). Distinct from per-server **backup file** retention.

| Setting | Default lean |
|---------|----------------|
| Master enable + cron | **Off** · cron e.g. `30 4 * * *` (app timezone) |
| Jobs purge | On when cleanup enabled · **30 days** · never deletes pending/running |
| Audit purge | On when cleanup enabled · **30 days** (can differ from jobs) |
| nmap runs / artifacts | **Off** until enabled · **30 days** when on |

**Run now** enqueues Job type `stale_data_cleanup` (preview counts in the card). Admin-only. Removing a **server** still **keeps** unlinked Jobs/Audit by default — time purge is the bulk growth control ([Remove a server](../day-to-day/remove-server.md)).

## Common tasks

| Goal | Path |
|------|------|
| Is Redis/Celery healthy? | Settings → **Status** → Check now |
| Nightly herder backup | Settings → **PiHerder backup** → schedule + path |
| Force everyone onto 2FA | Settings → **General** → security policy |
| Trim old Jobs / Audit | Settings → **General** → Stale data cleanup |
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
