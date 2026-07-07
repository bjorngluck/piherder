# PiHerder Theming Variables

**Purpose**: Complete audit of CSS custom properties (variables) and their usage on the Docker management and editor screens.  
**Goal**: Pure var-driven theming via `themes.css`. All color assignments come from `:root` (light) and `.dark` (dark).  
**Date**: 2026-07-07  
**Status**: Post-cleanup (many direct Tailwind colors removed, using `bg-surface`, `code-surface`, `btn-*`, `banner-*`, `card`, etc.)

## Core CSS Variables

These are defined in `app/static/css/themes.css`.

| Variable              | Light Value     | Dark Value      | Description / Notes |
|-----------------------|-----------------|-----------------|---------------------|
| `--color-bg`          | #f8f9fa        | #0a0f1c        | Page background (body uses `bg-bg`) |
| `--color-surface`     | #ffffff        | #111827        | Cards, panels, modals (`bg-surface`, `.card`) |
| `--color-text`        | #111827        | #f1f3f5        | Primary text (body, most content) |
| `--color-primary`     | #e60012        | #e60012        | Raspberry Pi red (`.btn-primary`, `.text-primary`, `.bg-primary`) |
| `--color-accent`      | #00a651        | #00a651        | Green accent (`.btn-accent`, `.text-accent`, `.bg-accent`) |
| `--color-border`      | #e5e7eb        | #374151        | Borders (`.border-border`) |
| `--color-muted`       | #6b7280        | #a1a1aa        | Muted text, placeholders (`.text-muted`) |
| `--color-code-bg`     | #f1f3f5        | #09090b        | Code editors, logs, pre (`.code-surface`, `pre`, `.log-output`) |
| `--color-code-text`   | #111827        | #e4e4e7        | Text inside code areas |
| `--color-code-border` | #e5e7eb        | #27272a        | Borders for code surfaces |
| `--color-success`     | #059669        | #10b981        | Success states (used in `.banner-success`) |
| `--color-danger`      | #dc2626        | #f87171        | Danger / error (`.banner-error`, `.btn-danger`) |
| `--color-warning`     | #d97706        | #fcd34d        | Warnings (`.banner-warning`) |

**Notes on classes in themes.css**:
- `body { background-color: var(--color-bg); color: var(--color-text); }` → use `class="bg-bg"` on body.
- `.card, .bg-surface { background-color: var(--color-surface); border: 1px solid var(--color-border); }`
- `pre, .log-output, .code-surface { background-color: var(--color-code-bg); color: var(--color-code-text); border-color: var(--color-code-border); }`
- Buttons: `.btn-secondary` uses surface + text + border vars.
- `.btn-accent` and `.btn-primary` use accent/primary (white text).
- `.btn-danger` currently hardcodes red (not pure var — candidate for fix).
- Banners (`.banner-success` etc.) currently use some hardcoded values inside the class (not pure vars — candidate for update to use `--color-success` etc.).
- Form controls (input/textarea/select) use `--color-surface`, `--color-border`, `--color-text`.

---

## Docker Management Screen

**Main template**: `app/templates/docker.html`  
**Included**: `docker_containers_table.html`  
**Key sub-areas**: Header, containers table, compose projects grid, status banners, quick-edit modals, logs modal, build/undeploy modals, unused list.

### Key Elements & Variable Usage

