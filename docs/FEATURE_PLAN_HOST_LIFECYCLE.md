# Feature plan — Host lifecycle & operator console (H2.75)

**Status:** Design agreed 2026-07-17 · **P1 Docker bulk shipped** (2026-07-18) · **P2 wizard active in v0.7.0** ([PLAN_v0.7.0.md](PLAN_v0.7.0.md))  
**Horizon:** H2.75 · P1 via [PLAN_v0.6.0.md](PLAN_v0.6.0.md); **P2 ship plan → [PLAN_v0.7.0.md](PLAN_v0.7.0.md)**; P3–P5 post-0.7  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § [Horizon 2.75](ROADMAP_ECOSYSTEM.md#horizon-275--host-lifecycle--operator-console-post-rc) · [PLAN_v0.7.0.md](PLAN_v0.7.0.md) · [PLAN_v0.6.0.md](PLAN_v0.6.0.md) · [SPEC.md](../SPEC.md) · [ADMIN.md](ADMIN.md) · Wiki [Docker](../wiki/docker/overview.md) · [Add server](../wiki/day-to-day/add-server.md)

## Goal

Deepen **day-to-day host operations** and **first-time host bring-up** without changing PiHerder’s **SSH-first** model:

1. Project-level **Docker bulk** start / stop / restart  
2. **Wizard** add-host onboarding  
3. **Richer host stats**, healthchecks, **allowlisted** remote commands  
4. **Bootstrap scripts** + hostname / Pi-hole DNS handoff  
5. Optional **web SSH console** (server-side key injection) — highest security bar  

**Non-goals for this plan:** replace Uptime Kuma; VNC/RDP; agent-based management; arbitrary root shell without console product; full network-manager / static-IP product; anonymous first-boot registration.

---

## Decisions (locked unless reversed)

| # | Decision |
|---|----------|
| 1 | **v0.6.0 RC2** — P1 Docker bulk **shipped**; **P2 wizard ships in v0.7.0** — [PLAN_v0.7.0.md](PLAN_v0.7.0.md) |
| 2 | Ship order = **risk/value:** Docker bulk (0.6) → wizard (0.7) → stats/commands → bootstrap/DNS → web SSH last |
| 3 | **Not required for minimal 1.0.0** unless we deliberately pull P1 into 1.0 as polish |
| 4 | Web SSH: private key **never** sent to the browser; decrypt only in server memory for the PTY session |
| 5 | Web SSH: **operator/admin** only; prefer **step-up 2FA**; env kill switch `PIHERDER_SSH_CONSOLE=false` (default off until GA) |
| 6 | “Run command” is **allowlist / template** only — not a free shell (free shell = console item 5) |
| 7 | First-boot phone-home requires **enrollment token** / pre-created server row — never open join |
| 8 | Static IP remains **script + docs**; PiHerder does not become DHCP/server networking UI |
| 9 | All privileged actions: **preview → confirm → audit** (+ Job when long-running) |
| 10 | Reuse existing patterns: server bulk (`POST /servers/bulk`), stack jobs (`docker_stack_*`), least-priv scripts, Pi-hole A fan-out |

---

## Phases

| Phase | Name | Target | Status |
|-------|------|--------|--------|
| **P1** | Docker project bulk control | 0.6.0 nice-to-have | **Done** (2026-07-18) |
| **P2** | Add-host wizard | **v0.7.0** | **In progress** — shell on `main` path (`/servers/new`); [PLAN_v0.7.0.md](PLAN_v0.7.0.md) |
| **P3** | Host stats + healthcheck + allowlisted commands | post-0.7 | Planned |
| **P4** | Bootstrap scripts + hostname + DNS handoff | post-0.7 | Planned |
| **P5** | Web SSH console | Last; separate ship bar | Planned / under consideration |

---

## Phase 1 — Docker project bulk control

### Problem

Operators restart a whole stack one container at a time, or drop to SSH for `docker compose restart`. Server-list bulk already covers fleet OS/container patch/backup; **per-project service lifecycle** is missing.

### What

On the host **Docker** page (project row / stack ⋯ / project detail):

| Action | Remote (preferred) | Notes |
|--------|-------------------|--------|
| **Stop all services** | `docker compose stop` in project dir | Graceful |
| **Start all services** | `docker compose start` | |
| **Restart all services** | `docker compose restart` | |
| (Later) **Down / Up** | `compose down` / `up -d` | Separate confirm; volume wipe **out of scope** |

Optional later: multi-project multi-select on the same host; fleet-wide “restart all *slug*” only with strong confirm.

### UX sketch

```text
Docker page — project card / ⋯ menu
┌─────────────────────────────────────────────┐
│  my-stack · 3 services · Up                 │
│  [⋯]  Check updates · Deploy · Full editor  │
│       ── lifecycle ──                       │
│       Stop all services…                    │
│       Start all services…                   │
│       Restart all services…                 │
└─────────────────────────────────────────────┘

Confirm modal
┌─────────────────────────────────────────────┐
│  Restart all services — my-stack            │
│  Host: pi-garage                            │
│  Services: web, db, redis                   │
│  [ Cancel ]  [ Restart all ]                │
└─────────────────────────────────────────────┘
        → JobHold modal (live log) → Jobs / Audit
```

### Behaviour

| Rule | Detail |
|------|--------|
| Feature flag | **Docker / containers** must be on |
| Role | operator+ (same as other Docker mutate) |
| Job types | e.g. `docker_stack_stop` / `docker_stack_start` / `docker_stack_restart` (or one type + `action` in details) |
| Concurrency | Align with stack check/deploy exclusivity: at most one mutating stack job per host (or per project — prefer **per host** for compose lock safety) |
| Inventory | Refresh snapshot after terminal success |
| Template stacks | Allowed (lifecycle ≠ compose file edit); do not bypass desired-state for file content |
| Audit | `docker_stack_stop` / `_start` / `_restart` + project name + client IP |

### Acceptance criteria (P1)

- [x] Project ⋯ offers Stop all / Start all / Restart all when Docker feature on  
- [x] Confirm lists project name; **Stop** uses danger styling  
- [x] Runs as Job with live log; success/fail updates Jobs + Audit (`docker_stack_stop` / `_start` / `_restart`)  
- [x] Second concurrent stack mutation on same host → `JobAlreadyActive` / HTTP 409 attach  
- [x] Viewer cannot invoke (operator+ on bulk lifecycle path)  
- [x] Wiki [Docker overview](../wiki/docker/overview.md) + compose-edit updated  
- [x] pytest: `tests/test_docker_stack_lifecycle.py` (compose_action + enqueue exclusive + execute)  

**Shipped paths:** `enqueue_docker_stack_lifecycle` · `POST /servers/{id}/docker/compose/{stop|start|restart}` (empty service) · project ⋯ menu + lifecycle confirm + JobHold.

### File map (expected)

| Area | Path (indicative) |
|------|-------------------|
| Router | `app/routers/docker*.py` or stack actions module |
| Service | compose lifecycle helper next to stack check/deploy |
| UI | Docker project partial / ⋯ menu + confirm |
| Jobs | job type constants + runner |
| Tests | `tests/test_docker_stack_lifecycle.py` (new) |

---

## Phase 2 — Add-host wizard

### Problem

Today: Add server form → open **SSH access** → deploy key → test → least-priv → features → schedules → optional DNS. New operators miss order or leave password bootstrap stored.

### What

Single guided flow (reuse existing actions; **orchestration only**):

| Step | Content |
|------|---------|
| 1 Identity | Name, hostname/IP, port, SSH user |
| 2 Trust | Generate/upload key; optional one-time password |
| 3 Connect | Deploy key → Test connection |
| 4 Privilege | Least-priv (Debian/Pi OS) or skip (HAOS); Docker base dir + ACL if needed |
| 5 Features | Backups / OS / Docker toggles |
| 6 Schedules | **Checks only** default; apply schedules off |
| 7 Network | Optional FQDN + Manage A on Pi-holes |
| 8 Done | Summary + CTAs: first backup, update check, open Docker |

Progress: step indicator; “Save & exit” leaves a partial server (same as today mid-setup).

### UX sketch

```text
/servers/new/wizard  (or modal multi-step on /servers/new)
  (1) Identity ── (2) Trust ── (3) Connect ── …
  ●━━━━○━━━━○━━━━○━━━━○━━━━○━━━━○

  [ Back ]                    [ Continue → ]
```

### Acceptance criteria (P2)

- [x] New entry point “Add server (wizard)” + keep advanced/single-form path or collapse into wizard only  
- [x] Each step calls existing SSH/feature/DNS endpoints (no duplicate business logic)  
- [x] Password bootstrap cleared recommendation at Connect success *(in-wizard clear CTA + deploy option)*  
- [x] HAOS path skips automated least-priv with clear copy  
- [x] Wiki [Add a server](../wiki/day-to-day/add-server.md) primary path becomes wizard  

---

## Phase 3 — Host stats, healthcheck, allowlisted commands

### Problem

Host status is a short SSH snapshot. Operators want load/disk/temp and occasional safe commands without a full shell.

### What

| Capability | Direction |
|------------|-----------|
| **Stats** | load, memory, disk, uptime, temp (if available), reboot-pending — **cached** (TTL); not continuous poll on every navigation |
| **Healthchecks** | Built-in probes (SSH ok, docker ps, apt lock free?) + optional operator-defined shell **from allowlist templates** |
| **Run command** | Catalog of templates (`df -h`, `uptime`, `docker system df`, …) with optional typed args; preview → confirm → Job + audit log tail |

### Explicit non-goals (P3)

- Free-form root shell  
- Continuous metrics time-series (use Grafana)  
- Viewer-run commands  

### Acceptance criteria (P3)

- [ ] Server Host status shows expanded stats card + last collected time + Refresh  
- [ ] Commands only from allowlist (config/code table); unknown strings rejected  
- [ ] operator+ only; audit action + client IP + command id (not secrets)  
- [ ] Stats collection fails soft (partial fields) without failing whole page  

---

## Phase 4 — Bootstrap scripts + hostname + DNS

### Problem

Least-priv exists **after** the host is already reachable with some user. Operators want **pre-join** scripts and automatic DNS when a new Pi appears.

### Layers

| Layer | Deliverable |
|-------|-------------|
| **A — Offline scripts** | Download/copy: create `piherder` user, `authorized_keys`, sudoers, docker group; optional `hostnamectl` — wizard “Prepare host” step |
| **B — Hostname over SSH** | After connect, optional set hostname (audited) |
| **C — DNS handoff** | Enable Manage A + FQDN → existing Pi-hole fan-out (already close) |
| **D — First-boot callback** | cloud-init / script phones home with DHCP IP + enrollment token → update server IP / dns; **no anonymous register** |
| **E — Imaging** | Pi Imager / cloud-init full image — remains Horizon 3 depth |

### Enrollment (layer D — design)

```text
Admin creates server row (pending) + one-time enrollment token
        │
        ▼
Host first boot script → POST /api/v1/enroll (token + reported IP + hostname)
        │
        ▼
PiHerder binds IP, optional Pi-hole A, marks enrolled (token burned)
```

### Acceptance criteria (P4 A–C first)

- [ ] Wizard/SSH access: download bootstrap script with public key embedded or placeholder  
- [ ] Hostname set action (Debian/Ubuntu) with confirm + audit  
- [ ] Network step creates/updates Pi-hole A when integration present  
- [ ] Layer D/E: design note only until enrollment API ship decision  

---

## Phase 5 — Web SSH console

### Problem

Operators on a tablet/phone or locked-down PC want a terminal without exporting the herder private key.

### Architecture

```text
Browser (xterm.js)
    │  WSS /api/.../console?ticket=…
    ▼
PiHerder web (auth + step-up + ticket)
    │  Paramiko / asyncssh PTY
    ▼
Fleet host SSH (key from Fernet decrypt in memory only)
```

| Rule | Detail |
|------|--------|
| Key path | Browser **never** receives PEM |
| Roles | operator+ ; viewer denied |
| Step-up | TOTP (or force-2FA policy) before ticket mint |
| Ticket | Short TTL (e.g. 60s mint → 30–60 min session); single use to open WS |
| Limits | Max concurrent consoles per user/instance; idle disconnect |
| Kill switch | `PIHERDER_SSH_CONSOLE=false` default until feature GA |
| Audit | `ssh_console_open` / `close` + IP + duration; interactive command capture **best-effort only** (document limitation) |
| Threats | XSS → terminal; herder as jump host; shared sessions |

### Acceptance criteria (P5)

- [ ] Feature flag/env off by default  
- [ ] Step-up required when 2FA enabled or policy “console requires 2FA”  
- [ ] No private key in any HTTP response or frontend bundle  
- [ ] CSP + trusted TLS documented as requirement  
- [ ] Viewer 403; admin can disable globally  
- [ ] Security review note in SECURITY.md / ADMIN  

### Deferred / optional later

- Session recording  
- Shared break-glass “console as root” with dual control  

---

## Security summary

| Phase | Main controls |
|-------|----------------|
| P1 | RBAC, confirm, audit, job exclusivity |
| P2 | Same as today + no lingering bootstrap password guidance |
| P3 | Allowlist commands, RBAC, audit, cache TTL |
| P4 | Script content review, enrollment token, no open join |
| P5 | Step-up, tickets, kill switch, server-side keys only, CSP |

Align with design principles: auditable privileged actions; secrets encrypted at rest; opt-in dangerous surfaces.

---

## Wiki / docs impact (when implementing)

| Doc | Update |
|-----|--------|
| [wiki/docker/overview.md](../wiki/docker/overview.md) | P1 bulk lifecycle |
| [wiki/day-to-day/add-server.md](../wiki/day-to-day/add-server.md) | P2 wizard primary path |
| [wiki/getting-started/operator-scenarios.md](../wiki/getting-started/operator-scenarios.md) | Journey A + Docker row |
| [wiki/account-security/roles.md](../wiki/account-security/roles.md) | Console / commands if needed |
| [ADMIN.md](ADMIN.md) | Ops reference for each phase |
| [API.md](API.md) | If token scopes gain enroll/console |
| [SECURITY.md](../SECURITY.md) | Console threat model (P5) |

Operator wiki: short “Planned post-RC” callout only until a phase ships (no full plan in nav).

---

## Success criteria (horizon)

An operator can:

1. Restart an entire compose project from the Docker UI with audit trail (P1).  
2. Add a new Pi through a wizard without missing key deploy / features (P2).  
3. See host load/disk and run a few safe diagnostic commands (P3).  
4. Bootstrap a `piherder` user from a generated script and get Pi-hole A after join (P4 A–C).  
5. Optionally open a browser terminal without downloading the host key (P5, optional).

---

## Open questions (resolve at phase kickoff)

| # | Question | Default lean |
|---|----------|--------------|
| 1 | P1 exclusive lock: per host vs per project? | **Per host** (safer compose) |
| 2 | Wizard: replace classic form or dual entry? | Wizard primary; “Advanced” link |
| 3 | Command allowlist: code table vs admin-editable? | Code table first |
| 4 | Enrollment API under session only or token REST? | One-time token endpoint + rate limit |
| 5 | Console default off until 1.x.y? | **Yes** until security bar signed off |

---

## Changelog

| Date | Note |
|------|------|
| 2026-07-17 | Initial plan; aligned with ROADMAP H2.75; operator agreement on order and security bar |
| 2026-07-18 | P1 shipped in 0.6; P2 deferred at freeze then opened under [PLAN_v0.7.0.md](PLAN_v0.7.0.md) |
