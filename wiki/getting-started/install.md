# Install (Docker Compose)

## Steps

### 1. Clone and enter the repo

```bash
git clone https://github.com/bjorngluck/piherder.git
cd piherder
# optional: pin a release tag when cut (e.g. v0.5.0); otherwise stay on main
# git checkout v0.5.0
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
docker compose up -d --build
```

First build vendors frontend assets and needs outbound network (or pre-vendored static files). Schema migrations run on **web** startup via Alembic.

### 5. Open the UI

| Access | URL |
|--------|-----|
| With Caddy + certs | `https://your.host:8443` (or your `PIHERDER_PUBLIC_URL`) |
| Caddy HTTP | `http://your.host:8888` |
| Direct to web (no Caddy) | `http://localhost:8000` |

Continue: [First login](first-login.md) — **register the first admin** (no default password user).

### 6. Production security checklist

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

## Optional: published image

When multi-arch images are published (target **v0.5.0 RC**), you can replace `build: .` with an image tag. Until then, **compose build** is the primary path. See [Publish image](../developers/publish-image.md).

---

## Upgrade later

```bash
git fetch --tags
git checkout main     # or a release tag, e.g. v0.5.0 when published
docker compose up -d --build
```

Always keep a [self-backup](../operations/self-backup.md) and the same `PIHERDER_MASTER_KEY` before major upgrades.
