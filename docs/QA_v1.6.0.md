# PiHerder v1.6.0 — operator QA / sign-off

**Branch:** `v1.6.0-dev` → `main` · tag **`v1.6.0`** (cut after merge)  
**Code freeze:** *open*  
**Package:** **`1.5.0`** until freeze  
**Operator QA:** *not started*

This file is **maintainer-only** (repo `docs/`). It is **not** published on the operator wiki. Walk the **operator** pages while ticking boxes: [HAOS hosts](../wiki/day-to-day/haos-hosts.md) · [API tokens](../wiki/operations/api-tokens.md) · [web SSH](../wiki/day-to-day/web-ssh-console.md) · [Move a service](../wiki/docker/service-migration.md). Screenshot capture list: [wiki/assets/screenshots/README.md](../wiki/assets/screenshots/README.md).

Plan: [PLAN_v1.6.0.md](PLAN_v1.6.0.md) · HA design: [FEATURE_PLAN_HOME_ASSISTANT.md](FEATURE_PLAN_HOME_ASSISTANT.md) §7.

1.5 production sign-off stays [QA_v1.5.0.md](QA_v1.5.0.md) (historical).

---

## How to run this

| | |
|--|--|
| **Instance** | Rebuild **`v1.6.0-dev`** (`docker compose build web celery-worker && docker compose up -d`). About / footer still **1.5.0** until freeze |
| **HACS** | Separate repo (not this image). HA instance with HACS; PiHerder token `read`; IP allowlist = HA host |
| **Browsers** | Desktop Chrome or Firefox **and** one phone |
| **Accounts** | One **admin**, one **operator** (2FA enrolled), one **viewer** |
| **Hosts** | At least **two** real SSH Docker hosts + one HAOS (never a Move dest). One host with `tmux` or `screen` for Mux-1 |
| **Flags** | Move wizard: `PIHERDER_SERVICE_MIGRATE=true` then recreate **web** (Undo-1 only). Console: `PIHERDER_SSH_CONSOLE=true`. Demo never copies / mux |

---

## HA-p2 Slice 1 — HACS on HA (Must)

- [ ] Config flow: base URL + token + TLS verify + poll interval  
- [ ] Bad token / missing `read` fails closed  
- [ ] Fleet sensors: herder up, host count, OS/container updates, reboot pending, running jobs, Move in progress  
- [ ] One HA **device** per PiHerder host (OS type, last seen, reboot pending, backup age)  
- [ ] **Open in PiHerder** reaches `{origin}/servers/{id}`  
- [ ] Poll does **not** SSH the fleet (DB snapshots only)  
- [ ] No start/stop / Move / Files / console / OS apply from HA  
- [ ] Plugin is **not** in the PiHerder image  
- [ ] Wiki + HACS readme install path  

## Mux-1 — host tmux/screen (Must)

- [ ] Host mux **off** → same PTY as 1.5  
- [ ] Host mux **on**, tmux present → session `ph-u{user}-s{server}-n{tab}`  
- [ ] Hide / app-switch **detaches**; ✕ **kills** the host session  
- [ ] Missing binary → plain PTY + note (console still opens)  
- [ ] Recreate **web** while detached: host session still there  
- [ ] Never apt-install; demo never mux; viewer 403  
- [ ] Privileged vs fleet: no attach across identities  
- [ ] Wiki leftover `ph-u*` on host remove  

## Q-80 — unit ≥ 75% (Must)

- [ ] Unit ≥ 75% (fail-under 75) — CI `--cov-fail-under=75`  
- [ ] No live SSH / HA / two-host copy in CI  

## Slice 1b — snapshot entities (Should; may slip)

- [ ] Herder read APIs: last docker inventory, fleet services, disk/OS facts  
- [ ] HA container entities (running / uptime / image) from snapshots  
- [ ] HA service up/down + host disk  
- [ ] Still no start/stop from HA  

## Docs-archive-0x (Should)

- [ ] `docs/PLAN_v0.*` and `RELEASE_v0.*` live under `docs/archive/v0/`  
- [ ] Stubs at old paths (no 404)  
- [ ] `mkdocs build --strict`  
- [ ] FEATURE_PLAN / ROADMAP / SPEC / ADMIN / 1.0+ PLAN/RELEASE still in `docs/`  

## CSP-n Slice 1 (Should; may slip)

- [ ] Per-request script nonce; 71 inline `<script>` stamped  
- [ ] `script-src-attr 'unsafe-inline'`; style still `'unsafe-inline'`  
- [ ] `onclick` **not** rewritten  
- [ ] Report-Only on **demo** before enforce  
- [ ] OpenAPI `/docs` `/redoc` still load  
- [ ] Turnstile login (when keys set) still works  

## Undo-1 — fail-path Move undo (Should; may slip)

- [ ] Flag **off** → undo 404  
- [ ] Failed post-flip Move (`cutover` / rebind / `validate`): JobHold **Undo** preview → confirm  
- [ ] DNS/NPM back to source; dest **stopped**; source **started**; dest dir+volumes **stay**  
- [ ] Green Move has no Undo  
- [ ] Pre-flip fail still **Start source stack** (not Undo)  
- [ ] Viewer 403; demo never; token API never POSTs undo  

## 1.5 regression

- [ ] Recycle **web** mid-Move still safe; recycle **worker** still fails honestly  
- [ ] Host lock + HAOS refuse  
- [ ] Reports pin/hide/reorder + Move jobs card  
- [ ] Expired session → Sign in (not JSON)  
- [ ] Host Reboot uses `systemctl reboot --ignore-inhibitors`  
