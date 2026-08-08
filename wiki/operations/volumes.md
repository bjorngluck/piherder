# Volumes

## What this is

Host directories Docker Compose bind-mounts into the stack for **fleet backups**, **herder DR archives**, **avatars/logos**, and **TLS PEMs**.

## Why it matters

Backups and self-backup are only as durable as the disk under these paths. Celery workers must see the **same** `/backups` (and usually `/data`, `/herder_backups`) as web.

| Host path | Container | Purpose | In self-backup? |
|-----------|-----------|---------|-----------------|
| `${PIHERDER_BACKUP_HOST_PATH:-./backups}` | `/backups` | rsync destinations for **server** backups | **No** — separate DR ([Backups](../day-to-day/backups.md)) |
| `./piherder_backups` | `/herder_backups` | PiHerder **self-backup** archives (chown uid **1000** if permission errors) | These *are* the archives |
| `./piherder_data` | `/data` | Avatars, service logos, **nmap run XML** under `nmap/runs/` (Settings live in Postgres) | Avatars/logos **yes**; nmap XML **no** ([Self-backup](self-backup.md)) |
| `${PIHERDER_NMAP_VULN_PATH:-./piherder_nmap_vuln}` | `/var/lib/piherder/nmap-vuln` | Opt-in **vuln pack** (web **:ro**, nmap worker **rw**) — [LAN Discovery](../integrations/lan-discovery.md) | **No** |
| `./certs` | `/certs` (Caddy, ro) | Edge `fullchain.pem` + `privkey.pem` for **this** UI | **No** (fleet cert vault PEMs are in self-backup DB) |

**DR takeaway:** Losing the herder disk without off-box copies means: self-backup archives gone, fleet rsync trees gone, edge PEMs gone — even if you still know the master key. Store Scenario C offline — [Self-backup & DR](self-backup.md#honest-dr-what-fully-functional-means).

Secondary disk example:

```bash
PIHERDER_BACKUP_HOST_PATH=/home/you/backup
```

`web` and `celery-worker` must share the same `/backups` (and usually `/data`, `/herder_backups`) for multi-worker correctness.

### LAN Discovery volumes

When using profile **`nmap`**:

- **Vuln pack** lives on the host path above — never baked into image layers.  
- **Scan XML** lands under `piherder_data/nmap/runs/` (shared `DATA_ROOT`).  
- Optional purge of old nmap runs: Settings → **Stale data cleanup** (nmap toggle off by default).  
- **Worker fence** is env/compose, not a volume: `PIHERDER_NMAP_WORKER` is `0` on web and `1` on `celery-worker-nmap` — [env reference](env-reference.md#lan-discovery-nmap--opt-in).