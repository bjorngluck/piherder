# Troubleshooting

## What this is

A **symptom → page** map when something fails. Start here, then open Jobs, Audit, and Settings → Status before deep host dives.

## Why this section

RC line still has rough edges. Most failures cluster around SSH path, Celery/backups, push TLS, or template/Docker paths — the linked pages list concrete checks.

| Symptom | Page |
|---------|------|
| SSH / key / deps / docker group | [SSH, rsync & dependencies](ssh-rsync.md) |
| HAOS: no `ha`, versions `?`, disk empty | [HAOS hosts](../day-to-day/haos-hosts.md) · System info **Refresh**; SSH add-on + `ha` on PATH; rsync for backups |
| HAOS OS check says unsupported / apt | Set host profile **HAOS** or re-run check to auto-mark; rebuild web if image stale |
| Backup failed / stuck pending | [Backups](backups.md) |
| No push on phone / PWA | [Push / PWA](push.md) |
| Template deploy / Docker editor | [Templates & Docker](templates-docker.md) |
| From-host missing config sidecar / host labels | [From host](../service-templates/from-host.md) · [Templates troubleshooting](templates-docker.md#from-host-pull-incomplete) |
| Reboot hangs / UI stuck after reboot | [Updates — Reboot](../day-to-day/updates-and-patching.md#reboot) |
| Same patch job appears twice | [Jobs — Exclusive jobs](../day-to-day/jobs-audit-notifications.md#exclusive-jobs-one-per-type-per-host) · [Multi-worker](../operations/multi-worker.md) |
| Full editor link does nothing | [Compose edit](../docker/compose-edit.md#opening-the-editor) — use ⋯ **Full editor…** |
| Fleet Services empty | [Dashboard & Services](../day-to-day/dashboard-and-services.md) — bind Kuma monitors |
| Network map hosts not linked / cloud wrong | [Network maps](../integrations/dns-fabric.md) — set LAN/gateway/public IP; hard-refresh after rebuild |
| Hosts map focus won’t clear on second click | Hard-refresh for latest `fabric-mesh.js`; click same node again or **Clear focus** |
| Stack deps float away when Discovered is off | Hard-refresh `fabric-stack-expand.js`; fan re-anchors to compact layout |
| Server page slow / clicks lag 5–10s | Fixed: no live Pi-hole probe on every server detail paint; Backups no longer recursive `du` on load — rebuild/restart web |
| Device edit closes to Integrations instead of server | Open from the **LAN chip** on that host (includes `return=server:…`); Cancel/Save returns to the server |
| Layout stuck after phone rotate (esp. Network) | Hard-reload once after deploy; maps should reflow without leaving the page ([Appearance](../getting-started/appearance.md)) |
| Dashboard NPM hosts ≠ NPM proxy count | Dashboard uses poll `proxy_host_count`; poll NPM integration if stale |
| Cert deploy / renew failed | [Certificates](../integrations/certificates.md) · Jobs + Audit |
| `sudo: I'm sorry piherder…` / post-deploy denied | [Cert sudo denied](../integrations/certificates.md#cert-sudo-denied) — add NOPASSWD; match post-deploy exactly |
| Drift after host edit | [Deploy — Check drift](../service-templates/deploy.md#redeploy-ops-deployment-page) |
| Stack unhealthy after upgrade | [Status](../operations/status.md) · [Upgrades](../operations/upgrades.md) |
| Cannot open Settings tabs / herder restore | [Roles](../account-security/roles.md) — control plane is **admin only** |
| First boot asks to register / no default password | Expected — [First login](../getting-started/first-login.md) |

## Always useful

```bash
docker compose ps
docker compose logs web --tail=200
docker compose logs celery-worker --tail=200
```

- **Jobs** page for work unit status + log tail  
- **Audit** for who/what / client IP  
- **Settings → Status** for stack health ([Settings](../operations/settings.md))  
- Server **SSH access → Check dependencies** (or **Test connection**) for remote tools  
- Scenario index: [Operator scenarios](../getting-started/operator-scenarios.md)  

