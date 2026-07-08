# UI Unification & Mobile-First Refresh Plan

**Status:** In progress  
**Date:** 2026-07-08 (updated)  
**Branch:** `main`  
**Goal:** Deliver a cohesive, minimal, mobile-friendly UI while strictly preserving PiHerder's lightweight architecture and principles.

**Design direction ("Arrochar" style):**  
Clean, minimal, mobile-first. Uniform header on every screen. Consistent branding using primary red (`--color-primary`) in both light and dark modes. All long-running or state-changing actions use modal-based feedback (inspired by the existing Docker modals).

**Strict guardrails:**  
- Templates + `themes.css` only. No new dependencies, no new build steps, no architecture changes.  
- Reuse existing patterns: Docker modals + loading overlays, `PiHerderPoll`, `PiHerderUpdateLog`, progress helpers, Job-backed status, CSS vars, `.card`/`.btn-*` classes.  
- App code is **image-baked** (no source volume mounts in compose for forward workflow). Rebuild image (or `docker cp`) after template/CSS changes.  
- Living document: keep this file updated in git.

---

## Progress snapshot (2026-07-08)

| Phase | Status | Notes |
|-------|--------|-------|
| **A — Foundations** | ✅ Done (+ evolved) | Uniform shell, single-source nav, mobile hamburger, avatar menu, theme toggle fixed |
| **B — Dashboard + Server list** | ✅ Done | Compact rows, mobile stack, touch targets, backup modal title fix |
| **C — Server detail + Backups** | ✅ Done | Single Actions card, Backups/Docker dest cards, backups page polish |
| **D — Docker + pattern export** | ✅ Done | Shared `PiHerderProgress` in base; Docker header/actions/modals polished |
| **E — Remaining screens** | 🔄 Next | Audit, Herder backups, auth, add server polish |
| **F — Polish + SPEC** | ⏳ Pending | Full mobile/desktop matrix; update SPEC.md |

**Theme toggle:** Fixed on `main` (`8c07a21`) — root cause was a premature `:root` close in `themes.css` that dropped the `.dark` variable block from the CSSOM. FOUC script + delegated toggle remain correct.

---

## Context (Why this change)

Current UI grew organically across many Jinja templates. Pain points:

1. **Not mobile friendly** — tables, button rows, headers, and grids break or become cramped on small screens.
2. **Inconsistent design** — screens differ in spacing, headers, and button conventions (shell is now unified; page bodies still vary).
3. **Branding drift** — largely fixed in the global header; page-level CTAs still mixed.
4. **Poor feedback for long-running actions** — Docker is the gold standard; backups partially reuse progress modals; patch/reboot/etc. still uneven.
5. **Visual clutter** — server list/detail still busy with repeated pills and tiny button groups.
6. **Lightweight discipline** — offline-capable (vendored assets), Jinja + HTMX + Alpine + vanilla JS + `themes.css` only.

---

## Recommended Approach

**Core strategy: "Minimal Uniform Shell + Modal Feedback Everywhere"**

- Mobile-first (stack by default; expand on `md:`+).
- One shared shell (done): logo + PiHerder + FLEET + four nav links + account/theme.
- Standardize cards, sections, button clusters.
- Centralize feedback (reuse Docker + backup progress patterns; prefer lifting shared helpers into `base.html` over copying).
- Minimise & deduplicate server screens.
- Keep architecture pristine (templates + CSS only).

---

## Phase detail

### Phase A — Foundations ✅ DONE

Delivered and then extended beyond the original sketch:

- Uniform header / brand block in `base.html` (logo + "PiHerder" + red "FLEET").
- **Single source of truth** for nav: `nav_items` + `secondary_items` Jinja sets drive desktop links, mobile slide-out, and avatar dropdown.
- Mobile hamburger slide-out vs desktop horizontal nav (hard CSS media queries; no double menu).
- Account avatar menu with Account / Toggle theme / Sign out.
- Primary red for active + hover nav identity; dedicated `--color-nav-bg` and menu CSS vars.
- Theme toggle working (class + localStorage + full `.dark` variable block).
- Dashboard landing cleaned (minimal hero, primary CTAs).
- Image-baked deploy (no app source mounts).

**Key commits (representative):** `36f425c` (initial shell + plan), `26c5086` / `abfd37a` (single-source menu), `aa788d4` / `8a0bc06` (hamburger + no volume mounts), `8c07a21` (theme toggle CSS fix).

### Phase B — Dashboard + Server list ✅ DONE

