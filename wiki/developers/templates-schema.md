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

checklist:
  - title: DNS (manual)
    body: |
      Create an A/AAAA record…
```

### Substitution

- Placeholders: `{{VAR_NAME}}` only (case-sensitive).  
- No arbitrary Jinja/code in untrusted imports.  
- Secrets → encrypted in PiHerder; written to host `.env` mode `600`.

### Code

| Area | Path |
|------|------|
| Schema / catalog | `app/services/service_templates/` |
| Router | `app/routers/templates_svc.py` |
| Disk starters | `service_templates/` |
| Migration | `migrations/versions/…016_service_templates.py` |
| Tests | `tests/test_service_templates.py` |
