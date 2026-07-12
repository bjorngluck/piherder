# Contributing to this wiki

Docs are **Markdown in git** under `wiki/`, built with **MkDocs Material**.

## Edit flow

1. Edit or add pages under `wiki/`.  
2. Register new pages in `mkdocs.yml` → `nav:`.  
3. Preview locally:

   ```bash
   pip install -r requirements-docs.txt
   mkdocs serve
   ```

4. Open a PR / merge to `main`.

!!! tip "Live docs"
    Public site: **[https://bjorngluck.github.io/piherder/](https://bjorngluck.github.io/piherder/)**. After merge to `main`, rebuild and publish the site (`mkdocs build` → `gh-pages`, or the Docs GitHub Action if Pages uses Actions).

## Style

- Short pages, one job each (not another 750-line ADMIN).  
- Prefer numbered steps + tables + admonitions.  
- Code blocks for every command an operator must run.  
- Screenshots: real PNGs in `wiki/assets/screenshots/` when available; wireframe SVGs are interim.  
- Do **not** put `PLAN_*` / `FEATURE_PLAN_*` / SPEC checklists in the user nav — link out to GitHub raw/blob if needed.

## Images

```markdown
<figure class="ph-figure" markdown>
  ![Alt text](../assets/screenshots/dashboard.png)
  <figcaption>Describe what the operator should notice.</figcaption>
</figure>
```

Capture guide: `wiki/assets/screenshots/README.md`.

## Mermaid

Fenced `mermaid` blocks render in Material (diagrams for architecture and flows).

## Build strictness

```bash
mkdocs build --strict
```

Fix warnings (broken links, missing files) before merge.
