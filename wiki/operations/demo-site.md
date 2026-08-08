# Public demo

A **gated shared sandbox** where you can click through a synthetic fleet without installing PiHerder.

| | |
|--|--|
| **URL** | [https://piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) |
| **Access** | Request access from the project website (Cloudflare Access) |
| **Account** | Shared demo login after Access — not your private herder |

## What to expect

- Full UI with sample hosts, jobs, maps, and integrations
- Banner: shared account · data may reset · some actions simulated
- You **cannot** onboard real machines, use API tokens, or reach anyone’s home lab
- Job actions succeed as demos only (no live SSH)

## What this is not

- Not a multi-tenant SaaS for your own fleet  
- Not a place to store real config or secrets  
- Data can be wiped anytime (nightly or operator restore)

To run **your own** instance, see [Install](../getting-started/install.md).

Maintainer ops (VPS, seed, Cloudflare): [docs/DEMO_SITE.md](https://github.com/bjorngluck/piherder/blob/main/docs/DEMO_SITE.md) in the repo (not required for self-hosting).
