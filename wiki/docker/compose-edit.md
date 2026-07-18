# Compose edit & deploy

## What this is

Tools to **edit** compose (and related files) on a free-form host project, keep **version history**, **validate**, and **deploy** changes — including pull-only checks vs full `up -d`.

## Why it exists

Editing compose only over SSH loses history and audit. The editor records versions, can multi-file save, and runs deploy as a **job** with logs so operators see pull/up failures instead of silent “success.”

---

## End-to-end: small compose change

1. Docker page → project **⋯** → **Quick edit** (tiny change) or **Full editor…** (history / multi-file).  
2. Edit compose / override / `.env` as needed.  
3. Validate YAML if offered.  
4. **Save & Deploy** (or deploy action) — wait for job.  
5. Confirm containers healthy; check Audit for the change actor.

---

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

Tabs edit each file (file badges in the chrome); **Save & Deploy** writes the full set and redeploys. Version history stores multi-file snapshots (merge-on-save so one file no longer wipes the others).

**Word wrap:** toggle wrap in the editor. Line numbers stay aligned with wrapped lines (gutter heights remeasured after the overlay is forced to the editor size).

On the host, Compose still auto-loads override + `.env` in the project directory.

## Check updates vs Deploy

| Button | Effect |
|--------|--------|
| **Check updates** | Pull / image compare — no `up -d`. Runs as a **Job** (`docker_stack_check`) with live log. |
| **Deploy** | Pull + `up -d`. Runs as a **Job** (`docker_stack_deploy`) with live log; pending-update badge cleared on success. |
| **Stop / Start / Restart all** | `docker compose stop\|start\|restart` for the whole project. Jobs `docker_stack_stop` / `_start` / `_restart` with confirm + live log. |

Check, Deploy, and whole-project lifecycle open the job holding modal (same pattern as OS/container patch). Follow progress under **Jobs** or **Audit** if you leave the page. Stack **mutations** (deploy, stop, start, restart, template apply) share one exclusive lane per host; stack **check** is exclusive with other checks.

## New project wizard

Creates a project directory under the Docker base dir and optional initial compose.

## Template stacks

If a project is **template-managed**, prefer the [deployment / redeploy](../service-templates/deploy.md) flow. Full compose edit is intentionally gated so desired state stays authoritative. The ⋯ menu labels advanced raw edit clearly.
