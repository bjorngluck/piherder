# Upgrades

## What this is

How to move a running compose install to a newer **git tag or `main`**, rebuild images, and let Alembic migrate the database.

## Why a checklist

Upgrades change code *and* schema. A self-backup + unchanged master key is the difference between a smooth pull and an unrecoverable encrypted store.

!!! warning "RC1"
    Prefer tagged releases once published; treat `main` as moving. See [Home — RC1](../index.md#rc1).

```bash
# Config DR first
# Settings → PiHerder backup → run now
# Also snapshot Postgres volume if you can

git fetch --tags
git checkout main     # or a release tag (e.g. v0.5.0 when published)
docker compose up -d --build
# Alembic runs on web startup
docker compose run --rm --no-deps web pytest -q   # optional
```

## Checklist

- [ ] Self-backup successful (**admin** — Settings → PiHerder backup)  
- [ ] `PIHERDER_MASTER_KEY` unchanged and backed up offline  
- [ ] Read ship plan / release notes under `docs/PLAN_v0.5.0.md` or `docs/RELEASE_v*.md` when present  
- [ ] `docker compose ps` healthy  
- [ ] Smoke: login, one server, optional template  
- [ ] Hard-refresh browser once after UI/CSS deploys (query-busted stylesheets)  


## Breaking notes

Read the release doc for the version you jump to (migrations, new env keys, behaviour changes).
