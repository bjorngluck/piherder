# PiHerder v0.7.0 — development plan

**Status:** **Product-closed → packaging** (opened 2026-07-18 · product streams closed 2026-07-19)  
**Date opened:** 2026-07-18 · **Refreshed:** 2026-07-19 (product review — residual polish → **[v0.8.0 RC3](PLAN_v0.8.0.md)**)  
**Baseline:** `v0.6.0` (RC2 — tagged 2026-07-18)  
**Package version during cycle:** still `0.6.0` on tree until freeze packaging → **`0.7.0` at tag**  
**Related:** [PLAN_v0.6.0.md](PLAN_v0.6.0.md) · [PLAN_v0.8.0.md](PLAN_v0.8.0.md) · [RELEASE_v0.6.0.md](RELEASE_v0.6.0.md) · [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) · [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) · [wiki screenshots README](../wiki/assets/screenshots/README.md)

### Decision (locked)

| Choice | Value |
|--------|--------|
| Git tag target | **`v0.7.0`** |
| Theme | **Onboarding clarity** — guided add-host + docs that show the real UI + **automated E2E** |
| Planning frame | Single target; **must** ship wizard + screenshot pack + **Playwright E2E (Phase A + wizard B)**; polish only as capacity |
| Production path | ~~v0.5.0 RC1~~ → ~~**v0.6.0 RC2**~~ **tagged** → **v0.7.0** (this cycle) → **[v0.8.0 RC3](PLAN_v0.8.0.md)** → **v1.0** |
| Docs strategy | Living wiki + screenshot pack in same cycle as wizard; `RELEASE_v0.7.0.md` at tag |
| **E2E ship gate** | **Hard tag gate** — Playwright suite red **blocks** `v0.7.0` |
| **E2E CI** | **Separate job** on **main + PR** (path-filtered); fail the check if red |
| **Residual polish** | Open stream-C items **cut from 0.7** → **[PLAN_v0.8.0.md](PLAN_v0.8.0.md)** (RC3) |

### Decision (carry-forward from 0.6 freeze)

| Choice | Value |
|--------|--------|
| **Add-host wizard (H2.75 P2)** | **Must for 0.7.0** — primary onboarding path |
| **Wiki screenshot pack** | **Must for 0.7.0** — refresh stale + capture new 0.6 surfaces + wizard UI |
| **Advanced form** | **Keep** as secondary “Advanced / single form” path (not deleted) |
| **H2.75 P3–P5** | **Out of 0.7.0** (stats/commands · bootstrap depth · web SSH) |
| **LAN nmap** | **v0.8.0** (not this cycle) |

---

## 1. Theme

**Make first-week onboarding obvious.** v0.6.0 finished RC2 product polish (templates as Jobs, cert vault UX, Docker bulk, topology). Operators still onboard hosts via tribal knowledge: form → server detail → SSH access panel → features → schedules → DNS.

v0.7.0:

1. **Guides** that order of operations (add-host wizard)  
2. **Shows** the real UI in the wiki (screenshot pack)  
3. **Proves** shell + wizard still work via **Playwright E2E** (CI on every relevant PR)  
4. Optionally clears **small residual** Jobs/UX gaps **only if capacity remains** after 1–3  

This is **not** a second large product wave. High-risk surfaces (web SSH, first-boot phone-home, ACME-in-herder, nmap) stay out.

---

## 2. What 0.6.0 already delivered (do not re-build)

| Area | v0.6.0 state |
|------|----------------|
| Onboarding | Add-server form + SSH access panel (deploy key, least-priv, deps) — **capable, not guided** |
| Templates | Deploy/redeploy as Jobs + live log; exclusive stack lane |
| Certs | Setup guide, map presets, stage_sudo, self-managed edge map, Grafana UID 472 |
| Docker | Project ⋯ Stop/Start/Restart all as Jobs |
| Topology | Stack panel, map expand, edges, order; Kuma coverage page |
| Docs | Prose for dual TLS, certs, Docker bulk, topology — **PNGs may be stale / missing** |

