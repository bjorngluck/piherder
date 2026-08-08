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


## Breaking notes

Read the release doc for the version you jump to (migrations, new env keys, behaviour changes).
