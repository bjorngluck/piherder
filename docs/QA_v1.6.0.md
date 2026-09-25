# PiHerder v1.6.0 — operator QA / sign-off

**Branch:** `v1.6.0-dev` → `main` · tag **`v1.6.0`** (cut after merge)  
**Code freeze:** **yes** (2026-09-24). Release notes and package **1.6.0** on 2026-09-25.  
**Package:** **`1.6.0`**  
**Operator QA:** **signed** 2026-09-24 (Mux-1, HA-p2 Slice 1 and 1b, Q-80, Docs-archive, CSP-n, Undo-1, 1.5 regression). Screenshot boxes are not a walk. Last measured compose coverage **75.01%** (36304/48400) after pack `_q38`. CI fail-under **75**.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes.

Plan: [PLAN_v1.6.0.md](PLAN_v1.6.0.md) · HA design: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7 · mux: [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · API: [API.md](API.md).

1.5 production sign-off stays [QA_v1.5.0.md](QA_v1.5.0.md) (historical). Do **not** re-open 1.5 boxes here.

**Tag honesty:** freeze only with **HA-p2 Slice 1** + **Mux-1** + CI fail-under **75**. Should streams may slip. Do not bump version, merge, tag, or Hub until asked. The public demo is already on this branch (report-only CSP, Move off). Do not wipe its volumes and do not set `PIHERDER_CSP_ENFORCE` there for this walk.

**This pass is sign-off, not new features.** Fix only a feature or regression bug you hit while walking. Discover (HA Slice 2, Undo-2, Mux-2) and v1.7 (Brand, AC-fg, J-runtime) stay parked.

---

## Operator pages to walk

| Stream | Wiki |
|--------|------|
| Mux-1 | [Web SSH console](../wiki/day-to-day/web-ssh-console.md#host-mux-mux-1) · [Add a server](../wiki/day-to-day/add-server.md) · [HAOS hosts](../wiki/day-to-day/haos-hosts.md) · [Remove a server](../wiki/day-to-day/remove-server.md) |
| HA-p2 + 1b | [API tokens](../wiki/operations/api-tokens.md) · [Home Assistant → PiHerder](../wiki/integrations/home-assistant.md) · [System Info](../wiki/day-to-day/system-info.md) · [HAOS hosts](../wiki/day-to-day/haos-hosts.md) (path 1 vs path 2) · HACS readme in `bjorngluck/piherder-ha` |
| CSP | [Env reference](../wiki/operations/env-reference.md) (`PIHERDER_CSP`, `PIHERDER_CSP_REPORT_ONLY`, `PIHERDER_CSP_ENFORCE`) · [Public demo](../wiki/operations/demo-site.md) |
| Undo-1 | [Move a service](../wiki/docker/service-migration.md) step 10 · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Troubleshooting](../wiki/troubleshooting/index.md) |
| Move / regression | [Move a service](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Reports](../wiki/day-to-day/reports.md) |
| Screenshots | [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v160--pack-status) |

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.6.0-dev`** (`docker compose build web celery-worker && docker compose up -d`). App code is **not** bind-mounted. About / footer still **1.5.0** until freeze |
| **Migrate** | Web startup runs Alembic to **head**. Need **`043`** mux, **`044_host_facts`**, **`045_host_resources`**. If `/servers` 500s, `alembic_version` may be stuck before **040** (Postgres boolean bind; fixed `81d7a12`). Check: `SELECT version_num FROM alembic_version;` |
| **HACS** | [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.4** (tag `v0.2.4`) — **not** this image. Token **`read` only**; IP allowlist = HA egress. YAML `rest:` remains possible. Lovelace resource `/local/piherder-dashboard-card.js?v=0.2.4` as **JavaScript module**. Card header shows the PiHerder logo. Redownload + full HA restart after a plugin tag |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | ≥ **two** real SSH Docker hosts + one HAOS (never a Move dest). One Debian/Pi with **tmux** or **screen** for Mux-1. One host **without** mux binaries if you can spare it |
| **Flags** | Console: `PIHERDER_SSH_CONSOLE=true` then recreate **web**. Move wizard (Undo-1 / regression only): `PIHERDER_SERVICE_MIGRATE=true` then recreate **web** and **celery-worker**. Kill switch stays **false** on production and on the public demo. Demo never copies / mux / backup-from-HA |
| **Public demo** | Already on `v1.6.0-dev` (`df39014`). Footer still **1.5.0**. CSP is **Report-Only**. Use it only for the demo rows (login still renders, mux never, Move never). Do not stage Undo or a volume wipe there |
| **Where to look** | Local herder: `http://127.0.0.1:8000` (Caddy `:8888` / `:8443`). Public demo: [piherder-demo.hacknow.info](https://piherder-demo.hacknow.info) |

**Do not test (Out of 1.6):** Brand chrome, AC-fg grants, HA Slice 2/3 (backup / start-stop / Move-from-HA), Mux-2 leftover list in the UI, default-on Move, enforcing CSP on the public demo.

### Suggested order

1. Rebuild local **web** + **celery-worker**. Confirm Alembic **045**. About / footer still **1.5.0**.  
2. **Mux-1** on a Debian/Pi that already has `tmux` (screen row only if you have a screen-only host).  
3. **CSP** on the local install (it enforces), then one header check on the public demo (Report-Only).  
4. **HA-p2 Slice 1**, then **Slice 1b** on plugin **0.2.4**. Refresh System Info on each host first so CPU/memory/disk are not empty.  
5. **Docs-archive** (files in the repo; no UI).  
6. **1.5 regression** while Move is still off, then turn `PIHERDER_SERVICE_MIGRATE` on only for **Undo-1** on a disposable pair. Turn it **off** again when that section is done.  
7. **Screenshots** in the same sessions (table below). New filenames are not in the tree until you save the PNGs.  
8. Leave **Freeze gates** empty.

---

## Mux-1 — host tmux/screen (Must)

Code on `v1.6.0-dev` (`848116a` + docs `7186298` + migrate fix `81d7a12`). Migration **`043_console_mux`**. Flag **Edit → Features → Console mux** (default **off**; hidden on HAOS). Never apt-install.

Session name: `ph-u{user}-s{server}-n{tab}-f` (fleet) or `-p` (privileged). Tab index is clamped.

**Operator sign-off 2026-09-24:** every Mux-1 row below was walked and signed, including screen.

### Setup

- [x] `PIHERDER_SSH_CONSOLE=true`; recreate **web**; Alembic **043** applied  
- [x] Debian/Pi host has `tmux` **or** `screen` already (do not install from PiHerder)  
- [x] Console mux checkbox visible on that host’s **Features**; **not** on HAOS  

### Behaviour

- [x] Host mux **off** → same Paramiko PTY as 1.5 (no `ph-u*` on the host)  
- [x] Host mux **on**, tmux present → `tmux ls` shows `ph-u{user}-s{server}-n{tab}-f`  
- [x] Privileged identity → `-p` name; fleet cannot attach that session  
- [x] Hide / app-switch **detaches**; host session **stays**; re-open attaches  
- [x] Tab **✕** / bye **kills** the named host session (`tmux ls` empty for that name)  
- [x] Recreate **web** while hidden: host session **still there**; re-open attaches (Mux-1). Herder in-memory park is gone (expected)  
- [x] Missing binary → console still opens; honest note; plain PTY  
- [x] HAOS: checkbox hidden; save forces mux off; never mux  
- [x] Demo: never mux  
- [x] Viewer: console **403**  
- [x] Idle herder park expire does **not** `tmux kill-session`  
- [x] After **Remove server**: leftover `ph-u*` on the Pi is a **wiki** kill (`tmux ls` / `tmux kill-session -t NAME` or `screen -S NAME -X quit`). No in-app leftover list (Mux-2 Discover)  

### Screen backend (if a host has screen but not tmux)

- [x] Probe uses **screen**; Hide detaches; ✕ quits the screen session  

---

## HA-p2 Slice 1 — HACS on HA (Must)

Plugin is **`custom_components/piherder`** in [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) (**0.2.4**, tag `v0.2.4`). **Not** in this Docker image. Token **`read`**. Poll **DB snapshots** only — never SSH the fleet on the HA interval.

HA **device page** = one **Visit** (host). Docker / Backups / Alerts / Audit are **PiHerder fleet** card chips, not extra Visit links.

Path 1 (PiHerder **manages HAOS** over SSH) already shipped; do not regress [HAOS hosts](../wiki/day-to-day/haos-hosts.md). This stream is path 2: HA **observes** the fleet.

**Operator sign-off 2026-09-24:** every HA-p2 Slice 1 row below was walked and signed.

### Herder side

- [x] Admin creates token with **`read` only** (no `jobs` / `edit` / `files`)  
- [x] IP allowlist = HA host (HAOS ≈ appliance LAN IP; container HA may be a bridge IP — 403 until allowlist matches egress)  
- [x] `GET /api/v1/health` 200 with that token  
- [x] `GET /api/v1/servers` and `GET /api/v1/jobs?active_only=true` 200  
- [x] `GET /api/v1/summary` 200 (`read`)  
- [x] `GET /api/v1/servers` includes `os_pretty` / `hardware` after a host-facts snapshot (System Info icon or ~15 min)  
- [x] Token **without** `read` (or bad secret) fails closed (401)  
- [x] Plugin files **absent** from this image (`custom_components/` not in the tree)  

### HACS install

- [x] HACS → custom repository → integration  
- [x] Config flow: base URL + `ph_…` token + TLS verify + poll interval  
- [x] Bad URL / token **keeps the fields** (does not wipe the form)  
- [x] Bad URL / TLS fail is an error in the flow, not a silent empty dashboard  
- [x] Bad token / missing `read` fails closed  
- [x] Fleet **Plugin** sensor is **0.2.4** after Redownload + HA **restart**  

### Entities

- [x] Fleet sensors: herder up, host count, OS updates, container updates, reboot pending, running jobs, Move in progress  
- [x] One HA **device** per PiHerder server (hardware + OS pretty, last seen, reboot, backup)  
- [x] “Host down” is **`last_seen` age**, not a live SSH ping  
- [x] **Visit** on the host device reaches `{origin}/servers/{id}` (only Visit on that page)  
- [x] Lovelace **PiHerder fleet** card: resource `/local/piherder-dashboard-card.js?v=0.2.4` as **JavaScript module** (delete any `?v=0.2.2`, `?v=0.2.3`, or `/api/piherder/…` resource); header shows the PiHerder logo; YAML `type: custom:piherder-dashboard-card`; no “custom element doesn’t exist”  
- [x] Card fleet totals; expand host; chips open PiHerder (Host/Docker/Backups/Alerts/Audit) in the browser, not HA history  
- [x] Empty CPU/memory/disk on the card after **web** recreate + System Info refresh is a herder snapshot gap (Alembic **045**), not a card 404  
- [x] Jobs running on the fleet device is a **count**, not a link  
- [x] System Info on the herder shows the **stored** snapshot (pretty OS, hardware, CPU cores/load, memory, disk); refresh is the **icon** in that modal (no extra host-page button)  

### Hard no (fail the train if any of these happen)

- [x] Poll does **not** SSH the fleet  
- [x] No start/stop / restart from HA  
- [x] No Move, compose write, Files, console, decrypt keys, OS **apply** from HA  
- [x] `service_migrate` stays off `POST /api/v1/…/jobs`  
- [x] Demo never; no plugin in the PiHerder image  
- [x] Wiki + HACS readme install path exist  

### Slice 1b / 2 (do not block Slice 1 sign-off)

Slice **1b** (Should) and Slice **2** (Discover) have their own sections. First plugin tag is **read-only**.

---

## Q-80 — unit ≥ 75% (Must)

**Operator sign-off 2026-09-24.**

- [x] Unit line coverage on `app` ≥ **75%** — last compose **75.01%** (36304/48400) after `_q38`  
- [x] CI `--cov-fail-under=75`  
- [x] Packs landed through `_q37` (2026-09-21).  
Maintainer skim (not a browser walk):

- [x] No live SSH / HA / two-host copy / mux host in CI  
- [x] Mux tests: `tests/test_console_mux_v16.py` (service; no live SSH)  
- [x] HA plugin tests (other repo) mock `/api/v1` only  

---

## Slice 1b — snapshot entities (Should; may slip)

**Operator sign-off 2026-09-24.** Same plugin **0.2.4** and the same host device. Does not block Slice 1 sign-off. Refresh Docker on the herder first so inventory is not an empty snapshot. These routes never SSH.

- [x] `GET /api/v1/inventory` 200 with the **read** token (names, running, image, project — from the last Docker inventory)  
- [x] `GET /api/v1/servers/{id}/inventory` 200 for one host  
- [x] `GET /api/v1/services` 200 (stored fleet service chips; does not poll Kuma or NPM)  
- [x] HA host device: one sensor per container (running / image / uptime text) and one per monitored service (up/down), plus disk %  
- [x] Tapping a sensor opens **HA history**, not PiHerder (chips on the fleet card are the links)  
- [x] Still no start/stop / restart from those sensors  

---

## Docs-archive-0x (Should)

**Operator sign-off 2026-09-24.** Repo check. No UI.

- [x] `docs/PLAN_v0.*` and `RELEASE_v0.*` full text lives under `docs/archive/v0/`  
- [x] Stubs at the old `docs/` paths (title + link; opening the stub is not a 404)  
- [x] `FEATURE_PLAN_*`, `ROADMAP_ECOSYSTEM.md`, `SPEC.md`, `ADMIN.md`, and v1.0+ PLAN/RELEASE still in `docs/`  
- [x] `.venv-docs/bin/mkdocs build --strict` (mkdocs is not on PATH)  

---

## CSP-n Slice 1 (Should; may slip)

**Operator sign-off 2026-09-24.** Walk **enforce** on the **local** install. The public demo is already on this branch and stays **Report-Only** — use it only for that one row. Do not set `PIHERDER_CSP_ENFORCE` on the public host.

There is no new screen. DevTools → Network → the document response.

### Local (enforcing)

- [x] `GET /auth/login` sends **`Content-Security-Policy`** (not Report-Only)  
- [x] `script-src` is `'self'` plus `'nonce-…'` and does **not** include `'unsafe-inline'`  
- [x] Header also has `script-src-attr 'unsafe-inline'`. `style-src` still has `'unsafe-inline'`  
- [x] View source: an inline `<script>` has `nonce=`. An `onclick=` handler is still in the HTML (not rewritten)  
- [x] A control that uses `onclick` still runs (theme or a menu). HTMX swaps still run (`inlineScriptNonce` is set)  
- [x] `/docs` and `/redoc` still load. Their `script-src` still allows `'unsafe-inline'` (Swagger). They are not nonced  
- [x] Turnstile on login still renders when keys are set  
- [x] No `'unsafe-eval'` (unchanged). An Alpine page that needed `new Function` can still be blocked — that is the existing policy, not a Slice 1 regression, unless a page that worked on 1.5 is now blank  

### Public demo (report only)

- [x] `GET https://piherder-demo.hacknow.info/auth/login` sends **`Content-Security-Policy-Report-Only`** with a nonce and `script-src-attr`  
- [x] The login page still paints (onclick, conversion pixel). A missed script does not blank the page  
- [x] Demo OpenAPI stays **404** (tokens off). Do not use the demo for the `/docs` row  

---

## Undo-1 — fail-path Move undo (Should; may slip)

**Operator sign-off 2026-09-24.** Do this **last**, on a disposable pair, after the regression rows that need the flag off. Set `PIHERDER_SERVICE_MIGRATE=true` and recreate **web** and **celery-worker**. Turn the flag **off** again when finished. Never reverse a **green** Move. Never dest `down -v`. Never run this on the public demo.

Where the button is: JobHold on a **failed** `service_migrate`, and the same control on that job’s detail (`/jobs`). First click loads a preview into the log and relabels **Confirm undo**. Second click starts `service_migrate_undo`.

- [x] Flag **off** → `GET /servers/{id}/docker/migrate/undo/preview` is **404** (signed-in operator)  
- [x] **Green** Move: JobHold has **Succeeded** and **no** Undo  
- [x] Fail **before** names flip (stop / copy / dest-up): JobHold is **Start source stack**, not Undo  
- [x] Fail **after** names flip (`failed_step` `cutover`, `rebind`, or `validate`): **Undo** is offered. Preview names the project, the DNS/NPM swap, dest stop, source start. Confirm runs it  
- [x] After a successful undo: DNS/NPM point at the **source** again; source stack is **started**; dest stack is **`compose stop`** (container stopped, directory and volumes still on disk). Dest cert clone is still there; source cert target is enabled again  
- [x] A second undo of the same parent is refused  
- [x] Viewer on the undo URL is **403**. Public demo is **404**  
- [x] A `read` (or any) API token has **no** undo POST under `/api/v1`  
- [x] Recreate **celery-worker** during an undo **fails** that undo and leaves the dest tree (do not retry it automatically)  

---

## 1.5 regression

**Operator sign-off 2026-09-24.** Do not re-run the full 1.5 freeze pack unless chrome drifted. Spot-check:

- [x] Recycle **web** mid-Move still safe; recycle **worker** still fails honestly  
- [x] Host lock + HAOS refuse Move dest  
- [x] Reports pin/hide/reorder + Move jobs card  
- [x] Expired session → **Sign in** (not JSON `{"detail":"Please log in to continue"}`)  
- [x] `/api/v1` missing/expired Bearer still JSON 401  
- [x] Host Reboot uses `systemctl reboot --ignore-inhibitors` (not `--force`)  
- [x] Mux **off** still matches 1.5 console park  
- [x] Path 1 HAOS: System info / `ha` CLI check still works  

---

## Screenshots (freeze pack)

Owner: operator (not CI). Full capture notes, wire-into pages, and the “do not recapture” list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md#v160--pack-status).

PNGs are in `wiki/assets/screenshots/` and linked from the wiki pages in the table. Light theme, desktop width. No tokens, PEMs, or backup codes in frame.

| Pri | File | Surface | Must show | Wiki page |
|-----|------|---------|-----------|-----------|
| **P0** | `console-mux-features.png` | Edit → Features (Debian/Pi) | **Console mux** checkbox. Not an HAOS host | [Web SSH](../wiki/day-to-day/web-ssh-console.md) |
| **P0** | `console-mux-session.png` | Console, mux on | Banner that mux attached (`tmux` or `screen`) | [Web SSH](../wiki/day-to-day/web-ssh-console.md) |
| **P0** | `ha-hacs-config.png` | HA config flow | Base URL + token **masked** | [Home Assistant](../wiki/integrations/home-assistant.md) |
| **P0** | `ha-fleet-card.png` | Lovelace **PiHerder fleet** card | Fleet totals; one host expanded; chips Host/Docker/Backups/Alerts/Audit | [Home Assistant](../wiki/integrations/home-assistant.md) |
| **P0** | `ha-fleet-sensors.png` | HA devices | Fleet device + one host device. Plugin **0.2.4** if the sensor is in frame | [Home Assistant](../wiki/integrations/home-assistant.md) |
| **P1** | `ha-visit-host.png` | After device **Visit** | Browser on `{origin}/servers/{id}` | [Home Assistant](../wiki/integrations/home-assistant.md) |
| **P1** | `ha-slice1b-sensors.png` | Host device sensors | Container and/or service + disk %. No start/stop control | [Home Assistant](../wiki/integrations/home-assistant.md) |
| **P1** | `system-info-snapshot.png` | System Info modal | Stored CPU/memory/disk. Refresh is the **header icon** | [System Info](../wiki/day-to-day/system-info.md) |
| **P1** | `docker-migrate-jobhold-undo.png` | Failed post-flip JobHold | **Undo** (preview or **Confirm undo**). Disposable stack only. Do not replace the green Move shot | [Move a service](../wiki/docker/service-migration.md) |

Leave these files alone unless the chrome in the frame is actually wrong: `console-popup.png` (1.5 PTY), `docker-migrate-jobhold.png` (green Move), `docker-migrate-jobhold-start-source.png`, `system-info-haos.png` (path 1), `reports.png`, `demo-files.png`. CSP has no new screen — do not recapture login for the nonce.

---

## Freeze gates

Do not tick until the operator asks to freeze.

- [x] Mux-1 section signed  
- [x] HA-p2 Slice 1 section signed (plugin **not** in this image)  
- [x] Unit ≥ **75%**; CI `--cov-fail-under=75` — last compose **75.01%** (36304/48400)  
- [x] `mkdocs build --strict`  
- [x] **M-flag** still **false** (`PIHERDER_SERVICE_MIGRATE` default false)  
- [x] Version bump **`1.6.0`**  
- [x] Wiki banner, Home current release, and README point at **1.6.0** (Pages updates when this lands on `main`)  
- [x] GitHub **Release** (not Issue) after tag `v1.6.0` — body `docs/RELEASE_v1.6.0.md`  

---

## Live notes (this fleet)

| Date | Note |
|------|------|
| 2026-09-19 | Mux-1 code `848116a`; docs `7186298`. `/servers` 500: Alembic stuck at `039` (040 inserted integer `1` into boolean). Fixed `81d7a12`; this instance at `043_console_mux`. |
| 2026-09-19 | Operator: tmux testing “looks great so far”. Mux-1 boxes still empty until each row is walked. |
| 2026-09-19 | HACS **0.1.5**: Visit only. Host-facts **044**. System Info = snapshot + icon. Do not tick HA/Mux boxes from this note. |
| 2026-09-20 | HACS **0.2.2**: Lovelace fleet card loads via `/local/piherder-dashboard-card.js?v=0.2.2` (module). Device page still one Visit. Operator testing the card; HA boxes still empty. |
| 2026-09-20 | System Info wiki + modal CPU/memory (same **045** columns HA reads). Do not tick from this note. |
| 2026-09-20 | Q-80 `_q12`/`_q13`. Coverage still **~72.2%**. Do not raise fail-under. |
| 2026-09-20 | Q-80 `_q14`/`_q15` service leftovers + first router HTTP pack. Fail-under stays 70. |
| 2026-09-20 | Q-80 `_q16`/`_q17` jail/DNS plan + more routers. Fail-under stays 70. |
| 2026-09-20 | Q-80 `_q18`/`_q19` SFTP helpers, DNS save, auth/settings HTTP. Fail-under stays 70. |
| 2026-09-20 | Q-80 `_q20`/`_q21` list/search/read_text + login/ticket/DNS POSTs. Fail-under stays 70. |
| 2026-09-20 | Q-80 `_q22`/`_q23` put/mkdir/peek + 2FA/DNS CNAME/console revoke. Fail-under stays 70. |
| 2026-09-20 | Q-80 `_q24`/`_q25` unzip/attach-plan + TOTP/attach-cname/passkey options. Fail-under stays 70. |
| 2026-09-20 | Q-80 `_q26`/`_q27` zip helpers + account/avatar + DNS stack-edges. Fail-under stays 70. |
| 2026-09-20 | Full compose pytest `--cov=app`: **70.92%** (33925/47833). 31 failed (HTTP `cookies=` ignored by this TestClient). Do not raise fail-under. |
| 2026-09-20 | TestClient cookies jar patch + force-2FA autouse. smoke/list/rbac/recover/migrate-off: 89 passed. q28/q29 landed. |
| 2026-09-20 | Full compose `--cov=app`: **74.06%** (35423/47833). 1605 passed, 6 failed (force-2FA autouse + short recover password); those six re-run green after exclude/policy fix. Gap to 75% ~452 lines. |
| 2026-09-21 | Q-80 packs `_q30`–`_q37`. Full compose **75.04%** (35893/47833), 1620 passed. CI fail-under raised **70 → 75**. Mux-1 and HA boxes still empty. |
| 2026-09-21 | Docs-archive-0x and Slice 1b landed (plugin **0.2.3**). QA boxes for those rows stay empty until walked. |
| 2026-09-21 | CSP-n + Undo-1 on `df39014`. Public demo pulled to that commit (no volume wipe): Report-Only CSP, Move off, footer **1.5.0**. |
| 2026-09-21 | QA rewritten as the walk order above (plugin **0.2.3**, demo report-only row, Undo steps, screenshot filenames). Boxes still empty. |
| 2026-09-24 | Operator signed every Mux-1 row (setup, behaviour, screen) and the 1.5 mux-off regression. HA, CSP, and Undo boxes stay empty. |
| 2026-09-24 | Operator signed HA-p2 Slice 1 (herder token, HACS, entities, hard no) and Slice 1b. CSP and Undo stay empty. |
| 2026-09-24 | Operator signed Q-80 (coverage bar, CI fail-under, maintainer skim) and Slice 1b. CSP and Undo stay empty. |
| 2026-09-24 | Operator signed Docs-archive-0x and CSP-n Slice 1 (local enforce and demo Report-Only). Undo stays empty. |
| 2026-09-24 | Operator signed Undo-1 and the 1.5 regression spot-check. Screenshots and the remaining freeze gates stay empty. |
| 2026-09-24 | **Code freeze.** Streams signed. Next: screenshot pack. Package stays **1.5.0** until the version bump is asked. |
| 2026-09-25 | Screenshot pack committed. Package **1.6.0**. Release notes `docs/RELEASE_v1.6.0.md`. Wiki figures wired. |
