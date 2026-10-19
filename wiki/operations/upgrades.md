# Upgrades

## What this is

How to move a running compose install to a newer **git tag or `main`**, pull the published image, and let Alembic migrate the database.

## Why a checklist

Upgrades change code *and* schema. A self-backup + unchanged master key is the difference between a smooth pull and an unrecoverable encrypted store.

!!! tip "Prefer tags"
    Prefer **tagged production releases** (`v1.6.0` / later `1.6.x`). Treat untagged `main` as moving. See [Home](../index.md#release-status) · [RELEASE_v1.6.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.6.0.md).

```bash
# Config DR first
# Settings → PiHerder backup → run now
# Also snapshot Postgres volume if you can

git fetch --tags
git checkout v1.6.0   # or later 1.6.x
docker compose pull
docker compose up -d
# Alembic runs on web startup
# optional pin: PIHERDER_IMAGE=bjorngluck/piherder:1.6.0 docker compose up -d
```

## Checklist

- [ ] Self-backup successful (**admin** — Settings → PiHerder backup)  
- [ ] `PIHERDER_MASTER_KEY` unchanged and backed up offline  
- [ ] Read [RELEASE notes](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.6.0.md) for the version you jump to  
- [ ] `docker compose ps` healthy (image `bjorngluck/piherder:…`)  
- [ ] Smoke: login, Users recovery (if multi-user), one server, maps/ports, optional template  
- [ ] Hard-refresh browser once after UI/CSS deploys (query-busted stylesheets)  


## 1.1 → 1.2

Jump from **v1.1.x** to **v1.2.0**. Full notes: [RELEASE_v1.2.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.2.0.md).

1. Take a **1.1** self-backup (config pack) and keep it offline with `PIHERDER_MASTER_KEY`.  
2. Read the release notes. Pre-1.2 “full” archives are **not** a Postgres dump.  
3. Confirm `.env` has a **long random `SECRET_KEY`**. The 1.2 web process **will not boot** on the compose default (`change-me-in-prod`) unless you set `PIHERDER_ALLOW_INSECURE=true` (lab only).  
4. `git fetch --tags && git checkout v1.2.0` (or pull the `1.2.0` image).  
5. `docker compose pull && docker compose up -d` — Alembic runs on web start (includes **`039_ssh_hostkey_pin`**).  
6. Immediately run **Settings → PiHerder backup → Full DR** once and copy the archive off-box. Set the schedule to **Full**.  
7. **Test connection** once per host — first success **pins** the SSH host key. Later key changes are refused until you **reset the pin** under SSH access (rebuilds). Existing `ssh_username` values are **not** rewritten (new hosts default to **`pi`**).  
8. Hard-refresh the browser (compiled Tailwind CSS; no Play CDN).  
9. New / changed env (Compose has defaults):  
   - `PIHERDER_SSH_CONSOLE` — web SSH, **default off**  
   - `PIHERDER_CSP` — default on; **no `unsafe-eval`** (compiled CSS)  
   - `PIHERDER_TRUSTED_PROXY_CIDRS` — Compose trusts RFC1918 + loopback so Caddy can pass visitor IPs  
   - `PIHERDER_ALLOW_INSECURE` — **leave false** on a real fleet  
   - Web port is **`127.0.0.1:8000`** only (not published on the LAN)  
10. Set `PIHERDER_PUBLIC_URL` if you use email password reset or SSO (reset links and OIDC redirects are built from it only).  
11. Smoke: login, one host, one job, optional console (if you enabled it).

SSO / passkeys / demo mode are **new optional surfaces** — they do not turn on by themselves.

## 1.2 → 1.3

Jump from **v1.2.x** to **v1.3.0**. Full notes: [RELEASE_v1.3.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.3.0.md).

Settings policy is JSON in `appsetting`. Alembic **`040_ssh_identities`** (fleet + optional privileged SSH keys per host). Alembic **`041_console_transcripts`** (opt-in encrypted command audit; default off).

1. Self-backup first (**Full DR**). Keep `PIHERDER_MASTER_KEY`.  
2. `git fetch --tags && git checkout v1.3.0` (or pull `bjorngluck/piherder:1.3.0`).  
3. `docker compose pull && docker compose up -d` — Alembic runs on web start.  
4. **Compose change:** bundled compose **no longer injects defaulted** `PIHERDER_SSH_CONSOLE_*` knobs (idle, max, bind, …). Only the master enable `PIHERDER_SSH_CONSOLE` stays in compose. Timeouts live in **Settings → Console**. 2FA / grant live in **Settings → Security**.  
5. If you previously relied on those vars in `.env` **and** compose interpolation, they will not reach the container until you add that one line to compose `environment` (env then **locks** the knob). Otherwise Settings apply.  
6. Smoke: Settings → Security (password / 2FA) · Settings → Console (including **who may open privileged**) · one host **SSH access** (fleet identity still there; optional privileged card) · optional live shell if the flag is on.  
7. Alembic **`040_ssh_identities`** copies each host’s existing username + key into a **fleet** identity. `Server.ssh_username` is unchanged. Jobs keep using fleet.  
8. Alembic **`041_console_transcripts`** is empty until you turn on **Settings → Console → Command audit**. Default remains off.  
9. **Host Files** is **off** until you set `PIHERDER_HOST_FILES=true` and restart web. Transfer cap is **Settings → Files** (default 512 MiB, ceiling 32 GiB) unless you lock `PIHERDER_HOST_FILES_MAX_BYTES`. [Host Files](../day-to-day/host-files.md).  
10. **Reports** is on at `/reports` (no flag) — [Reports](../day-to-day/reports.md).  
11. Immediately run **Settings → PiHerder backup → Full DR** once and copy the archive off-box. Hard-refresh the browser.

## 1.3 → 1.4 {#13--14}

Jump from **v1.3.x** to **v1.4.0**. Notes: [RELEASE_v1.4.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.4.0.md).

Alembic **`042_compose_project_meta`** (per-project host lock). **Move a service** stays **off** until `PIHERDER_SERVICE_MIGRATE=true` and you recreate **web**.

1. Self-backup first (**Full DR**). Keep `PIHERDER_MASTER_KEY`.  
2. After tag: `git fetch --tags && git checkout v1.4.0` (or pull `bjorngluck/piherder:1.4.0`).  
3. `docker compose pull && docker compose up -d` — Alembic runs on web start.  
4. Optional: enable Move, recreate **web**, walk [Move a service](../docker/service-migration.md) on a disposable stack.  
5. Smoke: lock a hardware-bound project · 1.3 Settings / Reports / Files / console.  
6. Immediately run **Full DR** again. Hard-refresh the browser.

## 1.4 → 1.5 {#14--15}

Jump from **v1.4.x** to **v1.5.0**. Notes: [RELEASE_v1.5.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.5.0.md).

No new Alembic. Rebuild **web and celery-worker**. **Move** stays **off** until `PIHERDER_SERVICE_MIGRATE=true` and you recreate **web**.

1. Self-backup first (**Full DR**). Keep `PIHERDER_MASTER_KEY`.  
2. After tag: `git fetch --tags && git checkout v1.5.0` (or pull `bjorngluck/piherder:1.5.0`).  
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**.  
4. Optional: enable Move, recreate **web**, walk [Move a service](../docker/service-migration.md) on a disposable stack (recycle **web** mid-copy is now safe).  
5. Smoke: Reports pin/hide · 1.4 Settings / Files / console.  
6. Immediately run **Full DR** again. Hard-refresh the browser.

- **Move** runs on **celery-worker**. Recycle **web** mid-copy is safe. Recycle **worker** fails a running Move (staging kept).  
- **Reports** pin / hide / reorder + **Move jobs** card — [Reports](../day-to-day/reports.md). Cookie `ph_reports_layout`.

## 1.5 → 1.6 {#15--16}

Jump from **v1.5.x** to **v1.6.0**. Notes: [RELEASE_v1.6.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.6.0.md).

Alembic **043**, **044**, and **045** run on web start. Recreate **web** and **celery-worker**. **Move** stays **off**. Home Assistant is a separate HACS repo, not this image.

1. Self-backup first (**Full DR**). Keep `PIHERDER_MASTER_KEY`.
2. After tag: `git fetch --tags && git checkout v1.6.0` (or pull `bjorngluck/piherder:1.6.0`).
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**.
4. Confirm `alembic_version` includes `045_host_resources`. Open System Info and use the refresh icon once per host you want on the HA card.
5. Optional: HACS [piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.4** with a **read** token. [Home Assistant](../integrations/home-assistant.md).

## 1.6 → 1.7 {#16--17}

**v1.7.0** is tagged. Notes: [RELEASE_v1.7.0.md](https://github.com/bjorngluck/piherder/blob/v1.7.0/docs/RELEASE_v1.7.0.md). Recreate **web** and **celery-worker**, confirm Alembic **046**, and update HACS to plugin **0.3.0**. Move stays off. The next jump is [1.7 → 1.8](#17--18).

## 1.7 → 1.8 {#17--18}

**v1.8.0** is tagged. Notes: [RELEASE_v1.8.0.md](https://github.com/bjorngluck/piherder/blob/v1.8.0/docs/RELEASE_v1.8.0.md). The patch on that line is **[v1.8.1](https://github.com/bjorngluck/piherder/blob/v1.8.1/docs/RELEASE_v1.8.1.md)**. Pins `1.8.1` / `1.8` and `1.8.0` stay valid. Pins `1.7.0` / `1.7` stay valid. The next jump is [1.8 → 1.9](#18--19).

**v1.8.1** refreshes PyJWT to **2.15.1** and urllib3 to **2.8.0**. No new migration. Recreate **web** and **celery-worker**. About should say **1.8.1**.

Alembic **047** runs on web start. Recreate **web** and **celery-worker**. HACS plugin **[0.4.4](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.4.4)** with resource `?v=0.4.4`. The MCP adapter stays **[0.2.0](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.2.0)** and does not start the one-service jobs. Move stays off. Google Drive copy is Settings → PiHerder backup. The public demo is the **1.7.0** image.
6. Optional: `PIHERDER_SSH_CONSOLE=true`, recreate **web**, tick **Console mux** only on a Debian host that already has `tmux` or `screen`.
7. Move stays **off** until `PIHERDER_SERVICE_MIGRATE=true` and you recreate **web**. Public demo stays Report-Only CSP. Do not set `PIHERDER_CSP_ENFORCE` there.
8. Immediately run **Full DR** again. Hard-refresh the browser.

## 1.8 → 1.9 {#18--19}

**v1.9.0** is tagged. Notes: [RELEASE_v1.9.0.md](https://github.com/bjorngluck/piherder/blob/v1.9.0/docs/RELEASE_v1.9.0.md). Pin `1.8.1` / `1.8` stays the previous image for that line. The next jump is [1.9 → 1.10](#19--110).

No new database revision. Recreate **web** and **celery-worker**. About should say **1.9.0**. Move is **on** unless `PIHERDER_SERVICE_MIGRATE=false`. A LAN share can be saved beside Google Drive. HACS plugin **[0.5.0](https://github.com/bjorngluck/piherder-ha/releases/tag/v0.5.0)** with resource `?v=0.5.0`. Adapter **[0.3.1](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.3.1)** sends the four one-service jobs. OneDrive is unselectable on this **1.9.0** tag.

## 1.9 → 1.10 {#19--110}

**v1.10.0** is tagged. Notes: [RELEASE_v1.10.0.md](https://github.com/bjorngluck/piherder/blob/v1.10.0/docs/RELEASE_v1.10.0.md). Image `1.10.0` / `1.10` / `latest`. Pin `1.9.0` / `1.9` stays the previous image.

Alembic **048** runs on web start. Recreate **web** and **celery-worker**. About should say **1.10.0**. OneDrive is a third row on **Copy the backup drive**. A destination can copy each new self-backup. Hosted `/mcp` can sign an agent in through the browser. Plugin **0.5.0** and adapter **0.3.1** stay on this **1.10.0** image. The next jump is [1.10 → 1.11](#110--111).

## 1.10 → 1.11 {#110--111}

**v1.11.0** is the current release. Notes: [RELEASE_v1.11.0.md](https://github.com/bjorngluck/piherder/blob/v1.11.0/docs/RELEASE_v1.11.0.md). Image `1.11.0` / `1.11` / `latest`. Pin `1.10.0` / `1.10` stays the previous image.

Alembic **049** and **050** run on web start. Recreate **web** and **celery-worker**. About should say **1.11.0**. A passkey counts as 2FA when Force 2FA is on. A host can send files straight to a saved Drive, OneDrive, or LAN share. Hosted `/mcp` can start a Move, read or start a LAN Discovery scan, and tidy devices. Docker → **Repository…** can update an existing checkout. Plugin **0.5.0** stays. Adapter **[0.4.2](https://github.com/bjorngluck/piherder-mcp/releases/tag/v0.4.2)** sends `confirm=true` on discovery purge. `uvx --refresh piherder-mcp`, then restart the MCP client. The public demo is updated separately. The next train is [v1.12.0](https://github.com/bjorngluck/piherder/blob/v1.12.0-dev/docs/PLAN_v1.12.0.md) on `v1.12.0-dev`. Must is in on that branch. A direct copy can use an amd64 or arm64 rclone. A missed scheduled backup or container check opens a warning. A destination copy scheduled before those host backups opens a warning. `list_servers` and `get_server` return the effective backup time. Docker prune is a job. The GitHub adapter readme badges herder 1.11.0. About lists each scheduled job's last fire and next fire. An OS patch job keeps apt's error output, including when sudo drops DEBIAN_FRONTEND. The history sweep is in on this tree. The push denylist checks added lines when its secret is set. The three lockfiles pin Mako 1.4.3 and multidict 6.9.1. Actions stay on major tags. Six medium Dependabot alerts stay open on main until this branch lands. PyPI 0.4.2 still badges 1.10.0 until the adapter cut that ships with this herder. It is not an upgrade path yet. Package stays **1.11.0**.

## Breaking notes

Read the release doc for the version you jump to (migrations, new env keys, behaviour changes).