0.7 **orchestrates and documents**; it does not replace the SSH / feature / DNS engines.

---

## 3. Workstreams — status board

### Summary

| Stream | Must for 0.7? | Status |
|--------|---------------|--------|
| **B** Add-host wizard (H2.75 P2) | **Must** | **Done** (product) — 8-step wizard, connect/deploy/test/clear-password, advanced form, wiki primary; PNG capture only remains in A |
| **A** Wiki screenshot pack + wizard docs | **Must** | **Prose done** · **PNG pack open** (tag gate — inventory ready) |
| **E** E2E platform (Playwright A + wizard B) | **Must** | **Done** (product) — Phase A + B1–B5 local green (11 tests); private-key DOM assert in B2; CI workflow on main/PR |
| **C** Residual Jobs / polish | Nice / capacity | **Capacity closed** — done items ship in 0.7; **open items → v0.8.0 RC3** |
| **D** Packaging (unit + E2E green, RELEASE, Hub) | Must at tag | **Open** — freeze week only |

**Testing pyramid**

```text
Playwright E2E (browser)     ← must · separate CI job · Chromium
HTTP TestClient smoke        ← should · cheap · unit job
Unit / service pytest        ← must · existing · no browser
```

---

### B — Add-host wizard (H2.75 P2) — **must**

**Problem:** Correct order is tribal knowledge; password bootstrap can linger; new operators bounce between form and SSH access panel.

**Solution:** Guided multi-step wizard that **reuses** existing SSH / feature / DNS endpoints (**orchestration only**). Design locked in [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) § Phase 2.

| Step | Content | Reuses (indicative) |
|------|---------|---------------------|
| 1 Identity | Name, hostname/IP, port, SSH user | Create server row (today’s add form fields) |
| 2 Trust | Generate/upload key; optional one-time password | Existing key encrypt + store path |
| 3 Connect | Deploy key → Test connection → **clear password CTA** on success | `server_ssh` deploy-key / test |
| 4 Privilege | Least-priv (Debian/Pi OS) or **skip (HAOS)** + Docker base dir if needed | Least-priv script / run path; host deps chips |
| 5 Features | Backups / OS / Docker toggles | Server feature flags |
| 6 Schedules | **Checks only** by default; apply schedules off | Existing schedule apply |
| 7 Network | Optional FQDN + Manage A on Pi-holes | Host DNS / fabric path |
| 8 Done | Summary + CTAs (first backup, update check, open Docker) | Links only |

#### UX decisions (locked for kickoff — reverse only with explicit note)

| # | Question | Decision |
|---|----------|----------|
| B1 | Wizard vs classic form | **Wizard primary** on “Add server”; **Advanced** link keeps single-page form |
| B2 | URL | Prefer `/servers/new` (wizard) + `/servers/new/advanced` (form) — or `/servers/add` wizard + query/path for advanced; pick one and stay consistent with nav CTAs |
| B3 | Progress model | Step indicator; **Save & exit** leaves a partial server (same as mid-setup today) — resume via server detail / re-open wizard at last incomplete step when practical |
| B4 | State storage | Prefer **server row + existing fields** as source of truth; wizard is a view over that row (not a parallel draft table) |
| B5 | Business logic | **No duplicate** deploy-key / least-priv / DNS logic — call existing service functions / routes |
| B6 | Roles | operator+ (same as add server today); viewer denied |
| B7 | HAOS | Explicit copy: skip automated least-priv; show deps / feature guidance only |
| B8 | Password | After Connect success, strong CTA to **clear stored SSH password**; do not force-clear without confirm if still needed |

#### UX sketch

```text
/servers/new  (wizard)
  (1) Identity ── (2) Trust ── (3) Connect ── (4) Privilege ── …
  ●━━━━○━━━━○━━━━○━━━━○━━━━○━━━━○━━━━○

  [ Back ]     [ Save & exit ]     [ Continue → ]

/servers/new/advanced  → existing single form (kept)
Servers list CTA: “Add server” → wizard
```

