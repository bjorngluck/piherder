# PiHerder v0.5.0

**Date:** 2026-07-17  
**Git tag:** `v0.5.0`  
**Baseline:** `v0.4.0`  
**Theme:** First RC — production-trustworthy templates, ecosystem connectors, network maps, multi-arch image, open source (MIT)

**Plans:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `0.5.0` · `0.5` · `latest`

---

## Highlights

### Multi-arch Docker Hub image (install story complete)

- Published **`bjorngluck/piherder`** for **amd64 + arm64** (Pi-friendly herder hosts).
- Official `docker-compose.yml` **pulls** `bjorngluck/piherder:latest` (no local build).
- Pin a release: `PIHERDER_IMAGE=bjorngluck/piherder:0.5.0 docker compose up -d`
- Process: [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md)

### Ecosystem connectors

- **Pi-hole v6 multi-instance** — connect N instances, stats, deep links, primary, Gravity / DNS restart / flush
- **Pi-hole DNS/CNAME** — list on primary path; add/remove fans out to all enabled instances
- **Nginx Proxy Manager** — token auth, proxy hosts (read + bind), cert inventory
- **Managed certificates** — NPM pull or PEM upload; encrypted fullchain+key; deploy targets (pair / combined / pfx); renew loop (≤21d, scheduler every 6h)
- Herder self-backup includes managed certs + targets

### Network maps (DNS fabric)

- Host DNS (`dns_name` / manage A) + service mappings (CNAME or host identity)
- Catalog → **Network**: import from Pi-hole; Hosts map (Internet → Router → LAN / cloud); Path map
- Network settings: LAN CIDR, gateway, public WAN IP
- Kuma infrastructure resolve; mobile hide/fullscreen and orientation reflow

### Templates → production trust

- Redeploy **volume editor**; from-host edge cases; clearer failure banners
- **Config drift** — scheduled (6h) + manual Check; alert + audit
- **Import host `.env`** into encrypted source of truth
- **Apply last known config** + guided restore from matching backup sources
- Catalog **Deploy** wizard restored and screenshot-backed in the wiki

### Fleet ops polish

- Exclusive OS/container jobs (409 + existing `job_id`); deferred reboot (no hang on herder host)
- Servers list **bulk actions** (check/patch/backup)
- Docker full editor links; backup complete audit always recorded
- App **timezone** display across Audit / Jobs / Notifications / fleet UI
- **Catalog** hub: Integrations · Certificates · Templates · Network
- Grafana preferred names by dashboard UID; users create modal; server audit footer links
- Ops-hero UI consistency; dashboard constellation mesh; mobile layout reflow

### Open source & packaging

- **MIT** license; public GitHub repo; CONTRIBUTING welcome guide
- Living **MkDocs wiki** → https://piherder-docs.hacknow.info/ (GitHub Actions Pages)
- Locked third-party deps (`requirements.lock.txt` with hashes) for reproducible images
- Session JWT: **python-jose → PyJWT**
- Architecture maintainability: router/package splits, ops CSS, `dns_fabric` package
- About page + GitHub release update notice in-app
- Hardened `.dockerignore` (no local backups/certs/data/git in Hub images)

### Security / RC hardening

- First-register admin path; closed registration → ask admin
- Instance DR and secret-path review (step-up 2FA, no cleartext in audit, host `.env` mode `600`)
- **Audit client IP** on request-driven events (Caddy XFF)
- Password policy UX (characters, soft max ~72)
- User delete integrity (push/notification FKs)

---

## Intentionally not in v0.5.0

| Horizon | Items |
|---------|--------|
| **Post-RC / H2.75** | Host lifecycle console — bulk Docker start/stop/restart, richer host stats, web SSH, wizard onboarding — [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) |
| **Later** | Advanced secret backends · NPM write (create/edit proxies) · Cloudflare DNS API · service migrate host→host · large curated pack · K8s · multi-tenant · optional AI · git template catalog (nice-to-have) |

---

## Breaking / migration notes

| Change | Action |
|--------|--------|
| **Compose uses published image** | `docker compose up -d` pulls Hub; local source changes need a custom build or override image |
| **New Alembic migrations** (since v0.4.0) | `017` Pi-hole/NPM/certs · `018` audit client IP · `019` cert target label · `020` DNS fabric — apply on web startup |
| **License** | PolyForm → **MIT** (open source) |
| Encrypted secrets / certs | Same **`PIHERDER_MASTER_KEY`** required for restore and DR |
| Session JWT | PyJWT only (no python-jose) — recreate sessions if needed (re-login) |

Existing v0.4.0 deployments: pull new image / checkout tag, keep `.env` + volumes, `docker compose up -d`.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.5.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and SECRET_KEY — never use compose defaults in production
docker compose up -d
```

Optional pin:

```bash
export PIHERDER_IMAGE=bjorngluck/piherder:0.5.0
docker compose up -d
```

Docs: [Install](https://piherder-docs.hacknow.info/getting-started/install/) · [README](../README.md)

### Upgrade from v0.4.0

```bash
# 1) Self-backup + confirm PIHERDER_MASTER_KEY is safe offline
git fetch --tags
git checkout v0.5.0
docker compose pull
docker compose up -d
```

Migrations run on web startup. Open **Catalog** for Integrations / Certificates / Templates / Network.

---

## Package version

`pyproject.toml` / `APP_VERSION` → **`0.5.0`**

---

## Docs & tests

| Doc | Role |
|-----|------|
| [ADMIN.md](ADMIN.md) | Operator guide |
| [API.md](API.md) | REST `/api/v1` (409 exclusive jobs) |
| [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) | Multi-arch publish process |
| [PLAN_v0.5.0.md](PLAN_v0.5.0.md) | Ship bar (frozen at tag) |
| Wiki | https://piherder-docs.hacknow.info/ |

**Notable tests:** `test_service_templates` · `test_integrations_*` · `test_certificates` · `test_dns_fabric` · `test_job_exclusive` · `test_request_ip_audit` · `test_herder_backup` · `test_app_update`

---

## Verify after upgrade

1. `docker compose ps` — web healthy; image `bjorngluck/piherder:…`
2. About page shows **0.5.0**
3. **Catalog** — Integrations / Certificates / Templates / Network
4. Template deploy + drift Check + apply last known config (lab host)
5. Pi-hole / NPM connect if used; cert list loads
6. Network maps: Hosts + Path (mobile hide / fullscreen)
7. Bulk server actions + exclusive job 409 reuse
8. Audit entries show client IP when via Caddy
9. Jobs Cancel still works; backup terminal audit present

---

## Commits since v0.4.0

```bash
git log --oneline v0.4.0..v0.5.0
```

Representative areas (not exhaustive — ~65 commits):

| Area | Summary |
|------|---------|
| Connectors | Pi-hole multi, NPM, managed certs + renew |
| Network | DNS fabric maps, Kuma infra, mobile |
| Templates | Drift, env import, volume editor, last config |
| Fleet | Exclusive jobs, bulk actions, reboot, audits |
| UI | Catalog, ops-hero, About, brand, mobile |
| OSS / RC | MIT, wiki, lockfiles, multi-arch Hub image |
| Security | PyJWT, first-register, audit IP, DR gaps |

---

**End of v0.5.0 release notes.**
