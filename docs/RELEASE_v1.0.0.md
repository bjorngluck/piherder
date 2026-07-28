# PiHerder v1.0.0

**Status:** **Production-ready** (feature-complete on `main`; tag/Hub publish after operator freeze)  
**Date:** 2026-07-28  
**Git tag:** `v1.0.0` *(at publish)*  
**Baseline:** `v0.9.0` (last pre-production)  
**Theme:** **First production release** — security hardening · authz · validation · credential recovery · known-issue burn-down · operator docs

**Plans:** [PLAN_v1.0.0.md](PLAN_v1.0.0.md)  
**Prior:** [RELEASE_v0.9.0.md](RELEASE_v0.9.0.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags (at publish):** `1.0.0` · `1.0` · `latest`

---

## Highlights

### First production bar

- **Secure defaults** — cookie flags (`HttpOnly` / `SameSite=Lax` / `Secure` when HTTPS), same-origin POST guard, tighter auth rate limits, weak `SECRET_KEY` startup warning  
- **Auth entry** — unauthenticated `/` → login (no empty dashboard tease)  
- **Authorization (AC)** — session required on Docker log SSE + build SSE (build = operator+); RBAC matrix tests  
- **Input validation (AV)** — shared sink validators (paths, hostnames, SSH users, cron, docker actions, cert maps)  
- **Admin credential recovery** — UI **Users → Recover…** (temp password, clear 2FA, full access reset, force sign-out via `session_version`). No email required  
- **Host lockout recovery** — sole-admin path: `python -m app.cli.recover_admin` / `./scripts/recover-admin.sh` (Docker exec); wiki [Locked out](../wiki/troubleshooting/locked-out.md)  
- **Known-issue burn-down** — Docker bfcache wait modal, map second-click unlock, brand-in-buttons, Kuma coverage mobile cards, mute/unmute chrome, NPM certs mobile dense list, DNS records clarity  

### From 0.9 (still first-class)

- HAOS path 1 (SSH / `ha` CLI), LAN Discovery, Network maps, templates OOTB/Yours, dense lists, self-backup DR, PWA/push, REST API tokens  

---

## Upgrade from v0.9.0

1. **Self-backup** (Settings) and/or volume snapshot.  
2. Keep the same **`PIHERDER_MASTER_KEY`**.  
3. Pull / rebuild image for **1.0.0** (or rebuild `main` until Hub tags land).  
4. `docker compose up -d` — Alembic applies migrations (includes `User.session_version`).  
5. Smoke: login, Users recovery actions, Docker logs stream, force-2FA if enabled.  

Full checklist: [ADMIN.md — production](ADMIN.md) · wiki [Upgrades](../wiki/operations/upgrades.md).

---

## Verify (operator)

- [ ] Login / logout; 2FA path if enabled  
- [ ] Users: create user, **Recover…** → reset password / clear 2FA / sign out sessions / reset access  
- [ ] Host recovery (optional): `./scripts/recover-admin.sh list` then `reset-access --email … --generate --yes` on a test admin  
- [ ] Viewer cannot mutate fleet; admin-only Users  
- [ ] Docker logs stream requires session; build stream operator+  
- [ ] DNS hub records legend + coverage mobile stack  
- [ ] Self-backup download/restore still works with same master key  

---

## Residual / post-1.0

| Item | Destination |
|------|-------------|
| Email / self-service password reset | v1.1+ |
| Full cert distribute wizard | v1.1 |
| Discovery last-seen / purge polish | v1.1 |
| SSO / OIDC, multi-tenant ACLs | Post-1.0 |
| Coverage fail-under 60% | Not a 1.0 gate (hold ≥55%) |

---

## Developer notes

- Unit coverage hold **≥ 55%** (`--cov-fail-under=55`)  
- Tests: `test_security_v10`, `test_authz_matrix_v10`, `test_input_validation_v10`, `test_admin_credential_recovery_v10`  
- Package version: `pyproject.toml` + `app/version_info.py` → **1.0.0** at tag  

---

## Changelog summary

Product work since `v0.9.0` is the production hardening train on `main`. Full history: `git log v0.9.0..v1.0.0` (after tag). Plan: [PLAN_v1.0.0.md](PLAN_v1.0.0.md).
