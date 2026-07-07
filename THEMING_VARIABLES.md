# PiHerder Theming Variables

**Purpose**: Complete per-page audit of CSS custom properties (variables) and semantic classes after full theming cleanup.  
**Goal**: Ensure the entire app UI is purely var-driven via `themes.css` (`:root` for light, `.dark` for dark). Templates use `bg-surface`, `bg-bg`, `text-muted`, `border-border`, `text-accent`, `btn-*`, `banner-*`, `status-*`, `card`, `code-surface`, etc. exclusively for colors.  
**Date**: 2026-07-07 (updated post full cleanup)  
**Status**: Full sweep complete across all templates. Hardcoded zinc/white/emerald/blue/etc. colors replaced. Theme toggle effective on all pages.

## Core CSS Variables (from themes.css)

| Variable              | Light Value     | Dark Value      | Description / Notes |
|-----------------------|-----------------|-----------------|---------------------|
| `--color-bg`          | #f8f9fa        | #0a0f1c        | Page background (`bg-bg` on body) |
| `--color-surface`     | #ffffff        | #111827        | Cards, panels, modals, nav, inputs (`bg-surface`, `.card`) |
| `--color-text`        | #111827        | #f1f3f5        | Primary text color |
| `--color-primary`     | #e60012        | #e60012        | Brand red (`.text-primary`, `.bg-primary`, `.btn-primary`) |
| `--color-accent`      | #00a651        | #00a651        | Green accent (`.text-accent`, `.bg-accent`, `.btn-accent`) |
| `--color-border`      | #e5e7eb        | #374151        | Borders (`.border-border`) |
| `--color-muted`       | #6b7280        | #a1a1aa        | Secondary/muted text, placeholders (`.text-muted`) |
| `--color-code-bg`     | #f1f3f5        | #09090b        | Logs, editors, pre blocks (`.code-surface`, `pre`, `.log-output`) |
| `--color-code-text`   | #111827        | #e4e4e7        | Text inside code/log areas |
| `--color-code-border` | #e5e7eb        | #27272a        | Code surface borders |
| `--color-success`     | #059669        | #10b981        | Success (used by `.banner-success`, `.status-success`) |
| `--color-danger`      | #dc2626        | #f87171        | Danger/error (`.banner-error`, `.btn-danger`, `.text-danger`, `.status-failed`) |
| `--color-warning`     | #d97706        | #fcd34d        | Warnings (`.banner-warning`, `.text-warning`, `.status-warning`, `.status-stopped`) |

**Key classes provided by themes.css**:
- `body { background-color: var(--color-bg); color: var(--color-text); }` → `class="bg-bg"`
- `.card, .bg-surface { background-color: var(--color-surface); border: 1px solid var(--color-border); }`
- `pre, .log-output, .code-surface { background-color: var(--color-code-bg); color: var(--color-code-text); border: 1px solid var(--color-code-border); }`
- Buttons: `.btn-secondary` (surface + text + border), `.btn-accent`, `.btn-primary`, `.btn-danger` (now uses `--color-danger`)
- Form fields: inputs/select/textarea use surface/border/text
- `.text-muted`, `.border-border`, `.text-accent`, `.text-danger`, `.text-warning`, `.text-text`
- `.status-pill` + `.status-success`/`.status-failed`/`.status-running`/ etc.
- `.banner-success`/`.banner-error`/`.banner-warning` (with `.dark` variants)
- `.table-header`, `.action-pill`

---

## Base / Shared Layout (base.html)

**Affects**: Nav, footer, modals, theme toggle on *every* page.

