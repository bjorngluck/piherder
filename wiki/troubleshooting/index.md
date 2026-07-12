# Troubleshooting

| Symptom | Page |
|---------|------|
| SSH / key / deps / docker group | [SSH, rsync & dependencies](ssh-rsync.md) |
| Backup failed / stuck pending | [Backups](backups.md) |
| No push on phone / PWA | [Push / PWA](push.md) |
| Template deploy / Docker editor | [Templates & Docker](templates-docker.md) |

## Always useful

```bash
docker compose ps
docker compose logs web --tail=200
docker compose logs celery-worker --tail=200
```

- **Jobs** page for work unit status + log tail  
- **Audit** for who/what  
- **Settings → Status** for stack health  
- Server **Re-check dependencies** for remote tools  
