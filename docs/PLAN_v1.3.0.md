# PiHerder v1.3.0 — operator policy, scale UX, multi-identity console, alerts, insights, host files

**Status:** **Active** — branch `v1.3.0-dev`  
**Date opened:** 2026-08-18 (planning capture 2026-08-10)  
**Git branch:** `v1.3.0-dev` (integration) · merge → `main` at freeze → tag `v1.3.0`  
**Package / image version (at tag):** `1.3.0`  
**Theme:** Operator-configurable security policy · multi-identity console · optional command audit · console knobs · map/alert granularity · fleet-scale list UX · Reports history · **host Files manager (flag off)**  
**Baseline:** `v1.2.0` (identity + webshell + gated demo — 2026-08-18)  
**Mode:** Focus · polish · discover · pull-in by capacity · defer enhanced work to **v1.4**  
**Related:** [RELEASE_v1.2.0.md](RELEASE_v1.2.0.md) · [PLAN_v1.2.0.md](PLAN_v1.2.0.md) · [PLAN_v1.4.0.md](PLAN_v1.4.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) P5 · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [FEATURE_PLAN_SSO_OIDC.md](FEATURE_PLAN_SSO_OIDC.md) · [ADMIN.md](ADMIN.md) · [wiki/operations/alerts-email-webhooks.md](../wiki/operations/alerts-email-webhooks.md) · [SECURITY.md](../SECURITY.md)

> **Active train after 1.2.** Ship operator-owned security and console policy, least-priv / privileged **Connect as…**, and lists that stay usable at fleet scale. Discover insights and confined host files; pull Should only when Must is green. Keep `main` patchable for **v1.2.x** while this train runs on `v1.3.0-dev`.

---

## 0. Intent

After 1.2, operators who harden fleets and grow host/container counts need:

1. **Security policy they own** (password rules, 2FA/step-up) without rebuilding images  
2. **Console policy they own** (timeouts, re-auth, concurrency) without only env vars  
3. **Least-privilege by default on the host** — manage with a constrained herder SSH user, open a **privileged** shell only when chosen (separate key/user)  
4. **Deeper optional shell audit** — who ran what in the webshell (commands ± responses), with redaction for secrets  
5. **Alerts they can tune** (severity + what fires on maps / channels)  
6. **Lists that scale** (page size, filters, free-text / semantic search) when many servers and Docker services exist  
7. **At-a-glance reporting** — discovery + a **thin slice** of reporting / custom dashboarding (not a full BI product)  
8. **Host file transfer** — jailed SFTP explorer over fleet SSH, optional privileged Connect as… (edit/zip/search/preview; not console zmodem)

**Carry-over from earlier plans (still in 1.3 path):** fine-grained roles (**AC-fg**), ACME-in-herder (under consideration), residual HA REST/path2, branding, k8s/bare — see §6 and [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md).

**Parked from the 1.2 review (Cap unless Must is green):**

| Item | Notes |
|------|--------|
| Nonce / hash CSP | Drop `'unsafe-inline'` for scripts (eval already gone in 1.2 — compiled Tailwind) |
| First-run UX | Hide Catalog / maps until first host + backup; Settings URL rename |
| One job runtime | Collapse Celery / BackgroundTasks / thread pools; scheduler off the web process |

---

## 1. Decision lock (train open)

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.3.0-dev`** |
| Production line | **`main` @ `v1.2.0`** — hotfixes → **`v1.2.x`**, port into `v1.3.0-dev` |
| Git tag (freeze) | **`v1.3.0`** (RCs: `1.3.0-rc.N` if needed) |
| Image tags (freeze) | `1.3.0` · `1.3` · `latest` (multi-arch); keep `1.2` / `1.2.x` pins valid |
| In-scope streams | **L** lists · **P** password policy · **T** (T6 Must; T1–T5 Should) · **W-id** core · **W-cfg** · **A** · **N** Reports history · **F** Files manager · **W-audit** Deep · **Q** quality/freeze |
| Out-of-focus | Multi-tenant SaaS · SAML · ACME-in-herder as Must · **W-mux** · **AC-fg** implementation · video / dual-control console · full BI · WinSCP-in-herder · service migration (**v1.4**) |
| Mode | Operator-owned policy · no half-built auth / file / audit surfaces · Must → Should → Discover |
| Coverage | **≥ 55%** unit; focused tests for policy, list queries, multi-identity tickets, path jail |
| E2E | Settings policy save · one large list page-size · connect-as privileged confirm · console settings smoke if flag on |
| Semver | Additive minor; document migrations for defaults that change behaviour |
| Version bump | `1.3.0` **at freeze only** (package stays `1.2.0` on this branch until then) |
| Policy storage | **App Settings** (DB) with env as override / bootstrap where it already exists |
| Host SSH identities | At least **two** optional credentials per host: **fleet / least-priv** (default jobs + console + Files) + **privileged** (break-glass console + Files); separate Fernet keys |
| Shell audit | **Opt-in**; default off; command/response is **discover → promote** |
| Insights | **N2 history reports** at `/reports` (Jobs / nmap runs / console Audit) — not Grafana, not status portlets. **N3** Cap |
| Host files | **F Deep** — host **Files** button + ops hero + explorer (edit/zip/perms/search/move/preview/folder upload/`.env` step-up; thin Docker volumes + `docker cp`). API `files` fleet list/get/put. Flag **off** until GA. Richer API → v1.4+ |

```text
main @ v1.2.0 (+ v1.2.x patches)
  └─ v1.3.0-dev → merge → main → tag v1.3.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → Should → Discover | Do not start Discover while Must is open |
| Prod critical bugs | **main** as **1.2.x** first, then port here |
| Demo never grows teeth | Files off · transcripts off · privileged console off |
| Residual Cap | Pull only if **L** + **P** + **T6** + **W-id** core are green |
| Service migration | Stays on [PLAN_v1.4.0.md](PLAN_v1.4.0.md) — do not add to this freeze |

