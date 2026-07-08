# UI Unification & Mobile-First Refresh Plan

**Status:** Draft (plan mode)  
**Date:** 2026-07-08  
**Branch context:** main (worktree 2026-07-08-2cc520f0)  
**Goal:** Deliver a cohesive, minimal, mobile-friendly UI while strictly preserving PiHerder's lightweight architecture and principles.

---

## Context (Why this change)

Current UI has grown organically across many Jinja templates. Pain points:

1. **Not mobile friendly** — tables, button rows, headers, and grids break or become cramped on small screens. No systematic responsive strategy.
2. **Inconsistent design & experience** — every screen (dashboard, server list, server detail, docker, audit, backups, herder-backups, auth pages) has different header styles, logo placement, spacing, and button conventions.
3. **Branding drift** — logos appear at varying sizes, taglines are inconsistent ("BACKUP YOUR PI FLEET", "BACKUPS"), accent/primary usage varies.
4. **Poor feedback for long-running / async actions** — Docker screen is the best example (page loading overlay + dedicated modals for Build/Deploy/Undeploy/Logs/Service select). Most other actions (backup, patch, retention, reboot) either redirect silently, use browser confirm, or have duplicated one-off modals.
5. **Visual clutter & repetition** — Server list cards + server detail + docker management repeat the same concepts (status pills, many tiny action buttons, feature toggles, "Run Jobs", backup status banners, "Manage" links). Screens feel busy.
6. **Lightweight discipline** — Must remain offline-capable (vendored assets), no new frameworks or build steps, continue using Jinja + HTMX + Alpine + vanilla JS + themes.css + base.html fallbacks. Plan itself must live in git.

The Docker modals + loading overlay pattern + existing `PiHerderPoll` / progress helpers are the reference implementation to spread.

---

## Recommended Approach (Single coherent direction)

**Core strategy: "Minimal Uniform Shell + Modal Feedback Everywhere"**

- Adopt **mobile-first** (stack everything by default; only expand on `md:`+).
- Create **one shared page header pattern** (logo + title + subtitle + back link + primary action) and use it uniformly.
- Standardize **cards, sections, and button clusters** into a few reusable visual patterns (primary action row, secondary row, compact status row).
- **Centralize feedback**: 
  - Reusable loading/feedback modal (or extend the docker page-loading pattern).
  - Promote action modals (confirm + options) for all destructive or long-running operations, modeled exactly on docker's build/deploy/undeploy modals.
  - Use the existing progress polling pattern for anything that streams (backups, patches, builds).
- **Minimise & deduplicate**:
  - Server list: compact row cards, collapse move controls or make them touch-friendly.
  - Server detail: one consolidated "Actions" area + progressive disclosure (Backups / Docker links open their full pages; avoid duplicating full status + buttons everywhere).
  - Docker: already good — keep and polish the same modal language.
  - Remove repeated "feature pills", repeated backup status blocks, repeated tiny button groups.
- **Branding lock-in**:
  - Single source of truth for logo placement + sizing in base + shared header.
  - Consistent use of `--color-*` vars and `.btn-*`, `.card` classes.
  - One tagline treatment.
- **Keep architecture pristine**:
  - All changes in templates + `themes.css` + small vanilla/HTMX/Alpine additions inside `base.html`.
  - No Python restructuring required for UI layer (routers/services stay exactly as-is).
  - Vendored assets untouched.
  - Every action still audited (existing behavior).

**Phased rollout inside the plan (so we can land incremental PRs or commits):**
- Phase A: Foundations (base + CSS + shared header component)
- Phase B: Server list + dashboard (biggest clutter + mobile wins)
- Phase C: Server detail + backups pages (dedupe sections)
- Phase D: Docker polish + propagate modal/feedback patterns to every long action
- Phase E: Audit, herder-backups, auth pages, consistency sweep
- Phase F: Polish, accessibility, final verification

---

## Critical Files to Modify

**High impact / shared:**
- `app/templates/base.html` — nav, shared fallback CSS, common modal primitives, page header include/macro, global JS helpers (progress, feedback modal)
- `app/static/css/themes.css` — mobile-first responsive additions, unified button sizing, compact card rules, touch targets, consistent spacing scale

