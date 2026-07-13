# PiHerder admin guide

Practical reference for operators and admins: roles, users, security policy, schedules, Docker inventory, feature flags, Jobs page, production deploy, and API tokens.

> **Prefer the user wiki** for day-to-day reading: repo [`wiki/`](../wiki/) built with MkDocs (`pip install -r requirements-docs.txt && mkdocs serve`). This file remains the long-form single-document reference and source material for the wiki.

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

Manual UI/API triggers share the same exclusivity: a second `os_patch` / `container_patch` / update-check on a host that already has that type **pending/running** reuses the existing job (HTTP **409** + `already_active` on async/API paths). Celery multi-slot concurrency does **not** re-run these jobs — they execute on the web process. See [wiki multi-worker](../wiki/operations/multi-worker.md).

Scheduled apply/audit attribution shows as **system / scheduler** (no user id).

### Bulk actions (Servers list)

**Where:** `/servers` — checkboxes + **Select all visible**.

| Action | Feature flag required on host |
|--------|--------------------------------|
| Check OS / Upgrade OS | OS patch |
| Check containers / Patch containers | Docker / containers |
| Backup | Backups |

`POST /servers/bulk` with `action` + comma-separated `server_ids` (session auth). Ineligible hosts are skipped. Exclusive-job rules still apply per host.

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
| **Integrations + bindings** | Kuma / Grafana connectors, encrypted credentials, query templates (`config_json`), all bindings/mappings |
| **Operational settings** | Timezone, force 2FA, self-backup schedule, fleet check defaults (from DB `appsetting`; restored back into DB) |
| **Avatars** | Files under `DATA_ROOT/avatars` packed as `data/avatars/…` in the tar |
| **Audit log** | Only in **full** mode (optional, capped) |

| Not included | Why |
|--------------|-----|
| **Jobs** queue | Ephemeral; re-run work as needed |
| Per-server rsync backup **files** on `~/backup` | Different volume; use normal backup retention |
| **Service logo files** under `DATA_ROOT/service_logos/` | Paths restored on bindings; re-fetch favicon or re-upload after DR |
| **External products** (Kuma / Grafana instances) | Only PiHerder-side config is backed up |

**Restore:** dry-run previews counts; apply upserts by id/email/endpoint. Encrypted fields only work with the **same master key**. After restore, web may need a restart so the scheduler picks up herder cron / VAPID from DB.

---

## 4b. Remove a server from the fleet

**Where:** Server detail → **Edit** → **Remove** tab → **Remove server…**

| What happens | What does **not** |
|--------------|-------------------|
| Server row + stored SSH credentials removed from PiHerder DB | No SSH / remote changes |
| Schedules unregistered; active jobs cancelled | Docker stacks, volumes, media untouched |
| Compose drafts in PiHerder deleted | Host `piherder` user / sudoers / keys left as-is |
| Jobs, audit, notifications unlinked (history kept) | Backup archives under the backup volume kept |

Confirm by typing the **exact server name**.

### Optional host cleanup (piherder user)

After (or instead of) removing the server from the UI, run the cleanup script **on the target host as root** if you want to drop the least-priv account:

- Edit → **Remove** tab: **Copy script** / **Download .sh**
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

While a job runs, server UI modals (JobHold / progress) poll job status and log lines. Container/OS patch streams progress into the job details for the holding modal. If the job was already active, JobHold attaches to the existing `job_id` (409 path).

### Exclusive job types (per server)

| Types | Rule |
|-------|------|
| `os_patch`, `container_patch`, `os_update_check`, `container_update_check` | At most one **pending/running** of that type per server |
| `backup` | Per-host Redis mutex + Celery (separate path) |

### Jobs vs Audit vs Notifications

| System | Purpose |
|--------|---------|
| **Jobs** | Queue + progress of work units |
| **Audit** | Immutable history of actions (who/what/when, output snippet). Actor is the session user, **API token name + id** (when automation), or system/scheduler |
| **Notifications** | Dismissible inbox (updates pending, failed backup, etc.) |

### Backup audit completion

Backup jobs write lifecycle events (`backup_request` → `backup_queued` → `backup_running` → terminal `backup`). The **completed** row includes a compact snippet (per-source sizes, totals) so the Audit feed can show e.g. `2 sources · 1.5 MB` and duration. Use **Hide incomplete runs** to hide in-progress noise.

