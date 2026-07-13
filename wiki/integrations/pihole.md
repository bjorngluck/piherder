# Pi-hole integration

Connect one or more **Pi-hole v6** instances for stats, local DNS, and gravity/system actions.

## Requirements

- Pi-hole **v6** (REST API under `/api`). v5 is not supported.
- Web UI password or **app password** (Settings → API).
- Network path from the PiHerder container to each Pi-hole.

## Connect

1. Catalog → **Integrations** → **+ Pi-hole**
2. Base URL = origin only (e.g. `https://pihole.example.com` — no `/admin`)
3. Password → Save & connect
4. Mark **Primary** on the instance that owns DNS truth (first instance is primary by default)

## Features

| Feature | Notes |
|---------|--------|
| Stats tiles | Total queries, blocked, %, domains on lists, active clients |
| Multi summary | Integrations list sums queries/blocked across instances |
| Deep links | Open admin, Gravity, System settings |
| Local DNS / CNAME | Listed from the open instance; **add/remove applies to all enabled Pi-holes** (primary first) |
| Update Gravity | Long-running; audited |
| Restart DNS / Flush network | Audited; optional “all instances” |

Partial fan-out failures are reported in the flash message and audit trail.

## Related

- Deploy Pi-hole via [Templates](../service-templates/overview.md)
- Env fallback `PIHOLE_URL` still works if no integration exists
