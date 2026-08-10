# Public demo site (maintainer ops)

**Audience:** maintainers operating the gated public demo — **not** self-hosting wiki readers.

| | |
|--|--|
| **URL** | https://piherder-demo.hacknow.info |
| **Host** | Dedicated VPS (Docker Compose) — Hub/GHCR image or local build |
| **Mode** | `PIHERDER_DEMO_MODE=true` |
| **Shared login** | `demo@hacknow.info` / viewer role |
| **Public password source of truth** | **[wiki/operations/demo-site.md](../wiki/operations/demo-site.md)** (live docs site) |
| **User-facing wiki** | same page — credentials + limits for visitors |

## Public visitor path

```text
README / wiki home → https://piherder-demo.hacknow.info
  → optional Cloudflare Access (if still enabled)
  → optional Turnstile on login
  → demo@hacknow.info / password published on the live wiki
  → viewer UI (banner + synthetic fleet)
```

**Password policy:** the shared password is intentionally public (view-only sandbox). When you rotate it:

1. Set `PIHERDER_DEMO_PASSWORD` on the VPS `.env`
2. Force re-seed: `./scripts/demo-maintain.sh data-reset` (or wait for cron)
3. Update **`wiki/operations/demo-site.md`** (and README table if it still shows the old value) so the **live wiki** stays authoritative
4. Deploy docs (MkDocs / Pages) before or with the seed so visitors are not stuck on a stale password

Access (if enabled) remains an optional outer gate for spam reduction — do not treat the published password as a substitute for origin firewall / demo mode locks.

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
| `PIHERDER_DEMO_PASSWORD` | public shared password — keep in sync with [wiki demo page](../wiki/operations/demo-site.md) (current: `PiHerder@123?_`) |
| `PIHERDER_SSH_CONSOLE` | `false` |
| `ALLOW_OPEN_REGISTRATION` | `false` |
| `PIHERDER_UPDATE_CHECK` | `false` (optional noise reduction) |
| `PIHERDER_TURNSTILE_SITE_KEY` | Cloudflare Turnstile site key |
| `PIHERDER_TURNSTILE_SECRET_KEY` | Turnstile secret (server-side) |
| `PIHERDER_MASTER_KEY` / `SECRET_KEY` | **Unique to demo** (host `.env`) |

Stack shape matches the normal compose services: web, db, redis, celery-worker (nmap worker optional/off). Caddy (or another reverse proxy) terminates TLS on the VPS as you prefer.

## Cloudflare

1. DNS: `piherder-demo.hacknow.info` → orange-cloud to the VPS origin (or Tunnel CNAME).
2. **Access** (optional outer gate) — email allowlist / OTP if you want spam reduction beyond the published viewer password; not required for the wiki CTA.
3. **Turnstile** widget for the login form (keys in app env) — recommended.
4. **Web Analytics** (optional, privacy-friendly) — prefer this over Google Analytics *inside* the app.
5. WAF / bot fight as usual; rate limits on the origin still apply in-app.

Prefer locking the VPS so only Cloudflare can hit `:443` (CF IP allowlist or `cloudflared` Tunnel). SSH only from admin IPs. No VPN/path into the home lab.

**Host header:** Caddy serves only `PIHERDER_HOSTNAME` (demo: `piherder-demo.hacknow.info`). Other `Host` values and bare-IP requests get **421**. Set matching `PIHERDER_HOSTNAME` + `PIHERDER_PUBLIC_URL` in `.env` so CF cannot be used as an open proxy for arbitrary hostnames on this origin.

**Ports:** use tracked overlay **`docker-compose.demo-ports.yml`** — public **443 only** (Caddy), web on `127.0.0.1:8000`. After `git pull`, recreate so mounts match:

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.demo.yml \
  -f docker-compose.demo-ports.yml up -d --force-recreate caddy web
# optional intentional DOCKER-USER (CF in / DNS+CF HTTPS out):
#   sudo WAN=eth0 COMPOSE_NET=172.18.0.0/16 ./scripts/demo-docker-user.sh
```

### Turnstile troubleshooting

The **browser** widget talks to Cloudflare directly. Login still needs the **web container** to `POST https://challenges.cloudflare.com/turnstile/v0/siteverify` (with `remoteip`).

