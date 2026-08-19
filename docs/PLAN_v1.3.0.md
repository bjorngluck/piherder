# PiHerder v1.3.0 — operator policy, scale UX, multi-identity console, alerts, insights, host files

**Status:** **Active** — branch `v1.3.0-dev`  
**Date opened:** 2026-08-18 (planning capture 2026-08-10)  
**Git branch:** `v1.3.0-dev` (integration) · merge → `main` at freeze → tag `v1.3.0`  
**Package / image version (at tag):** `1.3.0`  
**Theme:** Operator-configurable security policy · multi-identity console · optional command audit · console knobs · map/alert granularity · fleet-scale list UX · thin-slice reporting / custom dashboards · **host file transfer (discover + thin slice)**  
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
8. **Host file transfer** — discovery + a **thin slice** of upload/download over the existing SSH/SFTP identity (not WinSCP-in-herder)

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
| In-scope streams | **L** lists · **P** password policy · **T** (T6 Must; T1–T5 Should) · **W-id** core · **W-cfg** · **A** · **N** discover + thin slice · **F** discover + thin slice · **W-audit** discover · **Q** quality/freeze |
| Out-of-focus | Multi-tenant SaaS · SAML · ACME-in-herder as Must · **W-mux** · **AC-fg** implementation · video / dual-control console · full BI · WinSCP-in-herder · service migration (**v1.4**) |
| Mode | Operator-owned policy · no half-built auth / file / audit surfaces · Must → Should → Discover |
| Coverage | **≥ 55%** unit; focused tests for policy, list queries, multi-identity tickets, path jail |
| E2E | Settings policy save · one large list page-size · connect-as privileged confirm · console settings smoke if flag on |
| Semver | Additive minor; document migrations for defaults that change behaviour |
| Version bump | `1.3.0` **at freeze only** (package stays `1.2.0` on this branch until then) |
| Policy storage | **App Settings** (DB) with env as override / bootstrap where it already exists |
| Host SSH identities | At least **two** optional credentials per host: **fleet / least-priv** (default jobs + console) + **privileged** (break-glass console); separate Fernet keys |
| Shell audit | **Opt-in**; default off; command/response is **discover → promote** |
| Insights | **Discover + N2 thin slice** — compose existing fleet signals; not a second Grafana |
| Host files | **Discover + F2 thin slice** — confined list / get / put under a jail; flag **off** until GA |

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
| 2 | Host files kill switch | **`PIHERDER_HOST_FILES=false`** until **F2** is complete enough to turn on; demo stays off either way |
| 3 | Files jail | **`docker_base_dir`** (expanded `~`) on Docker hosts; else SSH user home. Never `/`. HAOS out of thin slice |
| 4 | Files thin-slice shape | **F2 only** — list / get / put. No mkdir / delete / rename this freeze |
| 5 | Insights freeze shape | **N0** one-pager then **N2** built-in fleet board. **N3** custom layout stays Cap |
| 6 | Command audit | Spike **W-audit0** after W-id core; default **off**; demo never persists transcripts |
| 7 | Host mux (`screen`/`tmux`) | Stay Cap · low priority — do not start |
| 8 | **AC-fg** | Design note only this train. Three global roles remain. Implement only if Must is green **and** capacity |
| 9 | CSP nonces | Cap (parked from 1.2). Do not block freeze |
| 10 | Privileged identity | **Console-only** by default; jobs stay on fleet |
| 11 | `.env` / PEM download | Wiki-warn in thin slice; extra step-up is Cap |

---

## 1b. Recommended delivery order (parallelizable)

