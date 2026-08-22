# Contributing to this wiki

Docs are **Markdown in git** under `wiki/`, built with **MkDocs Material**, published via GitHub Pages at **[piherder-docs.hacknow.info](https://piherder-docs.hacknow.info/)**.

Repo-level contributor rules: [CONTRIBUTING.md](https://github.com/bjorngluck/piherder/blob/main/CONTRIBUTING.md).

## Documentation version strategy (locked)

### Default (through 0.x and into 1.0)

| Layer | Role |
|-------|------|
| **`wiki/` → piherder-docs.hacknow.info** | **How it works now** — single living operator guide for the current line |
| **`docs/RELEASE_vX.Y.Z.md`** | **What changed in this version** — upgrade notes, features, breaking changes |
| **GitHub Releases** | Same narrative as RELEASE notes + tags (in-app About / update banner link here) |
| **`docs/PLAN_*` · `FEATURE_PLAN_*` · SPEC · `docs/QA_v*`** | Maintainer planning and **freeze QA** — **not** in operator nav |

**Do not** create a separate full wiki tree per minor/patch (no `wiki-v0.5/`, `wiki-v0.6/` forks).

### When a feature ships

1. **Update the existing page** (or add one page if the topic is new).  
2. Prefer the same PR (or same release branch) as the code.  
3. If behaviour depends on version, add a short callout on the **section**, not a whole parallel site:

   ```markdown
   !!! note "Availability"
       Available from **v0.5.0**. Older tags lack this UI.
   ```

4. Put the release story in **`docs/RELEASE_vX.Y.Z.md`** (created at freeze / tag). Freeze **QA checklists** live in **`docs/QA_vX.Y.Z.md`** — do not publish them as wiki pages. Operator how-to stays in `wiki/`.  
5. Bump in-app version constants when tagging (`app/version_info.py`, `pyproject.toml`) so About + update checks stay honest.

### Version callouts — when to use them

| Situation | What to write |
|-----------|----------------|
| New capability | *Requires PiHerder ≥ **vX.Y.Z**.* |
| Behaviour change | Short *Before / after* or *Upgrade note* |
| Breaking change | RELEASE notes **and** an admonition on the page operators will hit |
| Env flag / optional | Env reference + one line on the feature page |
| Entire major line still supported | Only then consider multi-version docs (see **v1.0** below) |

### What not to do

- Do not leave operator docs describing removed defaults (e.g. seeded admin) without a release note.  
- Do not dump full `PLAN_*` / SPEC checklists into the user-facing wiki.  
- Do not hand-edit built `site/` or the old `gh-pages` tree — always edit `wiki/` sources.  
- Publish is **GitHub Actions** (Settings → Pages → Source: **GitHub Actions**). CI validates with `mkdocs build --strict`.

---

## Doc conventions

| Practice | Expectation |
|----------|-------------|
| Single living wiki on `main` | Documents the **1.x** production line |
| RELEASE notes per tag | Required for every `v1.x.y` |
| Feature PRs | Update wiki when UX/API changes |
| Operator page pattern | **What this is** → **Why** → **End-to-end** → reference |
| No process notes on operator pages | No freeze lists, screenshot QA callouts, or PLAN residual spam |

Before tag: `mkdocs build --strict` green; install / first-login / roles / env-reference accurate for the version.

---

## Edit flow (text)

1. Edit or add pages under `wiki/`.  
2. Register new pages in root `mkdocs.yml` → `nav:`.  
3. Preview locally:

   ```bash
   pip install -r requirements-docs.txt
   mkdocs serve
   # http://127.0.0.1:8000
   ```

4. Strict check: `mkdocs build --strict`.  
5. Commit, push, merge to `main`.  
6. **Docs** workflow deploys Pages automatically on `main` when `wiki/**` or `mkdocs.yml` change.

!!! tip "Live docs"
    **[https://piherder-docs.hacknow.info/](https://piherder-docs.hacknow.info/)**  
    `edit_uri` on each page opens the file on GitHub — fine for small text fixes; use a **local clone** for screenshots and multi-file work.

## Screenshots (best practice)

**Use a local clone of the repo, save PNGs under `wiki/assets/screenshots/`, update Markdown, preview with `mkdocs serve`, then commit and push.**

### Why local + git

| Benefit | Detail |
|---------|--------|
| Preview | Material theme, nav, figure captions as operators see them |
| Batch | Many captures in one PR without fighting the web UI |
| Quality gate | `mkdocs build --strict` catches missing files and bad links |
| History | Binaries versioned with the prose that references them |

### Step-by-step

1. Run PiHerder (compose) and open the UI in a desktop browser.  
2. Set **light** theme (default for docs).  
3. Capture the page (OS tool or browser). Crop as needed.  
4. Save as e.g. `wiki/assets/screenshots/dashboard.png`.  
5. In the matching `.md`, use:

   ```markdown
   <figure class="ph-figure" markdown>
     ![Dashboard](../assets/screenshots/dashboard.png)
     <figcaption>Fleet summary and attention table.</figcaption>
   </figure>
   ```

6. Remove any `<span class="ph-wireframe-badge">wireframe</span>` once the real image is live.  
7. `mkdocs serve` → confirm the image.  
8. `mkdocs build --strict`.  
9. `git add` PNG + markdown → commit → push → merge.

### Conventions

- **Default:** light + desktop (~1400–1600px).  
- **Optional:** one dark showcase (`*-dark.png`), one mobile only where layout differs (`*-mobile.png`).  
- **Not required:** four variants of every screen.  
- Inventory + tips: [`wiki/assets/screenshots/README.md`](https://github.com/bjorngluck/piherder/blob/main/wiki/assets/screenshots/README.md).  
- Operator-facing theme notes: [Appearance](../getting-started/appearance.md).

### What not to do

- Do not paste multi‑megabyte full-desktop PNGs without cropping.  
- Do not commit secrets visible in UI (API tokens, PEM previews — those should not appear in UI anyway).  
- Do not edit only the built `site/` tree by hand — always edit `wiki/` sources (Actions publishes from CI).

## Style

- Short pages, one job each (not another 750-line ADMIN).  
- Prefer numbered steps + tables + admonitions.  
- Code blocks for every command an operator must run.  
- Link **scenarios** from [Operator scenarios](../getting-started/operator-scenarios.md).  
- Do **not** put `PLAN_*` / `FEATURE_PLAN_*` / SPEC checklists in the user nav — link out to GitHub blob if needed.  
- Feature availability: use a short admonition (*Available from **vX.Y.Z***) rather than duplicating pages.

## Mermaid

Fenced `mermaid` blocks render in Material (architecture and flows).

## Build strictness

```bash
mkdocs build --strict
```

Fix warnings (broken links, missing files) before merge.