---

## 1a. Kickoff leans (locked 2026-08-18)

| # | Question | Decision |
|---|----------|----------|
| 1 | Which policy stream is Must? | **Deep (signed 2026-08-18):** **P** + **T1–T6**. Force-2FA grace **0–60** days (home-lab). Destructive-job step-up stays Cap. |
| 12 | W-cfg depth | **Deep (signed 2026-08-19):** idle / max / concurrency / ticket / hold / bind / revalidate / scrollback in Settings. Kill switch env-only. Factor knobs remain slice 1. Compose does not inject defaulted `PIHERDER_SSH_CONSOLE_*` or Settings cannot apply. |
| 13 | L depth | **Deep (signed 2026-08-19):** L1–L6 on Servers + Docker + discovery list; L5 aliases; L4 chips compose with `q`; Servers `fav=1` sort; `GET /api/v1/servers` `q`/`limit`/`offset` (cap 100). Integrations / notifications / templates stay later. |
| 14 | W-id depth | **Deep (signed 2026-08-19):** W-id1–9. Two roles only (`fleet` / `privileged`). Privileged console-only. Settings knob who may elevate (`admin` default, or `operator`). Privileged mint always re-prompts 2FA. Alembic `040_ssh_identities`. Dual-write `Server.ssh_*` as fleet cache. |
| 2 | Host files kill switch | **`PIHERDER_HOST_FILES=false`** until **F** is complete enough to turn on; demo stays off either way |
| 3 | Files jail | **Fleet:** docker_base if Docker on, else home; never `/`. **Privileged:** `/` minus `/proc` `/sys` `/dev` `/run`. **HAOS in** (SSH/SFTP). |
| 4 | Files manager verbs | list / get / put / **mkdir** / **delete** (recursive) / **rename** / **move** / **edit** / **zip** / **unzip** / **chmod** / **chown** / **search** (names + contents) / **preview** / **folder upload** / thin **docker cp** + volumes. `.env` step-up. API expansions → v1.4+. |
| 5 | Insights freeze shape | **Revised 2026-08-19:** N2 is **Job/scan/console history** at `/reports` (backups, OS patches, LAN live, Docker deploys, console). Status portlets rejected. **N3** Cap |
| 6 | Command audit | **Deep (signed 2026-08-19):** W-audit0–6. Option A PTY tap. Default **off**. Optional **require on every session**. Own Fernet table (`041`). Privileged warns when off, still allows. Demo never persists. |
| 7 | Host mux (`screen`/`tmux`) | Stay Cap · low priority — do not start |
| 8 | **AC-fg** | Design note only this train. Three global roles remain. Implement only if Must is green **and** capacity |
| 9 | CSP nonces | Cap (parked from 1.2). Do not block freeze |
| 10 | Privileged identity | **Console + Files** (Connect as…); jobs stay on fleet. API Files is fleet-only. |
| 11 | `.env` / PEM download | List always. Open / edit / download / preview / content-search needs 2FA grant (same cookie as privileged Files). |

---

## 1b. Recommended delivery order (parallelizable)

```text
Phase 0  Finish v1.2.0 tag / Hub  ✅ done (v1.2.0)
    │
Phase 1  Foundations (parallel)  ← P+T (slice 1), W-cfg (slice 2), L Deep (slice 3), W-id Deep (slice 4) landed
    ├─ L1 shared list chrome (per_page + pager + q)
    ├─ P1/P2 password-policy settings schema + safe defaults
    └─ W-id1/W-id2 model + migrate 1.x single key
    │
Phase 2  Core Must
    ├─ L2 Servers · Docker · discovery  ← landed with slice 3 Deep
    ├─ P3–P5 copy + audit + docs
    ├─ T6 factor-agnostic account step-up
    └─ W-id3–W-id6 Connect as… + test SSH  ← landed with slice 4 Deep
    │
Phase 3  Should (after Must green)
    ├─ T1–T5 · W-cfg · A  ← A Deep landed (slice 6)
    ├─ N0 → N2  ← landed (slice 7)
    └─ F Deep  ← landed (flag off until operators opt in)
    │
Phase 4  Discover / Cap + freeze
    ├─ N3 · AC-fg note
    ├─ Docs + QA + screenshot pack for new surfaces
    └─ Version bump 1.3.0 · tag · Hub
```

---

## 2. Streams (seed backlog)

### Stream **P** — User-configured password policy

**Today:** Fixed code defaults in `app/services/password_policy.py` — min length 10, upper + lower + digit, specials optional, soft max ~72 bytes (bcrypt). Text is shown on register / change / admin create.  
**Wanted:** Admin (Settings) can set policy for the instance without code changes.

| ID | Item | Notes |
|----|------|--------|
| P1 | Settings UI: min length, require upper/lower/digit/special, optional max length (≤72) | Persist in app settings; `policy_rules_text()` / `validate_password()` read config |
| P2 | Safe defaults + migration | Existing fixed policy becomes the default seed; never weaken silently below a documented floor unless admin opts in |
| P3 | Surface copy everywhere | Register, account password, admin create/reset, recover-admin CLI uses same rules text |
| P4 | Audit on policy change | Who changed rules + summary of new constraints |
| P5 | API / docs | ADMIN + wiki password section; optional read-only `GET` for automation later |

**Non-goals (P):** Per-user policies; breached-password dictionaries (nice later); SSO IdP password rules (out of herder scope).

---

### Stream **T** — User-driven 2FA enforcement and step-up policy

**Today (slice 1 Deep landed):** Settings → Security owns force-2FA scope / grace **0–60** / trusted-device skip rules / step-up windows / factor matrix / IdP-MFA opt-in (fail closed). T6 any-factor mutations. Console factor knobs + grant window are here (not on the Console card). SSO shares the same 2FA helpers as password login.  
**Wanted:** Operators configure **who must enroll**, **when step-up fires**, and **how long step-up grants last** — not only a binary force-2FA flag + env for console. **Done** for T1–T6 (destructive-job step-up stays Cap).