**Page templates (all must be touched for uniformity):**
- `app/templates/dashboard.html`
- `app/templates/server_list.html`
- `app/templates/server_detail.html`
- `app/templates/server_backups.html`
- `app/templates/docker.html` + fragments (`docker_containers_table.html`, `docker_logs.html`, `docker_build_progress.html`, `docker_compose_edit.html`, `new_docker_project.html`)
- `app/templates/audit.html`
- `app/templates/herder_backups.html`
- `app/templates/login.html`, `register.html`, `add_server.html`

**Supporting / reused code (mostly read-only reference):**
- Existing modal implementations in `docker.html` (build-modal, deploy-confirm-modal, undeploy-confirm-modal, logs-modal, service-select-modal, page loading overlay)
- Backup progress modal + `PiHerderPoll` / `startBackupProgressPoll` logic in `server_list.html` (and duplicated in `server_backups.html`)
- `app/routers/servers.py`, `server_backups.py`, `server_docker.py` — only for route knowledge, no logic changes expected
- `app/services/jobs.py` + progress endpoints — existing async feedback mechanism

**New or extracted (minimal):**
- Optional: `app/templates/partials/page_header.html` (or Jinja macro inside base) — decide during implementation
- One shared feedback/progress modal definition (prefer single definition in base.html, toggled by JS)

---

## Existing Functions, Patterns & Utilities to Reuse

**UI patterns (gold standard):**
- Docker page loading overlay (`#docker-page-loading` + JS that forces show on load/bfcache) — `app/templates/docker.html:6-55`
- Action modals: `showBuildModal` / `hideBuildModal`, deploy/undeploy confirm patterns (`docker.html:379+`)
- Logs modal + streaming: `showLogsModal`, `loadLogsForModal`, `expandLogsToFullScreen`
- Service select modal for multi-service actions

**Progress / feedback:**
- `window.PiHerderPoll` + `PiHerderUpdateLog` (global, already loaded via base or inline)
- `startBackupProgressPoll`, `showBackupProgressModal`, `backupProgressUrl` (`server_list.html:119+`)
- Job-backed status (DB Job row + details) used for "running" banners

**Styling system:**
- CSS vars in `themes.css` (bg/surface/text/accent/border + semantic banners)
- `.card`, `.btn`, `.btn-accent`, `.btn-secondary`, `.btn-danger`, `.btn-ghost`
- `.banner-success`, `.banner-warning`, `.banner-error`, `.status-*` classes
- Existing responsive bits (a few `grid-cols-1 md:grid-cols-*` and media queries in audit)

**Branding assets:**
- `/static/images/piherder-logo-small.png` and `.svg` — use consistently at defined sizes (e.g. h-8 or h-10)
- Accent color `#00A651` / red primary already defined

**Architecture invariants (never violate):**
- Offline / vendored only (no new CDNs)
- Jinja2 templates
- HTMX for partials where already used
- Alpine only where currently present
- All privileged work still goes through Celery + AuditLog

---

## Key Design Decisions (chosen direction)

- **Header unification**: Every content page starts with a consistent header block: (optional back link) + logo (small) + title + optional subtitle + right-side primary action. Use a Jinja macro or copy-once pattern in base.
- **Button clusters**: Max 2–3 primary actions visible; everything else in "…" or secondary row. On mobile: stack or become full-width.
- **Touch targets**: Minimum 44px tall for actionable items on small screens.
- **Modals for everything long-running or destructive**:
  - Any "Run X" that can take >2s → show a modal immediately with title + "Starting…" + live log area (reuse docker style).
  - Use the existing X-PiHerder-Async header + job_id response pattern.
- **Minimisation on server screens**:
  - Server list: one line per server (name + host + last status + 2–3 compact actions). Move arrows become a small menu or drag if we keep ordering.
  - Server detail: "Backups" and "Docker" become prominent cards/links that go to dedicated pages (already partially true). Consolidate the 6–8 action buttons into a single "Actions" card with clear labels. Remove duplicate feature pills.
  - Docker compose cards: shrink the 8 tiny buttons into 2–3 grouped actions + "more" that opens the existing modals.