```text
Phase 0  Finish v1.2.0 tag / Hub  ✅ done (v1.2.0)
    │
Phase 1  Foundations (parallel)  ← current
    ├─ L1 shared list chrome (per_page + pager + q)
    ├─ P1/P2 password-policy settings schema + safe defaults
    └─ W-id1/W-id2 model + migrate 1.x single key
    │
Phase 2  Core Must
    ├─ L2 Servers · Docker · discovery
    ├─ P3–P5 copy + audit + docs
    ├─ T6 factor-agnostic account step-up
    └─ W-id3–W-id6 Connect as… + test SSH
    │
Phase 3  Should (after Must green)
    ├─ T1–T5 · W-cfg · A
    ├─ N0 → N2
    └─ F0 sign-off → F2 (flag off until ready)
    │
Phase 4  Discover / Cap + freeze
    ├─ W-audit0 promote-or-Cap · N3 · AC-fg note
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

**Today:** Instance **Force 2FA** (enroll wall); login step-up when TOTP/passkey enrolled; secrets step-up cookie; webshell step-up with env knobs (`REQUIRE_2FA_EVERY_SHELL`, passkey prefer/require, backup codes). SSO shares the same 2FA helpers as password login.  
**Wanted:** Operators configure **who must enroll**, **when step-up fires**, and **how long step-up grants last** — not only a binary force-2FA flag + env for console.

| ID | Item | Notes |
|----|------|--------|
| T1 | Policy matrix in Settings → Security | Force 2FA (all users / admins only / operators+ / off); grace **0–60** days after enable (home-lab); optional trusted-device skip rules |
| T2 | Step-up surfaces catalog | Document + configure: secrets view, sensitive account actions, console open, (optional) destructive jobs — each with re-auth window minutes |
| T3 | Factor policy | Allow TOTP / passkey / backup codes for **login** vs **step-up** (e.g. console: passkey preferred / required; backup codes never for shell) |
| T4 | Alignment with SSO | Keep “IdP MFA does not replace herder 2FA” unless an explicit admin option; no silent skip |
| T5 | Audit + break-glass | Policy changes audited; sole-admin / recover path documented when force-2FA + lost factors |
| T6 | Account mutation step-up is factor-agnostic | **Carry [KI-account-stepup-factors](RELEASE_v1.2.0.md#known-issues-ship-with-awareness).** SSO unlink/link: any enrolled 2FA (passkey step-up, TOTP, backup code) — not TOTP-only when TOTP is present. Passkey revoke: same helper (any other 2FA or remaining passkey), not password-only. Password remains the fallback when no 2FA is enrolled. |

**Non-goals (T):** Passwordless-only login day one (unless residual after 1.2); per-host 2FA (belongs with **AC-fg**).

**Depends on:** 1.2 WebAuthn + SSO step-up behaviour stable (reuse helpers).

---

### Stream **W-cfg** — Configurable console timeouts, 2FA step-up, session limits

**Today:** Webshell limits are largely **env-only** (`PIHERDER_SSH_CONSOLE_*`: idle, max session, ticket TTL, max per user / global, grant minutes, revalidate, every-shell 2FA, bind IP/device, scrollback). Flag default off.  
**Wanted:** Safe **in-app Settings** (admin) for the knobs operators actually tune, with env as hard ceiling or bootstrap.

| ID | Item | Notes |
|----|------|--------|
| W1 | Settings → Console (or Security) panel | Idle timeout, max session length, max concurrent shells per user + global, ticket lifetime, step-up grant window |
| W2 | 2FA step-up knobs in UI | Require 2FA every new shell; allow backup codes; prefer/require passkey — same semantics as env, stored in settings |
| W3 | Precedence rules | Document: env kill switch still master; settings fill defaults; optional “env wins if set” for air-gapped deploys |
| W4 | Live limits without restart | Prefer settings reload without full process restart where safe |
| W5 | Wiki + DEMO | Demo keeps console off; document that public demo does not expose these knobs as a multi-tenant shell farm |

**Non-goals (W-cfg):** Session recording; dual-control console; raising global caps beyond a hard server ceiling (DoS).

**Depends on:** 1.2 Stream **W** shipped and operationally trusted.

---

### Stream **W-id** — Multi-identity host access (least-priv + privileged)

**Today (1.2):** One SSH identity per server (`ssh_username` + one encrypted private key). Jobs and webshell both use that identity. Operators who want least-privilege fleet automation must either over-privilege the herder user or rekey manually outside the product.  
**Wanted:** Model **multiple named identities** per host (start with two), pick which to use for **console** (and later optionally for specific job classes). **Discover** enrollment UX and sudo/capability notes during 1.3 design.

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

**Today (1.2):** Audit rows for console open / close / grant / deny (+ client IP, duration, actor). Interactive **command capture** was intentionally **best-effort / not promised** ([FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) P5).  
**Wanted:** **Discover** a lower level of auditing: record **commands issued** via webshell and **responses** (or summaries), with **optional redaction** when passwords/secrets appear. Opt-in per instance or per session.

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

**Today:** Notifications have severity (`info` / `warning` / `critical`); webhook/SMTP min severity; some stack-health / cert verify alerts; map and inventory surfaces raise alerts with limited operator control over *which* map events and *how loud*.  
**Wanted:** Clearer **severity mapping** and **granular enable/filters** so map noise (flapping hosts, optional ports, discovery churn) does not equal cert-fail critical.

| ID | Item | Notes |
|----|------|--------|
| A1 | Alert taxonomy review | Inventory map / stack / discovery / cert / job event types → default severity table (documented) |
| A2 | Per-category severity overrides | Settings: e.g. “inventory down” = warning, “cert verify fail” = critical; optional mute categories |
| A3 | Map-specific options | Which map edges/devices raise alerts; optional debounce / re-alert interval; link back to map focus |
| A4 | Channel filters depth | Beyond min severity: event allowlist/denylist per webhook and mail (extend Wh-lite) |
| A5 | UI | Alerts page filters by severity + category; bulk resolve by category |

**Non-goals (A):** Full SIEM; PagerDuty product; multi-tenant routing trees.

---

### Stream **L** — Pagination, page size, free-text / semantic search filters

**Today:** Jobs and Audit already use **per-page** + filters; many dense surfaces (Servers list, Docker services/stacks, discovery devices, templates, notifications, maps device lists) load large tables or cards with limited paging / inconsistent search.  
**Wanted:** **App-wide list pattern** so fleets with many hosts and containers stay usable.

| ID | Item | Notes |
|----|------|--------|
| L1 | Shared list chrome | `per_page` choices (e.g. 10/20/50/100), page controls, total count, remember preference (user or cookie) |
| L2 | Priority surfaces | Servers list · server Docker (projects/services) · discovery devices · integrations lists · notifications · templates catalog · (extend jobs/audit consistency) |
| L3 | Free-text filter | Case-insensitive match across name, hostname, IP, labels, project, image — same “search box” pattern as Audit |
| L4 | Structured filters | Status, role/kind, host, unhealthy only, favourites first — composable query params |
| L5 | “Semantic” search (pragmatic) | **Not** embedding ML day one: tokenised multi-field search + optional synonym/aliases (e.g. `ha` → homeassistant); document as **smart free-text**, not vector search |
| L6 | Performance | Server-side limit/offset or keyset; avoid loading entire Docker inventory into the browser when possible |
| L7 | API alignment | Optional `limit`/`offset`/`q` on list-ish `/api/v1` endpoints if still missing |

**Non-goals (L):** Full-text Postgres extensions required day one; client-only virtual scroll as the only strategy; Elasticsearch dependency.

---

### Stream **N** — Insights: discovery + thin-slice reporting / custom dashboards

**Today:** Dashboard / ops-hero pulses, Jobs, Audit, Alerts, per-host Overview, maps, and integration detail pages. No operator-owned **report layout** or savable **custom dashboard** of mixed widgets. Roadmap item **N** was “discovery + first thin slice post v1.0” ([ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md)).  
**Wanted:** Run a short **discovery** (what operators actually want on one screen), then ship a **thin slice** — not Grafana-in-herder.

| ID | Item | Notes |
|----|------|--------|
| N0 | **Discovery** | Interview / ops notes: top 5 “I open PiHerder to see…”. Inventory existing data sources (hosts online, job success rate, open alerts by severity, cert expiry, backup last-ok, docker unhealthy, nmap new devices, map down edges). Decide home vs dedicated **Reports** route |
| N1 | Metric registry (thin) | Named, versioned metrics/cards: `id`, label, query or service call, refresh hint, RBAC (viewer-safe). Reuse existing services — no parallel warehouse |
| N2 | Built-in “Fleet health” board | One default dashboard: 4–8 fixed widgets from the registry (counts + links into existing pages). Good enough for most single-operator labs |
| N3 | Custom dashboard v1 | User (or admin) can **add / remove / reorder** widgets from the registry on **one** personal or instance board; persist layout JSON; no arbitrary SQL |
| N4 | Time windows (optional Cap) | “Last 24h / 7d” on job/audit derived cards only where cheap; no long-term TSDB |
| N5 | Export Cap | Optional CSV/PDF of a single summary card or board later — not Must |
| N6 | Grafana coexistence | Keep deep metrics/graphs in Grafana; herder boards are **ops summary + navigation**, not timeseries product. Document “when to use which” |
| N7 | Demo seed | Seed a pretty default board so the public demo shows the surface |

**Product shape (sketch):**

```text
Reports / Dashboard (custom)
  ├─ Widget: Hosts up/down          → /servers?status=…
  ├─ Widget: Open alerts by severity → /notifications
  ├─ Widget: Backups stale          → /servers?… or jobs
  ├─ Widget: Certs expiring ≤30d    → certificates
  └─ [ + Add widget ] from registry
