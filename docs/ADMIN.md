# PiHerder admin guide

Practical reference for operators and admins: roles, users, security policy, schedules, Docker inventory, feature flags, Jobs page, production deploy, and API tokens.

Related: [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md](FEATURE_PLAN_PWA_PUSH_NOTIFICATIONS.md) · [DECISION_IOS_PUSH.md](DECISION_IOS_PUSH.md) · [DECISION_PLAN_STABILISATION.md](DECISION_PLAN_STABILISATION.md) · [SECURITY.md](../SECURITY.md)

---

## 1. Roles (RBAC)

Three roles, lowest → highest privilege:

| Role | Read fleet UI | Run backups / patch / Docker / schedules | Manage users |
|------|---------------|------------------------------------------|--------------|
| **viewer** | Yes | No (POST/PUT/PATCH/DELETE blocked except self-service) | No |
| **operator** | Yes | Yes | No |
| **admin** | Yes | Yes | Yes (`/auth/users`) |

### Viewer self-service (allowed writes)

Viewers may still:

- Log out
- Edit their account (profile, password, avatar)
- Manage their own 2FA
- Complete first-login password change and force-2FA onboarding
- Dismiss / interact with **notifications**
- Manage own **Web Push** subscription and preferences (`/api/push`, Account)

They cannot start jobs, change servers, open the Users page, or change Settings security policy.

### How enforcement works

- All logged-in roles can **GET** most pages (read-only browsing).
- Mutating methods (`POST` / `PUT` / `PATCH` / `DELETE`) are checked in auth middleware.
- User admin routes always require **admin**, including GET.
- Legacy users with a missing role are treated as **admin** (same as `normalize_role`).

### Sole admin protection

You cannot demote or delete the **last active admin**. Promote another user first.

---

## 2. User administration

**Where:** avatar menu → **Users** (admin only), or Account → “Manage users & roles”.  
**URL:** `/auth/users`

Each user card shows **last login** (app timezone) and a link to that user’s **Audit trail** (`/audit?user_id=…`). Last login updates on successful password login, trusted-device skip of 2FA, or completed 2FA challenge.

### Create a user

1. Enter email and role (viewer / operator / admin).
2. Use **Generate** (or set a strong password manually). Strength meter + policy apply.
3. On success, a one-time panel shows login URL, email, temporary password, and copyable invite text — **shown once**.
4. New users have **`must_change_password`** set: they must set their own password before using the fleet.

### Password policy

Enforced on register, account change, and admin create:

- At least **10** characters
- At least one **uppercase**, one **lowercase**, one **digit**
- Max **72** bytes (bcrypt limit)
- Special characters recommended by the strength meter, not hard-required

### Roles and delete

- Change role from the user list (sole-admin rules apply).
- Delete requires explicit confirm; you cannot delete yourself.

### Open registration

Only the **first** account can self-register. After that, only admins create users.

---

## 3. Security policy (force 2FA)

**Where:** **Settings** (`/herder-backups?tab=general`) → **Security policy**.

| Setting | Effect |
|---------|--------|
| **Force 2FA for all** | Every user without TOTP is redirected to `/auth/force-2fa` before the fleet UI. Password change-on-first-login still runs first if required. |

Stored in PostgreSQL (`appsetting` singleton) with timezone, fleet check defaults, and self-backup schedule — restored with DB dumps and PiHerder self-backup (not a separate volume JSON file).

Optional 2FA (when not forced): Account → enable TOTP, backup codes, optional trusted device.

---

## 4. Schedules

Configured per server under **Edit → Schedules** (General / Features / Schedules tabs). Cron uses **5 fields**: `minute hour day month day_of_week` (APScheduler). Check schedules use the app timezone from Settings; same for apply schedules.

**Feature flags** (Edit → Features) hard-hide dest cards and ⋯ actions on the server screen when off (Backups, OS patch, Docker/containers).

