# PiHerder v0.4.0

**Date:** 2026-07-12  
**Git tag:** `v0.4.0`  
**Baseline:** `v0.3.0`  
**Theme:** Service templates foundation + post-0.3 quality (Docker deploy honesty, jobs, notifications)

**Plans:** [PLAN_v0.4.0.md](PLAN_v0.4.0.md) · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md)  
**Roadmap:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) Phase 6

Image registry publish (Docker Hub / GHCR) remains optional; operators build with `docker compose up -d --build`. See [PUBLISH_IMAGE.md](PUBLISH_IMAGE.md).

---

## Highlights

### Service templates (Horizon 2 / Phase 1)

- Top-level **Templates** nav: operator-owned catalog (create, edit, save)
- **Deploy wizard:** variables → Docker-enabled host → preview → confirm → audit
- **Desired state V1** per host+project (`stack_deployments`): Fernet-encrypted secrets, versioned config, redeploy
- **Variable types:** string, port, password, int, url, email, **boolean**, **volume**
  - Volumes: named Docker volume, folder under project (`./…`), or absolute host path
- **From host:** pull live compose + `.env`; parameterize volumes, ports, booleans, secrets into a new template
- **OOTB pack:** NPM, Uptime Kuma, Pi-hole, Grafana (volume-aware defaults)
- **Secrets (home production):**
  - PiHerder = encrypted source of truth
  - Host gets locked-down **`.env`** (`chmod 600`); restarts offline without PiHerder
  - **Step-up 2FA** to view cleartext secrets (not satisfied by login 2FA alone)
  - Optional Settings flag: require 2FA for template deploy / secrets
- **Docker UX:** template-managed badge; full compose editor gated for template stacks
- **Wait modal** on preview, confirm deploy, redeploy, and from-host pull
- Post-deploy **checklist** (manual DNS, first login, …)
- Builtin starters under `service_templates/`; pure `builtin` rows refresh from disk; **Save** → `user` (never auto-overwritten)

### Post–v0.3.0 quality (B01–B06)

| ID | Fix |
|----|-----|
| **B01** | Docker Deploy records pull/up exit codes + output; success/fail banner (not silent success) |
| **B02** | Successful Deploy clears pending stack updates and resolves `container_updates` when none remain |
| **B03** | UI: **Check updates** = pull only; **Deploy** = pull + `up -d` |
| **B04** | Jobs list **Cancel** works |
| **B05** | Successful backup always resolves open `backup_failed` alert |
| **B06** | Notification dismiss is idempotent if already closed |

### Carried from v0.3.0

- Integration hub: **Uptime Kuma** + **Grafana**
- IAM/2FA, PWA + push, herder self-backup, token REST API, Status tab, host deps, multi-worker Celery

---

## Intentionally not in v0.4.0

Template **polish** and deeper ops land after this freeze:

| Horizon | Items |
|---------|--------|
| **v0.4.x** | Config drift schedule · git template catalog · NPM integration connector · `.env` migrate UX · optional Docker/template Jobs with live log (B07 stretch) |
| **v0.5.0** | Template UX polish · restore + last known config · production wikis · Docker Hub multi-arch · RC freeze |
| **Later** | Advanced secret backends · automated DNS · expanded curated pack |

Open stretch bugs (not required for this tag): **B07** (async Docker deploy jobs), **B08** (logos in self-backup), **B09** (push on auto-resolve).

---

## Breaking / migration notes

- **New Alembic migration** `016_service_templates` — creates `servicetemplate` + `stackdeployment` (runs on app start / usual upgrade path).
- Restore of encrypted template secrets / deployment secrets requires the **same `PIHERDER_MASTER_KEY`**.
- Herder self-backup now includes template catalog rows and stack deployments (ciphertext only).
- Existing Docker projects are unchanged until you deploy a template or mark desired state via the wizard.
- Operators who edited a builtin template in the UI (`source=user`) keep their copy; disk starter refresh applies only to untouched `source=builtin` rows.

---

## Install

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.4.0
cp .env.example .env
# set PIHERDER_MASTER_KEY (Fernet) and other required vars
docker compose up -d --build
```

### Upgrade from v0.3.0

```bash
git fetch --tags
git checkout v0.4.0
docker compose up -d --build
```

Migrations apply on startup. Open **Templates** to seed builtin packs (NPM, Kuma, Pi-hole, Grafana).

Details: [README.md](../README.md) · [ADMIN.md](ADMIN.md) § Service templates · [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md)

---

## Package version

`pyproject.toml` → **`0.4.0`**

---

## Docs & tests

| Doc | Role |
|-----|------|
| [ADMIN.md](ADMIN.md) | Operator guide — templates, secrets step-up, from-host, deploy wait modal |
| [FEATURE_PLAN_TEMPLATES.md](FEATURE_PLAN_TEMPLATES.md) | Schema, sources, security model |
| [PLAN_v0.4.0.md](PLAN_v0.4.0.md) | Ship bar / bug list (frozen at tag) |
| [SPEC.md](../SPEC.md) | Phase 6 checklist |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Multi-horizon roadmap |

**Tests:** `tests/test_service_templates.py`, `tests/test_env_file_ui.py` (render, volumes/booleans, from-host parameterize, secrets redaction).

---

## Commits since v0.3.0

```text
git log --oneline v0.3.0..v0.4.0
```

| Commit | Summary |
|--------|---------|
| `ba56c2b` | fix(docker): surface deploy pull/up results |
| `d33f286` | fix(docker): resolve container update alert after deploy |
| `538e7e2` | docs: draft PLAN_v0.4.0 |
| `069b065` | fix: jobs list cancel + backup-failed dismiss/resolve |
| `4686009` | docs: track post-0.3 fixes for release notes |
| `c8463c5` | feat(templates): service templates v1 (volumes, step-up secrets, wait modal) |
| *(this release)* | docs + version freeze `0.4.0` |

---

## Verify after upgrade

1. `docker compose ps` — web healthy  
2. **Templates** — four OOTB packs listed  
3. Deploy a lab stack (e.g. Uptime Kuma) — wait modal appears; host project + locked `.env`  
4. **View secrets** requires step-up 2FA  
5. Docker page shows **Template** badge on managed project  
6. Jobs **Cancel** still works; Docker Deploy still shows pull/up result banner  

---

**End of v0.4.0 release notes.**