| Element / Area              | Current Class(es)                          | Maps To                          | Light Value          | Dark Value           | Notes |
|-----------------------------|--------------------------------------------|----------------------------------|----------------------|----------------------|-------|
| Body                        | `bg-bg`                                    | `--color-bg`, `--color-text`    | #f8f9fa / #111827   | #0a0f1c / #f1f3f5   | Good |
| Top nav                     | `bg-surface`, `border-b border-border`    | `--color-surface`, `--color-border` | #ffffff / #e5e7eb | #111827 / #374151 | Good |
| Logo "BACKUPS" badge        | `text-accent`                              | `--color-accent`                | #00a651             | #00a651             | Good |
| Nav links + hover           | `hover:text-accent`                        | `--color-accent`                | #00a651             | #00a651             | Good |
| Theme toggle button         | `hover:bg-surface border border-border`   | surface + border                | #ffffff / #e5e7eb   | #111827 / #374151   | Good |
| User email / sign out       | `text-muted`, `hover:text-danger`         | `--color-muted`, `--color-danger` | #6b7280 / #dc2626 | #a1a1aa / #f87171 | Good |
| Login button (logged out)   | `bg-surface border border-border`         | surface + border                | #ffffff / #e5e7eb   | #111827 / #374151   | Good |
| Footer                      | `text-muted border-t border-border`       | `--color-muted`, `--color-border` | #6b7280 / #e5e7eb | #a1a1aa / #374151 | Good |
| Modal scrims (all pages)    | `bg-black/70`                              | (intentional dark overlay)      | black/70            | black/70            | Keep as-is |
| Modal content               | `.card` or `bg-surface border border-border` | surface + border             | #ffffff / #e5e7eb   | #111827 / #374151   | Good |

---

## Dashboard

**Template**: `app/templates/dashboard.html`

| Element / Area                  | Current Class(es)                     | Maps To                     | Light | Dark | Notes |
|---------------------------------|---------------------------------------|-----------------------------|-------|------|-------|
| Tagline                         | `text-accent`                         | `--color-accent`           | #00a651 | #00a651 | Good |
| Descriptive text                | `text-muted`                          | `--color-muted`            | #6b7280 | #a1a1aa | Good |
| Stat cards                      | `.card`                               | `--color-surface` + border | #ffffff / #e5e7eb | #111827 / #374151 | Good |
| Stat labels                     | `text-muted`                          | `--color-muted`            | #6b7280 | #a1a1aa | Good |
| "Manage servers →" etc. links   | `text-accent hover:underline`         | `--color-accent`           | #00a651 | #00a651 | Good |
| Quick links (Settings)          | `text-accent`                         | `--color-accent`           | #00a651 | #00a651 | Good |
| Bullet lists                    | `text-muted`                          | `--color-muted`            | #6b7280 | #a1a1aa | Good |

---

## Server List

**Template**: `app/templates/server_list.html`

| Element / Area                     | Current Class(es)                          | Maps To                          | Light | Dark | Notes |
|------------------------------------|--------------------------------------------|----------------------------------|-------|------|-------|
| Description text                   | `text-muted`                               | `--color-muted`                 | #6b7280 | #a1a1aa | Good |
| Move up/down buttons               | `hover:bg-surface`                         | `--color-surface`               | #ffffff | #111827 | Good |
| Hostname                           | `text-muted`                               | `--color-muted`                 | #6b7280 | #a1a1aa | Good |
| Status text ("last: ...", "backup running…") | `text-accent`                        | `--color-accent`                | #00a651 | #00a651 | Good |
| Action buttons                     | `btn btn-secondary`                        | surface + text + border         | Various | Various | Good (removed blue overrides) |
| Empty state                        | `text-muted`                               | `--color-muted`                 | #6b7280 | #a1a1aa | Good |
| Backup progress modal              | `.card`, `text-accent`, `text-muted`      | surface / accent / muted        | Good | Good | Good |
| Table / rows (if present)          | Uses `card` patterns                     | surface + border                | Good | Good | Good |

---

## Server Detail

**Template**: `app/templates/server_detail.html` (largest page, many modals + dynamic HTML)