**Docker inventory:** compose/container lists are stored as a DB snapshot (`docker_inventory_*` columns) and refreshed in the background (open server/Docker, after mutations, and a fleet job every ~10 minutes for hosts with Docker enabled). The Docker page renders the last snapshot immediately; use **Force refresh** for a full re-collect.

### Update checks (safe — detect only)

| Schedule | Does | Does not |
|----------|------|----------|
| **OS packages (apt)** | Count ready packages, phased count, reboot-pending | Run upgrade |
| **Container images** | Pull/compare image IDs per compose project | `compose up -d` |

Enable checkbox + cron (default suggestion often midnight). Results feed the dashboard, badges, and notifications.

### Patch apply (opt-in — **runs real upgrades**)

Off by default. Requires the matching **feature flag** on the server (OS patch / Docker–containers under **Edit → Features**).

| Option | Behaviour |
|--------|-----------|
| Enable scheduled apply | Registers APScheduler job |
| Only when last check found updates | Skips if last check count is `0` (unknown/`null` still allows run) |
| OS: full-upgrade | Uses `full-upgrade` instead of `upgrade` (with update + autoremove) |
| Cron | e.g. weekly Sunday `30 3 * * 0` |

Also skipped when:

- Feature or apply toggle is off
- A job of the same type is already **pending/running** on that server

Scheduled apply/audit attribution shows as **system / scheduler** (no user id).

### Backups

Per-server backup enable + cron on the server/backups UI. Enqueues **Celery** workers (web never runs rsync).

### PiHerder self-backup

**Settings → PiHerder backup** tab: manual run, schedule (config-only or full), restore. Separate from per-server rsync backups.

Archives are format **v2** compressed `.tar.gz` under the herder backups volume (`./piherder_backups` → `/herder_backups`). Host dir must be writable by the container user (uid 1000).

| Included | Notes |
|----------|--------|
| **Servers** | All fields (encrypted SSH keys/passwords, schedules, inventory snapshot, feature flags) |
| **Users** | Full rows: password **hashes**, roles, profile, encrypted TOTP secret |
| **TOTP backup codes** + **trusted devices** | 2FA recovery / remember-device state |
| **Docker compose versions** | Multi-file draft/history per project |
| **Push VAPID** | Encrypted private key + public key (same `PIHERDER_MASTER_KEY` required on restore) |
| **Push subscriptions + preferences** | Devices may still need re-permission if browser endpoint died |
| **Notifications** | Recent open/dismissed alerts (capped) |
| **Operational settings** | Timezone, force 2FA, self-backup schedule, fleet check defaults (from DB `appsetting`; restored back into DB) |
| **Avatars** | Files under `DATA_ROOT/avatars` packed as `data/avatars/…` in the tar |
| **Audit log** | Only in **full** mode (optional, capped) |

| Not included | Why |
|--------------|-----|
| **Jobs** queue | Ephemeral; re-run work as needed |
| Per-server rsync backup **files** on `~/backup` | Different volume; use normal backup retention |

**Restore:** dry-run previews counts; apply upserts by id/email/endpoint. Encrypted fields only work with the **same master key**. After restore, web may need a restart so the scheduler picks up herder cron / VAPID from DB.

---

## 4b. Remove a server from the fleet

**Where:** Server detail → **Remove from PiHerder** (danger zone).

| What happens | What does **not** |
|--------------|-------------------|
| Server row + stored SSH credentials removed from PiHerder DB | No SSH / remote changes |
| Schedules unregistered; active jobs cancelled | Docker stacks, volumes, media untouched |
| Compose drafts in PiHerder deleted | Host `piherder` user / sudoers / keys left as-is |
| Jobs, audit, notifications unlinked (history kept) | Backup archives under the backup volume kept |

Confirm by typing the **exact server name**.

### Optional host cleanup (piherder user)

After (or instead of) removing the server from the UI, run the cleanup script **on the target host as root** if you want to drop the least-priv account:

- Server detail → **Remove from PiHerder** card: **Copy script** / **Download .sh**
- Or **SSH access → Host cleanup script** (same script)
- Direct download: `GET /servers/{id}/ssh/cleanup-script`
- Repo: `scripts/cleanup-piherder-user.sh`

