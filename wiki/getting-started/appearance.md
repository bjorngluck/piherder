# Appearance (light & dark)

PiHerder ships with **built-in light and dark themes** (Raspberry Pi red / green accents). There is **no** operator custom logo or colour branding in current releases — that is a far-horizon idea only.

## How to switch

| Where | Action |
|-------|--------|
| Header / avatar menu | **Toggle theme** |
| First visit | Follows **system** preference when possible |

Your choice is stored in the browser (local preference). It does not change other operators’ views.

## What theme does *not* affect

- Server data, jobs, or audits  
- Self-backup / restore  
- API tokens or REST behaviour  

## Documentation screenshots

The **public wiki** uses **light theme + desktop** captures by default (print-friendly, consistent).

| Kind | When |
|------|------|
| **Default** | One light desktop PNG per feature page |
| **Optional showcase** | One dark desktop and/or one mobile shot only where layout differs (e.g. Network maps, PWA) |
| **Not required** | Full matrix of light×dark×mobile for every page |

Capture conventions: [`wiki/assets/screenshots/README.md`](https://github.com/bjorngluck/piherder/blob/main/wiki/assets/screenshots/README.md) · edit flow: [Contributing docs](../developers/contributing-docs.md#screenshots-best-practice).

## Related

- [PWA & Web Push](../account-security/pwa-push.md) — install to home screen (theme still toggles in-app)  
- Theme sandbox (developers): `/static/theme-test.html` on a running instance  