| Element / Area                          | Current Class(es)                                      | Maps To                              | Light Value | Dark Value | Notes |
|-----------------------------------------|--------------------------------------------------------|--------------------------------------|-------------|------------|-------|
| Breadcrumb / header links               | `text-accent`                                          | `--color-accent`                    | #00a651    | #00a651   | Good |
| Hostname line                           | `text-muted`                                           | `--color-muted`                     | #6b7280    | #a1a1aa   | Good |
| Feature badges (Backups on/off etc.)    | `bg-bg border border-border`                           | `--color-bg` + border               | #f8f9fa / #e5e7eb | #0a0f1c / #374151 | Good |
| System info / Docker buttons            | `btn btn-secondary`                                    | surface + border + text             | Good       | Good      | Good |
| Status banners (reboot, pending)        | `banner-success`, `banner-warning`                     | success / warning vars              | See core   | See core  | Good |
| Action buttons (Run Backup, Patch, Reboot) | `btn btn-accent`, `btn btn-secondary`, `btn btn-danger` | accent / surface / danger         | Good       | Good      | `.btn-danger` now uses var |
| Backups section card                    | `.card`                                                | `--color-surface` + border          | #ffffff    | #111827   | Good |
| Status pills (running, queued, last success) | `bg-bg border...`, `status-success`, `status-failed`, `text-accent` | bg/surface or status vars | Good | Good | Good |
| SSH Key modal                           | `.card`, `code-surface`, `btn btn-accent` / `btn btn-secondary` | surface + code + accent     | Good       | Good      | Good |
| Edit Server modal                       | `.card`, inputs use `bg-surface border border-border`, labels `text-muted` | surface/border/muted     | Good       | Good      | Good |
| OS Patch modal + progress               | `.card`, `btn btn-secondary`, `log-output`             | surface + code                      | Good       | Good      | Good |
| Backup Config modal                     | `.card`, selects/inputs `bg-surface border border-border`, `text-muted` | surface/border/muted | Good | Good | Good |
| Backup / Remove / Details / System modals | `.card`, `log-output`, `code-surface`, `btn-*`, `text-muted` / `text-accent` | surface + code + muted/accent | Good | Good | Good |
| Dynamic system info HTML (JS)           | `bg-surface border border-border`, `text-muted`, `text-accent`, `code-surface` | surface + muted + accent + code | Good | Good | Good |
| Table rows (backup sources in modals)   | `border-b border-border hover:bg-bg`                   | border + bg                         | Good       | Good      | Good |

---

## Audit Log

**Template**: `app/templates/audit.html` (includes filters, table, details modal)

| Element / Area                  | Current Class(es)                                      | Maps To                              | Light Value          | Dark Value           | Notes |
|---------------------------------|--------------------------------------------------------|--------------------------------------|----------------------|----------------------|-------|
| Header / description            | `text-muted`                                           | `--color-muted`                     | #6b7280             | #a1a1aa             | Good |
| Breadcrumb link                 | `text-accent`                                          | `--color-accent`                    | #00a651             | #00a651             | Good |
| Filter form                     | `.card`, inputs/selects `bg-surface border border-border` | surface + border               | #ffffff / #e5e7eb   | #111827 / #374151   | Good |
| Filter labels / "Filtered" text | `text-muted`                                           | `--color-muted`                     | #6b7280             | #a1a1aa             | Good |
| Clear link                      | `text-accent`                                          | `--color-accent`                    | #00a651             | #00a651             | Good |
| Main table container            | `.card`                                                | `--color-surface` + border          | #ffffff / #e5e7eb   | #111827 / #374151   | Good |
| Table header                    | `text-muted border-b border-border table-header`       | muted + border + surface            | #6b7280 / #ffffff   | #a1a1aa / #111827   | Good |
| Table rows                        | `border-b border-border hover:bg-bg table-row`        | border + bg                         | #e5e7eb / #f8f9fa   | #374151 / #0a0f1c   | Good |
| Row text (timestamps, summary)  | `text-muted`, `text-text`                              | muted + text                        | #6b7280 / #111827   | #a1a1aa / #f1f3f5   | Good |
| Server links in table           | `text-accent`                                          | `--color-accent`                    | #00a651             | #00a651             | Good |
| Action pill                     | `action-pill`                                          | `--color-bg` + `--color-text` + border | #f8f9fa / #111827 / #e5e7eb | #0a0f1c / #f1f3f5 / #374151 | Good |
| Status pills                    | `status-pill status-success` etc.                      | status-* definitions (vars inside)  | See core vars       | See core vars       | Good (uses new status classes) |
| View button                     | `btn btn-secondary`                                    | surface + text + border             | Good                | Good                | Good |
| Empty state / footer text       | `text-muted`                                           | `--color-muted`                     | #6b7280             | #a1a1aa             | Good |
| Details modal + pre             | `.card`, `log-output`                                  | surface + code vars                 | Good                | Good                | Good |
| Close buttons                   | `text-muted hover:text-text btn btn-ghost`             | muted + text                        | Good                | Good                | Good |

