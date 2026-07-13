# Self-backup & DR

**Settings → PiHerder backup** — not the same as per-server rsync backups.

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
| Templates + stack deployments | Ciphertext secrets |
| Audit log | **Full** mode only (optional, capped) |

## Not included

| Content | Why |
|---------|-----|
| Jobs queue | Ephemeral |
| Per-server rsync **files** | Different volume |
| Service logo files | Re-fetch/upload after DR (v0.5 stretch B08) |
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
