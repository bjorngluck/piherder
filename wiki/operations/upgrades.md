# Upgrades

```bash
# Config DR first
# Settings → PiHerder backup → run now
# Also snapshot Postgres volume if you can

git fetch --tags
git checkout v0.4.0   # or newer
docker compose up -d --build
# Alembic runs on web startup
docker compose run --rm --no-deps web pytest -q   # optional
```

## Checklist

- [ ] Self-backup successful  
- [ ] `PIHERDER_MASTER_KEY` unchanged and backed up offline  
- [ ] Read release notes under `docs/RELEASE_v*.md`  
- [ ] `docker compose ps` healthy  
- [ ] Smoke: login, one server, optional template  

## Breaking notes

Read the release doc for the version you jump to (migrations, new env keys, behaviour changes).