| Element / Area                  | Current Class(es)                          | Maps To Var(s)                  | Light Value | Dark Value | Notes / Issues |
|---------------------------------|--------------------------------------------|---------------------------------|-------------|------------|----------------|
| Page cards / panels             | `.card`                                    | `--color-surface`, `--color-border` | #ffffff / #e5e7eb | #111827 / #374151 | Good |
| Main content background         | `bg-bg` (on body)                          | `--color-bg`                    | #f8f9fa    | #0a0f1c   | Good |
| Action buttons (Deploy, etc.)   | `btn btn-accent`, `btn btn-secondary`, `btn btn-danger` | `--color-accent` (bg), `--color-surface` (secondary), `--color-danger` (danger) | Green / white / #b91c1c | Same (primary/accent are brand) | `.btn-danger` still hardcoded red |
| Status banners (up-to-date, built, prune) | `banner-success`, `banner-warning`, `banner-error` | `--color-success` etc. (inside banner defs) | See banner section | See banner | Using banner classes (good), but banner defs have hardcodes |
| Compose project cards           | `.card`                                    | `--color-surface`               | #ffffff    | #111827   | Good |
| Quick-edit tabs (Compose/Dockerfile) | `btn btn-accent`, `btn btn-secondary` (after recent changes) | `--color-accent`, `--color-surface` | Green / white | Same | JS toggles were updated to var classes |
| qe-editor (quick edit textarea) | `code-surface`                             | `--color-code-bg`, `--color-code-text`, `--color-code-border` | #f1f3f5 / #111827 / #e5e7eb | #09090b / #e4e4e7 / #27272a | Good |
| Logs modal content              | `.card`                                    | `--color-surface`               | #ffffff    | #111827   | Good |
| Select (auto-refresh)           | `bg-surface`                               | `--color-surface`               | #ffffff    | #111827   | Still has some `border-zinc-300 dark:border-zinc-700` (Tailwind remnant) |
| Unused list                     | `code-surface`                             | `--color-code-bg` etc.          | #f1f3f5    | #09090b   | Good |
| Modal inners (build, undeploy, etc.) | `bg-surface` + `.card`                    | `--color-surface`, `--color-border` | #ffffff    | #111827   | Good |
| Loading overlay inner           | `.card`                                    | `--color-surface`               | #ffffff    | #111827   | Good |
| Text in headers / descriptions  | `text-zinc-500 dark:text-zinc-400` (many places) | Not using var yet (Tailwind)   | #6b7280    | #a1a1aa   | Should become `text-muted` |
| Container table rows / badges   | Some `bg-emerald-...`, `bg-surface`        | Mixed                           | -          | -         | Partial cleanup; running row still has emerald hardcode in some cases |
| Scrims / overlays (bg-black/70) | `bg-black/70`                              | Not var-driven (intentional dark scrim) | black/70   | black/70  | Keep as-is for modals |

**Remaining non-var color usage on this screen** (to be cleaned):
- Many `text-zinc-* dark:text-zinc-*`
- Border classes with `border-zinc-* dark:border-zinc-*`
- A few conditional emerald/amber in status (now mostly banners)
- JS class toggles in some places still reference zinc in comments or old strings
- `.btn-danger` implementation hardcodes red

---

## Compose Editor Screen

**Template**: `app/templates/docker_compose_edit.html` (when not is_dockerfile)

### Key Elements & Variable Usage

| Element / Area                     | Current Class(es)                                      | Maps To Var(s)                          | Light Value | Dark Value | Notes / Issues |
|------------------------------------|--------------------------------------------------------|-----------------------------------------|-------------|------------|----------------|
| Success / error / warning messages | `banner-success`, `banner-error`, `banner-warning`    | `--color-success` etc. (inside defs)   | See banner | See banner | Good |
| Version bar                        | `card`                                                 | `--color-surface`, `--color-border`    | #ffffff / #e5e7eb | #111827 / #374151 | Good |
| Version pills / links              | `banner-warning`, `banner-success`, `bg-surface`      | surface + semantic banners             | Various    | Various   | Still mixes some conditional logic |
| Toolbar (wrap toggle etc.)         | `bg-surface`                                           | `--color-surface`                      | #ffffff    | #111827   | Good |
| Line info bar                      | `bg-surface`                                           | `--color-surface`                      | #ffffff    | #111827   | Good |
| Main editor box                    | `compose-editor-wrap code-surface`                     | `--color-code-bg`, `--color-code-text`, `--color-code-border` | #f1f3f5 / #111827 / #e5e7eb | #09090b / #e4e4e7 / #27272a | Core fix — uses code-surface |
| Gutter (line numbers)              | `code-surface`                                         | `--color-code-bg` etc.                 | #f1f3f5    | #09090b   | Good |
| Syntax highlight `<pre>`           | `code-surface`                                         | `--color-code-bg`, `--color-code-text` | #f1f3f5 / #111827 | #09090b / #e4e4e7 | Good |
| Errors panel / list                | `card`                                                 | `--color-surface`                      | #ffffff    | #111827   | Good (some internal red hardcodes remain for error styling) |
| Buttons (Save Draft, Deploy, Cancel) | `btn btn-secondary`, `btn btn-accent`                | surface / accent                       | Various    | Various   | Good |
| Version select                     | `bg-surface`                                           | `--color-surface`                      | #ffffff    | #111827   | Good |
| Internal editor `<style>` tokens   | `.tok-key`, `.tok-string` etc. (with .dark variants) | Hardcoded colors (not vars)            | Various    | Various   | Syntax highlighting — still has direct color rules (candidate to move to vars) |
| Error highlights                   | `.error-line` (hardcoded reds)                         | Not var-driven                         | #b91c1c etc| #f87171 etc| Internal style |

