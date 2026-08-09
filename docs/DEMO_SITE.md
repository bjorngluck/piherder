# Public demo site (maintainer ops)

**Audience:** maintainers operating the gated public demo — **not** self-hosting wiki readers.

| | |
|--|--|
| **URL** | https://piherder-demo.hacknow.info |
| **Host** | Dedicated VPS (Docker Compose) — Hub/GHCR image or local build |
| **Mode** | `PIHERDER_DEMO_MODE=true` |
| **User-facing wiki** | [wiki/operations/demo-site.md](../wiki/operations/demo-site.md) (slim) |

## Invitee path

```text
WordPress CTA → request access
  → Cloudflare Access (email OTP / IdP)
  → https://piherder-demo.hacknow.info
  → optional Turnstile on login
  → demo@… / shared password
  → full clickable UI (banner + synthetic fleet)
```

Do **not** promise an ungated open demo: Access is the outer gate.

## What demo mode does

| Behaviour | Detail |
|-----------|--------|
| Banner | Non-dismissible “Demo — shared account · data resets · some actions simulated” |
| Onboard | Add-server wizard / real SSH blocked |
| API | Docs visible; token create + Bearer use **403** |
| Jobs | Canned success (“Demo simulation”) — no live SSH |
| nmap / cert edge | Live outbound blocked |
| Webshell | Forced off |
| Seed | Auto on empty DB; **ops CLI only** (no in-app restore — shared admin vandalism) |
| Shared login role | **`viewer`** — same RBAC/menus as production viewers (not shared admin) |
| Shared identity | Password / 2FA still **locked** on the shared user (one visitor must not lock others out) |
| Account creation | **None** — no register / Users create / SSO JIT |
| Fleet config | Blocked by normal viewer RBAC + demo write guard; **canned job runs** still allowed for the click-through |
| Ops re-seed | CLI only (no in-app admin seed UI) |

Never point demo at the home-lab network or reuse production `PIHERDER_MASTER_KEY`.

## Environment (Docker Compose)

Use the demo overlay [`docker-compose.demo.yml`](../docker-compose.demo.yml) on the VPS:

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
```

| Variable | Example / note |
|----------|----------------|
| `PIHERDER_DEMO_MODE` | `true` |
| `PIHERDER_HOSTNAME` | `piherder-demo.hacknow.info` |
| `PIHERDER_PUBLIC_URL` | `https://piherder-demo.hacknow.info` |
| `PIHERDER_DEMO_EMAIL` | `demo@hacknow.info` |
| `PIHERDER_DEMO_PASSWORD` | default `Piherder@1` (override/rotate as needed) |
| `PIHERDER_SSH_CONSOLE` | `false` |
| `ALLOW_OPEN_REGISTRATION` | `false` |
| `PIHERDER_UPDATE_CHECK` | `false` (optional noise reduction) |
| `PIHERDER_TURNSTILE_SITE_KEY` | Cloudflare Turnstile site key |
| `PIHERDER_TURNSTILE_SECRET_KEY` | Turnstile secret (server-side) |
| `PIHERDER_MASTER_KEY` / `SECRET_KEY` | **Unique to demo** (host `.env`) |

Stack shape matches the normal compose services: web, db, redis, celery-worker (nmap worker optional/off). Caddy (or another reverse proxy) terminates TLS on the VPS as you prefer.

## Cloudflare

1. DNS: `piherder-demo.hacknow.info` → orange-cloud to the VPS origin (or Tunnel CNAME).
2. **Access** application on that hostname (allowlist + WordPress “request access” process).
3. **Turnstile** widget for the login form (keys in app env).
4. **Web Analytics** (optional, privacy-friendly) — prefer this over Google Analytics *inside* the app.
5. WAF / bot fight as usual; rate limits on the origin still apply in-app.

Prefer locking the VPS so only Cloudflare can hit `:443` (CF IP allowlist or `cloudflared` Tunnel). SSH only from admin IPs. No VPN/path into the home lab.

## Seed & reset

| Action | How |
|--------|-----|
| First boot | Empty Postgres + demo mode → auto-seed on web lifespan |
| **UI restore** | **Removed** — shared admin must not wipe the fleet for everyone |
| CLI (ops) | `docker compose exec web python scripts/demo_seed/seed.py --force` |
| Hard wipe | `./scripts/demo_seed/reset.sh --wipe` (compose down -v, up, seed) |
| Nightly | Host cron calling force seed or volume wipe + re-up |

`POST /herder-backups/demo-restore` returns **403** in demo mode. Also blocked: password change/reset, 2FA, SSO login/link, Users admin, OIDC/alerts/security settings writes, herder restore/delete.

RPO: **demo data is disposable**. Do not store real config on the demo instance.

Seed pack details: [scripts/demo_seed/README.md](../scripts/demo_seed/README.md).

## WordPress

- Marketing / SEO / screenshots stay on WordPress.
- CTA: “Request demo access” → collect email → add to Cloudflare Access policy.
- Do not iframe the live app (Access breaks embeds).

## Security checklist

- [ ] Access app enforced (no public bypass policy left on)
- [ ] Turnstile keys set on demo
- [ ] Unique Fernet + session secrets
- [ ] Demo VPS isolated from home-lab network
- [ ] Webshell off; registration off
- [ ] Known shared password documented for ops only (or rotated + Access-only distribution)
- [ ] CLI re-seed tested; confirm Settings has **no** Demo restore tab

Further: [SECURITY.md](../SECURITY.md) · [ADMIN.md](ADMIN.md) · [PLAN_v1.2.0.md](PLAN_v1.2.0.md)
