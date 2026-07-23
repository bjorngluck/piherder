# Template schema

Operator plan / schema detail also in [`docs/FEATURE_PLAN_TEMPLATES.md`](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_TEMPLATES.md).

## `template.yaml` (sketch)

```yaml
schema_version: 1
slug: uptime-kuma
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
  - name: ADMIN_PASSWORD
    type: password
    secret: true
    generate: true
  - name: FEATURE_X
    type: boolean
    default: "false"
  - name: APP_DATA
    type: volume
    volume_target: /app/data
    volume_default_mode: named
    default: app_data

files:
  - path: docker-compose.yml
  - path: .env
    from: .env.sample
  # Optional sidecars (editor “Additional files” / from-host relative mounts)
  - path: promtail-config.yaml

checklist:
  - title: DNS (manual)
    body: |
      Create an A/AAAA record…
```

### Substitution

- Placeholders: `{{VAR_NAME}}` only (case-sensitive).  
- Applied to **all** stored file bodies (compose, `.env`, additional files).  
- No arbitrary Jinja/code in untrusted imports.  
- Secrets → encrypted in PiHerder; written to host `.env` mode `600`.

### Additional files (v0.9)

| Topic | Behaviour |
|-------|-----------|
| Storage | Paths in definition `files` + `file_contents` (same as compose) |
| From host | Relative bind mounts that look like files (`.yml`, `.yaml`, `.json`, …) are imported; directory binds stay volume vars |
| Host literals | Short host name → `NODE_NAME`; FQDN → `HOST_FQDN`; other remote URLs → dedicated vars (e.g. `LOKI_URL`) |
| Deploy | All rendered files written next to the project on the host |

### Catalog source badges (v0.9)

| Badge | `source` | Notes |
|-------|----------|--------|
| **OOTB** | `builtin` / `starter` | Disk seed; refresh while still builtin |
| **Yours** | `user` | After Save / from-host — never disk-overwritten |
| **Imported** / **Git** | `import` / `git` | Operator-owned variants |

### Code

| Area | Path |
|------|------|
| Schema / catalog | `app/services/service_templates/` |
| From host / harden | `from_host.py`, `harden.py` (`discover_relative_config_files`, `parameterize_host_literals`) |
| Router | `app/routers/templates_svc.py` |
| Disk starters | `service_templates/` |
| Migration | `migrations/versions/…016_service_templates.py` |
| Tests | `tests/test_service_templates.py`, `tests/test_template_source_badge.py`, `tests/test_from_host_extra_files.py` |