| Login `code=` | Meaning | Fix |
|---------------|---------|-----|
| `verify-unreachable` | Container cannot reach siteverify | Almost always **DNS inside the container** (`Temporary failure in name resolution`). Recreate `web` after `git pull` (demo overlay sets `dns: 1.1.1.1` / `8.8.8.8` — **must** `up -d --force-recreate`, not restart). Or set host `/etc/docker/daemon.json` → `"dns": ["1.1.1.1","8.8.8.8"]` and `systemctl restart docker`. Probe: `docker compose exec web python -c "import socket; print(socket.getaddrinfo('challenges.cloudflare.com',443))"` then siteverify POST — expect **HTTP 400** + `invalid-input-secret`. |
| `invalid-input-secret` | Wrong secret in env | Match `PIHERDER_TURNSTILE_SECRET_KEY` to dashboard secret for the same site key |
| `missing-remoteip` | Proxy did not pass visitor IP | Caddy / CF: `CF-Connecting-IP` or first `X-Forwarded-For` hop |
| `timeout-or-duplicate` / `invalid-input-response` | Stale or empty token | Complete the widget once, submit login once (do not spam refresh) |

Logs: `docker compose logs web --tail=100 | grep -i turnstile`

## Seed & reset

| Action | How |
|--------|-----|
| First boot | Empty Postgres + demo mode → auto-seed on web lifespan |
| **UI restore** | **Removed** — shared admin must not wipe the fleet for everyone |
| CLI (ops) | `docker compose exec web python scripts/demo_seed/seed.py --force` |
| Data reset | `./scripts/demo-maintain.sh data-reset` (or `./scripts/demo_seed/reset.sh`) |
| Hard wipe | `./scripts/demo-maintain.sh redeploy --wipe` (down -v, up, seed) |
| Clean redeploy | `./scripts/demo-maintain.sh redeploy` (pull + build + force-recreate + seed) |

`POST /herder-backups/demo-restore` returns **403** in demo mode. Also blocked: password change/reset, 2FA, SSO login/link, Users admin, OIDC/alerts/security settings writes, herder restore/delete.

RPO: **demo data is disposable**. Do not store real config on the demo instance.

Seed pack details: [scripts/demo_seed/README.md](../scripts/demo_seed/README.md).

### Cron (demo VPS — install yourself)

Nothing in the app installs cron. On the **demo VPS only**, after `git pull`:

```bash
# once
sudo mkdir -p /var/log/piherder-demo
sudo chown "$USER:$USER" /var/log/piherder-demo

# edit user + absolute repo path, then install
sudo cp scripts/cron.d/piherder-demo.example /etc/cron.d/piherder-demo
sudo chmod 644 /etc/cron.d/piherder-demo
```

| Schedule (UTC, example) | Command | Effect |
|-------------------------|---------|--------|
| `0 */6 * * *` | `demo-maintain.sh data-reset` | Force re-seed synthetic fleet (keeps Postgres volume) |
| `15 5 * * *` | `demo-maintain.sh redeploy` | `git pull --ff-only`, build, force-recreate stack, force seed (05:15 UTC — quieter for US visitors) |

Notes:

- **Root is not required** if the cron user owns the clone and is in the `docker` group. Create `/var/log/piherder-demo` owned by that user (or log under the repo). Root only matters if you also re-apply iptables (`DEMO_REAPPLY_FW=1`).
- Redeploy **refuses a dirty working tree** (won't `git pull` over local edits). Keep the VPS clone clean.
- Optional daily volume wipe: append `--wipe` to the redeploy line (more downtime; empty DB then seed).
- Optional after redeploy: `DEMO_REAPPLY_FW=1` + passwordless sudo for `scripts/demo-docker-user.sh` (usually unnecessary — host `DOCKER-USER` rules survive container recreate; re-run after reboot).
- Flock lock: `/tmp/piherder-demo-maintain.lock` so 6h reset and daily redeploy cannot overlap.
- Smoke-test once as the cron user before enabling:  
  `./scripts/demo-maintain.sh data-reset` then check the log path.

## WordPress / marketing

- Marketing / SEO / screenshots stay on WordPress.
- CTA: **“Try the demo”** → [https://piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) + link to wiki credentials ([Public demo](https://piherder-docs.hacknow.info/operations/demo-site/)).
- If Access is enabled, keep a “request access” path for the outer gate; otherwise point straight at the shared viewer login.
- Do not iframe the live app (Access / CSP / cookies often break embeds).

## Security checklist

- [ ] Access app enforced (no public bypass policy left on)
- [ ] Turnstile keys set on demo
- [ ] Unique Fernet + session secrets
- [ ] Demo VPS isolated from home-lab network
- [ ] Webshell off; registration off
- [ ] Shared password matches live wiki [demo-site](../wiki/operations/demo-site.md) + VPS `.env` + last force seed
- [ ] CLI re-seed tested; confirm Settings has **no** Demo restore tab

Further: [SECURITY.md](../SECURITY.md) · [ADMIN.md](ADMIN.md) · [PLAN_v1.2.0.md](PLAN_v1.2.0.md)