**Remaining issues specific to editor**:
- Syntax token colors (`.tok-*`) are hardcoded in the component `<style>` block.
- Error line styling uses direct hex values.
- Some hover states and conditional classes still carry Tailwind color + dark: variants.
- `border-zinc-*` remnants on the editor wrapper.

---

## Dockerfile Editor Screen

**Template**: `app/templates/docker_compose_edit.html` (when `is_dockerfile=true`)

Usage is **identical** to the Compose editor above (same template, different title and save paths).

Key differences in rendered UI:
- Title changes ("Edit Dockerfile" vs "Edit Compose")
- Some help text differs
- No "YAML validation" toggle in some paths

Variable mapping is the same as Compose Editor table.

---

## Global / Shared Elements (used across screens)

| Class              | Vars Used                          | Light          | Dark           | Notes |
|--------------------|------------------------------------|----------------|----------------|-------|
| `bg-bg`            | `--color-bg`                       | #f8f9fa        | #0a0f1c        | Body |
| `bg-surface`       | `--color-surface`, `--color-border` | #ffffff / #e5e7eb | #111827 / #374151 | Cards, panels |
| `code-surface`     | `--color-code-*`                   | #f1f3f5 / #111827 / #e5e7eb | #09090b / #e4e4e7 / #27272a | Editors, logs, pre |
| `btn-secondary`    | `--color-surface`, `--color-text`, `--color-border` | Surface + text | Surface + text | Most secondary actions |
| `btn-accent`       | `--color-accent` (white text)      | #00a651        | #00a651        | Positive actions |
| `btn-danger`       | Hardcoded `#b91c1c`                | #b91c1c        | #b91c1c        | Needs var |
| `banner-*`         | Mix of semantic + hardcoded        | Various        | Various        | Needs full var conversion |
| `text-muted`       | `--color-muted`                    | #6b7280        | #a1a1aa        | Use instead of zinc muted |

---

## Recommendations for Full Var-Driven Cleanup

1. Convert remaining `text-zinc-* dark:text-*` and `border-zinc-*` to `text-muted`, `border-border`, or new text classes.
2. Make `.btn-danger` use a `--color-danger` var.
3. Update `.banner-*` definitions to use the `--color-success/danger/warning` vars instead of hardcoded hex.
4. Move syntax token colors (`.tok-*`) in the editor into CSS vars (e.g. `--color-syntax-key`).
5. Remove all `dark:` Tailwind modifiers from color-related classes in these templates.
6. Audit JS that does `classList.add('bg-...')` or sets `style.background`.
7. Ensure `body` and `nav` stay on `bg-bg` / `bg-surface`.

This document lists the current state so it can be systematically fixed.

---

**Next step for user**: Use the tables above to identify and replace any remaining non-var color usage on the Docker management and editor screens. Once updated, run the usual sync + restart to verify light/dark on those pages.
