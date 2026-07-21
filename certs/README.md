# TLS certificates for Caddy

PiHerder’s Caddy service expects **trusted** certificate files here (mounted at `/certs` in the containers).

## Required files

| File | Description |
|------|-------------|
| `fullchain.pem` | Certificate + intermediate chain (PEM) |
| `privkey.pem` | Private key (PEM) |

Do **not** commit these files. `*.pem` is gitignored.

## Ways to place certs

### A — Catalog → Certificates → Apply to this PiHerder (preferred)

1. Pull from NPM or **Upload PEM** into the vault.  
2. Open the cert → **Apply to this PiHerder**.  
3. Web writes `fullchain.pem` + `privkey.pem` here and POSTs the Caddyfile to Caddy admin `/load` with `Cache-Control: must-revalidate` so file-based TLS is re-read (no SSH, no sudo). Without that force header Caddy treats an unchanged Caddyfile as a no-op and keeps the previous cert in memory.

Stock `docker-compose.yml` mounts `./certs` on **web** (RW) and **caddy** (RO), and enables Caddy admin on `caddy:2019` (not published to the host).

Ensure the host directory is writable by the container user (usually host UID matching the image user, or group-writable):

```bash
# example: allow write for the compose user
chmod 775 certs   # or chown to the UID that runs the web container
```

### B — Manual copy

Copy PEMs from wherever you issue certs, then:

```bash
chmod 600 certs/privkey.pem
chmod 644 certs/fullchain.pem
docker compose up -d caddy
```

## Hostname

Set in `.env` (example):

```bash
PIHERDER_HOSTNAME=piherder.example.com
PIHERDER_PUBLIC_URL=https://piherder.example.com:8443
```

Compose maps host **8443 → container 443**. Include `:8443` in `PIHERDER_PUBLIC_URL` unless something else terminates HTTPS on 443 for you.

## Local development without certs

Use `Caddyfile.dev` (self-signed `tls internal`). Web Push / reliable Android PWA install need trusted HTTPS on a real hostname — see `docs/ADMIN.md`.
