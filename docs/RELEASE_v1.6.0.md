# PiHerder v1.6.0

**25 September 2026.** Home Assistant can watch the fleet. A Debian host can keep its shell in `tmux` or `screen`. The web UI locks down inline scripts. A Move that failed after names flipped can be undone.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.6.0` · `1.6` · `latest` (amd64 + arm64). Pins `1.5.0` / `1.5` stay valid.

Operator how-to: wiki [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/) · [Web SSH](https://piherder-docs.hacknow.info/day-to-day/web-ssh-console/) · [System Info](https://piherder-docs.hacknow.info/day-to-day/system-info/) · [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/). Technical record: [PLAN_v1.6.0](https://github.com/bjorngluck/piherder/blob/v1.6.0/docs/PLAN_v1.6.0.md). Maintainer QA: [QA_v1.6.0](https://github.com/bjorngluck/piherder/blob/v1.6.0/docs/QA_v1.6.0.md).

---

## What’s new

### Home Assistant watches the fleet

Install [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.4** with HACS. It is not inside the PiHerder image. Give it a token with scope **`read`** only.

You get a fleet device, one device per host, and a Lovelace card (logo, fleet totals, expand a host, chips for Host / Docker / Backups / Alerts / Audit). **Visit** on a host device opens that host in PiHerder. Sensors for containers, services, and disk percent sit on the host device. They are status only. There is no start/stop, no Move, and no backup-from-HA.

The card reads the same stored System Info snapshot as the herder. Refresh that snapshot with the header icon (or wait for the scheduler) after upgrade. Empty CPU or disk means the snapshot has not been written yet.

Wiki: [Home Assistant](https://piherder-docs.hacknow.info/integrations/home-assistant/) · [System Info](https://piherder-docs.hacknow.info/day-to-day/system-info/) · [API tokens](https://piherder-docs.hacknow.info/operations/api-tokens/)

### Console mux

On a Debian or Pi host, Edit → Features → **Console mux**. PiHerder attaches `tmux`, then `screen`, otherwise a plain shell. It never installs those programs. **Hide** leaves the named session on the Pi. **✕** kills it. HAOS and the public demo never mux. The flag stays off until you tick it.

Wiki: [Web SSH](https://piherder-docs.hacknow.info/day-to-day/web-ssh-console/)

### Inline scripts use a nonce

Home installs send `Content-Security-Policy` with a per-request script nonce. `script-src` no longer allows `'unsafe-inline'`. `onclick` handlers stay allowed (`script-src-attr`). The public demo stays **Report-Only**. Do not set `PIHERDER_CSP_ENFORCE` on the demo.

### Undo a Move that failed after names flipped

If Move fails at cutover, rebind, or validate, the job offers **Undo move**. Confirm it. Dest is `compose stop` first (never `down -v`), then names go back to the source, then the source starts. Dest files and volumes stay. A green Move has no Undo — run a new Move the other way. After Undo succeeds, the button is hidden. A failed Undo can be retried. A second Undo while one is still running is refused.

Kill switch **`PIHERDER_SERVICE_MIGRATE`** stays **off**.

Wiki: [Move a service](https://piherder-docs.hacknow.info/docker/service-migration/)

---

## Defaults (opt-in stays off)

| | Default |
|--|---------|
| Move a service | **off** (`PIHERDER_SERVICE_MIGRATE`) |
| Console mux | **off** per host (and never on HAOS or the demo) |
| Web SSH console | **off** (`PIHERDER_SSH_CONSOLE`) |
| Host Files (real SFTP) | **off** (`PIHERDER_HOST_FILES`) |
| Command audit | **off** (Settings) |
| CSP on the public demo | **Report-Only** |

---

## Upgrade from 1.5

Alembic **043** (console mux flag), **044** (host facts), and **045** (CPU / memory / disk) run when **web** starts.

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. Pull `bjorngluck/piherder:1.6.0` (or `git checkout v1.6.0`).
3. `docker compose pull && docker compose up -d` — recreate **web** and **celery-worker**.
4. Confirm the database revision includes `045_host_resources`. Refresh System Info once per host you want on the HA card.
5. Move stays **off** until you set `PIHERDER_SERVICE_MIGRATE=true` and recreate **web**.
6. HACS: redownload tag **v0.2.4** and restart Home Assistant if you already had an older plugin.

[Wiki upgrades](https://piherder-docs.hacknow.info/operations/upgrades/#15--16)

---

## Honest limits

| | |
|--|--|
| Home Assistant | Observes only. No start/stop, Move, Files, or console from HA. |
| Mux | Needs `tmux` or `screen` already installed. Leftover sessions after you delete a host are cleaned on the Pi, not in the UI. |
| CSP | `onclick` is still allowed. The public demo does not enforce the policy. |
| Undo | Fail-path only. Stop failure leaves names on dest. Recycle **celery-worker** mid-undo fails that undo. |
| Not this release | Branding, finer grants, remaining jobs on Celery, a read-only MCP adapter (v1.7 inbox). Move stays off by default. |

---

From [v1.5.0](https://github.com/bjorngluck/piherder/releases/tag/v1.5.0). Docs: [piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/)
