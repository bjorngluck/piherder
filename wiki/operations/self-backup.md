# Self-backup & DR

## What this is

**PiHerder self-backup** archives the **control plane** (database config, encrypted secrets, users, templates, integrations, etc.) into `.tar.gz` files under the herder backup volume. It is **admin only**.

It is **not** the same as per-server rsync backups of fleet files.

## Why it exists

If the herder host dies, fleet hosts keep running — but you lose the map of keys, schedules, and bindings unless you have an archive **and** the same `PIHERDER_MASTER_KEY`. Self-backup is the DR product for the brain of the fleet.

**Where:** **Settings → PiHerder backup**

---

## End-to-end: first DR pack

1. Write down / offline-store `PIHERDER_MASTER_KEY` (from `.env`).  
2. Settings → **PiHerder backup** → run a **full** or **config_only** backup once.  
3. Download or copy the archive off the herder host.  
4. Enable a schedule (cron uses app timezone).  
5. On a lab stack, practice **dry-run restore**, then a real restore if you maintain a spare.  
6. After restore, restart web so scheduler / VAPID pick up DB state.

Journey: [Operator scenarios — Journey F](../getting-started/operator-scenarios.md#journey-f).

---

Archives: format **v2** `.tar.gz` under `./piherder_backups` → `/herder_backups`. Host dir must be writable by container user (uid 1000).

## Included

| Content | Notes |
|---------|--------|
| Servers | Encrypted SSH keys/passwords, schedules, inventory, flags |
| Users | Hashes, roles, profile, encrypted TOTP |
| TOTP backup codes + trusted devices | 2FA recovery state |
| Docker compose versions | Multi-file history |
| Push VAPID + subscriptions | Same master key on restore |
| Notifications | Recent (capped) |
| Integrations + bindings | Encrypted credentials |
| Operational settings | Timezone, force 2FA, schedules (`appsetting`) |
| Avatars | Packed under `data/avatars/…` |
| Service logos | Packed under `data/service_logos/…` (integration icons) |
| Templates + stack deployments | Ciphertext secrets |
| Audit log | **Full** mode only (optional, capped) |

## Not included

| Content | Why |
|---------|-----|
| Jobs queue | Ephemeral |
| Per-server rsync **files** | Different volume — see [Backups](../day-to-day/backups.md) |
| External Kuma/Grafana instances | Only PiHerder-side config |

## Restore

1. Dry-run previews counts.  
2. Apply upserts by id/email/endpoint.  
3. Encrypted fields need the **same** `PIHERDER_MASTER_KEY`.  
4. Restart web so scheduler / VAPID pick up DB state.

## Schedule

Enable cron + mode (`config_only` | `full`) + keep count in Settings. Manual run available.

Cron wall-clock uses the **app timezone** from **Settings → General** (same setting as Audit/Jobs display).

## Timezone

Archive list **mtime** and other Settings timestamps are shown in the app timezone. Changing timezone does not rewrite stored UTC values.
