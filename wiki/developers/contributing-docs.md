# Contributing to this wiki

Docs are **Markdown in git** under `wiki/`, built with **MkDocs Material**, published to **GitHub Pages**.

Repo-level contributor rules: [CONTRIBUTING.md](https://github.com/bjorngluck/piherder/blob/main/CONTRIBUTING.md).

## Documentation version strategy (locked)

### Default (through 0.x and into 1.0)

| Layer | Role |
|-------|------|
| **`wiki/` → github.io** | **How it works now** — single living operator guide for the current line |
| **`docs/RELEASE_vX.Y.Z.md`** | **What changed in this version** — upgrade notes, features, breaking changes |
| **GitHub Releases** | Same narrative as RELEASE notes + tags (in-app About / update banner link here) |
| **`docs/PLAN_*` · `FEATURE_PLAN_*` · SPEC** | Maintainer planning only — **not** in operator nav |

**Do not** create a separate full wiki tree per minor/patch (no `wiki-v0.5/`, `wiki-v0.6/` forks).

### When a feature ships

1. **Update the existing page** (or add one page if the topic is new).  
2. Prefer the same PR (or same release branch) as the code.  
3. If behaviour depends on version, add a short callout on the **section**, not a whole parallel site:

   ```markdown
   !!! note "Availability"
       Available from **v0.5.0**. Older tags lack this UI.
   ```

4. Put the release story in **`docs/RELEASE_vX.Y.Z.md`** (created at freeze / tag).  
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
- Do not hand-edit built `site/` or `gh-pages` — always edit `wiki/` sources.

---

## Toward official **v1.0.0** (docs + process)

Goal for **1.0.0**: operators can install, run day-to-day fleet ops, and upgrade with **one clear story** — not a maze of versioned sites.

### Keep (confirmed)

| Practice | At 1.0 |
|----------|--------|
| Single living wiki on `main` | **Yes** — documents the **1.x** line |
| RELEASE notes per tag | **Yes** — required for every `v1.x.y` |
| Feature PR updates wiki when UX/API changes | **Yes** |
| In-app About + GitHub release check | **Yes** — points at tags / release notes |

### Solidify before or at the 1.0 tag

1. **Freeze bar for docs** (same PR/release checklist as code):  
   - `mkdocs build --strict` green  
   - Home / install / first-login / roles / env-reference accurate for the tagged version  
   - Operator scenarios index covers every first-class nav area  
   - Screenshots: replace remaining wireframes for critical paths (install, servers, docker, templates, network, About) where possible  

2. **Single release note template** (`docs/RELEASE_v1.0.0.md` and later):  
   - Highlights  
   - Upgrade from 0.5.x (migrations, env keys, breaking UI)  
   - Docs / wiki changes of note  
   - Known limitations  

3. **Version framing on the home page**  
   - **Current line:** v1.0.x  
   - **Previous major** (if any): link RELEASE or a short “0.x archive” note — not a full second wiki  

4. **Support policy (write down at 1.0)**  
   - Document which tags get security fixes (e.g. latest 1.0.x only, or N-1).  
   - If only **latest 1.x** is supported, **do not** maintain multi-version MkDocs.  

5. **Optional later: multi-version wiki**  
   Only if you support **two majors in parallel** (e.g. 1.x + 2.x) with real install/API divergence.  
   Then use a version selector (e.g. [mike](https://github.com/jimporter/mike) + Material) with:  
   - `latest` / `1.x` → living docs  
   - `0.x` or `1.0` frozen snapshot **once** at EOL of that line  
   Not per minor release.

### Pre-1.0 (0.5.x RC) remains

- Ship **v0.5.0** with living wiki + `RELEASE_v0.5.0.md` at freeze.  
- Treat 0.5.x docs as the draft of 1.0 operator docs; clean wireframes and checklists as you stabilize.

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
    **[https://bjorngluck.github.io/piherder/](https://bjorngluck.github.io/piherder/)**  
    `edit_uri` on each page opens the file on GitHub — fine for small text fixes; use a **local clone** for screenshots and multi-file work.

## Screenshots (best practice)

**Use a local clone of the repo, save PNGs under `wiki/assets/screenshots/`, update Markdown, preview with `mkdocs serve`, then commit and push.**

That is the supported path for RC documentation with images.

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
- Do not edit only the built `site/` or `gh-pages` tree by hand — always edit `wiki/` sources.

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