```bash
# On the host
sudo bash cleanup-piherder-user.sh                 # sudoers + docker group; keep user
USER_NAME=piherder REMOVE_USER=1 sudo -E bash cleanup-piherder-user.sh
DRY_RUN=1 sudo -E bash cleanup-piherder-user.sh    # preview
```

Does not remove Docker projects or data. Does not remove the server from the PiHerder UI — do that separately if still listed.

---

## 5. Jobs page

**Where:** nav **Jobs** · `/jobs`  
Also: compact **Jobs** panel on each server detail page.

### What is a job?

A row in the job queue for long-running work:

| Type | Typical trigger |
|------|-----------------|
| `backup` | Manual or backup cron → Celery |
| `os_patch` / `container_patch` | Manual or apply schedule → thread pool / UI background task |
| `os_update_check` / `container_update_check` | Manual or check schedule |
| `retention` | Retention cleanup |
| `herder_backup` | PiHerder self-backup |

Statuses: `pending` → `running` → `success` / `failed`.

### Fleet Jobs UI

- Filters: server, status, type, date range, per-page
- **Active only** — pending + running
- Click a row → detail modal (summary, log tail, scheduled flag)
- Link to **Audit log** for historical action trail

### Live progress

While a job runs, server UI modals (JobHold / progress) poll job status and log lines. Container/OS patch streams progress into the job details for the holding modal.

### Jobs vs Audit vs Notifications

| System | Purpose |
|--------|---------|
| **Jobs** | Queue + progress of work units |
| **Audit** | Immutable history of actions (who/what/when, output snippet). Actor is the session user, **API token name + id** (when automation), or system/scheduler |
| **Notifications** | Dismissible inbox (updates pending, failed backup, etc.) |

---

## 6. Public hostname, trusted TLS, and PWA / Web Push

Android **installable PWA** and **Web Push** need a **secure context** with a **trusted certificate** and a stable origin. Self-signed Caddy (`tls internal` / `Caddyfile.dev`) is fine for local UI poking; it is **not** reliable for push on phones.

### Hostname and public URL

In `.env` (compose loads these for **web** and **caddy**):

```bash
PIHERDER_HOSTNAME=piherder.hacknow.com
# Include :8443 when using compose host mapping 8443→443
PIHERDER_PUBLIC_URL=https://piherder.hacknow.com:8443
```

- **DNS:** point `PIHERDER_HOSTNAME` at the host (or your outer reverse proxy).
- **Ports (default compose):** HTTP `8888→80`, HTTPS `8443→443`. Open `https://your.host:8443` unless something else terminates 443 for you.

### Volume-mounted TLS (recommended)

1. Place PEM files in the repo’s `certs/` directory (gitignored):

   | File | Role |
   |------|------|
   | `certs/fullchain.pem` | Certificate + chain |
   | `certs/privkey.pem` | Private key |

2. SANs on the cert must include `PIHERDER_HOSTNAME`.
3. Restart Caddy: `docker compose up -d caddy`
4. Browser should show a **trusted** lock for `PIHERDER_PUBLIC_URL`.

See also `certs/README.md`. For local self-signed only, mount `Caddyfile.dev` instead of `Caddyfile`.

### Web Push (VAPID)

**Default (recommended):** on web startup PiHerder **auto-generates** a VAPID key pair once and stores it in Postgres (`pushvapidconfig`). The private key is **Fernet-encrypted** with `PIHERDER_MASTER_KEY`. You do **not** need to run a generate script or set `VAPID_*` env vars for normal use.

Contact claim defaults to `VAPID_CONTACT` if set, else `mailto:admin@<PIHERDER_HOSTNAME>`, else `mailto:piherder@localhost`.

