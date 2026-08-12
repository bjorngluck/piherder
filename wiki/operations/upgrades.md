# Upgrades

## What this is

How to move a running compose install to a newer **git tag or `main`**, pull the published image, and let Alembic migrate the database.

## Why a checklist

Upgrades change code *and* schema. A self-backup + unchanged master key is the difference between a smooth pull and an unrecoverable encrypted store.

!!! tip "Prefer tags"
    Prefer **tagged production releases** (`v1.1.0` / later `1.1.x`). Treat untagged `main` as moving. See [Home](../index.md#release-status) · [RELEASE_v1.1.0.md](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.1.0.md).

```bash
# Config DR first
# Settings → PiHerder backup → run now
# Also snapshot Postgres volume if you can

git fetch --tags
git checkout v1.1.0   # or later 1.1.x
docker compose pull
docker compose up -d
# Alembic runs on web startup
# optional pin: PIHERDER_IMAGE=bjorngluck/piherder:1.1.0 docker compose up -d
```

## Checklist

- [ ] Self-backup successful (**admin** — Settings → PiHerder backup)  
- [ ] `PIHERDER_MASTER_KEY` unchanged and backed up offline  
- [ ] Read [RELEASE notes](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.1.0.md) for the version you jump to  
- [ ] `docker compose ps` healthy (image `bjorngluck/piherder:…`)  
- [ ] Smoke: login, Users recovery (if multi-user), one server, maps/ports, optional template  
- [ ] Hard-refresh browser once after UI/CSS deploys (query-busted stylesheets)  


## 1.1 → 1.2 (when tagged)

Do this **after** `v1.2.0` is tagged and you choose to leave 1.1.x.

1. Take a **1.1** self-backup (config pack) and keep it offline with `PIHERDER_MASTER_KEY`.  
2. Read [RELEASE_v1.2.0.md](https://github.com/bjorngluck/piherder/blob/v1.2.0-dev/docs/RELEASE_v1.2.0.md). Pre-1.2 “full” archives are **not** a Postgres dump.  
3. `git fetch --tags && git checkout v1.2.0` (or pull the `1.2.0` image).  
4. `docker compose pull && docker compose up -d` — Alembic runs on web start.  
5. Immediately run **Settings → PiHerder backup → Full DR** once and copy the archive off-box. Set the schedule to **Full**.  
6. New / changed env (Compose has defaults):  
   - `PIHERDER_SSH_CONSOLE` — web SSH, **default off**  
   - `PIHERDER_CSP` — default on  
   - `PIHERDER_TRUSTED_PROXY_CIDRS` — Compose trusts RFC1918 + loopback so Caddy can pass visitor IPs  
   - Web port is **`127.0.0.1:8000`** only (not published on the LAN)  
7. Set `PIHERDER_PUBLIC_URL` if you use email password reset (links are built from it only).  
8. Smoke: login, one host, one job, optional console (if you enabled it).

SSO / passkeys / demo mode are **new optional surfaces** — they do not turn on by themselves.

## Breaking notes

Read the release doc for the version you jump to (migrations, new env keys, behaviour changes).
