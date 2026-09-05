# PiHerder v1.4.0

**Pending sign-off.** Feature freeze on `v1.4.0-dev` (2026-09-04). Hub / `main` stay **[v1.3.0](RELEASE_v1.3.0.md)** until QA, screenshots, and any last bugfixes land — then merge, tag `v1.4.0`, Hub `1.4.0` / `1.4` / `latest`. Package footer still reads **1.3.0** until that bump.

You already stop a stack, copy its files, retarget a name, and start it on another Pi. This release makes that **one audited job** — with a lock so hardware-bound stacks stay put.

**Image (when tagged):** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.4.0` · `1.4` · `latest` (amd64 + arm64). Pins `1.3.0` / `1.3` stay valid.

Technical pipeline, Must IDs, and freeze gates: **[PLAN_v1.4.0.md](PLAN_v1.4.0.md)**. Operator how-to: wiki [Move a service](../wiki/docker/service-migration.md). Maintainer ticks: [QA_v1.4.0.md](QA_v1.4.0.md).

---

## What’s new

### Move a service (opt-in)

Pick a compose project, pick another Docker host, confirm downtime. PiHerder **stops the source**, copies the project (and named volumes) through the herder, **starts dest**, then retargets DNS **or** the Nginx Proxy Manager backend. Maps, Kuma, Grafana container chips, and cert targets follow dest. Hardware-bound stacks can be **locked to this host**; HAOS never moves.

Default leftover: source stays **stopped** with data on disk. You can also undeploy (keep volumes) or, with an extra confirm, remove the source project and copied named volumes. Dest is never wiped.

Kill switch **`PIHERDER_SERVICE_MIGRATE`** (default **off**). Recreate **web** after you turn it on. Operator+ only. Public demo never copies.

Wiki: [Move a service](../wiki/docker/service-migration.md) · [Journey Move](../wiki/getting-started/operator-scenarios.md#journey-move)

### Demo Files (public sandbox)

On the [public demo](../wiki/operations/demo-site.md), **Files** is a canned tree — browse README / compose / a logo, no SFTP, writes refused. Real Host Files on your herder is unchanged (still flag-off).

Wiki: [Host Files](../wiki/day-to-day/host-files.md)

---

## Defaults (opt-in stays off)

| | Default |
|--|---------|
| Move a service | **off** (`PIHERDER_SERVICE_MIGRATE`) |
| Host Files (real SFTP) | **off** (`PIHERDER_HOST_FILES`) |
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Command audit | **off** (Settings) |
| Source leftover after Move | **leave stopped** (data kept) |

---

## Upgrade from 1.3

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.  
2. After tag: pull `bjorngluck/piherder:1.4.0` (or `git checkout v1.4.0`). Until then, only the `v1.4.0-dev` tree.  
3. `docker compose pull && docker compose up -d` — Alembic **`042_compose_project_meta`**.  
4. Move stays **off** until you set `PIHERDER_SERVICE_MIGRATE=true` and recreate **web**.  
5. Smoke: lock a hardware-bound stack · optional Move on a disposable unlocked stack · 1.3 surfaces (policy, Reports, Files, console).

[Wiki upgrades](../wiki/operations/upgrades.md#13--14)

---

## Honest limits

| | |
|--|--|
| Move | Stop-first (brief downtime). Not live/zero-downtime. |
| NPM | Backend retarget only — no create/delete proxy hosts, no ACME in the herder. |
| Leftover remove | Optional; dest is never deleted. |
| Screenshots | Freeze pack **not captured yet** — wiki figures for Move land with PNGs. |
| Not this release | Auto-rollback · full NPM CRUD · Files token API · host `tmux`/`screen` · CSP nonces |

---

From [v1.3.0](RELEASE_v1.3.0.md). Docs: [piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/)