---

## Server Backups

**Template**: `app/templates/server_backups.html`

| Element / Area                     | Current Class(es)                           | Maps To                     | Light | Dark | Notes |
|------------------------------------|---------------------------------------------|-----------------------------|-------|------|-------|
| Breadcrumbs                        | `text-accent`, `text-muted`                | accent + muted             | Good | Good | Good |
| Last backup status pill            | `status-success` / `status-failed`         | status vars                | Good | Good | Good |
| Section card                       | `.card`                                    | surface + border           | Good | Good | Good |
| Active running banner              | `bg-accent/10 border border-accent`        | accent (with opacity)      | Good | Good | Good |
| Source text / destinations         | `text-muted`, `font-mono`                  | muted                      | Good | Good | Good |
| Table header                       | `text-muted border-b border-border table-header` | muted + border + surface | Good | Good | Good |
| Table rows / text                  | `text-muted`, `text-accent`                | muted + accent             | Good | Good | Good |
| Buttons (Backup, Log, Remove)      | `btn btn-accent`, `btn btn-secondary`, `btn btn-danger` | accent / surface / danger | Good | Good | Good |
| Add source form                    | `bg-surface border border-border`          | surface + border           | Good | Good | Good |
| Error messages                     | `text-danger`                              | `--color-danger`           | #dc2626 | #f87171 | Good |
| Retention / schedule text          | `text-muted`                               | muted                      | Good | Good | Good |
| Modals (config, remove, progress)  | `.card`, `code-surface`, `log-output`, `btn-*`, `text-muted`, `text-accent`, `text-danger` | Full var set | Good | Good | Good |

---

## Herder Backups / Settings

**Template**: `app/templates/herder_backups.html`

| Element / Area                  | Current Class(es)                          | Maps To                     | Light | Dark | Notes |
|---------------------------------|--------------------------------------------|-----------------------------|-------|------|-------|
| Breadcrumb                      | `text-accent`                              | accent                     | Good | Good | Good |
| Description                     | `text-muted`                               | muted                      | Good | Good | Good |
| Timezone / schedule cards       | `.card`                                    | surface + border           | Good | Good | Good |
| Form controls                   | `bg-surface border border-border`          | surface + border           | Good | Good | Good |
| Labels                          | `text-muted`                               | muted                      | Good | Good | Good |
| Status messages (banner)        | `banner-success`, `banner-error`           | success/danger             | Good | Good | Good |
| Manual backup section           | `.card`                                    | surface                    | Good | Good | Good |
| "Run backup now" button         | `btn btn-secondary`                        | surface                    | Good | Good | Good |
| Backups table                   | `.card`, table-header, `border-border`     | surface + border + muted   | Good | Good | Good |
| Download / Delete / Preview     | `btn btn-secondary`, `btn btn-danger`      | surface / danger           | Good | Good | Good |
| Restore form section            | `.card`                                    | surface                    | Good | Good | Good |
| Warning header (Restore)        | `text-warning`                             | `--color-warning`          | #d97706 | #fcd34d | Good |
| Help text                       | `text-muted`                               | muted                      | Good | Good | Good |

---

## Add Server

**Template**: `app/templates/add_server.html`

| Element / Area             | Current Class(es)                     | Maps To              | Light | Dark | Notes |
|----------------------------|---------------------------------------|----------------------|-------|------|-------|
| Description                | `text-muted`                          | muted               | Good | Good | Good |
| All form inputs            | `bg-surface border border-border`     | surface + border    | Good | Good | Good |
| Password warning labels    | `text-warning`                        | `--color-warning`   | #d97706 | #fcd34d | Good |
| Help text                  | `text-muted`                          | muted               | Good | Good | Good |

