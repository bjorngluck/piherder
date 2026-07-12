# Feature plan — Service templates (v0.4.0 Phase 1)

**Status:** **Shipped in v0.4.0** (foundation). Ops depth + polish + RC → **v0.5.0** (single target).  
**Horizon:** H2 / v0.4.0 foundation · v0.5.0 RC  
**Related:** [PLAN_v0.5.0.md](PLAN_v0.5.0.md) · [PLAN_v0.4.0.md](PLAN_v0.4.0.md) · [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) Phase 6

## Goal

Operators pick a **versioned service template**, fill variables in a wizard, preview rendered compose/env, choose a host, and deploy over SSH. PiHerder stores **desired state V1** (files + Fernet-encrypted secrets) for view/edit/redeploy.

## Schema (`template.yaml`)

```yaml
schema_version: 1
slug: uptime-kuma          # unique id
name: Uptime Kuma
description: Monitoring with a simple UI
category: monitoring      # proxy | dns | monitoring | observability | other
version: "1.0.0"
tags: [kuma, monitoring]

variables:
  - name: PROJECT_NAME
    label: Project folder name
    type: string            # string | int | port | password | email | url | boolean | volume
    default: uptime-kuma
    required: true
    secret: false
  - name: KUMA_PORT
    label: Host port
    type: port
    default: "3001"
  - name: ADMIN_PASSWORD
    label: Admin password
    type: password
    secret: true
    generate: true          # offer random value in wizard
    required: true
  - name: FEATURE_X
    label: Enable feature
    type: boolean
    default: "false"
    true_value: "true"      # optional (default true/false)
    false_value: "false"
  - name: APP_DATA
    label: App data storage
    type: volume
    volume_target: /app/data   # container path (required)
    volume_default_mode: named # named | bind_relative | bind_absolute
    default: app_data          # volume name or path depending on mode at deploy
    help: Deploy wizard chooses named volume, ./project folder, or host path.

# Files under files/ relative to template dir; {{VAR}} substituted
files:
  - path: docker-compose.yml
  - path: .env
    from: .env.sample       # optional source name in files/

checklist:
  - title: DNS (manual)
    body: |
      Create an A/AAAA record for your Kuma hostname pointing at this host.
      Pi-hole / Cloudflare automation is planned later.
  - title: First login
    body: Open http://HOST:{{KUMA_PORT}} and complete setup.

# Optional hints for later phases (ignored in Phase 1 deploy engine)
options:
  supports_docker_secrets: true
  npm_proxy: false
```

### Substitution

- Placeholders: `{{VAR_NAME}}` only (case-sensitive).  
- No arbitrary Jinja/code execution in untrusted imports.  
- Secret variables are written into `.env` (or secret files) and stored **encrypted** in PiHerder; non-secrets may appear in compose.
- **boolean** → wizard Yes/No; substitutes `true_value` / `false_value` (defaults `true`/`false`). Not secrets.
- **volume** → wizard picks mode + name/path; `{{VAR}}` expands to a full short mount `source:target`. Top-level Compose `volumes:` is synced for **named** modes. Modes: `named` (Docker volume), `bind_relative` (`./…` under project), `bind_absolute` (host `/…`).

### Sources

| Source | Phase |
|--------|--------|
| `user` — created or edited in the UI (authoritative) | 1 |
| `builtin` — starter seed from disk if slug **missing**; refresh when `source=builtin` and checksum changes | 1 |
| `import` — zip upload (then editable as `user`) | 1 |
| `git` — catalog pull | 2 |

**Operator ownership:** Create / Edit / Save in the UI is the primary authoring path. After Save, `source=user` and disk starters never overwrite.

## Data model

| Table | Role |
|-------|------|
| `servicetemplate` | Catalog entry (builtin/import/user); definition JSON + checksum |
| `stackdeployment` | Desired state per host+project: template ref, config_version, files, secrets_encrypted, variables, drift_status |

`DockerVersion` remains file history snapshots after deploy (unchanged UX for compose edit).

## Wizard flow

1. **Catalog** — list templates  
2. **Configure** — variables form (secrets, booleans, volumes/modes, ports; auto-generate secrets)  
3. **Host** — servers with Docker enabled; inventory project/container counts  
4. **Preview** — rendered files (secrets masked); wait modal while rendering if needed  
5. **Confirm** — write + optional `compose up`; **blocking wait modal** (SSH write, `.env` 600, pull/up); optional 2FA gate  
6. **Done** — post-deploy checklist + link to Docker / deployment page  

## From host

1. Pick host + compose project  
2. Optional harden: secrets → `.env` placeholders  
3. Parameterize short-form **volumes** + **host ports**; infer **boolean** env flags  
4. Open editor with full variable set → Save as `user` template  

## Security

- Secrets: `encrypt_str` / Fernet via `PIHERDER_MASTER_KEY`  
- **Step-up 2FA** for any cleartext secret UI (`secrets_unlock` cookie, ~10 min) — not satisfied by login 2FA alone  
- Audit: `template.deploy`, `template.redeploy`, `template.from_host`, `template.secrets_unlock` (no raw secrets in details)  
- Setting `template_require_2fa`: when true, user must have TOTP enabled to deploy/view secrets  
- RBAC: operator+ to deploy; viewer read catalog only  
- Template-managed Docker stacks: badge + compose-edit gate (edit via deployment desired state)

### Secrets model (locked — home production)

| Now | Later (roadmap) |
|-----|-----------------|
| PiHerder encrypted source of truth | Swarm / external vault / sealed host store |
| Deploy writes host **`.env`** with secrets + **`chmod 600`** | Optional no-persistent-file inject |
| Restarts offline (Docker restart policy; no PiHerder) | Optional scheduled secret refresh from PiHerder |
| Move secrets out of compose YAML into `.env` variables | Full advanced secret backends |

Compose `secrets: file: ./secrets/…` is **not** the default path (still cleartext files; most apps need env).

## OOTB pack (Phase 1)

- `npm` — Nginx Proxy Manager (volume vars for data / LE / MySQL)  
- `uptime-kuma` — Uptime Kuma (`KUMA_DATA` volume)  
- `pihole` — Pi-hole (etc + dnsmasq volumes)  
- `grafana` — Grafana (`GRAFANA_DATA` volume + `GF_USERS_ALLOW_SIGN_UP` boolean)  

Defaults use **named** volumes; deploy can switch to project folder or host path.

## UX feedback (sync ops)

Long template operations use shared **`PiHerderWaitModal`** (`data-wait-title` / `data-wait-message` on forms): preview, confirm deploy, redeploy, from-host pull. Spinner + message until navigation completes. Live job logs for template deploy remain a stretch (Jobs-based path → **v0.5.0** nice-to-have / **B07**).

## Out of v0.4.0 / follow-ons (→ v0.5.0)

| Track | Items |
|-------|--------|
| **v0.5.0 primary** | Drift scheduler · `.env` migrate UX · template UX polish (redeploy volume editor, from-host) · restore + last known config · production wikis · multi-arch image · RC freeze |
| **v0.5.0 nice-to-have** | Git catalog pull · NPM connector · async deploy job stream (**B07**) |
| **Later** | Advanced secret stores · DNS automation · expanded pack |

See [PLAN_v0.5.0.md](PLAN_v0.5.0.md), [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md), and [RELEASE_v0.4.0.md](RELEASE_v0.4.0.md).
