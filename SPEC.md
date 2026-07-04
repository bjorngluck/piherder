# PiHerder Specification & Roadmap

> **Repository:** [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder)  
> **Status:** v0.1.0 — Phase 1 largely complete  
> **Last updated:** 2026-07-04

This document is the canonical spec for PiHerder. Use it to track work in a [GitHub Project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects) — each unchecked item below maps cleanly to an issue or project card.

---

## Vision

PiHerder is a self-hosted fleet manager for Raspberry Pi (and other Linux) clusters. It replaces brittle cron + bash scripts with an auditable web UI while keeping SSH keys encrypted at rest and never storing plaintext secrets.

**Design principles**

- Replicate battle-tested shell-script behaviour exactly (backups, container patching, OS patching).
- Work offline / air-gapped once built (vendored frontend assets, no runtime CDN deps).
- Every privileged action is audited with user, server, status, and output snippet.
- Secrets decrypted only in memory for the duration of a job.

---

## Phase 1 — Core fleet management (v0.1) ✅

| Area | Status | Notes |
|------|--------|-------|
| SSH keypair generation & upload | ✅ | Fernet-encrypted at rest |
| Server CRUD + manual ordering | ✅ | |
| Per-server feature toggles | ✅ | Backups, OS patch, container patch |
| rsync backups over SSH | ✅ | Multi-source paths, dest overrides |
| Backup retention / cleanup | ✅ | |
| Per-server backup schedules | ✅ | APScheduler cron |
| Container patching | ✅ | `compose pull` + conditional `up -d` |
| OS patching (apt sequence) | ✅ | Reboot-required detection |
| Diagnostics | ✅ | ping, DNS, system info |
| Audit log + filtering | ✅ | |
| PiHerder self-backup & restore | ✅ | Compressed archives, optional audit |
| HTTPS via Caddy | ✅ | Non-standard ports 8888/8443 for co-existence |
| Pi-hole admin link | ✅ | Configurable `PIHOLE_URL` |
| Offline-ready frontend | ✅ | Vendored Tailwind, HTMX, Alpine |
| Docker Compose project browser | ✅ | List, redeploy, build, logs |
| Compose file editing + versioning | ✅ | Drafts, deploy, rollback |
| New Docker project wizard | ✅ | |
| User auth (register / login) | ✅ | Single-user v1 |

---

## Phase 2 — Scheduling, API & polish

- [ ] Built-in scheduler UI for container patch and OS patch jobs (backup scheduling exists)
- [ ] REST API for all job triggers with token auth (partial — some endpoints exist)
- [ ] Webhook / notification integration wired end-to-end
- [ ] Per-server container-patch and OS-patch cron schedules
- [ ] Job queue visibility (running / queued / history per server)
- [ ] Alembic migrations replace runtime `ALTER TABLE` hacks
- [ ] Test suite (pytest) for backup, patching, and encryption paths
- [ ] Pre-built Docker Hub image published and documented
- [ ] `docker-compose` example with sensible defaults (no `~/` bind-mount assumptions)

---

## Phase 3 — Multi-user & advanced Docker

- [ ] Role-based access (admin / operator / read-only)
- [ ] Multi-user audit attribution
- [ ] Compose multi-file project support (override files, env files in UI)
- [ ] Image update notifications (digest comparison, changelog links)
- [ ] Fleet-wide dashboard (patch status across all servers)
- [ ] Backup restore wizard (select snapshot → restore paths)
- [ ] Rate limiting on auth endpoints
- [ ] Optional 2FA

---

## Phase 4 — Ecosystem

- [ ] Ansible / cloud-init bootstrap for new Pis
- [ ] Prometheus metrics exporter
- [ ] Mobile-friendly responsive pass
- [ ] Plugin hooks for custom job types

---

## Architecture

```
┌─────────────┐     HTTPS      ┌──────────┐
│   Browser   │ ──────────────▶│  Caddy   │
└─────────────┘                └────┬─────┘
                                    │
                              ┌─────▼─────┐
                              │  FastAPI  │
                              │  (web)    │
                              └─────┬─────┘
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌──────▼──────┐  ┌─────▼─────┐
              │ PostgreSQL │  │ APScheduler │  │  Paramiko │
              │   (db)     │  │  (cron)     │  │  (SSH)    │
              └────────────┘  └─────────────┘  └─────┬─────┘
                                                     │
                              ┌──────────────────────┼──────────────────────┐
                              │                      │                      │
                        ┌─────▼─────┐          ┌─────▼─────┐          ┌─────▼─────┐
                        │  Pi #1    │          │  Pi #2    │          │  Pi #N    │
                        │ docker    │          │ docker    │          │ docker    │
                        └───────────┘          └───────────┘          └───────────┘
```

**Stack:** FastAPI · SQLModel · PostgreSQL · Paramiko · cryptography (Fernet) · Jinja2 · Tailwind (vendored) · HTMX · Alpine · Caddy

---

## Security model

| Asset | Protection |
|-------|------------|
| `PIHERDER_MASTER_KEY` | Host `.env` only — never committed |
| SSH private keys | Fernet-encrypted in DB; decrypted in-memory per job |
| User passwords | bcrypt hashed |
| Sessions | JWT (HS256) |
| Transport | HTTPS via Caddy + Let's Encrypt (or self-signed for local) |

---

## Legacy script parity

PiHerder ports logic from these battle-tested scripts:

| Legacy script | PiHerder equivalent |
|---------------|---------------------|
| `backup_script.sh` | Per-server backup job |
| `backup_cleanup.sh` | Retention job |
| `docker-cluster-update.sh` | Container patch job |

Configurable per-server fields that map 1:1: `backup_paths`, `docker_base_dir`, `excluded_projects`, `retention_days`.

---

## Linking this spec to a GitHub Project

1. Push this repo to [github.com/bjorngluck/piherder](https://github.com/bjorngluck/piherder).
2. Create a new Project (user or org) on GitHub.
3. **Link the repository:** Project → Settings → Linked repositories → add `bjorngluck/piherder`.
4. **Create issues** from unchecked Phase 2–4 items above (copy title + acceptance criteria).
5. **Add issues to the project board** and group by Phase column or Milestone.
6. Pin `SPEC.md` in the repo README (already linked) for contributors.

---

## License

MIT — see [LICENSE](LICENSE).