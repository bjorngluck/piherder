# PiHerder v0.6.0

**Date:** 2026-07-18  
**Git tag:** `v0.6.0`  
**Baseline:** `v0.5.0`  
**Theme:** RC2 polish — template Jobs, cert vault UX, Docker bulk lifecycle, runtime topology + Kuma coverage

**Plans:** [PLAN_v0.6.0.md](PLAN_v0.6.0.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `0.6.0` · `0.6` · `latest`

---

## Highlights

### Templates as Jobs

- Template **deploy** and **redeploy** run as fleet Jobs (`template_deploy` / `template_redeploy`) with **JobHold live log**
- Secrets in job details are Fernet-encrypted and cleared when the job finishes
- Exclusive stack-mutation lane with Docker deploy / bulk lifecycle on the same host

### Certificate vault UX (RC2)

- First-cert **setup guide** at `/certificates/setup`
- **Map presets** (NPM, Caddy, Docker bind, OctoPi, Grafana, UniFi) with path preview
- Fleet write modes: **direct** and **stage_sudo** (sudoers snippet for least-priv)
- **Grafana** maps: deploy ownership **UID 472** (not root:600) so containers can read PEMs
- List/detail **map sync status** (in-sync / stale / empty CTAs)
- **Self-managed edge map** — Apply to this PiHerder writes `./certs` and reloads in-stack Caddy; optional mapping on/off; NPM renew re-applies while mapping is enabled
- Dual TLS story documented: edge Caddy vs Catalog vault

### Docker project bulk lifecycle (H2.75 P1)

- Project **⋯ → Stop all / Start all / Restart all** → confirm → Jobs  
  (`docker_stack_stop` / `_start` / `_restart`) with live log
- Same exclusive lane as stack Deploy and template deploy/redeploy

### Runtime topology + Kuma coverage (H2.5)

- **Stack panel** and path/hosts **map expand** (container order → columns L→R)
- `RuntimeEdge` suggest / accept / manual; compose graph inventory
- Kuma-bound inventory-down awareness
- Catalog **Coverage** page (`/dns/coverage`)

### Docs & packaging

- Wiki prose for dual TLS, certs, Docker bulk, topology (screenshot PNG refresh → **v0.7.0**)
- Herder self-backup format **v3** includes `runtime_edges` (+ prior cert/template/DNS tables)
- Full pytest pack green at freeze (**426** tests)

---

## Intentionally not in v0.6.0

| Horizon | Items |
|---------|--------|
| **v0.7.0** | **Add-host wizard** · wiki **screenshot pack** (stale + new 0.6 surfaces) — [screenshots README](../wiki/assets/screenshots/README.md) |
| **v0.7.x+** | Host stats / allowlisted commands · bootstrap depth · web SSH · multi-map cert deploy as Job · drift as Job |
| **v0.8.0** | **LAN discovery (nmap-class)** |
| **Later** | Configurable topology columns · ACME-in-herder · NPM proxy write CRUD · Cloudflare DNS · git template catalog · large curated pack |

Onboarding in 0.6 remains **Add server form + SSH access panel** (fully supported).

---

## Breaking / migration notes

| Change | Action |
|--------|--------|
| **New Alembic migrations** (since v0.5.0) | `021` runtime edges · `022` cert edge + write mode · `023` edge apply enabled — apply on web startup |
| **Herder backup format** | Version **3** (adds `runtime_edges`); restore remains backward compatible with older archives |
| Encrypted secrets / certs | Same **`PIHERDER_MASTER_KEY`** required for restore and DR |
| Caddy edge apply | Needs web `./certs` mount RW + Caddy admin on the compose network (see wiki [HTTPS](https://piherder-docs.hacknow.info/getting-started/https-tls/) · [Certificates](https://piherder-docs.hacknow.info/integrations/certificates/)) |

Existing v0.5.0 deployments: pull new image / checkout tag, keep `.env` + volumes, `docker compose up -d`.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.6.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and SECRET_KEY — never use compose defaults in production
docker compose up -d
```

Optional pin:

```bash
export PIHERDER_IMAGE=bjorngluck/piherder:0.6.0
docker compose up -d
```

Docs: [Install](https://piherder-docs.hacknow.info/getting-started/install/) · [README](../README.md)

### Upgrade from v0.5.0

```bash
# 1) Self-backup + confirm PIHERDER_MASTER_KEY is safe offline
git fetch --tags
git checkout v0.6.0
docker compose pull
docker compose up -d
```

Migrations run on web startup. Review **Certificates** (setup / edge map) and **Docker** project ⋯ bulk actions if you use those features.

---

## Package version

`pyproject.toml` / `APP_VERSION` → **`0.6.0`**

---

## Docs & tests

| Doc | Role |
|-----|------|
| [ADMIN.md](ADMIN.md) | Operator guide |
| [API.md](API.md) | REST `/api/v1` |
| [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md) | Multi-arch publish process |
| [PLAN_v0.6.0.md](PLAN_v0.6.0.md) | Ship bar (frozen at tag) |
| Wiki | https://piherder-docs.hacknow.info/ |

**Notable tests:** `test_certificates` · `test_docker_stack_lifecycle` · `test_compose_graph` · `test_dns_fabric` · `test_service_templates` · `test_herder_backup` · `test_job_exclusive`

---

## Verify after upgrade

1. `docker compose ps` — web healthy; image `bjorngluck/piherder:…`
2. About page shows **0.6.0**
3. Template deploy → Job + live log → deployment page
4. Certificates: setup guide, map preset, fleet deploy; optional **Apply to this PiHerder**
5. Docker project ⋯ Stop/Start/Restart all → Job
6. Network Path map: stack expand / panel reorder; Coverage page if Kuma is configured
7. Wiki dual TLS + certs + Docker lifecycle accurate (prose)
8. Jobs Cancel still works; audit trail present

---

## Commits since v0.5.0

```bash
git log --oneline v0.5.0..v0.6.0
```

Representative areas:

| Area | Summary |
|------|---------|
| Templates | Deploy/redeploy as Jobs + live log |
| Certs | Setup, presets, stage_sudo, edge map, Grafana 472, renew re-apply |
| Docker | Project bulk stop/start/restart Jobs |
| Topology | Runtime edges, stack expand, coverage |
| Docs | Dual TLS, freeze deferrals (wizard/screenshots → 0.7) |
| Backup | Format v3 + `runtime_edges` |

---

**End of v0.6.0 release notes.**
