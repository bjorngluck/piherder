# Troubleshooting

## What this is

A **symptom → page** map when something fails. Start here, then open Jobs, Audit, and Settings → Status before deep host dives.

## Why this section

Most failures cluster around SSH path, Celery/backups, push TLS, or template/Docker paths — the linked pages list concrete checks.

| Symptom | Page |
|---------|------|
| SSH / key / deps / docker group | [SSH, rsync & dependencies](ssh-rsync.md) |
| SSH “host key changed” / mismatch after rebuild | [SSH troubleshooting](ssh-rsync.md#cannot-connect) — reset the **pinned** key on SSH access, then Test connection |
| Web container exits immediately / weak `SECRET_KEY` | Set a long random `SECRET_KEY`. 1.2 **refuses boot** on compose defaults unless `PIHERDER_ALLOW_INSECURE` ([env](../operations/env-reference.md)) |
| HAOS: no `ha`, versions `?`, disk empty | [HAOS hosts](../day-to-day/haos-hosts.md) · System info **Refresh**; SSH add-on + `ha` on PATH; rsync for backups |
| HAOS OS check says unsupported / apt | Set host profile **HAOS** or re-run check to auto-mark; rebuild web if image stale |
| Backup failed / stuck pending | [Backups](backups.md) |
| No push on phone / PWA | [Push / PWA](push.md) |
| Template deploy / Docker editor | [Templates & Docker](templates-docker.md) |
| From-host missing config sidecar / host labels | [From host](../service-templates/from-host.md) · [Templates troubleshooting](templates-docker.md#from-host-pull-incomplete) |
| Reboot hangs / UI stuck after reboot | [Updates — Reboot](../day-to-day/updates-and-patching.md#reboot) |
| Same patch job appears twice | [Jobs — Exclusive jobs](../day-to-day/jobs-audit-notifications.md#exclusive-jobs-one-per-type-per-host) · [Multi-worker](../operations/multi-worker.md) |
| Full editor link does nothing | [Compose edit](../docker/compose-edit.md#opening-the-editor) — use ⋯ **Full editor…** or deployment **Open host file editor** |
| Drift after intentional host edit (keep change) | [Deploy — Accept host as desired](../service-templates/deploy.md#redeploy-ops-deployment-page) |
| Fleet Services empty | [Dashboard & Services](../day-to-day/dashboard-and-services.md) — bind Kuma monitors |
| Reports empty / history shorter than expected | [Reports](../day-to-day/reports.md) — needs finished Jobs / nmap runs / console Audit; [Cleanup](../operations/settings.md#stale-data-cleanup) can trim rows |
| Files dest-card missing / 404 | Flag `PIHERDER_HOST_FILES` (default off). Viewer 403. [Host Files](../day-to-day/host-files.md) |
| Files upload fails / too large | Default 512 MiB; stream through herder; raise `PIHERDER_HOST_FILES_MAX_BYTES` (ceiling 2 GiB) and any extra reverse-proxy body cap |
| Files download stuck ~12 MiB | Dedicated SFTP + Caddy must not gzip `application/octet-stream` (`flush_interval -1`). Rebuild web + Caddy. [Host Files](../day-to-day/host-files.md) |
| `.env` / PEM won’t open | Listing is allowed; open/download needs Passkey/TOTP (same grant as privileged Files). [Host Files](../day-to-day/host-files.md) |
| chmod/chown permission denied | Connect as privileged. If that user is not root, add NOPASSWD for `chmod`/`chown`. [Host Files](../day-to-day/host-files.md) |
| Docker volumes greyed out | Path is outside the current jail — **Connect as privileged**. `docker cp` copies **into** the current folder. |
| Network map hosts not linked / cloud wrong | [Network maps](../integrations/dns-fabric.md) — set LAN/gateway/public IP; hard-refresh after rebuild |
| Hosts map focus won’t clear on second click | Hard-refresh for latest `fabric-mesh.js`; click same node again or **Clear focus** |
| Stack deps float away when Discovered is off | Hard-refresh `fabric-stack-expand.js`; fan re-anchors to compact layout |
| Server page slow / clicks lag 5–10s | Fixed: no live Pi-hole probe on every server detail paint; Backups no longer recursive `du` on load — rebuild/restart web |
| Device edit closes to Integrations instead of server | Open from the **LAN chip** on that host (includes `return=server:…`); Cancel/Save returns to the server |
| Layout stuck after phone rotate (esp. Network) | Hard-reload once after deploy; maps should reflow without leaving the page ([Appearance](../getting-started/appearance.md)) |
| Dashboard NPM hosts ≠ NPM proxy count | Dashboard uses poll `proxy_host_count`; poll NPM integration if stale |
| Cert deploy / renew failed | [Certificates](../integrations/certificates.md) · Jobs + Audit |
| `sudo: I'm sorry piherder…` / post-deploy denied | [Cert sudo denied](../integrations/certificates.md#cert-sudo-denied) — add NOPASSWD; match post-deploy exactly |
| Drift after host edit (detect / revert) | [Deploy — Check drift / Apply last known](../service-templates/deploy.md#redeploy-ops-deployment-page) |
| Stack unhealthy after upgrade | [Status](../operations/status.md) · [Upgrades](../operations/upgrades.md) |
| Cannot open Settings tabs / herder restore | [Roles](../account-security/roles.md) — control plane is **admin only** |
| First boot asks to register / no default password | Expected — [First login](../getting-started/first-login.md) |
| Sole admin forgot password / lost 2FA / locked out | [Locked out / sole admin recovery](locked-out.md) — host CLI `recover-admin` |
| SSO / OIDC login fails or IdP down | [SSO / OpenID Connect](../account-security/sso-oidc.md) · [Locked out](locked-out.md) for break-glass |
| Console “too many shells” / idle disconnect | [Web SSH console](../day-to-day/web-ssh-console.md) · [Settings → Console](../operations/settings.md#console) |
| Console Settings fields read-only | A `PIHERDER_SSH_CONSOLE_*` env var is set (lock). Unset it or [env reference](../operations/env-reference.md) |

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

