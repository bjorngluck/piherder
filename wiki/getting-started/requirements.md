# Requirements

## What this is

What you need **before** install: the machine that runs PiHerder, the fleet hosts it will manage, and the secrets you must not lose.

## Why check this first

Most “it doesn’t work” days are missing disk, no SSH path to the fleet, or a lost `PIHERDER_MASTER_KEY`. Five minutes here saves an evening of troubleshooting.

---

## PiHerder host (where the stack runs)

| Need | Notes |
|------|--------|
| Linux host with **Docker** + **Docker Compose** v2 | x86_64 or arm64 (Pi 4/5 OK for small fleets) |
| Disk for images + volumes | Postgres, backups destination, herder archives, avatars |
| Ports | Default compose: **8888** (HTTP), **8443** (HTTPS), optional direct **8000** to web |
| Network to fleet | SSH (22 or custom) to managed hosts |

### Resource ballpark

| Fleet size | Rough guide |
|-----------|-------------|
| Lab / few Pis | 2 CPU, 2–4 GB RAM |
| Larger fleet | Raise `CELERY_CONCURRENCY`; more RAM for parallel rsync |

See [Multi-worker Celery](../operations/multi-worker.md).

## Managed hosts (the fleet)

| OS | Support notes |
|----|----------------|
| **Raspberry Pi OS / Debian / Ubuntu** | Full path: apt OS patch, least-priv user scripts, rsync sudo |
| **HAOS / specialised** | Key deploy + plain rsync guidance; no automated least-priv |

### Tools by feature

PiHerder probes dependencies for **enabled** features only (no auto-install on the remote).

| Feature enabled | Remote tools expected |
|-----------------|----------------------|
| Always | SSH + shell |
| Backups | `rsync` on PATH; `sudo -n rsync` **or** plain rsync (root/HAOS) |
| Docker / containers | `docker` (+ group/socket as needed) |
| OS patch | `apt-get` / apt |

## Operator workstation

- Modern browser (Chrome/Firefox/Safari/Edge)  
- For **Web Push on phones:** trusted HTTPS + (iOS) Add to Home Screen — see [PWA & Web Push](../account-security/pwa-push.md)

## Secrets you must keep safe

| Secret | Why |
|--------|-----|
| `PIHERDER_MASTER_KEY` | Fernet key for SSH keys, integration tokens, template secrets, VAPID private key |
| `SECRET_KEY` | Session/JWT signing |
| Postgres password | If you change defaults |
| TLS private key | `certs/privkey.pem` |

!!! danger "Never commit `.env` or PEMs"
    They are gitignored. Losing `PIHERDER_MASTER_KEY` means encrypted fields in a restored DB/self-backup cannot be decrypted.
