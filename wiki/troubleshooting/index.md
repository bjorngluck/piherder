# Troubleshooting

| Symptom | Page |
|---------|------|
| SSH / key / deps / docker group | [SSH, rsync & dependencies](ssh-rsync.md) |
| Backup failed / stuck pending | [Backups](backups.md) |
| No push on phone / PWA | [Push / PWA](push.md) |
| Template deploy / Docker editor | [Templates & Docker](templates-docker.md) |
| Reboot hangs / UI stuck after reboot | [Updates — Reboot](../day-to-day/updates-and-patching.md#reboot) |
| Same patch job appears twice | [Jobs — Exclusive jobs](../day-to-day/jobs-audit-notifications.md#exclusive-jobs-one-per-type-per-host) · [Multi-worker](../operations/multi-worker.md) |
| Full editor link does nothing | [Compose edit](../docker/compose-edit.md#opening-the-editor) — use ⋯ **Full editor…** |
| Fleet Services empty | [Dashboard & Services](../day-to-day/dashboard-and-services.md) — bind Kuma monitors |
| Network map hosts not linked / cloud wrong | [Network maps](../integrations/dns-fabric.md) — set LAN/gateway/public IP; hard-refresh after rebuild |
| Cert deploy / renew failed | [Certificates](../integrations/certificates.md) · Jobs + Audit |
| Drift after host edit | [Deploy — Check drift](../service-templates/deploy.md#redeploy-ops-deployment-page) |
| Stack unhealthy after upgrade | [Status](../operations/status.md) · [Upgrades](../operations/upgrades.md) |

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

