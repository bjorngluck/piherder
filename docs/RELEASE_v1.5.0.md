# PiHerder v1.5.0

**18 September 2026.** Move a stack while you recycle the UI. Pin the Reports cards you actually use. When a session expires you get **Sign in**, not a JSON error.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.5.0` · `1.5` · `latest` (amd64 + arm64). Pins `1.4.0` / `1.4` stay valid.

Operator how-to: wiki [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/) · [Reports](https://piherder-docs.hacknow.info/day-to-day/reports/) · [Jobs](https://piherder-docs.hacknow.info/day-to-day/jobs-audit-notifications/). Technical record: [PLAN_v1.5.0](https://github.com/bjorngluck/piherder/blob/v1.5.0/docs/PLAN_v1.5.0.md). Maintainer QA: [QA_v1.5.0](https://github.com/bjorngluck/piherder/blob/v1.5.0/docs/QA_v1.5.0.md).

---

## What’s new

### Move keeps running when you recycle the web UI

**Move a service** now runs on the **Celery worker** (same queue as backups). Recreating **web** mid-copy does **not** fail the job. Recreating **celery-worker** **does** fail it honestly — staging stays, and you can **Start source stack** if the fail was during copy or dest-up.

Kill switch **`PIHERDER_SERVICE_MIGRATE`** stays **off**. Recreate **web** (and keep **celery-worker** current) after you turn it on. Operator+ only. Public demo never copies.

Wiki: [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/) · [Multi-worker](https://piherder-docs.hacknow.info/operations/multi-worker/)

### Reports layout

On **Reports**, pin, hide, and reorder cards. Layout is per browser (cookie), not a fleet setting. A **Move jobs** card shows run / fail counts and last dest.

Wiki: [Reports](https://piherder-docs.hacknow.info/day-to-day/reports/)

### Sign in when the session expires

An expired or missing session on a UI page goes to **Sign in**. You should never see a JSON page `{"detail":"Please log in to continue"}`. API clients still get JSON 401.

### Host reboot actually reboots

**Reboot now** uses `systemctl reboot --ignore-inhibitors` so a leftover SSH or desktop session does not silently block the reboot. Confirm copy says those sessions are logged off. It is **not** `--force`.

Wiki: [Updates — Reboot](https://piherder-docs.hacknow.info/day-to-day/updates-and-patching/#reboot)

---

## Defaults (opt-in stays off)

| | Default |
|--|---------|
| Move a service | **off** (`PIHERDER_SERVICE_MIGRATE`) |
| Host Files (real SFTP) | **off** (`PIHERDER_HOST_FILES`) |
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Command audit | **off** (Settings) |
| Source leftover after Move | **leave stopped** (data kept) |

---

## Upgrade from 1.4

No new database migration. Rebuild **web and celery-worker** so Move is not still on the old web process.

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.  
2. Pull `bjorngluck/piherder:1.5.0` (or `git checkout v1.5.0`).  
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**.  
4. Move stays **off** until you set `PIHERDER_SERVICE_MIGRATE=true` and recreate **web**.  
5. Smoke: optional Move on a disposable unlocked stack (recycle **web** mid-copy if you want the 1.5 proof) · Reports pin/hide · 1.4 surfaces (lock, policy, Files, console).

[Wiki upgrades](https://piherder-docs.hacknow.info/operations/upgrades/#14--15)

---

## Honest limits

| | |
|--|--|
| Move | Still stop-first (brief downtime). Still flag-off. Recycle **celery-worker** mid-Move fails the job. No auto-rollback after DNS/NPM flip. |
| Reports layout | Per browser cookie, not synced across devices. |
| Not this release | HA integration that runs **on** Home Assistant (v1.6) · remaining jobs on Celery (v1.7) · host `tmux`/`screen` · instance branding · CSP script nonces · turning Move on by default |

---

From [v1.4.0](https://github.com/bjorngluck/piherder/releases/tag/v1.4.0). Docs: [piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/)
