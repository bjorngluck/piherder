# Upgrades

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
