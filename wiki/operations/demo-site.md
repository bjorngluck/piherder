# Public demo

A **limited, view-only shared sandbox** where you can click through a synthetic fleet without installing PiHerder.

| | |
|--|--|
| **URL** | [https://piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) |
| **Username** | `demo@hacknow.info` |
| **Password** | `PiHerder@123?_` |
| **Role** | Shared **viewer** — same menus as a production read-only user (not admin) |
| **Your fleet** | This is **not** your private herder — synthetic data only |

!!! warning "Password may rotate"
    The shared password is rotated from time to time. **This live wiki page always has the current password.** If login fails after a rotate, refresh this page (or re-open it from [piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/operations/demo-site/)) rather than relying on an old screenshot, blog post, or cached README.

## Login path

1. Open **[https://piherder-demo.hacknow.info](https://piherder-demo.hacknow.info)**.  
2. Sign in with the shared email/password above (Turnstile may appear on the form).  
3. Explore the UI — dashboard, hosts, jobs, maps, integrations — under the non-dismissible demo banner.

Some deployments also use **Cloudflare Access** as an outer email gate before the app login. If you see an Access prompt, complete that step first (project site / invite process), then use the shared demo account.

## What to expect

- Full clickable UI with **sample** hosts, jobs, maps, and integrations  
- Banner: shared account · data may reset · some actions simulated  
- You **cannot** onboard real machines, use API tokens, or reach anyone’s home lab  
- Job actions succeed as **demos only** (no live SSH)  
- **No new accounts** — use the shared credentials only  
- Password / 2FA on the shared user stay locked (visitors cannot lock each other out)  
- Fleet config changes blocked like production viewers; **simulated jobs** still work for the tour  
- Data re-seeds on a schedule (and after operator maintenance) — treat everything as disposable  
- **Audit client IPs are scrubbed** — login and other events still appear, but real visitor addresses are stored/shown as `redacted` (seeded lab IPs like `10.42.x` may remain). The shared account must not leak other people’s IPs.  

!!! note "Demo screens are not always 100% aligned with a real fleet"
    Some screens and highlighted features on the demo **will not match a real self-hosted implementation pixel-for-pixel**. Hosts, inventory, jobs, maps, and integrations are **seeded or simulated** so the sandbox stays safe and disposable. You may see canned job results, static sample data, or simplified / empty panels where a live deploy would talk to real Pis, Docker, or external services. Treat the demo as a **UI tour** — your own install against real hosts is the accurate product experience.

## What this is not

- Not multi-tenant SaaS for your own fleet  
- Not a place to store real config or secrets  
- Not an admin sandbox — **viewer-only** RBAC by design  
- Not a guarantee that every panel looks identical to production data  

To run **your own** instance, see [Install](../getting-started/install.md).

Maintainer ops (VPS, seed, Cloudflare, cron): [docs/DEMO_SITE.md](https://github.com/bjorngluck/piherder/blob/main/docs/DEMO_SITE.md) in the repo (not required for self-hosting).
