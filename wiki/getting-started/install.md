# Install (Docker Compose)

## Steps

### 1. Clone and enter the repo

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
# optional: pin a release
# git checkout v0.4.0
cp .env.example .env
```

### 2. Generate the master key

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste into `.env`:

```bash
PIHERDER_MASTER_KEY=...   # output of Fernet.generate_key()
SECRET_KEY=...            # e.g. openssl rand -hex 32
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
docker compose up -d --build
```

First build vendors frontend assets and needs outbound network (or pre-vendored static files). Schema migrations run on **web** startup via Alembic.

### 5. Open the UI

| Access | URL |
|--------|-----|
| With Caddy + certs | `https://your.host:8443` (or your `PIHERDER_PUBLIC_URL`) |
| Caddy HTTP | `http://your.host:8888` |
| Direct to web (no Caddy) | `http://localhost:8000` |

Continue: [First login](first-login.md).

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

## Optional: published image

When multi-arch images are published (target **v0.5.0 RC**), you can replace `build: .` with an image tag. Until then, **compose build** is the primary path. See [Publish image](../developers/publish-image.md).

---

## Upgrade later

```bash
git fetch --tags
git checkout v0.4.0   # or newer tag
docker compose up -d --build
```

Always keep a [self-backup](../operations/self-backup.md) and the same `PIHERDER_MASTER_KEY` before major upgrades.
