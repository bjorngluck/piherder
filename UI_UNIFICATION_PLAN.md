# UI Unification & Mobile-First Refresh Plan

**Status:** Draft (plan mode)  
**Date:** 2026-07-08  
**Branch context:** main (worktree 2026-07-08-2cc520f0)  
**Goal:** Deliver a cohesive, minimal, mobile-friendly UI while strictly preserving PiHerder's lightweight architecture and principles.

**Design direction ("Arrochar" style):**  
Clean, minimal, mobile-first. Uniform header on every screen. Consistent branding using primary red (`--color-primary`) in both light and dark modes. All long-running or state-changing actions use modal-based feedback (inspired by the existing Docker modals).  

**Strict guardrails:**  
- Templates + `themes.css` only. No new dependencies, no new build steps, no architecture changes.  
- Reuse existing patterns: Docker modals + loading overlays, `PiHerderPoll`, `PiHerderUpdateLog`, progress helpers, Job-backed status, CSS vars, `.card`/`.btn-*` classes.  
- The plan itself is a living document committed to git (`UI_UNIFICATION_PLAN.md`).

**Current implemented & pushed (ready for you to test now):**  
Commit `36f425c` on main delivers the foundation of the direction:  
- Uniform header menu (Dashboard / Servers / Audit / Settings) with primary red for active + hover states on all pages.  
- Consistent branding block (logo + "PiHerder" + red "FLEET" strapline) in header, dashboard, login, register.  
- Clean main landing page (dashboard) with minimal hero and primary-red focused CTAs and stats.  
- Mobile-friendly wrapping in the header.  
- All using existing CSS vars and classes (no new deps).

Pull the latest and test the header + dashboard on mobile (narrow viewport) and desktop, light + dark. This validates whether the "Arrochar" clean/minimal/primary-red direction feels right before we continue with Phases B+.

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
- **Centralize feedback** (reuse existing, do not invent new):
  - Reusable loading/feedback modal — extend the proven Docker page-loading overlay (`#docker-page-loading` pattern).
  - Action modals for confirm + options — copy the exact style from `docker.html` (build-modal, deploy-confirm-modal, undeploy-confirm-modal, logs-modal, service-select-modal).
  - Progress for streaming/long jobs — reuse `PiHerderPoll`, `PiHerderUpdateLog`, `startBackupProgressPoll`, `backupProgressUrl`, and Job-backed status already present in server_list.html and server_backups.html.
  - X-PiHerder-Async header + job_id JSON response pattern for async starts.
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

**Phased rollout (actionable increments — commit and test after each):**

- **Phase A — Foundations**  
  Uniform header component + brand block in `base.html`. Primary red focus for nav active/hover states and logo strapline. Add mobile-first responsive rules and touch-target helpers to `themes.css`. Extract or standardize one page header pattern. Add a shared minimal feedback modal skeleton in base (reusing existing modal markup patterns).

- **Phase B — Dashboard + Server list**  
  Apply uniform header to dashboard and server list. Clean landing page (minimal hero, consistent primary-red CTAs). Compact server list rows (name + host + status + 2-3 actions max). Ensure full-width stacking on mobile. Backup "▶" actions must open a progress modal (reuse existing backup progress pattern).

- **Phase C — Server detail + Backups pages**  
  Consolidate repeated sections (feature pills, multiple backup status blocks, "Run Jobs" buttons). One clear "Actions" area per page. Prominent links to dedicated Backups and Docker pages. All long-running actions (backup, retention, patch, reboot) must surface via modal or the standard progress flow.

- **Phase D — Docker Management + propagate patterns**  
  Keep and lightly polish existing strong modal set (loading overlay, build, deploy, undeploy, logs, service-select). Use these exact patterns as the template for any remaining actions on other pages. Ensure consistent button sizing and mobile stacking inside modals.

- **Phase E — Remaining screens (Audit, Herder Backups, Auth, Add Server)**  
  Apply uniform header + branding. Make filter forms and tables mobile-scrollable. Ensure View/Details modals and form submissions give clear feedback. Consistent use of `.card`, `.btn-*`, primary red for identity.

- **Phase F — Polish, verification, living document**  
  Full manual test matrix on mobile (≤360px) + desktop. Fix any remaining duplication or spacing issues. Update `UI_UNIFICATION_PLAN.md` (this file) and SPEC.md. Commit final state. No new files or dependencies.

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
- **Touch targets & mobile stacking (mandatory)**:
  - All interactive elements (buttons, links, form controls, row actions, nav items) must have a minimum tap target of 44×44 px on mobile.
  - Use `py-2`, `py-3`, or explicit `min-h-[44px]` / `min-h-11` classes for buttons and clickable rows.
  - On screens ≤640px: stack button groups vertically (`flex-col`), make cards full width, allow horizontal scroll only inside tables/logs.
  - Add sensible spacing: `gap-2` or `gap-3` between actions; avoid cramped rows.
  - Header nav must wrap cleanly and remain usable with fat fingers.
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

**Success criteria (measurable and practical):**

- **Mobile rendering**: No horizontal scrolling on the body at 360px viewport. All pages stack vertically. Tables, logs, and wide content areas are allowed internal horizontal scroll only.

- **Touch targets**: Every clickable element (buttons, nav links, row actions, form checkboxes, modal buttons) has a minimum 44px tap target height on small screens.

- **Header uniformity**: The identical header (logo + "PiHerder" + "FLEET" strapline in primary red, four main nav links with active red state, theme toggle, user info) is present and visually consistent on every page (dashboard, servers, detail, backups, docker, audit, herder-backups, login, register, add-server).

- **Branding consistency**: Primary red (`--color-primary`) is the dominant brand color for identity elements (logo strapline, active nav, main headings, key CTAs) in both light and dark themes. Accent green is used only for positive action buttons.

- **Feedback for actions**: Every host-touching operation (backup, container patch, OS patch, retention, deploy, undeploy, build, reboot, etc.) immediately shows a modal or live progress area. No silent redirects or browser-native confirms for long-running work.

- **Reuse only**: No new modal HTML/JS patterns are invented. All feedback uses the Docker modal family or the existing `PiHerderPoll` + progress helpers.

- **Minimal diff**: Changes limited to `app/templates/*.html` and `app/static/css/themes.css`. No new static files, no new Python UI logic, no dependency changes.

- **Living document**: `UI_UNIFICATION_PLAN.md` is kept up to date in git and committed with the UI work.

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
