# PiHerder v1.0.0

**Status:** **Tagged** — first production release  
**Date:** 2026-07-28  
**Git tag:** `v1.0.0`  
**Package / image version:** `1.0.0`  
**Baseline:** `v0.9.0` (last pre-production)  
**Theme:** **First production release** — security hardening · authz · validation · credential recovery · known-issue burn-down · operator docs

**Plans:** [PLAN_v1.0.0.md](PLAN_v1.0.0.md)  
**Prior:** [RELEASE_v0.9.0.md](RELEASE_v0.9.0.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md)  
**Docs:** https://piherder-docs.hacknow.info/

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) — multi-arch `linux/amd64` + `linux/arm64`  
**Tags:** `1.0.0` · `1.0` · `latest`

---

## Highlights

### First production bar

- **Secure defaults** — cookie flags (`HttpOnly` / `SameSite=Lax` / `Secure` when HTTPS), same-origin POST guard, tighter auth rate limits, weak `SECRET_KEY` startup warning  
- **Auth entry** — unauthenticated `/` → login (no empty dashboard tease)  
- **Authorization (AC)** — session required on Docker log SSE + build SSE (build = operator+); RBAC matrix tests  
- **Input validation (AV)** — shared sink validators (paths, hostnames, SSH users, cron, docker actions, cert maps)  
- **Admin credential recovery** — UI **Users → Recover…** (temp password, clear 2FA, full access reset, force sign-out via `session_version`). No email required  
- **Host lockout recovery** — sole-admin path: `python -m app.cli.recover_admin` / `./scripts/recover-admin.sh` (Docker exec); wiki [Locked out](../wiki/troubleshooting/locked-out.md)  
- **Trusted devices** — trust survives logout; per-account cookie; no duplicate rows when re-trusting the same browser  
- **Avatars** — cache-safe per-user image URLs; letter fallback when file missing  
- **Known-issue burn-down** — Docker bfcache wait modal, map second-click unlock, brand-in-buttons, Kuma coverage mobile cards, mute/unmute chrome, NPM certs mobile dense list, DNS records clarity  

### From 0.9 (still first-class)

- HAOS path 1 (SSH / `ha` CLI), LAN Discovery, Network maps, templates OOTB/Yours, dense lists, self-backup DR, PWA/push, REST API tokens  

---

## Upgrade from v0.9.0

1. **Self-backup** (Settings) and/or volume snapshot.  
2. Keep the same **`PIHERDER_MASTER_KEY`**.  
3. Pull the production image:

   ```bash
   docker compose pull
   # or pin:
   PIHERDER_IMAGE=bjorngluck/piherder:1.0.0 docker compose up -d
   ```

4. `docker compose up -d` — Alembic applies migrations (includes `User.session_version`).  
5. Smoke: login, Users recovery (if multi-user), trusted device + avatar if you use them, one server action, optional template.

Full checklist: [ADMIN.md](ADMIN.md) · wiki [Upgrades](../wiki/operations/upgrades.md).

---

## Install (new)

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v1.0.0
cp .env.example .env   # set PIHERDER_MASTER_KEY, SECRET_KEY, etc.
export PIHERDER_IMAGE=bjorngluck/piherder:1.0.0
docker compose up -d
```

Open the app URL → **Register** the first admin (no default password).  
Guide: [Install](https://piherder-docs.hacknow.info/getting-started/install/).

---

## Verify (operator)

- [x] Login / logout; 2FA path if enabled  
- [x] Trusted device: trust → logout → login skips 2FA (password still required)  
- [x] Avatar upload per account (no cross-account cache bleed)  
- [x] Users: create user, **Recover…** → reset password / clear 2FA / sign out sessions / reset access  
- [x] Host recovery: `./scripts/recover-admin.sh list`  
- [x] Viewer cannot mutate fleet; admin-only Users  
- [x] Docker logs stream requires session; build stream operator+  
- [x] DNS hub records legend + coverage mobile stack  
- [x] Self-backup download/restore still works with same master key  

---

## Residual / post-1.0

| Item | Destination |
|------|-------------|
| Full cert distribute wizard + discovery hygiene + operator elevation | **v1.1** — [PLAN_v1.1.0.md](PLAN_v1.1.0.md) (streams A–D, G, I) |
| Email / self-service password reset | **v1.2** path (needs email) |
| SSO / OIDC, multi-tenant ACLs | **v1.3** path |
| Coverage fail-under 60% | Not a 1.0 gate (hold ≥55%) |

Active train: branch **`v1.1.0-dev`** · [PLAN_v1.1.0.md](PLAN_v1.1.0.md).

---

## Developer notes

- Unit coverage hold **≥ 55%** (`--cov-fail-under=55`)  
- Tests: `test_security_v10`, `test_authz_matrix_v10`, `test_input_validation_v10`, `test_admin_credential_recovery_v10`, `test_recover_admin_cli_v10`, `test_avatar_and_trusted_device_v10`  
- Package version: `pyproject.toml` + `app/version_info.py` → **1.0.0**  
- Publish: [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md)  

---

## Changelog summary

Product work since `v0.9.0` is the production hardening train on `main`.  
Full history: `git log v0.9.0..v1.0.0`. Plan: [PLAN_v1.0.0.md](PLAN_v1.0.0.md).