1. Ensure trusted HTTPS + hostname (above) — mobile push needs a secure origin.
2. Start/restart **web** — logs should show `Web Push VAPID ready (source=generated)` (or `source=env` if overriding).
3. **Android:** Chrome → install PWA if prompted → **Account → Push notifications → Enable on this device**.
4. **iPhone / iPad (iOS 16.4+):** Safari → Share → **Add to Home Screen** → open the Home Screen icon → Account → **Enable on this device**. Push does **not** work from a plain Safari tab. See [DECISION_IOS_PUSH.md](DECISION_IOS_PUSH.md).
5. Use **Send test notification** to verify delivery to your devices only (not the whole fleet).
6. Toggle event types (backup failed, OS updates, reboot pending, …) and save.

Push fires only when a **new** open in-app notification is created (not on every fingerprint refresh). Payloads include both classic service-worker fields and **Declarative Web Push** shape for Safari reliability.

**Do not rotate keys casually** — changing the VAPID private key invalidates every device subscription; users must re-enable push.

#### Optional env override

Set `VAPID_PUBLIC_KEY` + `VAPID_PRIVATE_KEY` (+ optional `VAPID_CONTACT`) only if you need to pin keys (e.g. keep the same pair after a DB wipe). Env always wins over the DB row when both public and private are set.

```bash
# Only if you intentionally pin keys — not required for default auto-gen
# VAPID_PUBLIC_KEY=...
# VAPID_PRIVATE_KEY=...   # PEM; use \n escapes or quoted multi-line
# VAPID_CONTACT=mailto:admin@yourdomain.com
```

In-app **Notifications** still work if VAPID generation ever fails; Account will show push as unavailable.

### Prometheus metrics (`GET /metrics`)

Scrape-time gauges (DB only, no SSH). Path is **not** behind login cookies.

| Env | Purpose |
|-----|---------|
| `METRICS_TOKEN` | If set, require `Authorization: Bearer <token>` or `X-Metrics-Token` |
| `METRICS_BACKUP_STALE_HOURS` | Hours without a successful backup before a host counts as stale (default **36**) |

Example Prometheus scrape (internal Docker network is preferred):

```yaml
scrape_configs:
  - job_name: piherder
    metrics_path: /metrics
    static_configs:
      - targets: ["web:8000"]   # compose service name
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/piherder_token  # or credentials: "..."
```

Useful series: `piherder_up`, `piherder_db_up`, `piherder_servers*`, `piherder_jobs*`, `piherder_notifications_open*`, `piherder_servers_backup_stale`.

If `METRICS_TOKEN` is empty, treat `/metrics` like `/health` — private network only.

### Multi-file Docker projects

On a server’s **Docker → Edit compose**, PiHerder loads compose, override, `.env`, and Dockerfile when present. Tabs edit each file; **Save & Deploy** writes the full set and redeploys. Version history stores multi-file snapshots (merge-on-save so one file no longer wipes the others). Compose on the host still auto-loads override + `.env` in the project directory.

### Docker inventory cache

| Behaviour | Detail |
|-----------|--------|
| Storage | Per-server DB snapshot (`docker_inventory_json`, `docker_inventory_at`, `docker_inventory_status`) |
| Open Docker page | Renders **last snapshot** immediately (no blocking full SSH list) |
| Refresh | Background L1 collect (containers + compose discovery, **without** expensive mount `du` on list path) |
| Triggers | Stale on open (server detail + Docker), after Docker mutations, fleet job ~every **10 minutes** (hosts with Docker feature on), **Force refresh** button |
| Stale UI | Banner “Inventory as of …” / “Refreshing…”; last good list kept while refresh runs |
| Feature gate | Inventory refresh only for servers with **Docker / containers** enabled (`container_patch_enabled`) |

Mount path full resolve + `du` run on **container expand** (detail row open):
`GET /servers/{id}/docker/container/mounts?name=…` → full Source→Destination paths and per-path host disk usage. Inventory list stays fast; expand restores the previous “full paths + sizes” UX.

### Server screen vs Edit

| Surface | Purpose |
|---------|---------|
| Server detail | Ops: status chips, dest cards (Backups / Docker), host ⋯ actions, Jobs |
| Edit → General | Name, SSH, docker base dir, password |
| Edit → Features | Flags; off = hard-hide related UI |
| Edit → Schedules | OS/container check + apply crons |
| Backups page | Sources, path policy, backup cron, restore |

