# TLS certificates for Caddy

PiHerder’s Caddy service expects **trusted** certificate files here (mounted read-only at `/certs` in the container).

## Required files

| File | Description |
|------|-------------|
| `fullchain.pem` | Certificate + intermediate chain (PEM) |
| `privkey.pem` | Private key (PEM) |

Do **not** commit these files. `*.pem` is gitignored.

## Hostname

Set in `.env` (example):

```bash
PIHERDER_HOSTNAME=piherder.example.com
PIHERDER_PUBLIC_URL=https://piherder.example.com:8443
```

Compose maps host **8443 → container 443**. Include `:8443` in `PIHERDER_PUBLIC_URL` unless something else terminates HTTPS on 443 for you.

## Obtaining certs

Copy from wherever you already issue certs for the hostname (e.g. another reverse proxy, ACME on a machine with ports 80/443, DNS challenge, commercial CA).

Example layout after copy:

```text
certs/
  fullchain.pem
  privkey.pem
  README.md
```

Permissions (recommended on the host):

```bash
chmod 600 certs/privkey.pem
chmod 644 certs/fullchain.pem
```

Then restart Caddy:

```bash
docker compose up -d caddy
```

## Local development without certs

Use `Caddyfile.dev` (self-signed `tls internal`). Web Push / reliable Android PWA install need trusted HTTPS on a real hostname — see `docs/ADMIN.md`.