#### Acceptance criteria (ship gate)

- [x] Primary CTA **Add server** opens the wizard (`/servers/new`)  
- [x] Advanced / single-form path remains reachable (`/servers/new/advanced` + legacy `/servers/add`)  
- [x] Each step uses existing SSH / feature / DNS building blocks (no second implementation)  
- [x] Connect success surfaces **clear password** guidance/CTA *(in-wizard deploy/test/clear-password)*  
- [x] HAOS path skips automated least-priv with clear copy *(privilege step guidance)*  
- [x] Schedules default to **checks only** (no surprise apply jobs) *(guidance; no auto-enable apply)*  
- [x] Done step offers sensible CTAs (open server / SSH / Docker when on)  
- [x] operator+ only (`get_operator_user` on wizard routes)  
- [x] Wiki [Add a server](../wiki/day-to-day/add-server.md) primary path becomes the wizard  
- [ ] Screenshots: `add-server-wizard.png` (+ optional done shot) — **stream A / tag gate**  
- [x] Unit pytest for step helpers (`tests/test_server_wizard.py`)  
- [x] **E2E B1–B5** primary CTA, identity→trust, save & exit, clear-password, advanced  
- [x] Stable **`data-testid`** hooks on wizard steps + Continue / Back / Save & exit  
- [x] No private key PEM in browser DOM (asserted in E2E B2)  

#### File map (expected — adjust while implementing)

| Area | Path (indicative) |
|------|-------------------|
| Router | `app/routers/servers.py` and/or new `server_wizard.py` (thin) |
| SSH actions | `app/routers/server_ssh.py` · `app/services/ssh_onboarding.py` (reuse) |
| UI | `app/templates/add_server_wizard.html` (+ step partials if needed) |
| Nav / list CTA | `server_list.html` · dashboard empty states |
| Unit tests | `tests/test_server_wizard.py` (new) or extend lifecycle tests |
| E2E tests | `e2e/` Phase B journeys (stream E) |
| Wiki | `wiki/day-to-day/add-server.md` · operator-scenarios if journey A changes |

---

### A — Documentation & screenshot pack — **must**

Prose for 0.6 surfaces is largely current. PNGs are the gap.

| Item | Notes | Priority | Status |
|------|--------|----------|--------|
| Refresh **stale** high-priority shots | dashboard, server-list/detail, jobs, templates-deploy, certificates-list, dns-logical, … | Must | **Open** — inventory in [screenshots README](../wiki/assets/screenshots/README.md) |
| **New 0.6 surfaces** | cert setup/detail/edge; docker project lifecycle; jobs live log; coverage; stack panel | Must | **Open** — listed as “new / missing” in screenshots README |
| **Wizard shots** | `add-server-wizard.png` (+ done) | Must (with B) | **Open** — UI frozen enough to capture |
| Wizard primary path in wiki | [add-server.md](../wiki/day-to-day/add-server.md) rewrite; advanced form secondary | Must | **Done** |
| Operator scenarios Journey A | Align with wizard if present | Should | Prose updated; re-check at screenshot land |
| Topology / compose-sets prose | Docker overview, fabric, testing, e2e README | Must (with C) | **Done** (2026-07-19) |
| `RELEASE_v0.7.0.md` | Written at tag | Must | End (stream D) |
| ADMIN / SPEC / ROADMAP sync | This plan + freeze | Must | Ongoing through tag |

**Capture policy (unchanged):** light theme · desktop default · no full light×dark×mobile matrix. See screenshots README.

**Landing practice:** local git → PNGs + Markdown same PR → `mkdocs build --strict` → merge to `main` (Pages deploy). Prefer one **docs PR** near ship that includes wizard shots once UI freezes; interim capture of non-wizard 0.6 surfaces can land earlier.

---

### E — E2E platform (Playwright) — **must**

**Problem:** Unit tests do not exercise HTMX shells, nav, or multi-step wizard UI. Regressions ship until an operator notices.