---

## 7. Production deployment

### Environment variables

Full catalog with comments and defaults: **[`.env.example`](../.env.example)** (copy to `.env`). Compose injects those keys into **web** and **celery-worker** (Caddy only needs `PIHERDER_HOSTNAME`). Required: `PIHERDER_MASTER_KEY`, plus a strong `SECRET_KEY` in production.

### Volumes (compose defaults)

| Host path | Container | Purpose |
|-----------|-----------|---------|
| `${PIHERDER_BACKUP_HOST_PATH:-./backups}` | `/backups` | rsync destinations for server backups |
| `./piherder_backups` | `/herder_backups` | PiHerder self-backup archives (chown to uid 1000 if permission errors) |
| `./piherder_data` | `/data` | Avatars (operational Settings live in Postgres, not here) |
| `./certs` | `/certs` (Caddy, ro) | `fullchain.pem` + `privkey.pem` |

If you previously used `~/backup`, set in `.env`:

```bash
PIHERDER_BACKUP_HOST_PATH=/home/you/backup
```

### TLS & public URL

1. Set `PIHERDER_HOSTNAME` and `PIHERDER_PUBLIC_URL` (include port if not 443).  
2. Place PEMs in `certs/` (see `certs/README.md`).  
3. Prefer Caddy ports **8888/8443** or terminate TLS at Nginx Proxy Manager and reverse-proxy to `web:8000`.  
4. PWA + Web Push need **trusted** HTTPS (not `Caddyfile.dev` self-signed for phones).

### Upgrades

```bash
git pull   # or pull published image when available
docker compose up -d --build
# Schema: Alembic runs on web startup (migrations/)
docker compose run --rm --no-deps web pytest -q   # optional smoke
```

Back up `./piherder_backups` and the Postgres volume before major upgrades. Use **Settings → self-backup** for config + encrypted keys.

### Webhooks → Signal (or similar)

Env-style webhooks (legacy script parity):

```bash
WEBHOOK_URL=https://your-n8n-or-bridge/...
WEBHOOK_NUMBER=+1...
# WEBHOOK_RECIPIENTS=["+1..."]
```

Typical pattern: PiHerder → n8n webhook → Signal CLI. In-app notifications and optional Web Push remain available without webhooks.

### Prometheus / Grafana scrape

```yaml
scrape_configs:
  - job_name: piherder
    metrics_path: /metrics
    static_configs:
      - targets: ["web:8000"]
    authorization:
      type: Bearer
      credentials: "<METRICS_TOKEN>"
```

Set `METRICS_TOKEN` whenever `/metrics` is not on a fully private network. Series include `piherder_up`, `piherder_servers*`, `piherder_jobs*`, `piherder_notifications_open*`, `piherder_servers_backup_stale`.

### Image publish (when ready)

Documented target: multi-arch image on Docker Hub or GHCR (e.g. `bjorngluck/piherder:0.2.0`). Until then, build from this repo with `docker compose build`. See roadmap H0 in [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md).

