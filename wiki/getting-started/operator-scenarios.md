# Operator scenarios (index)

Quick map from **what you want to do** → the right wiki page. Use this after [Install](install.md) and [First login](first-login.md).

## First week

| Scenario | Doc |
|----------|-----|
| Compose install + secrets | [Install](install.md) |
| Register admin, change password | [First login](first-login.md) |
| Trusted HTTPS for PWA/push | [HTTPS & TLS](https-tls.md) |
| Light / dark theme | [Appearance](appearance.md) |
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
| Host A records, service paths, Hosts/Path maps (incl. mobile Hide map / Full screen / rotate) | [Network maps](../integrations/dns-fabric.md) |
| Switch light/dark; ops-hero layout; portrait↔landscape | [Appearance](appearance.md) |
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
| Herder self-backup / restore | [Self-backup](../operations/self-backup.md) |
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
