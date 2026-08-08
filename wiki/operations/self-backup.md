# Self-backup & DR

## What this is

**PiHerder self-backup** archives the **control plane** — the “brain” of the fleet: who can log in, which hosts you manage, encrypted SSH keys, schedules, integrations, cert vault, maps topology, pins, and more — into `.tar.gz` files under the herder backup volume.

It is **admin only**. **Where:** **Settings → PiHerder backup**.

It is **not**:

| Not this | That is… |
|----------|----------|
| A full PostgreSQL `pg_dump` of every table | Selective **JSON row snapshot** + a few files (portable, master-key aware) |
| Per-server **rsync** of docker/media trees | [Server backups](../day-to-day/backups.md) on the `/backups` volume |
| A VM/disk image of the herder host | You still install compose + image on the new machine |

!!! tip "One sentence"
    **Archive + same `PIHERDER_MASTER_KEY`** → rebuild a **fully functional control plane**.  
    **Not** by itself → every job log, nmap XML, edge TLS file, or rsynced host tree.

Journey: [Operator scenarios — Journey F](../getting-started/operator-scenarios.md#journey-f).

---

## Why it exists

If the **herder** host dies, fleet Pis and NAS boxes keep running — but you lose the map of keys, users, schedules, and bindings unless you have:

1. At least one self-backup archive **off** that host, and  
2. The **same** `PIHERDER_MASTER_KEY` that encrypted secrets when the archive was made.

Wrong or lost master key → restore may load rows, but SSH keys, cert PEMs, and integration credentials will not decrypt into usable secrets.

---

## Honest DR: what “fully functional” means

### Scenario A — Minimum pack (archive + master key)

**You keep:** latest self-backup `.tar.gz` + offline `PIHERDER_MASTER_KEY`.

**You do on a new machine:**

1. Fresh [Install](../getting-started/install.md) (compose, Postgres, Redis, Celery).  
2. Put the **same** `PIHERDER_MASTER_KEY` in `.env` (and a new or old `SECRET_KEY` — see below).  
3. Settings → PiHerder backup → **restore** (prefer **dry-run** first).  
4. Restart web so scheduler / VAPID pick up DB state.  
5. Smoke: login, open a host, run one SSH-backed action (e.g. test connection or backup).

**Result:** Day-to-day herder ops work again — fleet identity, encrypted keys, users/2FA/pins, integrations, cert vault, maps, discovery **inventory**, API token hashes, push config.

**You still need for a usable UI on the new host:**

| Piece | Why |
|-------|-----|
| Compose / image / empty Postgres | Archive is not a full VM |
| Network path from new herder → fleet SSH | Keys restore; routes must exist |
| Hostname / public URL (optional TLS) | `PIHERDER_HOSTNAME`, `PIHERDER_PUBLIC_URL`, edge `./certs` or new certificates for **this** site |
| Optional env (webhooks, metrics, nmap profile) | If you relied on env rather than Settings UI |

`SECRET_KEY` (session/JWT signing): a **new** value is fine — everyone signs in again. Keeping the old value only preserves existing browser cookies.

### Scenario B — Control plane + fleet file archives

**Also keep:** the host volume used for **server rsync** (`PIHERDER_BACKUP_HOST_PATH` / `/backups`) and know [Volumes](volumes.md).

**Result:** Scenario A **plus** reverse-rsync / restore of stack files you previously backed up from managed hosts. Self-backup never replaces that volume.

### Scenario C — Comfort pack (recommended for production)

| Offline / separate storage | Purpose |
|----------------------------|---------|
| `PIHERDER_MASTER_KEY` | Decrypt restored secrets |
| Latest self-backup `.tar.gz` | Control plane |
| Copy of `.env` (or at least public URL + non-secret toggles) | Faster rebuild; **never** commit secrets to git |
| Edge TLS PEMs if self-managed (`./certs`) | HTTPS for the herder UI without re-issue |
| Host rsync backup disk | Fleet file DR |
| Optional: Postgres volume snapshot | Extra belt-and-braces; still keep self-backup |

### Scenario D — What recovery is **not**

| Expectation | Reality |
|-------------|---------|
| “Only the tar.gz, no master key” | Encrypted fleet secrets unusable |
| “Restore replaces my lost docker recordings on the Pi” | Those were never in self-backup; use server backups or the Pi’s own disks |
| “Job history and every nmap XML come back” | Jobs and scan **runs** are excluded; inventory/schedules restore (format **v4**) |
| “External Kuma/Grafana data restores” | Only PiHerder-side connection config restores |

---

## What is in the archive

Format **v4** (v1–v3 still restore). Path: `./piherder_backups` → `/herder_backups` (container uid **1000** must write).

Mechanism: **JSON row snapshot** of durable tables + selected files under `DATA_ROOT` — not raw `pg_dump`.

### Included (control plane)

| Content | Notes |
|---------|--------|
| Servers | Encrypted SSH keys/passwords, schedules, inventory cache, feature flags |
| Users | Password hashes, roles, profile, encrypted TOTP secret |
| User favourites (pins) | ★ menu — per **user**, not per device (v4) |
| API tokens | Hash + scopes/prefix only; plaintext never stored (v4) |
| TOTP backup codes + trusted devices | 2FA recovery state |
| Docker compose versions | Multi-file history in DB |
| Push VAPID + subscriptions + preferences | Same master key on restore |
| Notifications | Recent (capped) |
| Integrations + bindings | Encrypted credentials |
| Managed certs + deploy targets | Encrypted PEMs as stored in vault |
| DNS fabric / runtime edges / topology | Maps + stack ownership |
| Port annotations | Sticky port roles on maps |
| LAN discovery | Schedules, devices, script results (v4) — not scan **run** XML |
| Templates + stack deployments | Ciphertext secrets |
| Operational settings | Timezone, force 2FA, herder backup schedule, etc. (`herder_config`) |
| Avatars | `data/avatars/…` in the tar |
| Service logos | `data/service_logos/…` |
| Audit log | **Full** mode only (optional, capped) |

### Not included (by design)

| Content | Why | After restore |
|---------|-----|----------------|
| Jobs queue / job history | Ephemeral | New jobs run normally |
| Password-reset tokens | Short-lived | Use forgot-password again if SMTP configured |
| Nmap **scan run** rows + XML under `DATA_ROOT/nmap` | Large / ephemeral | Inventory remains; re-scan for new run history |
| Per-server rsync file trees under `/backups` | Separate product | [Backups](../day-to-day/backups.md) · [Volumes](volumes.md) |
| External product databases (Kuma, Grafana, Frigate media, …) | Outside herder | Only connection config in PiHerder |
| Vuln pack volume (`piherder_nmap_vuln`) | Separate mount | Re-download / remount if you use LAN vuln scripts |
| Edge site PEMs on `./certs` | Host TLS for UI | Re-copy or re-issue; vault PEMs for **fleet** services are in the archive |
| Redis / in-flight Celery work | Ephemeral | Nothing to restore |

---

## End-to-end: first DR pack

1. Write down / offline-store **`PIHERDER_MASTER_KEY`** (from `.env`).  
2. Settings → **PiHerder backup** → run **config_only** (default) or **full** (adds audit).  
3. Download or copy the archive **off** the herder host (and preferably keep Scenario C items).  
4. Enable a schedule (cron uses **app timezone**).  
5. On a **lab** stack with the **same** master key: **dry-run restore**, then optional real restore.  
6. Restart web; smoke login + one fleet action.

---

## Restore

1. Target instance must use the **same** `PIHERDER_MASTER_KEY` as the archive.  
2. **Dry-run** previews counts (users, servers, integrations, …) without writing.  
3. Apply upserts by id/email/endpoint as implemented by restore.  
4. Restart web so scheduler / VAPID / caches pick up DB state.  
5. Confirm network reachability to managed hosts.

!!! warning "Master key"
    Restoring onto a stack with a **different** master key is a common DR failure mode: rows appear, secrets do not work. Treat the key like the archive itself.

---

## Modes & schedule

| Mode | Use |
|------|-----|
| **config_only** (default) | Control plane without audit bulk — preferred routine DR |
| **full** | Adds capped **audit** history |

Enable cron + keep count in Settings. Manual **Run now** always available.

Cron wall-clock uses the **app timezone** from **Settings → General** (same as Audit/Jobs display).

Archive list **mtime** and Settings timestamps are shown in the app timezone; stored values remain UTC.

---

## Related

| Topic | Page |
|-------|------|
| Host path mounts | [Volumes](volumes.md) |
| Per-host file backups | [Backups](../day-to-day/backups.md) |
| Env secrets | [Env reference](env-reference.md) |
| Upgrades (backup first) | [Upgrades](upgrades.md) |
| Who may restore | [Roles](../account-security/roles.md) (admin) |
| Operator journey | [Journey F](../getting-started/operator-scenarios.md#journey-f) |
