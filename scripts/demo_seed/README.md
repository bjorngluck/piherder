# Demo seed (Stream D)

Hand-authored synthetic fleet for **public demo** instances.

| Item | Value |
|------|--------|
| Hostname (prod demo) | `https://piherder-demo.hacknow.info` |
| Shared login | `demo@hacknow.info` / `PIHERDER_DEMO_PASSWORD` (default `Piherder@1`) |
| Flag | `PIHERDER_DEMO_MODE=true` |

## Docker Compose (local or VPS)

```bash
# From repo root — needs PIHERDER_MASTER_KEY in .env
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
# Empty DB auto-seeds on web startup when DEMO_MODE=1
# Force re-seed:
docker compose exec web python scripts/demo_seed/seed.py --force
# Or:
./scripts/demo_seed/reset.sh
# Hard wipe volumes:
./scripts/demo_seed/reset.sh --wipe
```

### Production demo VPS

Same overlay and env as above. Set:

- `PIHERDER_DEMO_MODE=true`
- `PIHERDER_HOSTNAME=piherder-demo.hacknow.info`
- `PIHERDER_PUBLIC_URL=https://piherder-demo.hacknow.info`
- `PIHERDER_DEMO_EMAIL` / `PIHERDER_DEMO_PASSWORD`
- `PIHERDER_SSH_CONSOLE=false`
- Unique `PIHERDER_MASTER_KEY` + `SECRET_KEY` (never lab keys)

Empty Postgres triggers auto-seed on first web start. Re-seed anytime with `seed.py --force` or **Settings → Demo**.

Nightly: host cron calling force seed (or `reset.sh --wipe` if you want a full volume recreate).

Maintainer VPS runbook: [docs/DEMO_SITE.md](../../docs/DEMO_SITE.md).

## What is seeded

- 6 hosts (`lab-core` … `lab-spare`) with static Docker inventory
- Jobs + audit history (success/fail mix)
- Nmap “Demo LAN” + devices
- Placeholder integrations (Pi-hole, Kuma, NPM) — no secrets
- DNS fabric records + runtime edges for maps
- `force_2fa=false` (shared password after CF Access)

## Never seeded

Private keys, API tokens, SMTP/VAPID production material, real backup roots, home-lab IPs.

## Security

Demo is **shared admin**. Gate with Cloudflare Access; prefer Turnstile on login. Reset often.