- **No new Python for UI** — any needed shared data (e.g. global branding strings) can live in `app/config.py` or templates context if truly needed.
- **Plan lives in git**: This file (`UI_UNIFICATION_PLAN.md`) is committed in the repo so it can be referenced and edited in future sessions or by collaborators.

---

## Verification & Testing (End-to-End)

**Manual flows to execute on both desktop and mobile viewport (or real phone):**

1. **Auth** — login, register (if available), logout. Check header + form consistency.
2. **Dashboard** — stats cards, links. Must feel clean on mobile.
3. **Servers list**
   - Add server flow
   - Reorder (if kept)
   - Click server name → detail
   - Click ▶ Backup → must open feedback modal + live progress (no silent redirect)
   - "Manage" and "Settings" actions
4. **Server detail**
   - All feature toggles visible but not noisy
   - Consolidated actions (backup, container patch, os patch, retention, reboot)
   - OS patch modal (already exists) must give clear feedback
   - "System Info", SSH key, Edit modals
   - Links to Backups page and Docker page
5. **Backups page** (`/servers/:id/backups`)
   - Configure sources
   - Run per-source or full backup → feedback modal + progress
   - View logs
6. **Docker Management**
   - Already strong; ensure loading overlay, all action modals, logs modal, build modal still work perfectly
   - Quick edit, full editor, new project
   - Container start/stop/restart/logs (including service select modal)
7. **Audit** — filters, table, View details modal. Must be scrollable and readable on mobile.
8. **Herder Backups (Settings)** — schedule forms, manual backup, restore preview. Consistent header.
9. **Long-running feedback test matrix**:
   - Backup (full + per-source)
   - Container patch
   - OS patch (with steps)
   - Build service
   - Deploy / Undeploy
   - Retention
   - Reboot (at least confirm + result banner)
10. **Theme toggle** still works; light + dark both look uniform.
11. **Accessibility quick check** — labels, focus, contrast via existing vars.

**Automated / semi-automated where easy:**
- Run the app (`docker compose up` or equivalent) and visually diff before/after via screenshots if tooling exists.
- Ensure no JS errors in console on all pages.
- HTMX fragments (containers table, etc.) still load.

**Plan-specific verification:**
- This `UI_UNIFICATION_PLAN.md` file itself is committed to git in the repo.
- After landing changes, update SPEC.md "Mobile-friendly responsive pass" line.

**Success criteria (qualitative):**
- Any screen fits comfortably in ~360px width without horizontal scroll (except wide tables that can scroll internally).
- Every action that talks to the host gives immediate visible feedback (modal or inline banner + progress).
- Repeated elements reduced by at least 50% on server list + detail pages (measured by distinct button groups / status blocks).
- Logo + colors + button styles feel identical everywhere.
- Still feels "light" — no new asset downloads, page weight similar.

---

## Out of Scope (for this plan)

- New backend features (scheduling UI for patches, multi-user, etc.)
- Major refactor of routers or services
- Adding a real component system or CSS framework
- Changing data models or audit format
- Dark/light theme redesign (we are only making existing theme consistent + responsive)

---

## Execution Notes / Lightweight Guardrails

- Prefer editing CSS vars and adding a few responsive classes over per-template hacks.
- Extract repeated header HTML into a single place (macro or include) early.
- Duplicate the proven modal + polling JS once into base.html rather than 5 copies.
- When in doubt, delete UI elements rather than rearrange them.
- Every commit touching templates should be reviewable in isolation.
- Keep this plan updated as we go (living document).
- Stick to vendored assets, Jinja + HTMX + Alpine + vanilla JS only.

---

## Next Steps After Plan Approval

1. This plan (`UI_UNIFICATION_PLAN.md`) is already in the git worktree — commit it so it can be interacted with via git.
2. Begin Phase A: base.html + themes.css foundations + shared header.
3. Iterate screen-by-screen using the Docker modal + progress pattern as the north star.
4. At each phase, run the verification matrix above.
5. When complete, mark items in SPEC.md and consider a follow-up PR description that references this plan.

---

**End of Plan** (living document — edit and re-commit as work proceeds)