**Supported deploy path:** Docker Compose (this repo). Platform reliability (host dependency checks, Settings → **Status**, multi-worker Celery) is live — see [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § Horizon 0.5. Kubernetes and bare/local install are under consideration only, not supported install paths today.

### Multi-worker Celery (backups)

Backups can run **in parallel across different hosts**. The same host never has two active backups at once (Redis mutex `piherder:server_lock:backup:{server_id}`).

| Concept | Meaning |
|---------|---------|
| **Node** | One Celery worker process/container (what `inspect().ping()` lists) |
| **Pool slots** | Prefork children inside a node (`CELERY_CONCURRENCY`) — each can run a backup |

**Default:** `1 node · 2 pool slots`. Those two slots already run independently. A second *node* is optional (HA during restarts, or more machines) — not required for two parallel backups on one host. Prefer raising `CELERY_CONCURRENCY` before scaling containers.

| Knob | Default | Notes |
|------|---------|--------|
| `CELERY_CONCURRENCY` | `2` | Pool slots in the `celery-worker` container. Raise for larger fleets (CPU/RAM + SSH budget). |
| `PIHERDER_SERVER_LOCK_TTL` | `7200` | Redis mutex TTL (seconds) if a worker dies mid-rsync. |
| Shared volumes | required | `web` and `celery-worker` must mount the same `/backups` (and usually `/data`, `/herder_backups`). |
| Cancel | unchanged | Jobs UI / API revoke via `Job.celery_task_id`; worker releases the mutex in `finally`. |
| Worker death | lock TTL + stale job cleanup | Abandoned DB rows are marked failed after the stale threshold. |

Optional multi-container scale: remove `container_name` from `celery-worker` and run `docker compose up -d --scale celery-worker=N` (same image, volumes, Redis). Status will show **N nodes** and sum of pool slots.

Full env list: [`.env.example`](../.env.example).

### Host dependency check

**Where:** Server detail (card + **Re-check**) · SSH access → **Check dependencies**. Also runs after successful SSH test, key deploy, and least-priv provision.

Probes tools needed for **enabled** features only (`rsync` / sudo path, `docker`, `apt`). Stores a snapshot on the server row. Does not install packages on the remote host — failures include short install/privilege hints.

### Stack Status

**Where:** Settings → **Status** (admin). Manual **Check now** plus a 2-minute scheduled poll. Covers web, PostgreSQL, Redis, Celery, APScheduler, and **mount free space** (fast; deduped when volumes share a disk). **Backup folder breakdown** (full `du` + top-level host sizes) is **on demand** via **View details** so large secondary disks do not slow every check. Celery shows **nodes** (containers) and **pool slots** (`CELERY_CONCURRENCY` — e.g. 1 node · 2 slots). Unhealthy components open in-app notifications (and webhook/push if configured); recovery resolves them.

---

## 8. Automation API tokens (`/api/v1`)

**Full reference:** [API.md](API.md) · interactive **OpenAPI** at `/docs` (tag **api-v1**).

**Where:** **Settings** → tab **API management** (`/herder-backups?tab=api`). Sub-panels: **Tokens** · **API reference** (in-app `docs/API.md`) · **Endpoint catalog**. **Admin only.**  
**Also:** `GET/POST /api/v1/tokens`, `DELETE /api/v1/tokens/{id}` with admin **session** (not Bearer).

| Model | Detail |
|-------|--------|
| Ownership | **Instance-wide**, admin-managed (not per-user PATs) |
| Secret | `ph_…` shown **once** at create or **rotate**; **Copy token** + **Test now** in UI; stored hashed |
| Test now | After create/rotate: verifies secret, scopes, and whether *your browser IP* passes the allowlist (admin session; no `read` scope required) |
| Capability scopes | `read` · `jobs` · `edit` — editable later without rotating |
| Feature allowlist | Optional `feature:backup` · `feature:os` · `feature:docker` (none = all features) |
| IP allowlist | Optional IPs/CIDRs per token; empty = any IP; enforced on backend using Caddy-forwarded client IP |
| Rotate | New secret, same name/scopes/IPs; old secret stops immediately |
| Revoke | Soft-disables secret; **row is kept** (name, id, scopes) for audit trail — never hard-deleted in UI |
| List filter | **Active** (default) · **Revoked** · **All** — counts on each pill |
| Last used | Updated on each successful Bearer request; shown in Settings |
| Audit trail | Link per token → `/audit?api_token_id=…` (actor shows token **name** + **id**; works after revoke) |
| Server flags | Jobs still require the server’s feature enabled (toggle via UI or `PATCH …/features`) |

| Scope | Allows |
|-------|--------|
| `read` | Catalog `GET /api/v1`, health, servers, jobs |
| `jobs` | `POST /api/v1/servers/{id}/jobs` |
| `edit` | `PATCH /api/v1/servers/{id}/features` |
| `feature:*` | Restrict which features jobs/edits may touch |

**CORS:** Off by default. Server-side n8n/HA/curl do not need it. Only set `CORS_ORIGINS` for browser apps on other origins (exact origins; never `*`). See [API.md](API.md).

**Client IP check:** Call via Caddy (8888/8443). `GET /api/v1/health` returns `client_ip` for debugging allowlists.

### Examples

```bash
# Catalog (scopes + endpoints)
curl -sS -H "Authorization: Bearer ph_…" \
  https://piherder.example.com/api/v1

# Health + resolved client IP (for allowlist debugging)
curl -sS -H "Authorization: Bearer ph_…" \
  https://piherder.example.com/api/v1/health

# List fleet
curl -sS -H "Authorization: Bearer ph_…" \
  https://piherder.example.com/api/v1/servers

# Enable backups feature then run backup
curl -sS -X PATCH -H "Authorization: Bearer ph_…" \
  -H "Content-Type: application/json" \
  -d '{"backup": true}' \
  https://piherder.example.com/api/v1/servers/1/features

curl -sS -X POST -H "Authorization: Bearer ph_…" \
  -H "Content-Type: application/json" \
  -d '{"job_type":"backup"}' \
  https://piherder.example.com/api/v1/servers/1/jobs
```

Prefer least privilege: e.g. n8n backup token = `read` + `jobs` + `feature:backup` + n8n host IP.

---

## 9. Quick admin checklist

1. Create operators/viewers from **Users**; share one-time invite.
2. Optionally enable **Force 2FA** under Settings → General.
3. Per server: **Edit → Features** → enable what you need → **Edit → Schedules** for checks → only then consider apply schedules.
4. Prefer “only if updates” on apply schedules; start with a quiet weekly window.
5. Use **Jobs** + **Audit** when diagnosing stuck or failed work; use Docker **Force refresh** if inventory looks stale after host-side changes.
6. For mobile: set hostname + mount trusted TLS certs; optionally configure VAPID for push.
7. For automation: create an API token with least scopes + IP allowlist; rotate if leaked; set `METRICS_TOKEN` if scraping Prometheus.
8. DR: Postgres volume + Settings → PiHerder backup; keep `PIHERDER_MASTER_KEY` safe for encrypted-field restore.

---

## 10. Implementation pointers (for developers)

| Concern | Location |
|---------|----------|
| Roles / middleware | `app/security/auth.py` |
| Password policy | `app/services/password_policy.py` |
| User admin routes | `app/routers/auth.py` (`/auth/users`) |
| Settings UI (tabs) | `app/routers/settings.py`, `app/templates/herder_backups.html` |
| Operational settings (DB) | `app/services/app_settings.py`, model `AppSetting` |
| Shared confirm modal | `app/templates/base.html` (`PiHerderConfirm`, `data-confirm`) |
| Scheduler registration | `app/services/scheduler.py` |
| Job create / progress | `app/services/jobs.py` |
| Fleet Jobs page | `app/routers/jobs_page.py`, `app/templates/jobs.html` |
| Web Push service / APIs | `app/services/push.py`, `app/routers/push.py` |
| Prometheus `/metrics` | `app/services/metrics.py`, `app/routers/metrics.py` |
| Token REST API | `app/routers/api_v1.py`, `app/services/api_tokens.py`, model `ApiToken` |
| CORS (opt-in) | `app/services/cors_policy.py`, env `CORS_ORIGINS` |
| Docker multi-file versions | `app/services/docker_versions.py`, compose edit UI |
| Docker inventory cache | `app/services/docker_inventory.py`, stack fragment in `server_docker.py` |
| PWA assets | `app/static/manifest.webmanifest`, `app/static/sw.js`, `/sw.js` |
| Unit tests | `tests/test_rbac.py`, `test_api_tokens.py`, `test_app_settings.py`, `test_cors_policy.py`, `test_herder_backup.py`, … |
| Herder self-backup | `app/services/herder_backup.py` |
| Ecosystem roadmap | `docs/ROADMAP_ECOSYSTEM.md` |