**Solution:** Browser E2E with **Playwright**, real web + Postgres (+ Redis as needed), **Chromium only**, separate CI job. Aligns with [ROADMAP Quality & platform](ROADMAP_ECOSYSTEM.md#quality--platform-post-rc--post-10-first-production) phases A/B.

#### Stack (locked)

| Item | Choice |
|------|--------|
| Tool | **Playwright** |
| Runner | **Python** `pytest-playwright` preferred (one mental model with unit pytest); Node `@playwright/test` only if blocked |
| Layout | `e2e/` at repo root (not mixed into pure unit `tests/`) |
| Base URL | env `PIHERDER_E2E_BASE_URL` (default `http://127.0.0.1:8000`) |
| Browser | **Chromium only** (0.7) |
| App under test | Compose set `docker-compose.e2e.yml` (services `e2e-web` / `e2e-db` / `e2e-redis`) under project **piherder**; port **18000**; no Caddy/TLS |
| Celery | Not required for Phase A; only if a Phase B path queues a job |
| Live SSH / real Pi | **Out of CI** |
| Seed | Deterministic admin, **2FA off**, registration policy allowing seed; synthetic master key |
| Selectors | Stable `data-testid` / role+name on wizard + primary CTAs; no Tailwind-class coupling |

#### Phase A — shell smoke (must, tag gate)

| # | Journey | Assert |
|---|---------|--------|
| A1 | Open `/login` | Form visible |
| A2 | Login as seeded admin | Land on dashboard (no force-password / 2FA for e2e user) |
| A3 | Nav: Dashboard, Servers, Catalog, Jobs, Audit, Settings | Expected shell / heading; no 5xx |
| A4 | Catalog tabs: Integrations, Certificates, Templates, Network | Reachable |
| A5 | Theme toggle once | No crash; theme changes |
| A6 | Logout (if exposed) | Back to login |

**Out of Phase A:** real fleet hosts, template deploy, cert upload, live map data, push, 2FA enrollment.

#### Phase B — critical paths for 0.7 (must, tag gate)

| # | Journey | Assert | SSH |
|---|---------|--------|-----|
| B1 | Primary **Add server** CTA → wizard | Step 1 Identity + step indicator | No |
| B2 | Identity → Trust (generate key) | Advances; **no private key PEM in DOM** | No |
| B3 | Connect step UI | Deploy/test controls present; controlled fail or mock success | Mocked |
| B4 | **Save & exit** mid-wizard | Partial server on list; resume works | No |
| B5 | **Advanced form** path | Still creates host | No |
| B6 | Viewer cannot add server | 403 / redirect | No |
| B7 | (Nice) Templates or certs list shell | Page loads | No |

**SSH strategy:** CI never opens outbound SSH to hardware. Prefer a **service-level mock** at deploy-key / test-connection. If mocking is too invasive for 0.7, B3 may assert UI + error surface only; unit tests own protocol depth — document which landed at freeze.

#### Phase C — out of 0.7 tag gate

Screenshot visual baselines, a11y `axe`, multi-browser matrix, full template/cert/Docker bulk E2E — **later**. Wiki PNG pack stays **manual** (stream A).

#### HTTP TestClient smoke (should)

`tests/test_http_smoke.py` (or similar): unauth login shell; auth dashboard/servers/jobs 200; viewer mutate 403. Runs in **existing** unit job — does not replace Playwright.

#### CI (must)

| Aspect | Requirement |
|--------|-------------|
| Unit job | Keep [`.github/workflows/test.yml`](../.github/workflows/test.yml); add HTTP smoke when ready |
| E2E job | **New** workflow (or job): path-filter `app/**`, `e2e/**`, compose/Dockerfile, lockfiles, workflow |
| Trigger | `push` + `pull_request` to main (and workflow_dispatch) |
| Steps | checkout → start stack → wait `/health` → install Chromium → run e2e → upload **trace/screenshot** on fail |
| Timeout | ~20–30 min |
| Gate | Red **blocks** merge narrative and **blocks v0.7.0 tag** |

**Flake policy:** Playwright auto-wait only (no arbitrary sleeps); stable test ids; optional single CI retry only for proven infra flakes; skip+issue rather than soft-fail the job.

#### Acceptance criteria (stream E)

- [x] `e2e/` runs locally with documented one-liner against compose stack *(scaffold + A1–A2)*  
- [x] GitHub Actions **e2e** job on main + PR; fails on regression *(workflow added)*  
- [x] Phase A green on clean seed *(A1–A6 local)*  
- [x] Phase B B1–B5 green local (B6 viewer RBAC — nice; **→ v0.8.0** if not trivial at freeze)  
- [x] Failure artifacts (trace / screenshot) retained on CI fail  
- [x] CONTRIBUTING or wiki developers documents run + debug  
- [x] No private key material visible in browser DOM (asserted in B2)  
- [ ] Reconfirm **E2E green in CI** on freeze week (ops, not new product)

#### File map

| Area | Path |
|------|------|
| Suite | `e2e/` (`conftest.py`, `helpers.py`, `test_shell_login.py`, …) |
| Compose harness | `docker-compose.e2e.yml` (compose set) + `scripts/e2e-up.sh` / `e2e-down.sh` (project `piherder`) |
| Seed | First-boot register in `e2e/helpers.py` (`e2e@piherder.test`) |
| CI | `.github/workflows/e2e.yml` |
| Deps | `pyproject.toml` optional `[e2e]` · host `pip install pytest-playwright` |
| Docs | `e2e/README.md` · wiki `developers/testing.md` · CONTRIBUTING |

---

### C — Residual polish (capacity only) — **closed for 0.7**

Pulled from 0.6 “open / post-0.6” notes. **None of these are tag gates.** Capacity after B+E was used for topology + compose sets; **remaining open items are deferred to [v0.8.0 RC3](PLAN_v0.8.0.md)** (2026-07-19 decision).

| Item | Why | Priority | Status |
|------|-----|----------|--------|
| Template **drift check as Job** + live log | Consistency with deploy/redeploy | Nice | **Done** (ships in 0.7) |
| **Topology annotations** (T0–T4): exact project match; category override; fixed tags; visual service stacks; map columns from vocab | Stack view clutter (e.g. e2e); deferred columns residual | Nice | **Done** core (ships in 0.7) |
| **Compose sets** (multi compose file, one project folder) | Split services across `docker-compose.yml` + `docker-compose.*.yml` without a second stack card | Nice | **Done** (ships in 0.7) |
| Cert **multi-map / multi-host deploy as Job** | Long ops pattern | Nice | **→ v0.8.0 RC3** |
| Template wizard copy / empty states | First-use clarity | Nice | **→ v0.8.0 RC3** |
| Docker management chips cohesive with stack panel (T6) | Stretch UX | Stretch | **→ v0.8.0 RC3** |
| List query / Docker inventory spam / fabric pulse | Light perf | Stretch | **→ v0.8.0 RC3** |
| Git template catalog pull | Ops depth | Out unless trivial | **→ v0.8.0 RC3** / later |
| E2E B6 viewer cannot add server | RBAC journey | Nice | **→ v0.8.0 RC3** if not free at freeze |
| Playwright Phase C / HTTP smoke expansion | Quality depth | Stretch | **→ v0.8.0 RC3** / later |

**Decision (2026-07-19):** No further product polish in 0.7. Remaining work before tag = **stream A screenshots** + **stream D packaging** + operator/CI confirmations.

#### Compose sets (active) — same project, sub-views

**Problem:** Operators want one directory / one Docker project card, but services split across extra compose files (e.g. main + e2e + build), not a second top-level stack.

**Rules (locked):**

| Concept | Behaviour |
|---------|-----------|
| **Project** | Still one folder + compose project name (directory) |
| **Primary compose** | `docker-compose.yml` / `compose.yml` |
| **Override** | Auto-merged by Compose (`*.override.yml`) — multi-file editor, not a “set” |
| **Compose set** | Extra `docker-compose.<name>.yml` / `compose.<name>.yml` in the **same** directory |
| **Docker view** | **Same project card**; pill sub-views All / main / each set (filter services) |
| **Not a new stack** | Do not invent a second project from a second file in the same folder |
| **Deploy** | Default = whole project; optional **Deploy this set** → `docker compose -f <file> …` still under same project name |
| **Fabric view groups** | Orthogonal (operator presentation); sets are **file/deploy** slices |

**Ship slice (this build):**

- [x] Discover compose sets when listing a project (inventory)
- [x] Docker page: under-project pills filter containers by set membership
- [x] Live files / editor probes include extra compose set files
- [x] Optional set-scoped deploy (`-f`) from project ⋯ when a set is selected
- [x] Unit tests for filename classify + set membership
- [x] Local piherder: e2e services live in `docker-compose.e2e.yml` (same project)

---

### D — Packaging (end of cycle)

| Item | Notes |
|------|--------|
| Unit pytest green | Full pack in image / CI |
| **E2E Playwright green** | Phase A + B in CI (stream E) |
| Version bump | `pyproject.toml` · `app/version_info.py` → `0.7.0` |
| `RELEASE_v0.7.0.md` | Highlights, upgrade, intentional outs |
| Git tag `v0.7.0` | + GitHub Release from notes |
| Hub multi-arch | `0.7.0` / `0.7` / `latest` |
| `mkdocs build --strict` | If wiki touched at tag (expected) |

---

## 4. Explicitly out of v0.7.0

| Area | Target | Reason |
|------|--------|--------|
| **H2.75 P3** Host stats / allowlisted commands | post-0.7 / 0.8+ | Lifecycle depth |
| **H2.75 P4** Bootstrap scripts + enrollment phone-home | post-0.7 / 0.8+ | Wizard may *link* existing least-priv only |
| **H2.75 P5** Web SSH console | later | High security bar |
| **LAN discovery / nmap** | **[v0.8.0 RC3](PLAN_v0.8.0.md)** | Product theme of next cycle |
| Stream-C residual (multi-map cert Jobs, template empty states, T6 chips, list/perf, git catalog) | **[v0.8.0 RC3](PLAN_v0.8.0.md)** | Cut at 2026-07-19 product review |
| Configurable topology columns (full link-to-column) | 0.8+ | T0–T4 annotations landed in 0.7 |
| ACME inside PiHerder | later | Use NPM / external |
| NPM proxy host write CRUD | later | |
| Cloudflare DNS automation | H2.5+ | |
| Large curated template pack | H3 | |
| Kubernetes / bare install as supported | under consideration | |
| Full v1.0 process freeze | v1.0 | |
| Live SSH / real Pi in GitHub Actions E2E | never for CI | Mock or UI-only Connect |
| Playwright Phase C (visual baselines, a11y, multi-browser) | **v0.8.0+** | |
| Auto-capture wiki PNGs from Playwright | later | Manual stream A pack stays |

**Thin bootstrap exception:** wizard Privilege step may surface **existing** least-priv / copy-paste scripts already on SSH access. That is **not** P4 (downloadable pre-join package, hostname set product, enrollment API).

---

## 5. Ship bar (`v0.7.0` tag)

### Must-have

| # | Item | Status |
|---|------|--------|
| 1 | Add-host wizard primary path (B acceptance criteria) | **Done** (product) |
| 2 | Advanced form still available | **Done** |
| 3 | Wiki: wizard primary + advanced secondary | **Done** |
| 4 | Screenshot pack: high-priority refresh + new 0.6 surfaces + wizard | **Open** — last product-ish tag gate |
| 5 | Unit pytest green | Reconfirm at freeze |
| 6 | **E2E scaffold + compose/CI harness** (E-a) | **Done** |
| 7 | **E2E Phase A shell smoke** (E-b) | **Done** local (A1–A6); reconfirm CI at freeze |
| 8 | **E2E Phase B wizard journeys B1–B5** (E-c) | **Done** local; reconfirm CI at freeze |
| 9 | **E2E failure artifacts** (trace/screenshot) on CI fail (E-d) | **Done** |
| 10 | Version `0.7.0` + git tag + multi-arch Hub | **Open** (stream D) |
| 11 | `RELEASE_v0.7.0.md` | **Open** (stream D) |
| 12 | Secret-path review unchanged (no key material to browser) | **Done** (E2E B2 asserts) |

### Should (not hard tag gates)

- HTTP TestClient smoke (E-e) — nice; else → 0.8  
- ~~Stream C residual~~ **cut → [v0.8.0 RC3](PLAN_v0.8.0.md)**

### Not tag gates (deferred)

- Playwright Phase C · P3–P5 · nmap · open C polish · topology column profiles → **[PLAN_v0.8.0.md](PLAN_v0.8.0.md)**

### QA smoke (operator + automation)

- [ ] **E2E Phase A** green in CI  
- [ ] **E2E Phase B** green in CI  
- [ ] **Wizard (manual):** new Pi through all steps → Test connection OK → password cleared → features set → optional DNS  
- [ ] **HAOS path:** least-priv skipped with understandable copy  
- [ ] **Advanced form:** still adds a host; SSH access panel still works  
- [ ] **Save & exit** mid-wizard: partial host usable; can continue  
- [ ] Wiki add-server page matches UI; screenshots render on Pages  
- [ ] Spot-check cert setup / Docker bulk / jobs live log pages against new PNGs  
- [ ] Unit `pytest` green  
- [ ] `mkdocs build --strict` green  
- [ ] About shows **0.7.0**; Hub pull of `0.7.0`  

---

## 6. Implementation order

```text
1. PLAN_v0.7.0 + E2E requirements (this refresh)   // docs
2. E2E scaffold + compose harness + A1–A2 login    // E early
3. Phase A nav smoke complete + CI e2e job         // gate exists
4. Wizard shell (routes, step chrome, advanced)    // B + data-testid hooks
5. E2E B1–B5 as wizard steps land                  // tests track feature
6. Steps 1–3 Identity / Trust / Connect            // core trust path
7. Steps 4–6 Privilege / Features / Schedules      // enablement
8. Steps 7–8 Network / Done CTAs                   // optional + finish
9. Wiki add-server rewrite (prose first)           // A with B
10. Screenshot pack (0.6 surfaces, then wizard)    // A (parallel OK)
11. HTTP TestClient smoke (should)                 // cheap extra
12. Capacity: C items only if green early          // optional
13. Freeze: unit + E2E · RELEASE · version · Hub
```

Parallelism: non-wizard screenshots and E2E scaffold can proceed while wizard deepens. **Do not** invent E2E only at freeze week.

---

## 7. Open questions (resolve early in cycle)

| # | Question | Default lean |
|---|----------|--------------|
| 1 | Exact paths: `/servers/new` vs `/servers/add` wizard? | **`/servers/new`** wizard; **`/servers/new/advanced`** form; redirect old `/servers/add` if needed |
| 2 | Resume wizard from partial host? | **Yes** — “Continue setup” on server detail when trust/connect incomplete; full re-entry from step 1 also OK |
| 3 | Schedule step UI depth? | **Toggles only** (enable checks); deep schedule editor stays on server detail |
| 4 | Network step without Pi-hole? | Show FQDN fields + short “add Pi-hole later” note; no hard dependency |
| 5 | Ship C multi-map cert Jobs? | **Only if** B+A+E done with time left |
| 6 | Dev version string when first code lands? | **`0.7.0.dev0`** until freeze packaging |
| 7 | Connect E2E: mock SSH vs UI-only fail assert? | **Mock if cheap**; else UI-only B3 + unit depth |
| 8 | pytest-playwright vs Node Playwright? | **Python first**; switch only if blocked |

---

## 8. Docs map (this cycle)

| Doc | Role |
|-----|------|
| **This file** | Living ship plan (incl. E2E requirements) |
| [FEATURE_PLAN_HOST_LIFECYCLE.md](FEATURE_PLAN_HOST_LIFECYCLE.md) | P2 design + acceptance (detail) |
| [RELEASE_v0.7.0.md](RELEASE_v0.7.0.md) | Written at tag only |
| [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) | Multi-horizon; Quality § Playwright phases |
| [SPEC.md](../SPEC.md) | Phase 6.5 checkbox for P2 + quality note |
| [wiki screenshots README](../wiki/assets/screenshots/README.md) | Capture inventory |
| `wiki/day-to-day/add-server.md` | Operator truth for onboarding |
| CONTRIBUTING / wiki developers | How to run unit + E2E |
| [PLAN_v0.6.0.md](PLAN_v0.6.0.md) | Frozen prior cycle |

---

## 9. Success criteria (operator + maintainer story)

After upgrading to **0.7.0**, an operator can:

1. **Add a Pi** through a **guided wizard** without hunting for Deploy key / features order.  
2. **Finish** with clear next jobs (backup / update check / Docker) or exit and resume.  
3. Still use the **advanced form + SSH access** path when they want one-shot entry.  
4. Read the **wiki** with **current screenshots** for onboarding, certs, Docker bulk, Jobs live log, and topology surfaces.  
5. Rely on 0.6 capabilities unchanged (templates Jobs, cert vault, stack lifecycle, maps).

Before tagging **0.7.0**, a maintainer (or CI) can:

1. Open a PR that touches wizard UI → **unit** + **e2e** jobs run.  
2. E2E proves **login → shell nav → add-server wizard** chrome and core steps.  
3. A broken Continue control or missing nav target fails CI with a **trace artifact**.  
4. Freeze requires **both** unit pytest and Playwright packs green.

---

## 10. Relationship to later releases

| Release | Contribution |
|---------|----------------|
| **0.6.0** | Template Jobs · cert UX · Docker bulk · topology+coverage · dual TLS prose (**RC2**) |
| **0.7.0** | **Add-host wizard** · **Playwright E2E (A+B)** · topology annotations + **compose sets** · **screenshot pack** (tag) · no further C polish |
| **0.8.0 RC3** | **LAN nmap** + residual polish cut from 0.7 · optional P3/quality depth — [PLAN_v0.8.0.md](PLAN_v0.8.0.md) |
| **v1.0** | Stable schema + REST + full docs freeze bar |

**After 0.7.0 tag:** → **[v0.8.0 RC3](PLAN_v0.8.0.md)** → **v1.0**.

---

## 11. Changelog (plan)

| Date | Note |
|------|------|
| 2026-07-18 | Dev phase opened; musts = wizard + screenshots; C = capacity only; P3–P5 and nmap out |
| 2026-07-18 | **E2E elevated to must** — Playwright Phase A + wizard Phase B; separate CI job; hard tag gate |
| 2026-07-18 | E2E scaffold: `e2e/`, compose harness, CI workflow, A1–A2 login (**2 passed** local) |
| 2026-07-18 | E2E Phase A complete: nav + catalog tabs + theme + logout (**6 passed** local) |
| 2026-07-18 | Wizard shell: `/servers/new` 8-step UI; advanced form; E2E B1/B5 (**8 e2e** + 4 unit) |
| 2026-07-18 | Connect in-wizard (deploy/test/clear-password); wiki wizard primary; E2E B2–B4 (**11 e2e**) |
| 2026-07-18 | Stream C: template **drift check as Job** + JobHold (exclusive per host) |
| 2026-07-19 | Stream C: **topology annotations (T0–T4)** + **compose sets** (Docker pills, set deploy, e2e file under project) |
| 2026-07-19 | **Product review:** B + E product-closed; C capacity closed; open C residual + nmap → **[PLAN_v0.8.0.md](PLAN_v0.8.0.md) RC3**; remaining 0.7 = screenshots (A) + packaging (D) |

---

**End of plan** — product streams closed; freeze narrative moves into `RELEASE_v0.7.0.md` at tag.