---

## Login / Register

**Templates**: `login.html`, `register.html`

| Element / Area               | Current Class(es)                          | Maps To             | Light | Dark | Notes |
|------------------------------|--------------------------------------------|---------------------|-------|------|-------|
| "SECURE FLEET CONTROL"       | `text-accent`                              | accent             | Good | Good | Good |
| Info box (default creds)     | `bg-surface border border-border text-muted` | surface + muted  | Good | Good | Good |
| Form inputs                  | `bg-surface border border-border ... focus:border-accent` | surface + border + accent | Good | Good | Good |
| Footer links                 | `text-accent hover:underline`              | accent             | Good | Good | Good |
| General text                 | `text-muted`                               | muted              | Good | Good | Good |

---

## Docker Management (updated)

(See earlier section — now fully aligned with `bg-surface`, `btn-*`, `text-muted`, `text-accent`, `status-*` where applicable, `code-surface`. JS modal strings and class toggles cleaned.)

---

## Compose Editor + Dockerfile Editor

(See earlier dedicated sections. Internal syntax tokens still have some direct colors for readability — these are intentional for code highlighting and use `var(--color-accent)` / `var(--color-warning)` / `var(--color-muted)` where possible. Error lines now use `--color-danger`.)

---

## Other / Minor Pages

- **Docker Logs** (`docker_logs.html`): Links use `text-accent`; buttons `btn btn-secondary`; text `text-muted`. Pre uses `log-output`.
- **Docker Build Progress**: Similar — `text-accent`, `btn btn-secondary`, `text-muted`.
- **New Docker Project**: `text-accent`, `text-muted`.

All share the base layout.

---

## Global / Shared + Recommendations (post-cleanup)

| Class / Pattern     | Vars Used                              | Light                  | Dark                   | Status |
|---------------------|----------------------------------------|------------------------|------------------------|--------|
| `bg-bg`             | `--color-bg`                           | #f8f9fa               | #0a0f1c               | Complete |
| `bg-surface` / `.card` | `--color-surface`, `--color-border` | #ffffff / #e5e7eb    | #111827 / #374151    | Complete |
| `code-surface` / logs | `--color-code-*`                     | #f1f3f5 / #111827 / #e5e7eb | #09090b / #e4e4e7 / #27272a | Complete |
| `text-muted`        | `--color-muted`                        | #6b7280               | #a1a1aa               | Complete |
| `text-accent`       | `--color-accent`                       | #00a651               | #00a651               | Complete |
| `btn-secondary`     | surface + text + border                | Good                  | Good                  | Complete |
| `btn-accent`        | `--color-accent`                       | Good                  | Good                  | Complete |
| `btn-danger`        | `--color-danger`                       | #dc2626               | #f87171               | Now uses var |
| `status-*`          | Mix of success/danger/warning + bg     | See core              | See core              | Added + used |
| `banner-*`          | success/danger/warning (with hardcodes inside) | See core         | See core              | Mostly var-backed |

**Completed in this pass**:
- All zinc / white / emerald / blue / red / amber hardcodes replaced with var-backed classes.
- Status pills centralized.
- Buttons, forms, tables, modals, dynamic JS HTML all updated.
- Base nav/footer/toggle cleaned for consistent theming.

**Remaining candidates for further tightening** (mostly non-blocking):
- A few syntax highlight colors in editor `<style>` (kept for code legibility).
- Modal scrims (`bg-black/70`) — intentional.
- Any future conditional classes added in JS should prefer `status-*` / `banner-*` / `btn-*`.

**Verification steps**:
1. Hard refresh browser.
2. Toggle theme (☀️/🌙) — all pages should update instantly via CSS vars.
3. Check light mode backgrounds are light on Docker + editors + audit + server detail.

This document now covers the full application per page after the complete theming cleanup.
