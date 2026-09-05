# PiHerder v1.1.0 — elevate production

**Status:** **Freeze** — branch `v1.1.0-dev` · [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md)  
**Date opened:** 2026-07-29  
**Git branch:** `v1.1.0-dev` (integration) · merge → `main` at freeze → tag `v1.1.0`  
**Package / image version (at tag):** `1.1.0`  
**Theme:** Elevate production — certs · discovery · identity · operator UX · topology/maps · integrations/API  
**Baseline:** `v1.0.0` (first production — 2026-07-28)  
**Related:** [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md) · [PLAN_v1.0.0.md](PLAN_v1.0.0.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [FEATURE_PLAN_PIHOLE_NPM_CERTS.md](FEATURE_PLAN_PIHOLE_NPM_CERTS.md) · [FEATURE_PLAN_LAN_NMAP.md](FEATURE_PLAN_LAN_NMAP.md) · [FEATURE_PLAN_RUNTIME_TOPOLOGY.md](FEATURE_PLAN_RUNTIME_TOPOLOGY.md) · [FEATURE_PLAN_INTEGRATIONS.md](FEATURE_PLAN_INTEGRATIONS.md) · [FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md](FEATURE_PLAN_IAM_2FA_UPDATES_NOTIFICATIONS.md) · [ADMIN.md](ADMIN.md) · [API.md](API.md) · [SECURITY.md](../SECURITY.md)

> **First minor after production.** Elevate what operators already run. **Focus · polish · discover · pull in by capacity · defer enhanced work to v1.2 / v1.3 paths.** Keep `main` patchable for **v1.0.x** while this train runs on `v1.1.0-dev`.

---

## 0. Decision lock

| Choice | Value |
|--------|--------|
| Integration branch | **`v1.1.0-dev`** |
| Production line | **`main` @ `v1.0.0`** — hotfixes → **`v1.0.x`**, port into `v1.1.0-dev` |
| Git tag (freeze) | **`v1.1.0`** |
| Image tags (freeze) | `1.1.0` · `1.1` · `latest` (multi-arch); keep `1.0` / `1.0.x` pins valid |
| In-scope streams | **A** certs · **B** discovery · **C** identity · **D** operator UX · **G** topology/maps · **I** integrations/API |
| Out-of-focus | **E** templates mega · **F** host lifecycle mega · **H** HA REST/path2 · k8s/bare/branding → **v1.2 / v1.3** |
| Mode | Focus · polish · discover · pull-in · defer by time |
| Coverage | **≥ 55%** unit; CI fail-under **55** |
| E2E | Touched surfaces get basic Playwright; no live SSH/nmap/NPM in CI |
| Semver | Additive minor; no silent contract breaks |
| Version bump | `1.1.0` **at freeze only** |

```text
main @ v1.0.0 (+ v1.0.x patches)
  └─ v1.1.0-dev → merge → main → tag v1.1.0 → Hub
```

| Rule | Practice |
|------|----------|
| Must → Should → Discover | Do not start Discover while Must is open |
| Time-box Discover | Spike → promote or defer with RELEASE note |
| No half-built paths | Complete or do not ship |
| Prod critical bugs | **main** as **1.0.x** first |
| Enhanced themes | **v1.2** or **v1.3** paths (§6) |

---

## 1. Goal

1. **A — Certs:** deploy-target wizard, one layout per target, top Deploy, post-deploy verify, sudoers correctness.  
2. **B — Discovery:** last-seen, hide, purge, filters.  
3. **C — Identity:** password policy + trusted-device detail; discover SMTP.  
4. **D — Operator UX:** human-readable schedules, favourites, cross-host jump.  
5. **G — Maps/fabric:** ports clarity + cross-host edge polish.  
6. **I — Integrations/API:** OpenAPI/bearer test UX + generic URL entries.  
7. Docs + quality freeze → merge → tag → publish.

---

## 2. Ship bar

| # | Stream | Item | Tier | Status |
|---|--------|------|------|--------|
| 1–3 | A | **P** cert refinement (wizard · one-type targets · deploy/verify · sudo) | Must | **Landed** (A1.1–A1.5b + **A1.4**); residual **A1.6** wiki polish / **A1.7** E2E as capacity |
| 4–7 | B | **S1–S4** last-seen · hide · purge · filters | Must | **Landed** (B1 hygiene; operator QA as needed) |
| 8–9 | C | **PP** password policy · **AB** trusted-device detail | Should | **Landed** (PP fixed policy; **AB** type · last IP · rename) |
| 10–12 | D | **E6** schedules · **J** favourites · **K** cross-host jump | Should | **Landed** (E6 `cron_human` · **J** ★ pins · **K** host jump) |
| 13–14 | G | **Ports** · **Topo-xhost** | Should | **Landed** (port chips `host→container` · cross-host manual edge picker) |
| 15–16 | I | **Y** OpenAPI/test UX · **Int-gen** generic URLs | Should | **Landed** (**Y** bearer Try a token + ReDoc; **Int-gen** HA/Frigate/n8n/custom links) |
| 18 | C | **AB-polish** trusted device edit UX | Cap | **Landed** — ✎ edit / inline rename |
| 19 | N | **Wh-lite** webhook for alerts (UI + event filter) | Cap | **Landed** — Settings → Alerts; env `WEBHOOK_*` fallback |
| 20 | C | **H-lite** SMTP + test send + optional alert mail | Cap | **Landed** — encrypted password; test email |
| 21 | C | **G1-lite** email password recovery | Cap | **Landed** — when SMTP OK; hashed 1h tokens |
| 17 | — | Docs freeze + tag + Hub | Must | Planned (mid-train docs pass 2026-07-29) |

**Success:** all Must + solid elevation from each of C, D, G, I (prefer full Should). Discover promoted or deferred in RELEASE.

**Capacity (locked 2026-07-29):** **AB-polish** · **Wh-lite** · **H-lite** · **G1-lite**.  
**Explicitly → v1.2:** WebAuthn / passkeys (not this train). Residual Cap: **P-job**, **S-hb**, **S-icon**.

---

## 3. Workstreams

### A — Certificates & TLS (**cert refinement** — elevated)

| Tier | ID | Item |
|------|-----|------|
| Must | **P** | Deploy-target wizard (modal): name · server · service · type · dest · perms · restart · sudoers · simulate · save |
| Must | **P-rename** | UI rename **Service map → Deploy target**; compact list + detail modal |
| Must | **P-layout** | One cert type per target (`pair` \| `combined` \| `pfx`); no new compound layouts |
| Must | **P-sudo** | Sudoers generator correctness (aligned with deploy) |
| Must | **P-fb** | Deploy actionable errors |
| Must | **P-chrome** | Top **Deploy** actions; Replace PEM as menu + modal |
| Must | **P-edge** | Clear path for valid TLS on this PiHerder (edge Apply) |
| Should | **P-verify** | Post-deploy validation (host fingerprint + optional URL/TLS probe) |
| Should | **P-job** | Multi-target deploy as Job |
| Discover | **P-sim** | Simulate sudoers (promote into Must with wizard) |
| → v1.2+ | **P-npm-w** | NPM proxy host write CRUD (if ever) |
| → **v1.3+** | **P-acme** | ACME-in-herder — **under consideration** (see §6.1); not v1.1 |

### B — LAN Discovery

| Tier | ID | Item |
|------|-----|------|
| Must | **S1–S4** | Last seen · hide · purge · filters |
| Should | **S-hb**, **S-icon** | Heartbeat on boot · icons by kind |
| Discover | **S-port** | Per-service port labels |
| → v1.2+ | — | Scan redesign / new vuln engines |

**Locks:** stale = offline flag; never auto-delete.

#### B1 hygiene (S1–S4) — implementation notes

| ID | Behaviour |
|----|-----------|
| **S1** | Show **Last seen** (relative + absolute title) on Devices list and device modal; drives offline after `STALE_AFTER_DAYS` (14) |
| **S2** | **Hide** / **Unhide** UI (state remains `ignored`); filter chip **Hidden**; off maps + Hosts overlay |
| **S3** | **Purge device** (modal) + **Purge N offline** (when Offline filter active); deletes scripts + row; linked must unlink first; rescan may re-create as *new* |
| **S4** | Filter chips with honest counts (unfiltered stats); offline stat on toolbar; search includes last-seen / hidden tokens |

### C — Identity

| Tier | ID | Item |
|------|-----|------|
| Should | **PP**, **AB** | Password policy · trusted-device detail |
| Cap | **AB-polish** | Edit icon / inline rename for trusted devices (no always-visible form) |
| Cap | **H-lite** | SMTP settings + test send + optional alert email |
| Cap | **G1-lite** | Self-service email password reset when SMTP OK |
| Cap | **Wh-lite** | Webhook alerts UI (with env fallback) — see § Cap channels |
| Discover | **H** / **H-ch** | Full mail + multi-channel matrix if capacity after Cap |
| → v1.2 | **G2-mail**, WebAuthn/passkeys | Admin mail reset polish · passkeys as 2FA |
| → v1.3 | **Z**, multi-tenant | SSO program |

#### C1 — implementation notes (landed)

| ID | Behaviour |
|----|-----------|
| **PP** | Existing fixed policy (≥10, upper/lower/digit, byte cap) — no further product work this train |
| **AB** | Account → Trusted devices: **device type** (from UA), **last IP**, **friendly rename**, last used / expires, per-device revoke + revoke all |
| **AB-polish** | Rename hidden until **✎ Edit**; Save / Cancel; Revoke stays one-click |
| **H-lite** | Settings → **Alerts**: SMTP host/port/security/user/password (Fernet) / from / alert recipients; **Send test** |
| **G1-lite** | Login → Forgot password when SMTP enabled; hashed token, expiry, rate limit; no open reset without SMTP |
| **Wh-lite** | Settings → **Alerts**: webhook URL + optional number/recipients/secret; filters for notifications / jobs / backups; env `WEBHOOK_*` if UI empty |

### D — Operator UX

| Tier | ID | Item |
|------|-----|------|
| Should | **E6**, **J**, **K** | Schedules · favourites · cross-host jump |
| Discover | **E9** | Selectable hero stats |
| Optional | **M** | Templates fleet overview |
| → v1.3 | **Brand** | Custom logo / accents |

#### D1 / D2 — implementation notes (landed)

| ID | Behaviour |
|----|-----------|
| **E6** | Shared `app/services/cron_human.py` — Jinja filter `cron_human` + `CRON_PRESETS` on backup / OS / container / nmap / self-backup / stale cleanup forms; plain English next to raw cron |
| **J** | Per-user **pins** (`UserFavourite`, migrations **033** / **034**). Kinds: `server_feature` · `app_page` · `integration`. Header **★** menu (grouped Host / App / Integrations, 2-col pills). Pin stars next to feature titles, Network hub map cards, fabric map chrome, integration detail names. Cap **24**. Map app pages **must** deep-link `/dns/physical#map` and `/dns/logical#map` so the SVG opens (list-first pages otherwise). Pin POST redirect preserves `#fragment`; **no** flash `msg` (star state is the feedback) |
| **K** | **Jump host** (`host_switcher`) on Overview / Docker / Backups / Services: host name → overview; ▾ → same feature on other fleet hosts. Jump list filtered by feature flags (`container_patch_enabled` for Docker, `backup_enabled` for Backups). Feature tabs via `host_feature_nav` |

**Code:** `app/services/nav_shortcuts.py` · `app/routers/favourites.py` · partials `pin_button` / `host_switcher` / `host_feature_nav` · `GET /account/favourites.json`

### G — Topology / maps / fabric

| Tier | ID | Item |
|------|-----|------|
| Should | **Ports**, **Topo-xhost** | Published ports · cross-host picker |
| Discover | **Topo-col**, **P6**, **Topo-prof** | Columns · shared services · profiles |
| → v1.2+ | **DNS-ext**, **Mig** | External DNS · host migrate |

#### G1 — implementation notes (landed)

| ID | Behaviour |
|----|-----------|
| **Ports** | `app/services/dns_fabric/ports.py` — parse published mappings; stack panel chips show **host→container** (or internal-only) |
| **Topo-xhost** | Manual edge picker can target **other host / project / container** (cross-host topology edges) |

### I — Integrations & API

| Tier | ID | Item |
|------|-----|------|
| Should | **Y**, **Int-gen** | OpenAPI/bearer test · generic URL entries |
| Discover | **Int-multi**, **N-thin** | Multi-instance · fleet health card |
| → v1.2+ | **N**, deep adapters | Full insights · full Frigate/n8n product |

#### I1 — implementation notes (landed)

| ID | Behaviour |
|----|-----------|
| **Y** | Settings → API **Try a token** + OpenAPI / ReDoc deep links |
| **Int-gen** | Integration type `generic_url` — presets **Home Assistant** · **Frigate** · **n8n** · **custom**. Base URL + optional health path + optional bearer for probe; Test/Poll = HTTP GET (2xx/3xx/401/403 = reachable). Bind to host/Docker as `role=service` chips on Services. **Not** a deep vendor adapter — wiki [generic-links](../wiki/integrations/generic-links.md). |

**Code:** `app/services/integrations/generic_url.py` · `integrations_generic` router · form/detail templates · `tests/test_integrations_generic.py`

---

## 4. Phased train

| Phase | Focus |
|-------|--------|
| **A0** | Plan lock (this document) |
| **A1** | Certs **P / P-sudo / P-fb** ← **first implementation** |
| **B1** | Discovery **S1–S4** (+ **S-hb**) |
| **D1** | **E6** schedules — shared `cron_human` + presets on backup / OS / container / nmap / self-backup / cleanup |
| **D2** | **J + K** navigation — ★ header pins (`UserFavourite`); Jump host on overview/backups/docker/services |
| **C1** | **PP + AB** — PP already fixed policy; AB trusted-device detail (type, IP, rename) |
| **G1** | **Ports + Topo-xhost** — stack panel port chips (`host→container`); manual edge To host/project/container |
| **I1** | **Y + Int-gen** |
| **Cap** | Discover pull-ins by capacity |
| **Freeze** | Docs · version · merge · tag · Hub |

---

## 5. Quality bar

| Gate | Target |
|------|--------|
| Unit | ≥ 55% line on `app` |
| CI fail-under | 55 |
| E2E | Touched surfaces; baseline green |
| Docs | `mkdocs build --strict` at freeze |
| CI labs | No live SSH / nmap / NPM / HA |

---

## 6. Later release paths

Not abandoned — scheduled as paths. Items may move between 1.2 and 1.3 as the train progresses.

### v1.2 path

| Theme | Items |
|-------|--------|
| Identity completion | Full **H** · **G2-mail** · multi-channel matrix · residual recovery |
| **WebAuthn / passkeys** | Second-factor passkeys first (not passwordless day one); coexist with TOTP + backup codes |
| Network / maps | **DNS-ext** · residual cert multi-deploy · **Mig** design · **map interactivity** M1–M5 (canned icons, focus pop-out, port ownership, custom pack) — [FEATURE_PLAN_MAP_INTERACTIVITY.md](FEATURE_PLAN_MAP_INTERACTIVITY.md) |
| Insights | **N-thin** → first **N** slices |
| Templates | **M** · **Git-cat** · git-rich start |
| HA | Add-on updates · component picker · wiki depth |
| Host lifecycle start | **HL-P3** stats/commands · **2c** cascades · **HL-P4** bootstrap |

### v1.3 path

Full plan: **[PLAN_v1.3.0.md](PLAN_v1.3.0.md)** — **Tagged**. Current train: [PLAN_v1.4.0.md](PLAN_v1.4.0.md) on `v1.4.0-dev`.

| Theme | Items |
|-------|--------|
| **Operator policy (P / T / W-cfg)** | Configurable password policy; 2FA force + step-up surfaces; in-app console timeouts / concurrency / step-up knobs |
| **Console identity + audit (W-id / W-audit)** | Least-priv + privileged host SSH identities (Connect as…); discover opt-in command/response shell audit with redaction |
| **Alerts + lists (A / L)** | Map/alert severity granularity; app-wide pagination, page size, free-text/smart search on dense lists |
| **Insights (N)** | Discovery + thin-slice reporting / custom dashboarding (registry + built-in board; optional one custom layout) |
| **Fine-grained roles / feature ACLs (AC-fg)** | Beyond global `viewer` / `operator` / `admin`: per-**host** access and/or per-**feature** gates (e.g. backups only, Docker yes / webshell no, certs, templates). UI + enforcement + optional OIDC group → custom role. **Not** multi-tenant SaaS. See [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) decision 2026-08-08. |
| SSO / OIDC (**Z**) | **→ v1.2 Stream S** (in flight); residual polish only if any after 1.2 |
| Web SSH (**HL-P5**) | **→ v1.2 Stream W** (in flight); residual after 1.2 if deferred |
| HA REST / S1 / path 2 | Integration track |
| **P-acme** ACME-in-herder | **Under consideration** — §6.1 (not committed) |
| NPM write CRUD | Optional if still needed after ACME/NPM pull depth |
| Full insights · branding | Horizon UX |
| k8s / bare | Deploy topologies |

Patches for security/data issues still ship as **v1.0.x** / **v1.1.x** regardless of path.

### 6.1 ACME-in-herder (**P-acme**) — under consideration from **v1.3+**

**Status:** Desired product direction; **not** v1.1 and not a v1.2 ship gate. Discovery / design may start whenever capacity allows; implementation target **v1.3 onwards**.

**Why later:** v1.1 elevates **distribute** (wizard, sudoers, maps). Issuance is a separate trust and ops surface. Operators who already run NPM should keep using it as the multi-provider issuer (PiHerder continues **pull + renew-via-NPM + vault + deploy**).

**Do not replace NPM.** ACME-in-herder is for fleets **without** NPM, air-gapped-ish labs that still want LE/public ACME, or dual-path flexibility. Prefer architecture *patterns* from NPM (Certbot + DNS plugin catalog, serial renewals) over importing NPM code — NPM is an orchestrator around **Certbot**, not a shared library.

#### Challenge model (open design — pick at discovery)

| Approach | Idea | Pros | Cons |
|----------|------|------|------|
| **A — Human-assisted** | Herder shows DNS TXT / HTTP token; operator pastes records (or confirms) then herder continues ACME | Simple; no DNS API secrets in herder; works with any DNS | Manual at issue/renew; renew friction |
| **B — DNS automation** | Herder (or worker) writes challenge via DNS provider API / RFC2136 / Pi-hole where possible | Hands-off renew; wildcards | Credentials, provider matrix tax, blast radius |
| **C — Hybrid (likely first ship)** | Issue: assisted or automated; renew: prefer automation when provider bound, else notify + assisted | Matches real labs | Two UX paths to document |
| **D — Delegate** | No native ACME; only deepen NPM/external issuer integration | Lowest risk | No path when NPM absent |

**Lean (2026-07-29):** treat **C** as default discovery hypothesis; **A** acceptable MVP if automation slips. Do **not** aim for NPM’s full ~80 DNS plugins on day one — thin set (e.g. Cloudflare, RFC2136, maybe Pi-hole/local) or human-assisted only.

#### Product locks if it ships

- Vault + service maps + stage+sudo remain the **distribution** story (same as today).  
- ACME material lands as another **source** next to `npm` / `upload`.  
- Serial renew jobs; clear audit; secrets encrypted at rest.  
- Air-gap: ACME off / N/A when no public ACME path.  
- Image size: prefer optional worker or lazy tools over baking every DNS plugin into the core image.

#### Explicit non-goals until discovery closes

- Replacing NPM SSL UI for proxy hosts.  
- Full Certbot DNS catalog in core image.  
- Auto-issue without operator email / ToS / rate-limit awareness.

#### Education path (can ship **before** product ACME)

PiHerder is fleet management **and** operator education. Novices often need “how do I *get* a PEM?” before “how do I deploy it?”

| Deliverable | When | Notes |
|-------------|------|--------|
| Wiki cookbook: obtain a cert with ACME / DNS | **Anytime** (even v1.1 Cap) — independent of **P-acme** code | Novice steps → vault upload → maps |
| Prefer **links to maintained upstream** | Always | EFF / Let’s Encrypt / plugin docs age better than forked prose |
| Optional **helper script** (Certbot in Docker CLI) | Optional under `scripts/` | Domains, email, DNS vs webroot; no API tokens in git |
| In-app “How to get a cert” deep-link | When UI capacity | Empty vault / setup → wiki |

**Tone:** “Here is how operators usually obtain PEMs; then PiHerder vaults and deploys them.” Not “PiHerder is your CA.”

**Suggested wiki shape** (new page or section on certificates):

1. **Pick a path** — have NPM → pull; no NPM → ACME below; have PEMs → Upload.  
2. **HTTP-01 vs DNS-01** — public port 80 vs wildcard / closed ports.  
3. **DNS-01 sketch** — TXT `_acme-challenge…`, wait, finish (human-assisted first).  
4. **Certbot in Docker** — copy-paste friendly; volume out PEMs; never paste private keys into logs.  
5. **Into PiHerder** — Upload fullchain + key → maps → deploy.  
6. **Renewal** — reminder / NPM auto-renew; native herder ACME later (product §6.1).  
7. **Upstream references** (link, don’t re-author):

| Topic | Prefer |
|-------|--------|
| Get Certbot by OS | [certbot.eff.org/instructions](https://certbot.eff.org/instructions) |
| User guide + DNS plugins | [EFF Certbot docs](https://eff-certbot.readthedocs.io/en/stable/using.html) |
| LE getting started | [letsencrypt.org/getting-started](https://letsencrypt.org/getting-started/) |
| Rate limits / staging | [LE rate limits](https://letsencrypt.org/docs/rate-limits/) · Certbot staging |
| Provider-specific | Official `certbot-dns-*` pages when operator picks a provider |

**Helper script stance:** optional convenience; print paths for vault upload; use **staging** first; warn about production rate limits.

---

## 6.2 Known issues at freeze (carry to RELEASE)

| ID | Issue | Destination |
|----|--------|-------------|
| **KI-rsync-vanished** | Rsync source files can vanish mid-run on busy trees (e.g. Frigate NVR recordings) → code 23/24; expected class of failure; no soft-success / retry in 1.1 | **v1.2+** — [PLAN_v1.2.0.md](PLAN_v1.2.0.md) **B-retry** · [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md) |

---

## 7. Freeze checklist

- [x] A Must (P, P-sudo, P-fb, wizard, verify) — residual A1.6/A1.7 polish  
- [x] B Must (S1–S4)  
- [x] C / D / G Should (PP+AB, E6+J+K, Ports+Topo-xhost)  
- [x] I Should full (**Y** + **Int-gen** landed)  
- [x] Cap **AB-polish · Wh-lite · H-lite · G1-lite** landed  
- [x] WebAuthn/passkeys deferred → v1.2  
- [x] Known issue **KI-rsync-vanished** documented in RELEASE + wiki (not fixed in 1.1)  
- [ ] Discover residual promoted or → v1.2/v1.3  
- [ ] Cert known-edges card updated for deploy-target wizard  
- [ ] `RELEASE_v1.1.0.md` highlights filled at tag · screenshots as needed  
- [ ] Unit ≥55% · E2E touch · `mkdocs build --strict`  
- [ ] Version `1.1.0` · merge · tag · Hub  
- [ ] ROADMAP + SECURITY supported versions  

---

## 8. Parallel: v1.0.x

| Severity | Where |
|----------|--------|
| Security / data-loss / auth | **`main`** → `v1.0.x` → port to `v1.1.0-dev` |
| 1.1 features | `v1.1.0-dev` only |

---

## 9. Migration

| Topic | Expectation |
|-------|-------------|
| Alembic | Prefer additive; document in RELEASE |
| Master key | Unchanged |
| REST | Compatible scopes |
| Upgrade 1.0 → 1.1 | Self-backup → pull → `compose up` |
| Deploy targets (was service maps) | Existing rows keep working; new wizard = one layout only; compounds legacy |

---

## 10. Phase A1 — Cert refinement (elevated product design)

**Goal:** Make vault → destination → deploy → verify a first-class, intuitive operator path — not a long form buried under service-map jargon.

**Product rename (UI):** **Service map** → **Deploy target**  
(Code/table may stay `CertificateTarget` for migration safety; user-facing copy uses **Deploy target**.)

### Operator outcomes (locked requirements)

| # | Outcome |
|---|---------|
| 1 | **Valid TLS on this PiHerder** — edge destination polished (vault → Apply → Caddy); not confused with fleet deploys |
| 2 | **Onboard a deploy target** with a dedicated guided wizard (modal preferred) |
| 3 | **Deploy** is a primary top-of-page action (per target + deploy all), not a messy footer task |
| 4 | **One layout / cert type per target** — `pair` **or** `combined` **or** `pfx` only (no compound `pair_and_*` for new targets) |
| 5 | Wizard collects: name · server · optional service · cert type (+ wiki) · final remote folder · filenames · chmod/chown · simple restart · generates sudoers + install steps · **simulate** sudoers · save wires real deploy commands |
| 6 | **Post-deploy validate** — prove new cert is live (OpenSSL / HTTPS probe vs vault fingerprint; optional verify URL) |
| 7 | Target list is **compact**; click opens detail modal (Deploy · Edit · Remove · sudoers cleanup hint) |
| 8 | **Replace PEM** is a menu action + modal, not a permanent page slab |

### Mental model (keep, rename)

```text
Vault (one TLS identity, encrypted)
  ├─ This PiHerder (Caddy edge)     ← Apply / self-managed
  └─ Deploy targets (fleet, SSH)    ← one consumer each
        layout = exactly one of: pair | combined | pfx
        write  = direct | stage+sudo (derived / recommended, not a mystery)
        after  = simple restart recipe → real post_deploy_command
        verify = optional URL / host:port probe after deploy
```

### Naming

| Old (UI) | New (UI) | Notes |
|----------|----------|--------|
| Service map | **Deploy target** | Matches model intent; avoids DNS “map” confusion |
| Add service map | **Add deploy target** / **New destination** | Wizard entry |
| Deploy all maps | **Deploy all** | Top bar primary |
| Map form (inline) | **Target wizard** (modal) | Multi-step |

### Cert types (one per target)

| Type | Files | When |
|------|-------|------|
| **PEM pair** | fullchain + privkey | Nginx, Caddy, most Docker TLS mounts |
| **Combined PEM** | single file (key then chain) | HAProxy / some “snakeoil” apps |
| **PFX (PKCS#12)** | `.pfx` | Windows / UniFi-style |

**Legacy:** existing rows with `pair_and_*` keep deploying until edited; new UI never offers compounds. Need both pair and PFX? → **two deploy targets**.

### Wizard (modal) — step sketch

1. **Name** — human label (“NPM custom SSL”, “Grafana volume TLS”).  
2. **Server** — fleet host with working SSH.  
3. **Service** (optional) — if a fleet service / stack is known, prefill path + restart; else freeform.  
4. **Cert type** — pair / combined / pfx + short help + wiki link.  
5. **Destination** — **final** remote folder (not staging). Staging is an implementation detail of stage+sudo.  
6. **Filenames** — only fields for the selected type.  
7. **Permissions** — chmod + chown (owner/group).  
8. **Restart** — simple recipes only:
   - `docker compose -f <file> restart` / `up -d` (compose path field)
   - `sudo systemctl restart <unit>`
   - None  
   Wizard stores the exact command PiHerder will run.  
9. **Privileges** — if destination is root-owned / not writable by SSH user → recommend **stage+sudo**; show generated **sudoers snippet** + copy steps (least-priv vs full user).  
10. **Simulate** — remote check: can we `sudo -n` the install/restart lines? Fail with actionable copy.  
11. **Save** — persist target; preview “what deploy will do”; offer **Deploy now**.

### Certificate detail page layout

| Zone | Content |
|------|---------|
| **Top bar** | Name · expiry pills · **Deploy all** · Apply to this PiHerder · ⋮ menu (Replace PEM, settings, delete) |
| **Vault card** | Domains, fingerprint, renew settings (compact) |
| **This PiHerder** | Compact edge row (status + Apply) |
| **Deploy targets** | Minimal rows: label · server · type · last status · last deploy time — **click → detail modal** |
| **Empty state** | CTA: **Add deploy target** opens wizard |

**Target detail modal:** full paths, perms, restart, last deploy message, sudoers snippet (copy), **Deploy**, **Edit** (re-open wizard), **Remove**, cleanup note (“remove this sudoers drop-in if unused”).

**Replace PEM:** ⋮ → modal (fullchain + key) → on success prompt Deploy all / Apply edge.

### Post-deploy validation (**P-verify**)

| Mode | How | Success |
|------|-----|---------|
| **Fingerprint on host** | SSH read installed file(s) / `openssl x509 -fingerprint` on remote PEM | Matches vault fingerprint |
| **TLS probe** | Optional `verify_host` / URL (or linked HTTP monitor later) — `openssl s_client` or HTTPS GET leaf | Presented cert fingerprint matches vault (or SANs + not-after sanity) |
| **Edge** | Read `./certs` + optional local Caddy/HTTPS probe | Same fingerprint |

Store last verify status/message/time on target (and edge fields on cert). Deploy UX shows **Deployed · Verified** / **Deployed · verify failed**.

**DB (additive):** e.g. `verify_url` / `verify_host` / `verify_port` / `last_verify_*` on `CertificateTarget` — only if needed beyond post-deploy host fingerprint check. Prefer host-file fingerprint first (works for DB TLS / non-HTTP consumers); URL probe is optional secondary.

### Inventory (current → target)

| Area | Location | Notes |
|------|----------|--------|
| Sudoers / stage paths | `certificates.py` | **A1.1 done** |
| Layouts | `LAYOUTS`, form | **Narrow new UI to 3 types**; keep deploy of legacy compounds |
| Inline map form | `certificates_detail.html` | **Replace** with compact list + modal wizard |
| Setup page | `certificates_setup.html` | Vault import path only; target wizard lives on cert detail |
| Deploy buttons | detail top + per-card bottom | Elevate **Deploy** top + modal; remove footer clutter |
| Replace PEM | detail slab | → menu + modal |
| Verify | post-deploy + Verify action | Host openssl/marker fingerprint |
| Tests | `test_certificates*.py` | Wizard fields, one-type layouts, verify helpers, sudo simulate |

### Breakdown (revised)

| Step | Work | Done when |
|------|------|-----------|
| **A1.0** | Path mismatch repro | **Done** |
| **A1.1** | Shared path helpers; snippet ↔ deploy | **Done** |
| **A1.2** | Server-truth paths in any remaining preview JS | **Done** — wizard sudoers uses selected server SSH user + default home |
| **A1.3a** | Rename UI → **Deploy target**; compact list | **Done** — list + detail modals |
| **A1.3b** | **Target wizard modal** (steps above) | **Done** — including simulate (**A1.5b**) |
| **A1.3c** | Restart recipes → real `post_deploy_command` | **Done** — compose / systemctl / custom |
| **A1.3d** | Page chrome: top Deploy, ⋮ Replace PEM modal | **Done** |
| **A1.4** | Deploy error copy polish | **Done** — `humanize_deploy_error` + richer post-deploy failures |
| **A1.5** | Post-deploy **verify** (host fingerprint; optional URL) | **Done** — openssl/marker vs vault; Verify button; optional URL later |
| **A1.5b** | Wizard **simulate** privileges | **Done** — SSH sudo/write probes |
| **A1.6** | Wiki + setup page rewrite | **In progress** mid-train docs pass; cookbooks → deploy target / wizard / verify |
| **A1.7** | Tests + E2E chrome | Unit pack expanded; Playwright wizard/modal shells as capacity |

### A1 out of scope

ACME-in-herder · NPM proxy write · **auto** install of sudoers over SSH (copy + operator still) · multi-target Celery job packaging (**P-job** Cap/Should) · full Kuma monitor linkage (optional later)

### Design defaults

| Question | Default |
|----------|---------|
| Product name | **Deploy target** |
| Wizard surface | Modal on cert detail (+ “Add” from empty state) |
| Cert types (new) | `pair` \| `combined` \| `pfx` only |
| Staging | Hidden when possible; shown only in sudoers explanation |
| Sudoers install | Operator copies drop-in; **Simulate** proves it |
| Verify | Always try host fingerprint after successful write; URL optional |
| Edge TLS | First-class compact card, same cert page |

### Immediate next (implementation order)

1. Operator QA on Cap (Alerts / forgot password / device edit) + A/B/I surfaces.  
2. **A1.6 / A1.7** residual — wiki/setup polish + Playwright chrome as capacity.  
3. Freeze checklist → `RELEASE_v1.1.0.md` · version · merge · tag · Hub.

---

## 11. Changelog (planning)

| Date | Note |
|------|------|
| 2026-07-29 | Branch `v1.1.0-dev` from `main` @ v1.0.0. Residual P+S plan opened. |
| 2026-07-29 | Elevation streams **A, B, C, D, G, I** locked. Mode: focus · polish · discover · pull-in · defer. |
| 2026-07-29 | Deferred framed as **v1.2 / v1.3 paths**. Phase **A1 certs** is first implementation slice. |
| 2026-07-29 | **A1.0–A1.1 landed:** path helpers + sudoers/deploy alignment for custom home / root; unit tests; partial **P-fb** error copy. Next: **A1.2** map-form JS → server truth, then wizard (**A1.3**). |
| 2026-07-29 | **P-acme** ACME-in-herder: desired · **under consideration from v1.3+** (not this train). Challenge model open: DNS automation vs human-assisted vs hybrid; NPM stays preferred multi-provider issuer. §6.1. |
| 2026-07-29 | **Education path:** wiki ACME/DNS cookbook + optional Certbot helper can ship **before** product ACME; prefer upstream links; novice path into vault+deploy. §6.1 Education path. |
| 2026-07-29 | **Cert refinement elevated:** rename Service map → **Deploy target**; one layout per target; modal wizard; top Deploy; Replace PEM menu; post-deploy verify; compact list + detail modal. §10 rewritten. |
| 2026-07-29 | **A1.2–A1.3 landed (UI):** pure `pfx` layout + restart recipes; cert detail compact list + detail modals + multi-step wizard; top Deploy / More (Replace PEM); server-truth sudoers preview; list/setup copy. **P-verify** still open. |
| 2026-07-29 | Deploy targets are **per service** (not stack); cert detail **groups services under host** (1–N deploys per machine). |
| 2026-07-29 | **P-verify** + **P-sim:** post-deploy host fingerprint verify (migration `032`); Verify action; wizard Simulate privileges over SSH. |
| 2026-07-29 | **TLS port probe:** optional endpoint on service target — `openssl s_client` on any TLS port (web, DB native TLS, STARTTLS postgres/mysql/…); host then herder. |
| 2026-07-29 | **Cert alerts:** open Notifications on deploy fail / verify fail-partial; auto-resolve on successful deploy + verify (and on target/cert delete). |
| 2026-07-29 | **B1 S1–S4 landed:** last-seen, hide/unhide, purge + bulk offline purge, filter chips with honest counts. |
| 2026-07-29 | **C1 AB landed:** trusted-device type, last IP, rename on Account. |
| 2026-07-29 | **D1 E6 landed:** `cron_human` + presets across schedule surfaces. |
| 2026-07-29 | **D2 J+K landed:** `UserFavourite` pins (★ menu, host/app/integration kinds; map pins open SVG via `#map`); cross-host jump with feature-flag filtering. Migrations **033** / **034**. |
| 2026-07-29 | **G1 Ports + Topo-xhost landed:** stack panel published-port chips; manual edge cross-host picker. |
| 2026-07-29 | **I Y partial:** Settings → API **Try a token** + OpenAPI/ReDoc deep links; **Int-gen** still open. |
| 2026-07-29 | Mid-train **docs/wiki/ADMIN/ROADMAP** pass for A–D–G–I landed work. |
| 2026-07-29 | **I Int-gen landed:** `generic_url` links (HA / Frigate / n8n / custom) + probe + Services bindings; wiki generic-links. Pin redirect no longer flashes raw `favourite_toggled`. |
| 2026-07-29 | **Cap locked for train:** AB-polish · Wh-lite · H-lite · G1-lite. **WebAuthn/passkeys → v1.2**. |
| 2026-07-29 | **Cap landed:** AB-polish (edit icon); Settings **Alerts** (Wh-lite + H-lite); G1-lite forgot/reset password; migration `035` password reset tokens. |
| 2026-07-30 | **Map interactivity M0 locked** (icons · pop-out · ports · custom pack) → [FEATURE_PLAN_MAP_INTERACTIVITY.md](FEATURE_PLAN_MAP_INTERACTIVITY.md). |
| 2026-07-30 | **Map M1+M2+M3-lite landed** (capacity): canned kind icons, locked focus pop-out, port role heuristics on stack chips. |
| 2026-07-30 | **Map M4 landed:** host ports panel, sticky roles (`PortAnnotation` / migration 036), nmap∪docker inventory, map focus port summary. **M5 custom pack** remains roadmap. |
| 2026-07-30 | **Map ports UX polish:** pop-out ~1.30×; progressive compact → ports-only → by-service; discovered devices; stack container ports; touch whole-callout. Docs/wiki synced. |
| 2026-07-29 | End-of-day **docs sync**: ROADMAP / ADMIN / wiki alerts cross-links; Cap + Int-gen reflected for next session (freeze path). |
| 2026-08-08 | **Known issue KI-rsync-vanished:** busy sources (Frigate/NVR-style) can fail rsync mid-run; document for 1.1 ship; retry/soft-success → v1.2+. |

---

**End of plan** — living on `v1.1.0-dev` until freeze into `RELEASE_v1.1.0.md`.
