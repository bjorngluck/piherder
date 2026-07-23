# Templates & Docker

## What this is

Fixes for **template deploy**, wait modals, compose editor, and related Docker host issues. Product pages: [Deploy](../service-templates/deploy.md) · [Compose edit](../docker/compose-edit.md).

## Wait modal sits forever

- SSH hang on host (disk, pull, network).  
- Check Jobs/Audit if available; `docker compose logs web`.  
- Host `docker compose` interactive prompts?

## Deploy “succeeded” but stack unhealthy

v0.4.0+ Deploy surfaces pull/up codes — re-read banner/audit.  
SSH to host and `docker compose ps` in the project dir.

## Cannot edit compose on a template stack

By design — use **deployment** desired state / redeploy.  
See [Secrets & template badge](../service-templates/secrets.md).

## Full editor link from quick edit does nothing

Use project **⋯ → Full editor…** (direct navigation).  
From quick edit, **Open full editor →** should open the multi-file page; if a modal overlay is stuck, refresh the Docker page.  
See [Opening the editor](../docker/compose-edit.md#opening-the-editor).

## Compose set pills missing on Docker project

Inventory must see `docker-compose.<name>.yml` next to the primary file in the same folder. **Force refresh**. Pills appear when there is more than one set (primary + at least one extra). See [Compose sets](../docker/overview.md#compose-sets-same-folder-one-project-card).

## From-host pull incomplete

- Odd multi-file layouts: primary compose is imported; **override** files are noted in messages but not merged into the template body.  
- **Sidecar configs** (`./promtail-config.yaml:…` etc.) should appear under **Additional files** after pull (v0.9+). If missing, confirm the path is a **file** bind (not a directory) and exists next to compose on the host.  
- Host labels not variableised: short hostname comes from the **fleet server** hostname/name — set those correctly before pull, or edit variables by hand.  
- Fallback: create template manually and paste compose + config files into **Additional files**.

## Step-up 2FA for secrets fails

- User must have TOTP enabled.  
- “Require 2FA for template deploy” setting on?  
- Unlock cookie expired (~10 min) — View secrets again.

## Inventory stale

**Force refresh** on Docker page — [Inventory cache](../docker/inventory.md).