### App timezone (display)

**Settings → General → timezone** (IANA name, e.g. `Africa/Johannesburg`) controls how UTC-stored timestamps render in the UI:

| Surface | Behaviour |
|---------|-----------|
| Audit | Event times + duration; header shows active zone |
| Jobs | Finished/started/queued times in app zone |
| Notifications | “Updated …” timestamps |
| Server detail / list | Last backup, last OS/container check, job times |
| Users | Last login |
| Schedules / self-backup cron | Fire times interpreted in app zone |

Storage remains **UTC** (DB `datetime.utcnow()`). Changing the timezone only changes display (and schedule wall-clock), not historical raw values.

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

### Service templates (v0.4.0)

**Templates** live under top-nav **Catalog** (Settings-style buttons: **Integrations** default | **Templates**). They are **your** versioned stack definitions. You **create**, **edit**, and **save** them; deploy is separate.

**Shipped in v0.4.0** (foundation; ops + polish → [PLAN_v0.5.0.md](PLAN_v0.5.0.md)).  
**Docs:** [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [PLAN_v0.4.0.md](PLAN_v0.4.0.md) · active [PLAN_v0.5.0.md](PLAN_v0.5.0.md)

#### Create / edit (operator-owned)

1. **Templates → + New template** or **From host…** (pull live compose/.env) or **Edit**  
2. Metadata: slug, name, category, version  
3. Paste or pull **docker-compose.yml**; use `{{VAR}}` in files / `${KEY}` for Compose env  
4. **Variables** as form rows (Add / Remove). Types: `string`, `port`, `password`, `int`, `url`, `email`, **`boolean`**, **`volume`**  
5. Tools on the editor:  
   - **Scan vars + volumes** — detect placeholders / env keys; parameterize hard-coded short mounts and host ports  
   - **Move secrets → .env** — rewrite password-like inline env to `${KEY}` + `.env` placeholders  
6. Checklist rows for post-deploy DNS / first-login notes  
7. **Save** — DB `user` source; operator edits are never overwritten by disk starters  

#### Variable types (non-secret config)

| Type | Deploy UI | Notes |
|------|-----------|--------|
| **boolean** | Yes / No | Writes `true_value` / `false_value` (defaults `true`/`false`) into files |
| **volume** | Storage type + name/path | Modes: **named** Docker volume, **folder in project** (`./…`), **host path** (`/…`). Compose uses `- {{VAR}}` → full short mount `source:target`. Requires `volume_target` (container path) |
| **port** / string / … | Normal fields | Ports validated 1–65535 |

Volume and boolean vars are **never** treated as secrets (no step-up 2FA).

#### Secrets model (home production — locked decision)

| Layer | Behaviour |
|-------|-----------|
| **PiHerder** | Source of truth; secrets Fernet-encrypted; edit/audit/redeploy here |
| **UI reveal** | Cleartext only after **2FA enabled** → **View secrets** → enter TOTP (**step-up**, even if you already used 2FA at login). Unlock cookie ~10 minutes; **Hide secrets** clears it |
| **Host project** | Locked-down **`.env`** (`chmod 600`) for Compose `${VAR}`; offline restarts work **without** PiHerder |
| **Docker page** | Template-managed stacks show a **Template** badge; full compose editor is gated (use deployment page for template-owned desired state) |
| **Not default** | Compose `./secrets/` files, Swarm secrets, vault inject — **roadmap** (advanced) |

#### From host

1. Templates → **From host…** → Docker-enabled server + project  
2. Optional: move secret-like values to `.env`  
3. Pull parameterizes **volumes**, **host ports**, **booleans**, and env/secrets into deploy variables; rewrites compose short mounts/ports to `{{VAR}}`  
4. Review in editor → **Save**  
5. Progress overlay while SSH pull runs  

#### Deploy flow

1. **Details** or **Deploy…** → fill variables (incl. volume mode) → pick Docker-enabled host  
2. **Preview** → **Confirm deploy**  
3. Blocking **wait modal** while PiHerder writes files over SSH, locks `.env`, and runs `compose pull` + `up -d` (page updates when finished)  
4. Desired state **Vn** stored encrypted in PiHerder  
5. **Redeploy** from the deployment page (same wait modal)  

#### Import zip

Archive with `template.yaml` + `files/`. Still fully editable in the UI after import.

#### Security settings

**Settings → Security policy:**

| Option | Effect |
|--------|--------|
| Require 2FA for all users | Existing force-2FA for the whole UI |
| **Require 2FA for template deploy & secrets** | Operator must have TOTP enabled to confirm deploy or view/edit secrets |

On-host `.env` is cleartext but mode `600` (owner-only). Treat host disk encryption and SSH access as part of home-lab security. Advanced secret stores are future roadmap.

#### Builtin pack refresh

Disk starters under `service_templates/` seed the DB when a slug is **missing**. Rows still marked `source=builtin` are **refreshed** from disk when the checksum changes. After you **Edit + Save**, source becomes `user` and is never auto-overwritten.

#### Self-backup

Herder self-backup includes `service_templates` catalog rows and `stack_deployments` (encrypted secrets travel as ciphertext — same `PIHERDER_MASTER_KEY` on restore).

---

### Uptime Kuma integration

Optional **integration hub** under top-nav **Catalog** (opens **Integrations** by default; **Templates** is the second button). You can **deploy** Kuma via Templates, then connect the integration for status/bindings.

**Design / plan:** [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md)

#### Connect Kuma

1. In Kuma: **Settings → API Keys** — enable API keys and create a key (copy once).
2. From a host that can reach Kuma (same path as PiHerder **web** and workers):

   ```bash
   curl -sS -u ":$KUMA_API_KEY" "https://uptime.example.com/metrics" | head
   ```

3. PiHerder → **Catalog → Integrations → + Uptime Kuma** — base URL + API key → Save.  
4. **Optional (recommended for deep links on Kuma 1.23):** add Kuma username/password on Edit. Metrics labels often omit numeric monitor ids; login syncs name → `/dashboard/{id}`. You can also type **Dashboard ID** per binding.  
5. Poll interval default **60s** (Settings on the integration); **Test** / **Poll now** available.

Credentials (API key + optional login) are Fernet-encrypted with `PIHERDER_MASTER_KEY` and included in **PiHerder self-backup**.

#### Binding scopes

| Scope | Role | Where you see it |
|-------|------|------------------|
| **SSH** | `ssh_reachability` | Server list chip, server detail, server Services (summary) |
| **Host service** | `service` without Docker project | Server detail “Host services”, **Services** page — e.g. Home Assistant on HAOS |
| **Docker** | `service` + compose project [/ container] | Docker stack chips + **Services** page |

- **Suggest matches** maps unbound servers to TCP/SSH monitors by hostname/IP/port.  
- HTTP monitors expose **TLS valid** + **days remaining** from Kuma Prometheus series.  
- Down transitions open in-app notifications (and optional Web Push: Account → **Integration monitor down**).

#### Services UI

| Path | Purpose |
|------|---------|
| `/integrations` | Connect Kuma, bind SSH + services, inventory |
| `/servers/{id}/services` | Per-host service list: URL, status, TLS, Open service / Open in Kuma, logos |
| `/services` | Fleet icon grid (dashboard **Services** tile) |
| Dashboard | Services count (+ down count) → `/services` |

#### Service logos

- **Auto:** favicon / apple-touch-icon fetch from the monitor’s HTTP URL (on bind and poll if missing).  
- **Manual:** Services page or fleet grid → **Logo…** → Upload / Fetch favicon / Remove.  
- Stored under `DATA_ROOT/service_logos/` (compose volume `./piherder_data` by default).

#### Reboot note

Least-priv sudoers allow **`/usr/sbin/reboot`** (and common paths). PiHerder schedules reboot in the background (`sleep 1` then `sudo -n` on the reboot binary) so SSH returns quickly, closes the client with a short timeout, and clears `reboot_pending` after a successful send. This avoids hangs when the host (especially the PiHerder host itself) dies mid-request.

### Grafana integration

Optional **read-mostly** link into an existing Grafana (**Catalog → Integrations**, same hub as Kuma). PiHerder does **not** deploy Grafana.

**Design:** [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md)

#### Connect Grafana

1. (Recommended) In Grafana: **Administration → Service accounts** — create a Viewer service account and token (`glsa_…`).
2. From a host that can reach Grafana:

   ```bash
   curl -sS -H "Authorization: Bearer $GRAFANA_TOKEN" "https://grafana.example.com/api/health"
   curl -sS -H "Authorization: Bearer $GRAFANA_TOKEN" "https://grafana.example.com/api/search?type=dash-db" | head
   ```

3. PiHerder → **Catalog → Integrations → + Grafana** — base URL, optional token, and **three template kinds**  
   (all use Grafana’s `var-` prefix):

   | Kind | When used | Default-style template |
   |------|-----------|------------------------|
   | **Host metrics** | Binding kind = Host metrics | `var-job={hostname_short}_exporter` |
   | **Containers (host)** | Containers, no container selected | `var-job={hostname_short}_cadvisor` |
   | **Containers (one)** | Containers + container name | `var-job={hostname_short}_cadvisor&var-container={container}` |
   | **Host logs** | Binding kind = Host logs | `var-host={hostname_short}` |

   `{hostname_short}` = first DNS label (`rpi5-1.hacknow.info` → `rpi5-1`).  
   Edit templates to match **your** Grafana variable names (`job`, `container`, `host`, …).
4. **Poll / Test** stores health and dashboard inventory (with token).
5. **Bind** with a **kind** (tabs on the integration detail page; **Clone** prefill supported):
   - **Host metrics** / **Host logs** → **Grafana** dest card on **server detail**
   - **Containers** host overview (no container) → server detail Grafana card  
   - **Containers** + container → Docker page (see below)
6. **Preferred name** (recommended when many hosts share a dashboard):
   - Stored on the **integration** as `config_json.display_names[dashboard_uid]`
   - Set via **Rename** on any binding row, or optional field when adding a bind
   - Applies to **all existing** binds of that UID and **any new** binds later; survives **Poll**
   - Blank + save clears preferred name → chips follow the Grafana title again
   - Per-row **Remove** deletes only that host/container link (preferred name stays)

Without a token you can still deep-link by pasting dashboard UIDs; inventory list will be empty. Token is Fernet-encrypted and included in herder self-backup (same `PIHERDER_MASTER_KEY` on restore).

#### Open Grafana from a container (mobile-friendly)

On **Docker** for a host, each bound container shows a **Grafana** chip (not a cryptic abbreviation). Tap opens the dashboard with host + container query vars already applied.

Also available without relying on hover tooltips:

| Surface | Action |
|---------|--------|
| Row chip | Tap **Grafana** → new tab with filter |
| Container **⋯** menu | **Grafana: &lt;dashboard title&gt;** |
| Expand container row | **Open &lt;dashboard&gt; in Grafana →** |

#### Paths

| Path | Purpose |
|------|---------|
| `/integrations/new/grafana` | Add connection |
| `/integrations/{id}` | Health, inventory, tabbed bindings |
| Server detail | Grafana rows → dashboard with host vars |
| Docker stack | Per-container **Grafana** chip / ⋯ / detail link |

Placeholders: `{hostname}`, `{hostname_short}`, `{name}`, `{name_lower}`, `{ip}` / `{ip_address}`, `{server_id}`, `{host}`, `{container}`, `{docker_container}`, `{project}`, `{docker_project}`, `{compose_service}`.  
Grafana variables need the **`var-`** prefix (`var-job=…`, not bare `job=…`).

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

Documented target: multi-arch image on Docker Hub or GHCR (e.g. `bjorngluck/piherder:0.5.0` at RC). Until then, build from this repo with `docker compose build`. See roadmap in [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md). Current git release: **v0.4.0** — [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md). In development: **v0.5.0** — [PLAN_v0.5.0.md](PLAN_v0.5.0.md).

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

**Not Celery:** OS/container patch and update checks run on **web** (BackgroundTasks / thread pools). Exclusive DB rules prevent two concurrent jobs of the same type on one host. Raising `CELERY_CONCURRENCY` does not double-run a container patch.

Full env list: [`.env.example`](../.env.example).

### Host dependency check

**Where:** Server detail shows a **read-only** snapshot. Re-check under **SSH access → Check dependencies** (also runs after successful **Test connection**, key deploy, and least-priv provision).

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
