# Feature Plan: Pi-hole + NPM + TLS certificates (v0.5.0)

**Status:** Implemented on main (v0.5.0 track)  
**Related:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · Wiki [DNS fabric](../wiki/integrations/dns-fabric.md) (host/service topology beyond raw Pi-hole CRUD)

## Goal

Ship first-class **Pi-hole (v6)** and **Nginx Proxy Manager** integrations, plus a **managed certificate store** with deploy/renew ops — including PEM upload for operators who do not use NPM.

## Decisions

| # | Decision |
|---|----------|
| 1 | Primary v0.5.0 workstream (may delay template polish / drift) |
| 2 | Pi-hole **v6 REST only** |
| 3 | One Pi-hole marked **primary**; DNS/CNAME CRUD fans out to **all enabled** Pi-holes |
| 4 | NPM proxy hosts **read-only**; multi-instance schema-ready |
| 5 | Cert sources: **npm** pull + **upload** (cleartext PEM → Fernet at rest) |
| 6 | Full cert ops: expiry ≤21d → NPM renew → poll 3m×5 → distribute targets |
| 7 | LE stays in NPM; PiHerder does not run ACME |

## Surfaces

| Area | Path |
|------|------|
| Integrations list | `/integrations` (+ Pi-hole summary strip) |
| Pi-hole detail | `/integrations/{id}` tabs: Overview, Local DNS, CNAME, Actions |
| NPM detail | Proxy hosts (bind), Certificates (pull) |
| Certificates | `/certificates`, `/certificates/upload`, `/certificates/{id}` |

## Data

- `Integration` types: `pihole`, `npm`
- `ManagedCertificate`, `CertificateTarget` (migration `017`)
- Herder self-backup includes cert rows (encrypted fields need same master key)

## Success criteria

- [x] Multi Pi-hole connect, stats poll, deep links, multi summary  
- [x] Primary DNS/CNAME fan-out with per-instance results  
- [x] Gravity / Restart DNS / Flush network (audited)  
- [x] NPM connect; proxy hosts RO + bind  
- [x] Cert pull from NPM + PEM upload  
- [x] Targets; deploy pair/combined/pfx; perms; post-deploy command  
- [x] Auto-renew schedule (6h) for NPM-sourced certs  
- [x] Herder backup includes certs  
- [x] pytest coverage for adapters + PEM parse  
- [x] **Network maps / DNS fabric** (follow-on): host DNS identity, service mappings, Pi-hole adopt, Hosts + Path maps, LAN/gateway/public IP + Kuma infra monitors — see PLAN § F.1 + wiki  

## File map

| Area | Path |
|------|------|
| Adapters | `app/services/integrations/pihole.py`, `npm.py` |
| Certs | `app/services/certificates.py` |
| Routers | `app/routers/integrations.py`, `certificates.py`, `dns.py` |
| Network / fabric | `app/services/dns_fabric.py`, `app/static/js/fabric-mesh.js`, `dns_*.html` |
| UI | `integrations_pihole_*.html`, `integrations_npm_*.html`, `certificates_*.html` |
| Migration | `017_pihole_npm_certs` (+ host DNS / service DNS migrations as landed) |
| Tests | `tests/test_integrations_pihole.py`, `test_integrations_npm.py`, `test_certificates.py`, `test_dns_fabric.py` |

**End of Feature Plan**
