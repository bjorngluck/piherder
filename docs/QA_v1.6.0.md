# PiHerder v1.6.0 — operator QA / sign-off

**Branch:** `v1.6.0-dev` → `main` · tag **`v1.6.0`** (cut after merge)  
**Code freeze:** *open*  
**Package:** **`1.5.0`** until freeze (About / footer stay 1.5.0)  
**Operator QA:** *in progress* — Mux-1 happy path 2026-09-19 (boxes below still empty until each row is walked). HA-p2 and Q-80 not signed.

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes.

Plan: [PLAN_v1.6.0.md](PLAN_v1.6.0.md) · HA design: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7 · mux: [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · API: [API.md](API.md).

1.5 production sign-off stays [QA_v1.5.0.md](QA_v1.5.0.md) (historical). Do **not** re-open 1.5 boxes here.

**Tag honesty:** freeze only with **HA-p2 Slice 1** + **Mux-1** + CI fail-under **75**. Should streams may slip. Do not bump version, merge, tag, Hub, or redeploy demo until asked.

---

## Operator pages to walk

| Stream | Wiki |
|--------|------|
| Mux-1 | [Web SSH console](../wiki/day-to-day/web-ssh-console.md) · [Add a server](../wiki/day-to-day/add-server.md) · [HAOS hosts](../wiki/day-to-day/haos-hosts.md) · [Remove a server](../wiki/day-to-day/remove-server.md) |
| HA-p2 | [API tokens](../wiki/operations/api-tokens.md) · [Home Assistant → PiHerder](../wiki/integrations/home-assistant.md) · [HAOS hosts](../wiki/day-to-day/haos-hosts.md) (path 1 vs path 2) · HACS readme in `bjorngluck/piherder-ha` |
| Move / regression | [Move a service](../wiki/docker/service-migration.md) · [Jobs](../wiki/day-to-day/jobs-audit-notifications.md) · [Reports](../wiki/day-to-day/reports.md) |
| Screenshots | [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md) |

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.6.0-dev`** (`docker compose build web celery-worker && docker compose up -d`). App code is **not** bind-mounted. About / footer still **1.5.0** until freeze |
| **Migrate** | Web startup runs Alembic to **head**. Need **`043`** mux, **`044_host_facts`**, **`045_host_resources`**. If `/servers` 500s, `alembic_version` may be stuck before **040** (Postgres boolean bind; fixed `81d7a12`). Check: `SELECT version_num FROM alembic_version;` |
| **HACS** | [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) **0.2.2** — **not** this image. Token **`read` only**; IP allowlist = HA egress. YAML `rest:` remains possible. Lovelace resource `/local/piherder-dashboard-card.js?v=0.2.2` as **JavaScript module** |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | ≥ **two** real SSH Docker hosts + one HAOS (never a Move dest). One Debian/Pi with **tmux** or **screen** for Mux-1. One host **without** mux binaries if you can spare it |
| **Flags** | Console: `PIHERDER_SSH_CONSOLE=true` then recreate **web**. Move wizard (Undo-1 / regression only): `PIHERDER_SERVICE_MIGRATE=true` then recreate **web**. Kill switch stays **false** on production. Demo never copies / mux / backup-from-HA |

**Do not test (Out of 1.6):** Brand chrome, AC-fg grants, HA Slice 2/3 (backup / start-stop / Move-from-HA), Mux-2 leftover list in the UI, default-on Move.

---

## Mux-1 — host tmux/screen (Must)

Code on `v1.6.0-dev` (`848116a` + docs `7186298` + migrate fix `81d7a12`). Migration **`043_console_mux`**. Flag **Edit → Features → Console mux** (default **off**; hidden on HAOS). Never apt-install.

Session name: `ph-u{user}-s{server}-n{tab}-f` (fleet) or `-p` (privileged). Tab index is clamped.

**Operator note 2026-09-19:** tmux happy path “looks great so far” — tick each row when that case is walked, not from this note.

### Setup

- [ ] `PIHERDER_SSH_CONSOLE=true`; recreate **web**; Alembic **043** applied  
- [ ] Debian/Pi host has `tmux` **or** `screen` already (do not install from PiHerder)  
- [ ] Console mux checkbox visible on that host’s **Features**; **not** on HAOS  

### Behaviour

- [ ] Host mux **off** → same Paramiko PTY as 1.5 (no `ph-u*` on the host)  
- [ ] Host mux **on**, tmux present → `tmux ls` shows `ph-u{user}-s{server}-n{tab}-f`  
- [ ] Privileged identity → `-p` name; fleet cannot attach that session  
- [ ] Hide / app-switch **detaches**; host session **stays**; re-open attaches  
- [ ] Tab **✕** / bye **kills** the named host session (`tmux ls` empty for that name)  
- [ ] Recreate **web** while hidden: host session **still there**; re-open attaches (Mux-1). Herder in-memory park is gone (expected)  
- [ ] Missing binary → console still opens; honest note; plain PTY  
- [ ] HAOS: checkbox hidden; save forces mux off; never mux  
- [ ] Demo: never mux  
- [ ] Viewer: console **403**  
- [ ] Idle herder park expire does **not** `tmux kill-session`  
- [ ] After **Remove server**: leftover `ph-u*` on the Pi is a **wiki** kill (`tmux ls` / `tmux kill-session -t NAME` or `screen -S NAME -X quit`). No in-app leftover list (Mux-2 Discover)  

### Screen backend (if a host has screen but not tmux)

- [ ] Probe uses **screen**; Hide detaches; ✕ quits the screen session  

---

## HA-p2 Slice 1 — HACS on HA (Must)

Plugin is **`custom_components/piherder`** in [bjorngluck/piherder-ha](https://github.com/bjorngluck/piherder-ha) (**0.2.2**). **Not** in this Docker image. Token **`read`**. Poll **DB snapshots** only — never SSH the fleet on the HA interval.

HA **device page** = one **Visit** (host). Docker / Backups / Alerts / Audit are **PiHerder fleet** card chips, not extra Visit links.

Path 1 (PiHerder **manages HAOS** over SSH) already shipped; do not regress [HAOS hosts](../wiki/day-to-day/haos-hosts.md). This stream is path 2: HA **observes** the fleet.

### Herder side

- [ ] Admin creates token with **`read` only** (no `jobs` / `edit` / `files`)  
- [ ] IP allowlist = HA host (HAOS ≈ appliance LAN IP; container HA may be a bridge IP — 403 until allowlist matches egress)  
- [ ] `GET /api/v1/health` 200 with that token  
- [ ] `GET /api/v1/servers` and `GET /api/v1/jobs?active_only=true` 200  
- [ ] `GET /api/v1/summary` 200 (`read`)  
- [ ] `GET /api/v1/servers` includes `os_pretty` / `hardware` after a host-facts snapshot (System Info icon or ~15 min)  
- [ ] Token **without** `read` (or bad secret) fails closed (401)  
- [ ] Plugin files **absent** from this image (`custom_components/` not in the tree)  

### HACS install

- [ ] HACS → custom repository → integration  
- [ ] Config flow: base URL + `ph_…` token + TLS verify + poll interval  
- [ ] Bad URL / token **keeps the fields** (does not wipe the form)  
- [ ] Bad URL / TLS fail is an error in the flow, not a silent empty dashboard  
- [ ] Bad token / missing `read` fails closed  
- [ ] Fleet **Plugin** sensor is **0.2.2** after Redownload + HA **restart**  

### Entities

- [ ] Fleet sensors: herder up, host count, OS updates, container updates, reboot pending, running jobs, Move in progress  
- [ ] One HA **device** per PiHerder server (hardware + OS pretty, last seen, reboot, backup)  
- [ ] “Host down” is **`last_seen` age**, not a live SSH ping  
- [ ] **Visit** on the host device reaches `{origin}/servers/{id}` (only Visit on that page)  
- [ ] Lovelace **PiHerder fleet** card: resource `/local/piherder-dashboard-card.js?v=0.2.2` as **JavaScript module**; YAML `type: custom:piherder-dashboard-card`; no “custom element doesn’t exist”  
- [ ] Card fleet totals; expand host; chips open PiHerder (Host/Docker/Backups/Alerts/Audit) in the browser, not HA history  
- [ ] Empty CPU/memory/disk on the card after **web** recreate + System Info refresh is a herder snapshot gap (Alembic **045**), not a card 404  
- [ ] Jobs running on the fleet device is a **count**, not a link  
- [ ] System Info on the herder shows the **stored** snapshot; refresh is the **icon** in that modal (no extra host-page button)  

### Hard no (fail the train if any of these happen)

- [ ] Poll does **not** SSH the fleet  
- [ ] No start/stop / restart from HA  
- [ ] No Move, compose write, Files, console, decrypt keys, OS **apply** from HA  
- [ ] `service_migrate` stays off `POST /api/v1/…/jobs`  
- [ ] Demo never; no plugin in the PiHerder image  
- [ ] Wiki + HACS readme install path exist  

### Slice 1b / 2 (do not block Slice 1 sign-off)

Slice **1b** (Should) and Slice **2** (Discover) have their own sections. First plugin tag is **read-only**.

---

## Q-80 — unit ≥ 75% (Must)

- [ ] Unit line coverage on `app` ≥ **75%**  
- [ ] CI `--cov-fail-under=75` (do **not** raise until the suite meets the bar)  
- [x] Packs landed 2026-09-19 (`test_coverage_v16.py` / `_q2`–`_q11`); suite **~72.2%** (term **72%**; 34055/47192). Fail-under still **70** until 75  
- [ ] No live SSH / HA / two-host copy / mux host in CI  
- [ ] Mux tests: `tests/test_console_mux_v16.py` (service; no live SSH)  
- [ ] HA plugin tests (other repo) mock `/api/v1` only  

---

## Slice 1b — snapshot entities (Should; may slip)

- [ ] Herder read APIs: last docker inventory, fleet services, disk/OS facts (same snapshots the UI stores)  
- [ ] HA container entities (running / uptime / image) from snapshots  
- [ ] HA service up/down + host disk  
- [ ] Still no start/stop from HA  

---

## Docs-archive-0x (Should)

- [ ] `docs/PLAN_v0.*` and `RELEASE_v0.*` live under `docs/archive/v0/`  
- [ ] Stubs at old paths (no 404)  
- [ ] `mkdocs build --strict`  
- [ ] FEATURE_PLAN / ROADMAP / SPEC / ADMIN / 1.0+ PLAN/RELEASE still in `docs/`  

---

## CSP-n Slice 1 (Should; may slip)

- [ ] Per-request script nonce; inline `<script>` stamped  
- [ ] `script-src-attr 'unsafe-inline'`; style still `'unsafe-inline'`  
- [ ] `onclick` **not** rewritten  
- [ ] Report-Only on **demo** before enforce  
- [ ] OpenAPI `/docs` `/redoc` still load  
- [ ] Turnstile login (when keys set) still works  

---

## Undo-1 — fail-path Move undo (Should; may slip)

Needs `PIHERDER_SERVICE_MIGRATE=true` on a disposable stack. Never reverse a **green** Move. Never dest `down -v`.

- [ ] Flag **off** → undo 404  
- [ ] Failed post-flip Move (`cutover` / rebind / `validate`): JobHold **Undo** preview → confirm  
- [ ] DNS/NPM back to source; dest **stopped**; source **started**; dest dir+volumes **stay**  
- [ ] Green Move has no Undo  
- [ ] Pre-flip fail still **Start source stack** (not Undo)  
- [ ] Viewer 403; demo never; token API never POSTs undo  

---

## 1.5 regression

Do not re-run the full 1.5 freeze pack unless chrome drifted. Spot-check:

- [ ] Recycle **web** mid-Move still safe; recycle **worker** still fails honestly  
- [ ] Host lock + HAOS refuse Move dest  
- [ ] Reports pin/hide/reorder + Move jobs card  
- [ ] Expired session → **Sign in** (not JSON `{"detail":"Please log in to continue"}`)  
- [ ] `/api/v1` missing/expired Bearer still JSON 401  
- [ ] Host Reboot uses `systemctl reboot --ignore-inhibitors` (not `--force`)  
- [ ] Mux **off** still matches 1.5 console park  
- [ ] Path 1 HAOS: System info / `ha` CLI check still works  

---

## Screenshots (freeze pack)

Owner: operator (not CI). Replace PNGs under `wiki/assets/screenshots/`; then `mkdocs build --strict`. Capture from rebuilt **`v1.6.0-dev`** (footer still 1.5.0 until freeze).

| Pri | Suggested file | Surface | Must show |
|-----|----------------|---------|-----------|
| **P0** | `console-mux-features.png` | Edit → Features | **Console mux** checkbox; not HAOS |
| **P0** | `console-mux-session.png` | Web SSH with mux on | Banner/note that host mux is attached (tmux or screen) |
| **P0** | `ha-hacs-config.png` | HA config flow | Base URL + token (secret masked) |
| **P0** | `ha-fleet-sensors.png` | HA device/sensors | Fleet counts + one host device |
| **P1** | `ha-visit-host.png` | HA device **Visit** | Lands on `/servers/{id}` |
| **P2** | Recapture only if chrome drifted | Reports / Move JobHold / HAOS | 1.5 pack still good unless broken |

1.4/1.5 Move + Reports pack stays unless a row above says recapture.

---

## Freeze gates

Do not tick until the operator asks to freeze.

- [ ] Mux-1 section signed  
- [ ] HA-p2 Slice 1 section signed (plugin **not** in this image)  
- [ ] Unit ≥ **75%**; CI `--cov-fail-under=75`  
- [ ] `mkdocs build --strict`  
- [ ] **M-flag** still **false** (worker does not imply GA)  
- [ ] Version bump **`1.6.0`** at freeze only  
- [ ] Wiki banner / Home **Next** row / README stay 1.5.0 on **main** until merge  
- [ ] GitHub **Release** (not Issue) after tag — body `docs/RELEASE_v1.6.0.md` when present  

---

## Live notes (this fleet)

| Date | Note |
|------|------|
| 2026-09-19 | Mux-1 code `848116a`; docs `7186298`. `/servers` 500: Alembic stuck at `039` (040 inserted integer `1` into boolean). Fixed `81d7a12`; this instance at `043_console_mux`. |
| 2026-09-19 | Operator: tmux testing “looks great so far”. Mux-1 boxes still empty until each row is walked. |
| 2026-09-19 | HACS **0.1.5**: Visit only. Host-facts **044**. System Info = snapshot + icon. Do not tick HA/Mux boxes from this note. |
| 2026-09-20 | HACS **0.2.2**: Lovelace fleet card loads via `/local/piherder-dashboard-card.js?v=0.2.2` (module). Device page still one Visit. Operator testing the card; HA boxes still empty. |