```

**Discovery exit criteria:** Written one-pager of N0 findings; pick **N2 only** vs **N2+N3** for freeze; reject scope creep (custom PromQL, multi-page BI, embedding iframes of random apps as “widgets” without security review).

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

**Wanted:** Operators who already use the webshell still drop to `scp` / FileZilla to drop a compose sidecar, a Frigate config, or pull a log. Run **discovery**, then ship a **thin slice** — confined list / get / put — not WinSCP-in-herder.

| ID | Item | Notes |
|----|------|--------|
| F0 | **Discovery** (this capture) | Inventory above; pick jail, size cap, RBAC; decide Files dest-card vs Docker-only vs console accessory |
| F1 | Shared confined SFTP helper | `list` / `stat` / `get` / `put` on one SSH session; resolve jail; reject `..`, NUL, symlink-escape; optional allow/deny prefixes (start from `backup_path_policy` + default OS denies) |
| F2 | Host **Files** dest-card | `/servers/{id}/files` — breadcrumb + one-directory listing (name, type, size, mtime); **download one file**; **upload one file** into the current dir. Pin/jump via `FEATURE_META` (`files`) |
| F3 | Default jail | **`docker_base_dir`** (expanded `~`) on Docker hosts; else SSH user home. Never `/`. HAOS: **out** of thin slice (no compose tree; document) |
| F4 | Caps + streaming | Upload hard cap (lean **16 MiB**, Settings/env later); download **stream** (`StreamingResponse`), do not `read()` whole file into RAM; `Content-Disposition: attachment`; no inline execute |
| F5 | RBAC / demo / audit | **operator+** for list/get/put; **viewer 403** (files are often secrets). Demo: disable or empty simulated tree. Audit `host_file_list` / `host_file_get` / `host_file_put` with **path + bytes + sha256**, never body |
| F6 | Secret-ish names | Thin slice: treat like compose editor (operator, no extra step-up) **but** wiki-warn `.env` / keys / PEMs. Cap: step-up on download, or block `id_rsa` / `*.pem` / `.env` unless confirmed |
| F7 | Identity | 1.2: same single fleet SSH user as jobs. **W-id:** Files stay on **fleet** identity; privileged identity is console-only unless later opted in |
| F8 | Wiki + ADMIN | When to use Files vs Docker editor vs Backups vs cert deploy; least-priv “SFTP only sees what the user can”; demo off |

**Product shape (thin slice):**

```text
Host: rpi5-4  →  dest card Files
  Jail: /home/pi/docker
  /home/pi/docker/frigate/
    ├─ docker-compose.yml     4.2 KiB   [Download]
    ├─ config.yml            18 KiB    [Download]
    └─ [Upload file…]
