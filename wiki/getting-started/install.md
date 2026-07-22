# Install (Docker Compose)

## What this is

The **supported** way to run PiHerder: Docker Compose stack (`web`, `db`, `redis`, `celery-worker`, `caddy`) with secrets in `.env` and data on host volumes.

## Why Compose

One command brings up the whole control plane with migrations, workers for backups, and optional TLS termination. Other topologies are not documented as supported in RC1.

!!! warning "RC1"
    Prefer a lab host first. See [Home — RC1](../index.md#rc1).

---

## Steps

### 1. Clone and enter the repo

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
git checkout v0.8.0   # or stay on main
cp .env.example .env
```

### 2. Generate secrets

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
openssl rand -hex 32   # SECRET_KEY
```

Paste into `.env`:

```bash
PIHERDER_MASTER_KEY=...   # Fernet key from above — never lose this
SECRET_KEY=...            # long random; not the compose default
```

### 3. (Recommended) Set public hostname

```bash
# .env
PIHERDER_HOSTNAME=piherder.example.com
PIHERDER_PUBLIC_URL=https://piherder.example.com:8443
```

Place trusted PEMs in `certs/` — full steps: [Trusted HTTPS & TLS](https-tls.md).

!!! tip "Local only / no certs yet"
    Mount `Caddyfile.dev` instead of `Caddyfile` for self-signed TLS. Fine for desktop testing; **not** reliable for phone Web Push.

### 4. Start the stack

```bash
docker compose up -d
```

Compose pulls multi-arch **`bjorngluck/piherder:latest`** from Docker Hub (`linux/amd64` + `linux/arm64`). Schema migrations run on **web** startup via Alembic.

To pin a release tag:

```bash
PIHERDER_IMAGE=bjorngluck/piherder:0.8.0 docker compose up -d
```

### 5. Open the UI

| Access | URL |
|--------|-----|
| With Caddy + certs | `https://your.host:8443` (or your `PIHERDER_PUBLIC_URL`) |
| Caddy HTTP | `http://your.host:8888` |
| Direct to web (no Caddy) | `http://localhost:8000` |

Continue: [First login](first-login.md) — **register the first admin** (no default password user).

### 6. (Optional) LAN Discovery nmap worker

Default `docker compose up` does **not** start nmap. Web **never** runs scans: it only enqueues to queue `nmap`.

**Worker fence (compose hard-codes — no `.env` required):**

| Process | `PIHERDER_NMAP_WORKER` |
|---------|------------------------|
| web + main celery-worker | `0` — tasks refuse to scan |
| `celery-worker-nmap` (+ `Dockerfile.nmap`) | `1` — only allowed executor |

Documented in [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example) and [Environment reference](../operations/env-reference.md#lan-discovery-nmap--opt-in).

```bash
docker build -f Dockerfile.nmap -t piherder:nmap-local .
docker compose --profile nmap up -d celery-worker-nmap
```

Ensure stock compose still publishes Postgres/Redis on **host loopback** (nmap worker uses host networking). Vuln pack dir defaults to `./piherder_nmap_vuln` (`PIHERDER_NMAP_VULN_PATH`). Never add `-Q nmap` to the main celery-worker. Operator guide: [LAN Discovery](../integrations/lan-discovery.md).

### 7. Production security checklist

| Item | Why |
|------|-----|
| Strong `PIHERDER_MASTER_KEY` + `SECRET_KEY` | Encrypts fleet secrets / signs sessions — not compose defaults |
| `PIHERDER_PUBLIC_URL=https://…` (or `COOKIE_SECURE=true`) | Session cookies get the `Secure` flag |
| Prefer Caddy **8888/8443** over exposing app **:8000** | Correct client IP for audit + TLS termination |
| `METRICS_TOKEN=…` if scrapers can reach `/metrics` | Empty token = open metrics on the app port |
| Leave `ALLOW_OPEN_REGISTRATION=false` | Only first admin self-registers; others via Users |
| Settings → PiHerder backup → run once | Offline archive + keep master key with it |

Full env catalog: [Environment reference](../operations/env-reference.md).

---

## Verify containers

```bash
docker compose ps
docker compose logs -f web --tail=100
```

Healthy web should accept HTTP; logs may show `Web Push VAPID ready` after first start.

---

## Volumes created on the host

| Host path | Purpose |
|-----------|---------|
| `./backups` (or `PIHERDER_BACKUP_HOST_PATH`) | Per-server rsync destinations |
| `./piherder_backups` | PiHerder self-backup archives |
| `./piherder_data` | Avatars / logos |
| `./certs` | TLS PEMs for Caddy |

Details: [Volumes](../operations/volumes.md).

---

## Image tags

Official image: [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder). Default compose tag is **`latest`**. See [Publish image](../developers/publish-image.md).

To develop against local source, restore `build: .` for `web` / `celery-worker` or build and set `PIHERDER_IMAGE` to a local tag.

---

## Upgrade later

```bash
git fetch --tags
git checkout v0.8.0    # or main
docker compose pull
docker compose up -d
```

Always keep a [self-backup](../operations/self-backup.md) and the same `PIHERDER_MASTER_KEY` before major upgrades.