| ID | Item | Notes |
|----|------|--------|
| T1 | Policy matrix in Settings → Security | **Landed.** Force 2FA (all users / admins only / operators+ / off); grace **0–60** days after enable (home-lab); optional trusted-device skip rules |
| T2 | Step-up surfaces catalog | **Landed** for account / secrets / console windows. Destructive jobs stay Cap |
| T3 | Factor policy | **Landed.** Login vs account vs secrets vs console × TOTP / passkey / backup |
| T4 | Alignment with SSO | **Landed.** IdP MFA skip opt-in, default off, fail closed |
| T5 | Audit + break-glass | **Landed.** `security_policy_changed`; recover-admin documented |
| T6 | Account mutation step-up is factor-agnostic | **Landed.** [KI-account-stepup-factors](RELEASE_v1.2.0.md#known-issues-ship-with-awareness) closed on this train |

**Non-goals (T):** Passwordless-only login day one (unless residual after 1.2); per-host 2FA (belongs with **AC-fg**).

**Depends on:** 1.2 WebAuthn + SSO step-up behaviour stable (reuse helpers).

---

### Stream **W-cfg** — Configurable console timeouts, 2FA step-up, session limits

**Today (slice 2 Deep landed):** Settings → **Console** owns idle, max session, concurrency, ticket TTL, park hold, bind IP/device, revalidate, scrollback. Factor knobs + grant window landed with **T** (Security card). Kill switch remains `PIHERDER_SSH_CONSOLE` (compose / env). Env wins when set and non-empty. Bundled compose does **not** inject defaulted `PIHERDER_SSH_CONSOLE_*` knobs. Flag default off.  
**Wanted:** Safe **in-app Settings** (admin) for the knobs operators actually tune, with env as hard ceiling or bootstrap. **Done** (Deep).

| ID | Item | Notes |
|----|------|--------|
| W1 | Settings → Console panel | **Landed.** Idle, max session, max per user + global, ticket TTL. Grant window stays on Security |
| W2 | 2FA step-up knobs in UI | **Landed with slice 1 / T3.** Every-shell, backup codes, prefer/require passkey — do not move onto Console |
| W3 | Precedence rules | **Landed.** Kill switch env-only; Settings fill defaults; env wins if set and non-empty |
| W4 | Live limits without restart | **Landed.** Idle/max/hold/revalidate on next WS tick; concurrency on next new shell (no eviction) |
| W5 | Wiki + DEMO | **Landed.** Demo 403 on write; not a multi-tenant shell farm |

**Non-goals (W-cfg):** Session recording; dual-control console; raising global caps beyond a hard server ceiling (DoS).

**Depends on:** 1.2 Stream **W** shipped and operationally trusted.

---

### Stream **W-id** — Multi-identity host access (least-priv + privileged)

**Today (slice 4 Deep landed):** `ServerSshIdentity` — one **fleet** row per host (jobs + default console; dual-written to `Server.ssh_*`) and optional **privileged** row (console-only). Console **Connect as…** with extra confirm + fresh 2FA. Settings → Console **who may elevate** (`admin` default / `operator`). Alembic `040`. Demo: fleet simulated only.  
**Wanted:** Least-priv fleet user by default; privileged break-glass only when chosen. **Done** for Must core (custom 3rd role, jobs-on-privileged, auto-provision privileged user stay out).

| ID | Item | Notes |
|----|------|--------|
| W-id1 | Data model | e.g. `ServerSshIdentity` (or JSON list): `id`, `label`, `role` (`fleet` / `privileged` / custom), `username`, encrypted private key, optional public key fingerprint, `is_default_for_jobs`, `is_default_for_console`, enabled |
| W-id2 | Migrate 1.x single key | Existing `ssh_username` + key → one **fleet** identity; UI remains simple for single-identity hosts |
| W-id3 | Host edit / onboard UI | Add / rotate / remove identities; never show PEM in clear without step-up; separate upload per identity |
| W-id4 | Console “Connect as…” | Ticket + UI picker: least-priv (default) vs privileged (extra confirm + stronger step-up / audit reason optional) |
| W-id5 | Jobs vs console | Default: **all automated jobs** stay on fleet/least-priv identity only; privileged key **console-only** unless admin later opts a job type in (out of default Must) |
| W-id6 | Test connection | Per-identity “test SSH”; show username + fingerprint in UI/audit, not key material |
| W-id7 | Discovery notes | Document recommended host setup: `piherder` (or deploy user) with docker/rsync group, no password sudo; separate `piherder-admin` / root-capable key for break-glass; deploy public keys via existing SSH deploy path per identity |
| W-id8 | RBAC | Who may open privileged console (admin only vs operator+); pairs with **AC-fg** later (“console elevated” feature gate) |
| W-id9 | Demo | Seed only least-priv synthetic identity; privileged console still disabled under `DEMO_MODE` |

**Product shape (sketch):**

```text
Host: lab-core
  ├─ Identity "fleet"     user=piherder        key=…  ← jobs + default console
  └─ Identity "elevated"  user=piherder-admin key=…  ← console only (opt-in)
Console open → Connect as: [ fleet (default) ▾ | elevated ]
```

**Non-goals (W-id):** Password-based SSH; agent-based multi-user; automatic discovery of all local OS users on the host (optional later spike only); shared break-glass dual-control (two-person rule).

**Depends on:** 1.2 webshell ticket path uses a single identity today — extend ticket payload with `identity_id`.

---

### Stream **W-audit** — Command-level webshell audit (discover → optional ship)

**Today (slice 5 Deep landed):** Opt-in command audit via server-side PTY tap (option A). Settings default **off**; optional **require on every session**. Fernet `consoletranscript` (Alembic `041`). Audit expand + `/audit/console/{id}` + `.txt` download for operator+. Viewer never. Demo never persists. Privileged Connect as… **warns** when audit is off and still allows.  
**Wanted:** Lower-level webshell audit with redaction. **Done** for the 1.3 thin bar (full ttyrec / video stay Cap).

| ID | Item | Notes |
|----|------|--------|
| W-audit0 | **Discovery spike** | Capture options: (A) line-buffered PTY transcript server-side · (B) shell wrapper / `script` · (C) client-sent command events only (weak). Prefer **A** with size caps |
| W-audit1 | Opt-in policy | Settings: off (default) · commands only · commands + truncated output · full session transcript (harder) |
| W-audit2 | Storage | Append-only blob or chunked rows tied to `console_session_id`; retention + max bytes per session; herder self-backup implications |
| W-audit3 | Redaction | Heuristics: password prompts (`password:`, `Password for`, sudo); patterns for tokens; optional “pause audit while typing password” control sequence; never claim perfect secrecy |
| W-audit4 | UI | Session detail: timeline of commands; download/export for admins; viewer role cannot read transcripts |
| W-audit5 | Integrity | Same encryption-at-rest bar as other secrets where feasible; audit **that** a transcript exists even if body purged |
| W-audit6 | Legal / ops docs | Retention, who can view, “this may capture secrets typed at the prompt”, disable in demo |
| W-audit7 | Non-goals clarity | **Not** video session recording; **not** dual-control approval; **not** perfect keystroke timing for forensics lab grade |

**Discovery exit criteria:** Spike proves (or rejects) reliable-enough command boundary detection on interactive bash + redaction of common password prompts without multi-second lag; estimate storage for 1h active session; decide Must vs Cap for 1.3 freeze.

**Security notes:**

- Transcripts are high-sensitivity (may contain secrets despite redaction). Default **off**.  
- Privileged-identity sessions (**W-id**) should force at least “commands only” or warn when audit is off.  
- Demo mode: never persist real transcripts.

**Depends on:** Stable 1.2 console WS path; **W-cfg** for retention knobs may share Settings surface.

---

### Stream **W-mux** — Host-side session multiplexer (`screen` / `tmux`) — **under consideration · low priority**

**Today (1.2):** Web console is a **direct SSH PTY** (`invoke_shell`). Soft resume / Hide & keep parks the PTY on the **herder**, not on the host. GNU **`screen`** / **`tmux` are not started or reattached** by PiHerder (operators may type them manually if installed).

**Wanted (later, optional):** Consider **defaulting console sessions into a host-side multiplexer** so work survives herder restart, long disconnects, and reconnect from another browser — durability **on the host**, independent of herder park.

| ID | Item | Notes |
|----|------|--------|
| W-mux0 | **Stance** | **Under consideration · low priority** — not a 1.3 Must; do not start until W-cfg / W-id / core console ops are stable |
| W-mux1 | Discovery | Prefer `tmux` vs `screen` (availability on Debian/RPi, non-interactive create/attach, naming) |
| W-mux2 | Product options | Off (default today) · opt-in per host · opt-in global · “default on when binary present” |
| W-mux3 | Session model | Named session per user/host (or per shell tab); clean detach on dock/close; reattach on next open |
| W-mux4 | Fallback | If `screen`/`tmux` missing → plain PTY + clear UI note; never fail open of console entirely |
| W-mux5 | Security / ops | Shared hosts: session isolation between operators; wipe on host remove; document residual processes if herder dies mid-session |
| W-mux6 | Demo | Stay **simulated** — no host mux on public demo |

**Why low priority:** Soft park already covers short app-switch UX. Host mux is extra complexity (binary detect, attach races, multi-operator isolation) for longer durability edge cases.

**Non-goals (W-mux):** Replacing herder soft-park; recording inside `screen`; forcing package install on every fleet host without operator consent.

**Depends on:** Mature 1.2+ console; optional synergy with **W-id** (mux only for fleet identity).

---

### Stream **A** — Map alert severity and granular alert options

**Today (slice 6 Deep landed):** Settings → Alerts **Alert policy** (per-category mute / severity / debounce / re-alert). New types: `host_down` (Kuma SSH), `nmap_new_device` (inbox per device, webhook digest per scan), `nmap_device_offline`, `map_infra_down` (gateway/WAN). Alerts page: severity + category filters, pager, bulk-dismiss matching. Webhook/SMTP category allowlist. No herder ping; no nmap port alerts.  
**Wanted:** Clearer **severity mapping** and **granular enable/filters** so map noise (flapping hosts, optional ports, discovery churn) does not equal cert-fail critical. **Done** (Deep).

| ID | Item | Notes |
|----|------|--------|
| A1 | Alert taxonomy review | **Landed.** Catalog in `alert_policy` + wiki table |
| A2 | Per-category severity overrides | **Landed.** Settings → Alerts; type overlays (Network inventory-down dual-write) |
| A3 | Map-specific options | **Landed.** host_down, nmap new/offline, map_infra; debounce / re-alert; map focus URLs. No port-churn alerts. |
| A4 | Channel filters depth | **Landed.** Webhook/SMTP category allowlist; payload `type`/`category` |
| A5 | UI | **Landed.** Severity + category + full type list; pager; bulk dismiss matching |

**Non-goals (A):** Full SIEM; PagerDuty product; multi-tenant routing trees.

---

### Stream **L** — Pagination, page size, free-text / semantic search filters

**Today (slice 3 Deep landed):** Servers, Docker stacks, and discovery **list** share `list_query` chrome — `q`, `per_page` 10/20/50/100, cookie `ph_per_page`, pager. Jobs/Audit use the same clamp + 100. `GET /api/v1/servers` is bounded.  
**Wanted:** **App-wide list pattern** so fleets with many hosts and containers stay usable. **Done** for the Must surfaces.

| ID | Item | Notes |
|----|------|--------|
| L1 | Shared list chrome | **Landed.** `per_page` 10/20/50/100, pager, cookie `ph_per_page` |
| L2 | Priority surfaces | **Landed** for Servers · Docker · discovery list. Integrations / notifications / templates catalog remain later |
| L3 | Free-text filter | **Landed.** Token AND across name, hostname, IP, project, image, MAC, … |
| L4 | Structured filters | **Landed** on existing chips + Servers `fav=1` (pins first, does not hide) |
| L5 | “Semantic” search (pragmatic) | **Landed.** Frozen aliases (`ha` → homeassistant, `pi-hole` → pihole, …). Not ML |
| L6 | Performance | **Landed.** Page after filter. Docker pages **projects**. Discovery list uses SQL offset when `q` is empty. Map unpaged |
| L7 | API alignment | **Thin landed.** `GET /api/v1/servers?q=&limit=&offset=` (default/max 100) + `total` |

**Non-goals (L):** Full-text Postgres extensions required day one; client-only virtual scroll as the only strategy; Elasticsearch dependency.

---

### Stream **N** — Insights: discovery + thin-slice reporting / custom dashboards

**Today (slice 7):** `/` remains the pulse. **Reports** (`/reports`) is **history Grafana never sees**: backups (dest occupancy), OS patch applies, LAN `hosts_up` per day, Docker deploys/patches, web-console sessions. Status portlets were rejected. Wiki: [reports.md](../wiki/day-to-day/reports.md).

| ID | Item | Notes |
|----|------|--------|
| N0 | **Discovery** | **Revised 2026-08-19.** Grafana never has dest size, patch rates, LAN census, Docker deploys, or console time. Status cards are not valuable. Route **`/reports`**. |
| N1 | Metric registry (thin) | Prometheus gauges stay in `insights.py` / `/metrics`. Not the Reports UI |
| N2 | Built-in reports | **Landed (history, not portlets).** `/reports`: backups + OS patches + LAN live + **Docker deploys/patches** + **console sessions**. 7/30/90d |
| N3 | Custom dashboard v1 | **Cap.** No widget picker this freeze |
| N4 | Time windows | **Landed** as 7/30/90 day chips on Reports. No TSDB |
| N5 | Export Cap | Optional CSV/PDF later — not Must |
| N6 | Grafana coexistence | **Landed (docs).** Host graphs → Grafana. PiHerder history → Reports |
| N7 | Demo seed | Demo Job rows fill the tables when present |

**Product shape (landed):**

```text
/reports  (7 / 30 / 90 days)
  ├─ Backups     dest occupancy + ok/fail (Jobs)
  ├─ OS patches  applies + apt packages when logged (Jobs)
  ├─ LAN live    hosts_up per day, carry-forward (NmapScanRun)
  ├─ Docker      deploys / image patches (Jobs); running-now = last inventory
  └─ Console     sessions, privileged, duration (Audit open/close)
```

**Discovery exit criteria:** Written findings; **N2 history reports** (not portlets); **N3 Cap**; reject PromQL / BI / iframe widgets.

**Non-goals (N):** Full Grafana replacement; arbitrary SQL / PromQL builder; multi-tenant shared gallery marketplace; real-time streaming charts; storing high-cardinality metrics history in Postgres forever.

**Depends on:** Stable 1.x data already in DB; **L** helps if boards link into long lists; **A** severity taxonomy improves alert widgets.

---

### Stream **F** — Host files: discovery + thin-slice upload / download

**Today:** PiHerder already moves bytes in **domain-specific** ways. There is **no** generic host file browser.

| Layer | What exists | Limit |
|-------|-------------|--------|
| Browser → herder | Avatar (`POST /auth/account/avatar`, ~2 MiB image) · service logos (`POST /services/{id}/logo`, ~512 KiB) · template zip import · herder-backup restore (multipart, confined to backup roots) · SSH private-key upload at onboard · cert PEMs as **textarea paste** (not a file picker) | Typed + size-checked; not host paths |
| Herder → browser | Herder archive download (`GET /herder-backups/download`, admin, confined) · avatars/logos/static `FileResponse` · host cleanup `.sh` · client-side textarea save · SSE streams (Docker logs, backup/patch progress) | Not “download a file from the Pi” |
| Herder ↔ host (SFTP) | Compose / Dockerfile / project sidecars (`docker_management` / `docker_versions`: 512 KiB text, one-level subdir, tmp+rename) · template deploy `files_for_sftp` · cert deploy (direct SFTP or stage + `sudo install`) · from-host pull of relative config mounts | Text / PEM only; no directory UI |
| Herder ↔ host (rsync) | Backup sources → dest root; restore is reverse rsync | Jobs, not a file picker |
| Web console (1.2) | PTY only | No scp / zmodem / drag-drop |

Reuse, do **not** fork: Paramiko `open_sftp` + tmp+rename (`docker_versions.write_project_files`) · `expand_remote_path` · `backup_path_policy` (deny prefixes, no `..`, allow list) · dest-card + `FEATURE_META` nav · avatar size-cap pattern · console RBAC (operator+, viewer 403, demo kill switch) · `.env` redaction in the compose editor (`env_file_ui`).

**Wanted (F Deep, signed 2026-08-19; UI complete 2026-08-20):** Operators drop a compose sidecar, a Frigate config, or pull a **large** log without leaving for `scp`. Explorer + Connect as… + fleet API list/get/put + 512 MiB streamed uploads + edit + zip/unzip + chmod/chown + search (names + contents) + move + folder upload + preview + `.env` step-up + thin Docker volumes/`docker cp`. Richer token API **under consideration v1.4+**. Not console zmodem.

| ID | Item | Notes |
|----|------|--------|
| F0 | **Discovery** (this capture) | Inventory above; pick jail, size cap, RBAC; decide Files dest-card vs Docker-only vs console accessory |
| F1 | Shared confined SFTP helper | `list` / `stat` / `get` / `put` on one SSH session; resolve jail; reject `..`, NUL, symlink-escape; optional allow/deny prefixes (start from `backup_path_policy` + default OS denies) |
| F2 | Host **Files** dest | Overview **Files** button (not a dest-card) · ops hero · `/servers/{id}/files` explorer; download; upload + **progress**; **mkdir**; **rename** / **move**; **delete** (recursive); **edit**; **zip** / **unzip**; **chmod** / **chown**; **search** (names + contents); preview ‹›; folder upload; Docker volumes + `docker cp`. Pin/jump `FEATURE_META` (`files`). |
| F3 | Jail | **Fleet:** docker_base if Docker on, else home; never `/`. **Privileged:** `/` minus virtual FS. **HAOS in**. |
| F4 | Caps + streaming | Default **512 MiB**. Settings → Files (ceiling **32 GiB**). Env `PIHERDER_HOST_FILES_MAX_BYTES` locks. Stream O(chunk); upload progress bar; attachment download; no inline. |
| F5 | RBAC / demo / audit | operator+; viewer 403; demo off. Audit `host_file_*` path + bytes + sha256, never body. |
| F6 | Secret-ish names | Show in listing. Open / edit / download / preview / content-search needs 2FA grant (same cookie as privileged Files). Compose editor still redacts `.env`. |
| F7 | Identity | Default **fleet**. Optional **privileged** like console (same elevate RBAC + 2FA grant). API fleet-only (`files` scope). |
| F8 | Wiki + ADMIN | Files vs Docker editor vs Backups vs certs; HAOS; flag; API. |

**Product shape (F Deep):**

```text
Host: rpi5-4  →  dest card Files
  Connect as: [ fleet ▾ | privileged ]
  Jail: /home/pi/docker          (fleet)  or  /  minus virtual FS (privileged)
  /home/pi/docker/frigate/
    ├─ docker-compose.yml     4.2 KiB   [Download] [Rename] [Delete]
    ├─ config.yml            18 KiB    [Download] [Rename] [Delete]
    └─ [New folder…]  [Upload file…  progress]
```

**Discovery exit criteria:** F0 review 2026-08-19: manager verbs (not list/get/put-only); HAOS in; privileged Connect as…; API `files`; 512 MiB + progress. **Promoted 2026-08-20:** edit, zip/unzip, recursive delete, multi-select, chmod/chown, search (names + contents), move, folder upload, preview, `.env` step-up, thin Docker volumes + `docker cp` into the jail. Console zmodem still out. Richer Files API → v1.4+.

**Security notes:**

- Least-priv SSH already limits SFTP to that user’s rights — jail is defense-in-depth if the key is over-privileged.  
- Resolve realpath on the host (or `stat` + refuse `S_ISLNK` that leaves the jail).  
- Writes use tmp + rename; never overwrite via unguarded `open`.  
- Kill switch: **`PIHERDER_HOST_FILES=false`** until operators opt in. Same family as the console flag.  
- Public demo must not expose real host trees.

**Non-goals (F) / defer past this minor:**

| Defer | Why |
|-------|-----|
| **API Files expansions** (zip / edit / chmod / recursive delete / privileged tokens) | **Under consideration v1.4+** — 1.3 API stays fleet list/get/put/mkdir/rename/empty-delete. Privileged + extra verbs stay UI + 2FA. |
| Console drag-drop / zmodem / `scp` from xterm | Separate from PTY; high XSS/DoS surface |
| Full media gallery / video player | Preview still images + hex peek only |
| Custom map icon pack (**M5**) | Adjacent upload, different store (`DATA_ROOT`) |
| Cert PEM file-picker | Nice polish on existing paste form — not this stream |
| Git-rich onboard (**Q**) | Already post-1.0 |

**Depends on:** Stable 1.2 SSH client + least-priv user; dest-card chrome; optional later **W-id** (fleet identity) and **T** (step-up on secret downloads).

---

## 3. Ship bar (locked 2026-08-18)

| Priority | Streams | Bar |
|----------|---------|-----|
| **Must** | **P** + **T1–T6** (slice 1 **Deep**) · **L Deep** (slice 3 — Servers + Docker + discovery) · **W-id Deep** (slice 4 — fleet + privileged + Connect as…) | Operator-owned security policy (full) + scale lists + least-priv/privileged connect-as |
| **Should** | **W-cfg Deep** · **A** · **W-audit Deep** (slice 5) · **N2** `/reports` history (after **N0**) · **F Deep** host Files manager (after **F0** sign-off) | Console knobs + alerts + opt-in command audit + thin reporting + confined host file transfer |
| **Discover / Cap** | **N0** discovery · **N3** custom layout · **W-mux** (screen/tmux, low priority) · **AC-fg** · ACME · branding · CSP nonces | Promote only if Must green |

Success criteria:

1. Admin can configure password policy; all password entry paths enforce it and show the same rules.  
2. Account SSO unlink + passkey revoke accept **any enrolled 2FA** (**T6**). Admin can set force-2FA scope / grace **0–60** days / step-up windows / factor matrix / IdP-MFA opt-in (**T1–T5**, default fail-closed).  
3. *(Should)* Console idle/max/concurrency/ticket/hold/bind/revalidate/scrollback adjustable in Settings without editing compose for common cases. `PIHERDER_SSH_CONSOLE` stays the env kill switch.  
4. Host can store **fleet** + **privileged** SSH identities (separate keys/users); console offers **Connect as…**; jobs stay on fleet by default. **Done** (slice 4 Deep).  
5. Opt-in command (± truncated output) audit with redaction heuristics and retention; default off; optional require-on-every-session; wiki warns about residual secret capture. **Done** (slice 5 Deep).  
6. *(Should)* Map/stack/cert-style alerts have documented severities and per-category tuning; channels respect filters. **Done** (slice 6 Deep).  
7. Servers + Docker + discovery lists support page size + free-text filter without loading unbounded HTML. **Done** (slice 3 Deep).  
8. *(Should)* Operators have **Reports** of PiHerder history Grafana cannot see (backups dest, OS patches, LAN live, Docker deploys, console sessions). **Done** (slice 7).  
9. *(If F promoted)* Operator can open **Files** on any SSH host (including HAOS), browse the jail, download / upload (progress, 512 MiB), mkdir / rename / move / recursive delete / edit / zip / search / preview; Connect as fleet (default) or privileged; `.env` needs 2FA to open; viewer cannot; API `files` is fleet list/get/put; path escape and oversize rejected; demo does not expose a real tree.

---

## 4. Quality bar (locked)

| Gate | Target |
|------|--------|
| Unit | Hold ≥ 55% (raise only if easy) |
| Tests | Policy validate matrix · settings round-trip · list query unit tests · console limit apply · multi-identity ticket + redaction unit tests · **F** path-jail / symlink-escape / size-cap unit tests |
| E2E | Settings policy save · one large list page-size · connect-as privileged confirm · console settings smoke if flag on |
| Docs | ADMIN + wiki Security / Alerts / Console (identities + audit) / list UX / Reports / **Host files**; `mkdocs build --strict` at freeze |
| Security | Policy changes audited; privileged console extra step-up; transcripts access-controlled; demo never stores real shell transcripts; dashboard widgets respect RBAC; **Files** jailed + audited + viewer-denied; demo tree off |

---

## 5. Dependencies on v1.2

| 1.2 deliverable | Why 1.3 needs it |
|-----------------|------------------|
| WebAuthn + step-up helpers | **T** / **W-cfg** factor policy builds on the same paths |
| SSO 2FA parity | **T** must not re-fork SSO vs password |
| Webshell tickets + env knobs | **W-cfg** moves knobs into settings; **W-id** extends ticket with identity; **W-audit** taps the same WS stream |
| Single-key server model | Migration baseline for **W-id**; **F** uses that same fleet identity |
| Paramiko SFTP (compose / certs / templates) | **F** extracts a confined helper — do not add a second SSH stack |
| Demo mode IP scrub / OpenAPI gate | Keep demo safe when settings surfaces expand; no shell transcripts on demo |
| Force 2FA + trusted devices | Baseline for **T** grace / skip rules |

1.2 bugs and polish found during capture go on **1.2** (or 1.2.x), not this document, unless explicitly deferred here.

---

## 6. Carry-over / residual (already on 1.3 path)

| Theme | Source | Notes |
|-------|--------|--------|
| **AC-fg** fine-grained roles | ROADMAP · PLAN_v1.1 §6 · PLAN_v1.2 §10 | **Train-open stance:** stay Cap. Keep three global roles. Per-host allowlist / per-feature gates are a later design — do not start schema this freeze unless Must is green **and** capacity remains |
| **P-acme** ACME-in-herder | PLAN_v1.1 §6.1 | Under consideration — not a Must for this seed |
| HA REST / path 2 | FEATURE_PLAN_HOME_ASSISTANT | Residual integration |
| Full insights beyond thin slice · branding · k8s/bare | ROADMAP H3 / quality | **N** seeds thin slice; deep BI stays far horizon |
| Host file manager beyond list/get/put | New 2026-08-16 | **F** seeds confined transfer; WinSCP / `docker cp` / zmodem stay deferred |
| **Service migration** (host→host compose move) | New 2026-08-17 | **→ v1.4 Stream M** — not a 1.3 add. [PLAN_v1.4.0.md](PLAN_v1.4.0.md) |

---

## 7. Out of scope (stay honest)

- Multi-tenant SaaS / org isolation  
- Replacing NPM with full ACME product as a 1.3 Must  
- Vector/embedding “AI search” as a hard dependency  
- **Video / full interactive session replay** and **dual-control** (two-person) console — still out; **W-audit** is opt-in command/transcript style only  
- Guaranteeing redaction catches every secret typed at a shell  
- Auto-enumerating all OS users on a host as “identities”  
- **Full custom BI** (arbitrary SQL/PromQL, multi-page analytics, Grafana replacement) — **N** is discover + thin slice only  
- **Full remote file manager** (WinSCP clone, console zmodem) — **F** is confined SFTP in the jail (thin `docker cp` into the jail / volume open; not the migrate copy engine)  
- **Service migration** (move a compose project host→host with dataset + DNS + TLS/Kuma) — **→ v1.4** ([PLAN_v1.4.0.md](PLAN_v1.4.0.md) · [FEATURE_PLAN_SERVICE_MIGRATION.md](FEATURE_PLAN_SERVICE_MIGRATION.md))  
- Weakening public **demo** into a multi-user admin sandbox  

---

## 8. Capture log

| Date | Note |
|------|------|
| 2026-08-10 | Opened while finishing **v1.2** demo/ops. Seed streams: **P** password policy · **T** 2FA/step-up policy · **W-cfg** console timeouts/limits/step-up · **A** map alert severity + granular alerts · **L** pagination + free-text/smart search. |
| 2026-08-10 | Added **W-id** multi-identity host SSH (least-priv fleet user + privileged user, separate keys, Connect as…) and **W-audit** discover lower-level webshell audit (commands + responses, optional password redaction). |
| 2026-08-10 | Final seed item: **N** insights — discovery + thin-slice reporting / custom dashboarding (metric registry, built-in fleet board, optional one custom layout; not Grafana). Planning capture for 1.3 considered complete for operator-requested themes. |
| 2026-08-11 | **W-mux**: optional host-side `screen`/`tmux` default for web console — **under consideration · low priority** (not 1.2; not a 1.3 Must). Soft park remains herder-side only today. |
| 2026-08-11 | **Carry from 1.2:** **KI-console-mobile-soft-tab** — mobile soft-Tab / IME path-completion polish (desktop OK). **v12** landed in 1.2 QA (flush + mute + drop echo); residual exotic IMEs only. |
| 2026-08-16 | **F** host files — discovery + thin-slice upload/download (confined SFTP list/get/put under `docker_base_dir` / home). Not a 1.2 add; not 1.1.1. Inventory of existing avatar/logo/backup/compose/cert SFTP paths captured in-stream. |
| 2026-08-17 | **Service migration** requested (stop → dataset copy → CNAME → both Pi-hole restartdns → dest start → TLS/Kuma · host lock for HAOS / Frigate TPU). Parked on **v1.4** — not this train. |
| 2026-08-18 | **T6 / KI-account-stepup-factors** from 1.2 QA: unlink TOTP-first; passkey revoke password-only. |
| 2026-08-18 | **Train opened** on `v1.3.0-dev`. Must/Should locked. Phase 1 current. Package version stays `1.2.0` until freeze. |
| 2026-08-18 | **Slice 1 Deep signed.** Policy Must = **P + T1–T6**. Force-2FA grace **0–60** days (home-lab). Destructive-job step-up Cap. |
| 2026-08-18 | **Slice 1 landed** on `v1.3.0-dev`: Settings password policy + force-2FA scope/grace + step-up windows + factor matrix + T6 any-factor mutations + T4 IdP MFA opt-in (fail closed). |
| 2026-08-19 | **Slice 2 Deep signed + landed:** W-cfg timeouts / concurrency / ticket / hold / bind / revalidate / scrollback in Settings → Console. Kill switch env-only. Compose no longer injects defaulted `PIHERDER_SSH_CONSOLE_*` knobs. |
| 2026-08-19 | **Docs pass:** wiki Settings / console / env / upgrades / 2FA / roles / demo + ADMIN / ROADMAP / SECURITY / CONTRIBUTING aligned with slices 1–2. |
| 2026-08-19 | **Slice 3 Deep signed + landed:** L list chrome on Servers + Docker + discovery. Shared cookie `ph_per_page`. API `/servers` capped at 100. |
| 2026-08-19 | **Slice 4 Deep signed + landed:** W-id fleet + privileged identities, Connect as…, Settings privileged-role knob, Alembic `040_ssh_identities`. |
| 2026-08-19 | **Slice 5 Deep signed + landed:** W-audit command audit (option A PTY tap, Fernet table `041`, Settings default off, optional require-all-sessions, privileged warn-when-off). |
| 2026-08-19 | **Slice 6 Deep signed + landed:** Stream **A** alert policy + map surface (host_down, nmap new/offline, map_infra, debounce/re-alert, Alerts filters). |
| 2026-08-19 | **Settings IA:** General (and Alerts policy) became hub cards + Edit modals so v1.3 policy forms are not a single scroll. |
| 2026-08-19 | **N0 signed:** Reports lives at `/reports` (not on `/`). Status portlets **rejected**. Grafana never sees dest size, patch rates, LAN census, Docker deploys, or console time. |
| 2026-08-19 | **Slice 7 N2/N4:** `/reports` history: backups, OS patches, LAN live, Docker deploys/patches, console sessions (7/30/90d). Status portlets not shown. Docs/wiki aligned. Next after test: review plan → **F0**. |
| 2026-08-19 | **F0 review → F Deep:** manager verbs (mkdir/delete/rename), HAOS in, privileged Connect as…, API `files` (fleet), 512 MiB streamed uploads + progress. Flag still off. |
| 2026-08-20 | **F promoted:** in-page text edit (compose-editor feel, 512 KiB), zip of files/folders, unzip with zip-slip refusal, multi-select, recursive delete. |
| 2026-08-20 | **F chmod/chown:** privileged Files; SFTP first; `sudo -n` if that identity is not root. Fleet may chmod files it owns. |
| 2026-08-20 | **F search / move / folder upload:** name search from current folder; SFTP move across dirs; webkitdirectory + drag-drop tree (zip-slip refused). |
| 2026-08-20 | **F API expansions → v1.4+ under consideration** (zip/edit/chmod/recursive/privileged tokens). 1.3 API stays fleet list/get/put. |
| 2026-08-20 | **F remaining UI:** content grep, image/hex preview, extra step-up for `.env`/PEM, thin Docker volumes + `docker cp` into the jail. |
| 2026-08-20 | **F polish:** Files **button** (not dest-card); fleet **nav** (`user` in template); ops hero; ⋯-only extra actions; list scrolls in-pane; zip **on host**; Settings transfer cap (32 GiB); preview ‹›; privileged save via `sudo -n tee`. |

Add deferred 1.2 items here as one-line bullets when freeze decides “→ 1.3”.

- **KI-console-mobile-soft-tab** — residual exotic-IME cases after 1.2 QA **v12** (main `cd do` → `docker/` path is in 1.2).
- **KI-account-stepup-factors** — Account SSO unlink + passkey revoke should accept any enrolled 2FA (Stream **T6**).

---

## 9. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Finish **v1.2.0** freeze / tag / Hub | **Done** — `v1.2.0` tagged · Hub multi-arch published |
| 2 | Open **`v1.3.0-dev`** + lock Must/Should | **Done** 2026-08-18 |
| 3 | Slice 1 Deep **P + T1–T6** | **Done** |
| 4 | Slice 4 Deep **W-id** (fleet + privileged + Connect as…) | **Done** |
| 5 | Slice 3 Deep **L** (Servers + Docker + discovery) | **Done** |
| 6 | Slice 5 Deep **W-audit** (opt-in commands ± truncated output; require-all-sessions option) | **Done** |
| 7 | Slice 6 Deep **A** (alert policy + map/discovery surface) | **Done** |
| 8 | Run **N0** insights discovery (one-pager) → **N2** | **Done** — `/reports` history (backups, OS, LAN, Docker, console) |
| 9 | Run **F0** files sign-off → **F Deep** (flag off until ready) | **Done** (flag still off; phone pass while testing) |

**Phase 1 execution order (parallelizable):** **L1** shared list chrome · **P1/P2** password-policy schema · **W-id1/W-id2** identity model + migrate single key.

Service migration stays on [PLAN_v1.4.0.md](PLAN_v1.4.0.md) — do not add it to this freeze.

---

*Living on `v1.3.0-dev` until freeze into `RELEASE_v1.3.0.md`.*