```

**Discovery exit criteria:** Written decision on (1) jail = docker_base vs home vs per-host allow list; (2) **F2 only** vs F2 + mkdir/delete; (3) whether `.env` download needs step-up. Reject scope creep (full manager, `docker cp`, console zmodem) at freeze.

**Security notes:**

- Least-priv SSH already limits SFTP to that user’s rights — jail is defense-in-depth if the key is over-privileged.  
- Resolve realpath on the host (or `stat` + refuse `S_ISLNK` that leaves the jail).  
- Writes use tmp + rename; never overwrite via unguarded `open`.  
- Kill switch: **`PIHERDER_HOST_FILES=false`** until **F2** is ready to turn on (locked at train open). Same family as the console flag.  
- Public demo must not expose real host trees.

**Non-goals (F) / defer past this minor (or later 1.3 Cap):**

| Defer | Why |
|-------|-----|
| Full file manager (rename, move, chmod/chown, multi-select, folder zip) | Product of its own; thin slice is get/put |
| In-browser edit of arbitrary files | Compose / Dockerfile editor already exists; do not fork it |
| Binary / image preview, media gallery | Download only |
| Recursive tree + search | List one dir |
| Console drag-drop / zmodem / `scp` from xterm | Separate from PTY; high XSS/DoS surface |
| `docker cp` / named-volume browser | Different trust + path model |
| Recursive upload / unzip-on-host | Zip-slip; confined-archive lessons from herder restore |
| Custom map icon pack (**M5**) | Adjacent upload, different store (`DATA_ROOT`) |
| Cert PEM file-picker | Nice polish on existing paste form — not this stream |
| Git-rich onboard (**Q**) | Already post-1.0 |

**Depends on:** Stable 1.2 SSH client + least-priv user; dest-card chrome; optional later **W-id** (fleet identity) and **T** (step-up on secret downloads).

---

## 3. Ship bar (locked 2026-08-18)

| Priority | Streams | Bar |
|----------|---------|-----|
| **Must** | **P** + **T1–T6** (slice 1 **Deep**) · **L** (Servers + Docker + discovery) · **W-id** core (fleet + privileged + Connect as…) | Operator-owned security policy (full) + scale lists + least-priv/privileged connect-as |
| **Should** | **W-cfg Deep** (timeouts/concurrency/bind in Settings; factor knobs landed with T3) · **A** · **W-audit** if spike green · **N2** built-in fleet board (after **N0**) · **F2** host Files list/get/put (after **F0** sign-off) | Console knobs + alerts + opt-in command audit + thin reporting + confined host file transfer |
| **Discover / Cap** | **N0** discovery · **N3** custom layout · **W-audit** spike · **W-mux** (screen/tmux, low priority) · **F** mkdir/delete/step-up · **AC-fg** · ACME · branding · CSP nonces | Promote only if Must green |

Success criteria:

1. Admin can configure password policy; all password entry paths enforce it and show the same rules.  
2. Account SSO unlink + passkey revoke accept **any enrolled 2FA** (**T6**). Admin can set force-2FA scope / grace **0–60** days / step-up windows / factor matrix / IdP-MFA opt-in (**T1–T5**, default fail-closed).  
3. *(Should)* Console idle/max/concurrency/ticket/hold/bind/revalidate/scrollback adjustable in Settings without editing compose for common cases. `PIHERDER_SSH_CONSOLE` stays the env kill switch.  
4. Host can store **fleet** + **privileged** SSH identities (separate keys/users); console offers **Connect as…**; jobs stay on fleet by default.  
5. *(If W-audit promoted)* Opt-in command (± response) audit with redaction heuristics and retention; default off; wiki warns about residual secret capture.  
6. *(Should)* Map/stack/cert-style alerts have documented severities and per-category tuning; channels respect filters.  
7. Servers + Docker + discovery lists support page size + free-text filter without loading unbounded HTML.  
8. *(If N promoted)* Operators have at least a **built-in fleet health board** of existing signals; custom layout is Cap.  
9. *(If F promoted)* Operator can open **Files** on a Docker host, list the jail, download one file, upload one file; viewer cannot; path escape and oversize rejected; demo does not expose a real tree.

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
- **Full remote file manager** (WinSCP clone, `docker cp`, console zmodem, unzip-on-host) — **F** is discover + confined list/get/put only  
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

Add deferred 1.2 items here as one-line bullets when freeze decides “→ 1.3”.

- **KI-console-mobile-soft-tab** — residual exotic-IME cases after 1.2 QA **v12** (main `cd do` → `docker/` path is in 1.2).
- **KI-account-stepup-factors** — Account SSO unlink + passkey revoke should accept any enrolled 2FA (Stream **T6**).

---

## 9. Immediate next steps

| # | Step | Status |
|---|------|--------|
| 1 | Finish **v1.2.0** freeze / tag / Hub | **Done** — `v1.2.0` tagged · Hub multi-arch published |
| 2 | Open **`v1.3.0-dev`** + lock Must/Should | **Done** 2026-08-18 |
| 3 | Slice 1 Deep **P + T1–T6** | **P landed** · T1–T6 this commit |
| 4 | Spike **W-id** model + console ticket identity field (no UI polish) | After slice 1 |
| 5 | Spike **L1** shared list chrome | After slice 1 |
| 6 | Spike **W-audit0** PTY capture + redaction; promote or Cap | After W-id core |
| 7 | Run **N0** insights discovery (one-pager) → **N2** | Phase 3 |
| 8 | Run **F0** files sign-off → **F2** (flag off until ready) | Phase 3 |

**Phase 1 execution order (parallelizable):** **L1** shared list chrome · **P1/P2** password-policy schema · **W-id1/W-id2** identity model + migrate single key.

Service migration stays on [PLAN_v1.4.0.md](PLAN_v1.4.0.md) — do not add it to this freeze.

---

*Living on `v1.3.0-dev` until freeze into `RELEASE_v1.3.0.md`.*
