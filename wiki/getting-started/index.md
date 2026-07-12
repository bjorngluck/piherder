# Getting started

This section gets you from zero to a running PiHerder with a first admin account.

## Path

1. [Requirements](requirements.md) — what you need on the host and the fleet  
2. [Install (Docker Compose)](install.md) — clone, `.env`, `docker compose up`  
3. [First login](first-login.md) — register admin, lock registration  
4. [Trusted HTTPS & TLS](https-tls.md) — required for PWA / mobile Web Push  

Then continue with [Add a server](../day-to-day/add-server.md).

## Supported install path

!!! success "Docker Compose only"
    The **supported** topology is the compose stack in this repository (`web`, `db`, `redis`, `celery-worker`, `caddy`).

    Kubernetes and bare-metal installs are **under consideration only** — not documented as supported paths.

## Time estimate

| Step | Typical |
|------|---------|
| Clone + generate secrets | 5 min |
| First `compose up --build` | 5–15 min (image build) |
| Register + open UI | 2 min |
| Trusted certs (if not ready) | depends on your CA / ACME |
| First server + key deploy | 10–20 min |

## Related repo docs

| Doc | Role |
|-----|------|
| [README](https://github.com/bjorngluck/piherder/blob/main/README.md) | Project overview |
| [`.env.example`](https://github.com/bjorngluck/piherder/blob/main/.env.example) | Full env catalog |
| [docs/ADMIN.md](https://github.com/bjorngluck/piherder/blob/main/docs/ADMIN.md) | Long-form admin reference (mirrored into this wiki) |