- [x] Clean dashboard landing (minimal hero, primary-red CTAs).
- [x] Compact server list rows: name + host + status + ≤3 primary actions (Backup / Manage / Settings).
- [x] Full-width stacking on mobile; touch-friendly controls (`.min-h-11` ~44px).
- [x] Reorder controls stay but compact / non-dominant (`.server-reorder-btn`).
- [x] Backup ▶ opens progress modal + live poll; modal title uses `data-server-name` (was broken via `.rounded-xl`).
- [x] Page header pattern (`.page-header` + `.page-header-actions`) for title + Refresh / Add Server.

### Phase C — Server detail + Backups pages ✅ DONE

- [x] Compact feature chips (one status row).
- [x] Remove duplicate Run Jobs / backup status / docker quick-access blocks.
- [x] One **Actions** card (backup, container patch, OS patch, retention, reboot).
- [x] Prominent **Backups** + **Docker** destination cards.
- [x] Page header with Edit / SSH key / System info.
- [x] Backups page: page header, full-backup CTA, scrollable sources table, touch targets, modal polish.
- [ ] (Deferred to D) Shared progress modal primitives in `base.html` (still page-local copies).

### Phase D — Docker + propagate patterns ✅ DONE

- [x] Shared backup progress modal + `window.PiHerderProgress` in `base.html`.
- [x] Global intercept for `form.backup-run-form`; compat aliases (`openBackupLog`, `stopCurrentBackup`, etc.).
- [x] Removed duplicate progress modals/JS from server_list, server_backups, server_detail.
- [x] Docker page-header + scrollable containers table + `.docker-action-btn` touch targets.
- [x] Docker modals use `.modal-content` + `min-h-11` action buttons.
- [ ] Logs / service-select modal micro-polish can continue in Phase E if needed.

### Phase E — Remaining screens ⏳

- Audit, Herder Backups, Auth, Add Server: same shell (already) + page body polish.
- Filter forms / tables mobile-scrollable; clear View/Details feedback.

### Phase F — Polish, verification, living document ⏳

- Manual matrix mobile (≤360px) + desktop, light + dark.
- Update this plan + SPEC.md ("Mobile-friendly responsive pass").
- No new files or dependencies unless a tiny Jinja partial is clearly justified.

---

## Critical Files

**High impact / shared:**
- `app/templates/base.html` — nav, fallback CSS, common modal primitives, global JS (`PiHerderPoll`)
- `app/static/css/themes.css` — vars, menu, buttons, mobile-first helpers, server-row patterns

**Page templates:**
- `dashboard.html`, `server_list.html`, `server_detail.html`, `server_backups.html`
- `docker.html` + fragments
- `audit.html`, `herder_backups.html`, `login.html`, `register.html`, `add_server.html`

**Reference (read-mostly):**
- Docker modals in `docker.html`
- Backup progress + poll in `server_list.html` / `server_backups.html` / `server_detail.html`
- Job + progress endpoints (no logic changes expected)

---

## Key Design Decisions

- **Header unification:** Global nav is the single shell; page bodies use a light title row (h1 + primary action), not a second brand block.
- **Button clusters:** Max 2–3 primary actions visible; rest secondary or linked pages.
- **Touch targets:** Prefer `min-h-11` / adequate padding on mobile interactive elements.
- **Modals for long-running / destructive work:** Docker style + existing backup progress poll.
- **No new Python for UI.**
- **Deploy:** Rebuild image for baked templates/static; do not reintroduce source volume mounts.

---

## Verification (end-to-end)

1. Auth — login / register / logout; theme toggle light ↔ dark.
2. Dashboard — clean on mobile.
3. Servers list — add, reorder, open detail, ▶ Backup → modal + live log, Manage / Settings.
4. Server detail — actions, OS patch modal, links to Backups/Docker.
5. Backups page — configure, run, progress modal.
6. Docker — loading overlay, all action modals, logs, build.
7. Audit / Herder backups — filters, tables, forms on mobile.
8. No body horizontal scroll at 360px; tables may scroll internally.
9. No new deps; only templates + `themes.css` (+ this plan / SPEC).

**Success criteria:** header uniformity, primary-red branding, modal feedback for host-touching ops, reuse-only patterns, minimal diff, living plan kept current.

---

## Out of Scope

- New backend features (patch scheduling UI, multi-user, etc.)
- Major router/service refactors
- New component frameworks or build steps
- Full theme redesign (consistency + responsive only)

---

## Next steps (execution)

1. ✅ Update this plan to match reality.
2. ✅ **Phase B:** compact `server_list.html` + CSS helpers.
3. ✅ **Phase C:** server detail + backups consolidation.
4. ✅ **Phase D:** shared progress modal + Docker polish.
5. 🔄 **Next: Phase E** — Audit, Herder backups, auth, add server polish.
6. Then F (matrix + SPEC). Commit after each coherent phase; rebuild image for live test when needed.

---

**End of Plan** (living document — edit and re-commit as work proceeds)
