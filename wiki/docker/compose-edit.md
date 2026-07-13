# Compose edit & deploy

## Opening the editor

From a project’s **⋯** menu on the Docker page:

| Action | What you get |
|--------|----------------|
| **Quick edit** | In-page modal (textarea) for compose and optional Dockerfile tab |
| **Full editor…** | Dedicated multi-file page: syntax highlighting, versions/drafts, validate YAML |

From **Quick edit**, use **Open full editor →** (or the Dockerfile equivalent when that tab is active) to leave the modal for the full page.

!!! tip "Desktop"
    Prefer **Full editor…** from the ⋯ menu when you need history, drafts, or multi-file tabs. Quick edit is best for small one-off edits.

## Multi-file projects

On **Docker → Full editor**, PiHerder loads when present:

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

If a project is **template-managed**, prefer the [deployment / redeploy](../service-templates/deploy.md) flow. Full compose edit is intentionally gated so desired state stays authoritative. The ⋯ menu labels advanced raw edit clearly.
