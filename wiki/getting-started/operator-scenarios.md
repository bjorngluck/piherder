# Operator scenarios

Quick map from **what you want to do** → the right wiki page, plus **end-to-end journeys** that string pages together.

Use this after [Install](install.md) and [First login](first-login.md).

!!! note "RC1 documentation"
    Journeys describe the **intended** operator path. If a step feels unfinished in the UI, note it while reviewing with screenshots — [Home RC1 notice](../index.md#rc1).

---

## End-to-end journeys

These are the stories the rest of the wiki supports. Walk them on a lab host before production.

### Journey A — New instance to first healthy host {#journey-a}

**Goal:** PiHerder is up; one Pi is keyed and visible on the dashboard.

| Step | Action | Why |
|------|--------|-----|
| 1 | [Install](install.md) + secrets | Stack + encryption key for fleet secrets |
| 2 | [First login](first-login.md) | First account becomes admin; registration locks |
| 3 | Optional [HTTPS](https-tls.md) | Trust for browsers and later push |
| 4 | [Add a server](../day-to-day/add-server.md) | Generate key, deploy, test connection |
| 5 | **Edit → Features** | Enable only backups / OS / Docker you need |
| 6 | Open [Dashboard](../day-to-day/dashboard-and-services.md) | Confirm the host appears; no mystery tiles yet |

**Done when:** Server detail shows green-enough SSH/deps for enabled features; no password left stored if key auth works.

!!! note "Planned improvement"
    A **wizard-driven** add-host path (and bootstrap scripts / optional web SSH) is planned post-RC — [Host lifecycle plan](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_HOST_LIFECYCLE.md).

---

### Journey B — Safe backup before you depend on it {#journey-b}

**Goal:** One successful rsync backup and a dry-run restore you understand.

| Step | Action | Why |
|------|--------|-----|
| 1 | Enable **Backups** on the server | Feature flag gates UI and bulk |
| 2 | Add source paths on [Backups](../day-to-day/backups.md) | PiHerder only copies what you list |
| 3 | **Run backup now** | Proves Celery + rsync + path policy |
| 4 | Open [Jobs](../day-to-day/jobs-audit-notifications.md) | See pending → running → success |
| 5 | Restore wizard → **dry-run** | See reverse rsync without writing |
| 6 | Only then schedule cron | Avoid automated failure noise |

**Done when:** `last_backup_at` updates; Audit shows request → complete; you know where files land on the herder host ([Volumes](../operations/volumes.md)).

---

### Journey C — Patch without silent upgrades {#journey-c}

**Goal:** Know what needs attention; apply only when you choose.

| Step | Action | Why |
|------|--------|-----|
| 1 | Enable **OS patch** and/or **Docker** | Flags unlock check/apply |
| 2 | Run **check** only ([Updates](../day-to-day/updates-and-patching.md)) | Safe: counts packages/images, no upgrade |
| 3 | Read Dashboard “need attention” | Aggregate view of the fleet |
| 4 | Manual apply on one host | Live log; exclusive job per type |
| 5 | Optional: schedule **checks** weekly | Always-on awareness |
| 6 | Optional later: schedule **apply** with “only if updates” | Never silent default auto-upgrade |

**Done when:** You can explain check vs apply, and Jobs shows who/what for both.

---

### Journey D — Deploy a known stack from a template {#journey-d}

**Goal:** Uptime Kuma (or NPM / Pi-hole / Grafana) runs from Catalog → Templates.

| Step | Action | Why |
|------|--------|-----|
| 1 | Host has **Docker** feature + working `docker` | Deploy target |
| 2 | [Templates overview](../service-templates/overview.md) → OOTB pack | Versioned recipe, not a one-off paste |
| 3 | [Deploy wizard](../service-templates/deploy.md) | Variables, host, preview, wait modal |
| 4 | Post-deploy checklist | DNS, first login, bind integrations |
| 5 | Optional: [Kuma integration](../integrations/uptime-kuma.md) | Status in fleet Services |
| 6 | Later: Check drift / redeploy | Desired state stays authoritative |

**Done when:** Deployment page exists; Docker shows the project; secrets only via step-up if required.

---

### Journey E — Homelab map (DNS + proxy + certs) {#journey-e}

**Goal:** Names, paths, and TLS are visible and mostly automated.

| Step | Action | Why |
|------|--------|-----|
| 1 | [Pi-hole](../integrations/pihole.md) + optional [NPM](../integrations/npm.md) | DNS truth + edge proxy inventory |
| 2 | Host **Edit → General** FQDN + manage A | A records fan out to Pi-holes |
| 3 | [Network maps](../integrations/dns-fabric.md) settings | LAN/gateway so Hosts map is readable |
| 4 | Adopt / import names from Pi-hole | Bring existing lab DNS into PiHerder |
| 5 | [Certificates](../integrations/certificates.md) pull or upload | Vault + maps → deploy to hosts |
| 6 | Dashboard network panel | Cheap pulse of named/mapped/NPM counts |

**Done when:** Hosts map shows home vs cloud; a cert map can redeploy PEMs after renew.

---

### Journey F — Disaster recovery for PiHerder itself {#journey-f}

**Goal:** You can rebuild the control plane without losing fleet config.

| Step | Action | Why |
|------|--------|-----|
| 1 | Offline copy of `PIHERDER_MASTER_KEY` | Encrypted fields useless without it |
| 2 | [Self-backup](../operations/self-backup.md) run once | Archive of DB config, users, keys, templates |
| 3 | Schedule herder backup | Regular DR hygiene |
| 4 | Know [Volumes](../operations/volumes.md) | What is in `piherder_backups` vs fleet `backups` |
| 5 | Practice dry-run restore on a lab stack | Confidence before a real outage |

**Done when:** Master key + at least one archive live **off** the herder host; restore dry-run understood.

---

### Journey G — Second operator with least privilege {#journey-g}

**Goal:** Someone else can run fleet jobs without full admin.

| Step | Action | Why |
|------|--------|-----|
| 1 | [Roles](../account-security/roles.md) | viewer vs operator vs admin |
| 2 | [Users](../account-security/users.md) create operator | Invite + one-time password |
| 3 | Optional [force 2FA](../account-security/two-factor.md) | Policy for the whole instance |
| 4 | Operator runs a backup/check | Confirms RBAC allows fleet mutate |
| 5 | Confirm they cannot open herder restore | Control plane stays admin-only |

---

## First week (quick links)

| Scenario | Doc |
|----------|-----|
| Compose install + secrets | [Install](install.md) |
| Register **first** admin (no default user), then invite others | [First login](first-login.md) · [Users](../account-security/users.md) |
| Trusted HTTPS for PWA/push | [HTTPS & TLS](https-tls.md) |
| Light / dark theme | [Appearance](appearance.md) |
| About PiHerder / GitHub / new version notice | Avatar menu → **About** (`/about`) |
| Add a Pi, deploy SSH key, features | [Add a server](../day-to-day/add-server.md) |
| Dashboard tiles & fleet services | [Dashboard & Services](../day-to-day/dashboard-and-services.md) |

## Backups & patching

| Scenario | Doc |
|----------|-----|
| Configure rsync sources + schedule | [Backups](../day-to-day/backups.md) |
| Restore path (dry-run then apply) | [Backups](../day-to-day/backups.md) |
| Check OS / images only | [Updates](../day-to-day/updates-and-patching.md) |
| Apply OS or container patch | [Updates](../day-to-day/updates-and-patching.md) |
| Bulk check/patch/backup many hosts | [Updates — Bulk](../day-to-day/updates-and-patching.md#bulk-actions-servers-list) |
| Reboot without hanging the UI | [Updates — Reboot](../day-to-day/updates-and-patching.md#reboot) |
| See job progress / cancel | [Jobs](../day-to-day/jobs-audit-notifications.md) |
| Who did what (incl. client IP) | [Audit](../day-to-day/jobs-audit-notifications.md) |

## Docker on hosts

| Scenario | Doc |
|----------|-----|
| Browse projects / containers / logs | [Docker overview](../docker/overview.md) |
| Fast open via inventory cache | [Inventory](../docker/inventory.md) |
| Edit compose, validate, deploy version | [Compose edit](../docker/compose-edit.md) |
| Stack Check updates vs Deploy | [Updates — Docker](../day-to-day/updates-and-patching.md#docker-check-updates-vs-deploy) |
| Prune dangling images / exited containers | [Docker overview](../docker/overview.md) |
| **Planned:** Stop/Start/Restart **all** services in a project | Post-RC — [FEATURE_PLAN_HOST_LIFECYCLE.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_HOST_LIFECYCLE.md) P1 |

## Service templates

| Scenario | Doc |
|----------|-----|
| Deploy OOTB NPM / Kuma / Pi-hole / Grafana | [Deploy](../service-templates/deploy.md) |
| Redeploy, volumes, wait modal | [Deploy — Redeploy](../service-templates/deploy.md#redeploy-ops-deployment-page) |
| Check drift / import host `.env` / apply last known config | [Deploy — ops](../service-templates/deploy.md#redeploy-ops-deployment-page) |
| Pull existing stack into a template | [From host](../service-templates/from-host.md) |
| View secrets (step-up 2FA) | [Secrets](../service-templates/secrets.md) |

## Catalog: integrations & network

| Scenario | Doc |
|----------|-----|
| Catalog tabs (Integrations / Certificates / Templates / Network) | [Integrations overview](../integrations/overview.md) |
| Connect Kuma + bind services | [Uptime Kuma](../integrations/uptime-kuma.md) |
| Grafana deep links + preferred names | [Grafana](../integrations/grafana.md) |
| Multi Pi-hole, DNS fan-out, gravity | [Pi-hole](../integrations/pihole.md) |
| Host A records, service paths, Hosts/Path maps, runtime stack expand + container order | [Network maps](../integrations/dns-fabric.md) |
| Switch light/dark; ops-hero layout | [Appearance](appearance.md) |
| NPM proxy hosts (read-only) + pull cert | [NPM](../integrations/npm.md) |
| Cert vault, maps, deploy, renew | [Certificates](../integrations/certificates.md) |

## Account, users, security

| Scenario | Doc |
|----------|-----|
| Viewer / operator / admin | [Roles](../account-security/roles.md) |
| Create users, roles | [Users](../account-security/users.md) |
| TOTP, backup codes, force 2FA | [2FA](../account-security/two-factor.md) |
| Install PWA + Web Push | [PWA & push](../account-security/pwa-push.md) |

## Operations & DR

| Scenario | Doc |
|----------|-----|
| `.env` keys | [Env reference](../operations/env-reference.md) |
| Volume mounts | [Volumes](../operations/volumes.md) |
| Settings tabs (backup, security, status, timezone, API) | [Settings](../operations/settings.md) |
| Stack Status healthy? | [Status](../operations/status.md) |
| Herder self-backup / restore (**admin only**) | [Self-backup](../operations/self-backup.md) · [Roles](../account-security/roles.md) |
| Upgrade compose / image | [Upgrades](../operations/upgrades.md) |
| More backup parallelism | [Multi-worker](../operations/multi-worker.md) |
| Prometheus / webhook | [Metrics](../operations/metrics-webhooks.md) |
| Token REST for n8n/HA | [API tokens](../operations/api-tokens.md) |

## When things go wrong

| Scenario | Doc |
|----------|-----|
| SSH / rsync / docker group | [SSH troubleshooting](../troubleshooting/ssh-rsync.md) |
| Backup stuck | [Backup troubleshooting](../troubleshooting/backups.md) |
| Push not arriving | [Push troubleshooting](../troubleshooting/push.md) |
| Template / compose editor | [Templates troubleshooting](../troubleshooting/templates-docker.md) |
| Symptom table | [Troubleshooting index](../troubleshooting/index.md) |
