# Compose edit & deploy

## Multi-file projects

On **Docker → Edit compose**, PiHerder loads when present:

- `docker-compose.yml` (or compose.yaml)  
- override file  
- `.env`  
- `Dockerfile`  

Tabs edit each file; **Save & Deploy** writes the full set and redeploys. Version history stores multi-file snapshots (merge-on-save so one file no longer wipes the others).

On the host, Compose still auto-loads override + `.env` in the project directory.

## Check updates vs Deploy

| Button | Effect |
|--------|--------|
| **Check updates** | Pull / image compare — no `up -d` |
| **Deploy** | Pull + `up -d`; audit + banner show pull/up exit codes and output |

## New project wizard

Creates a project directory under the Docker base dir and optional initial compose.

## Template stacks

If a project is **template-managed**, prefer the [deployment / redeploy](../service-templates/deploy.md) flow. Full compose edit is intentionally gated so desired state stays authoritative.
