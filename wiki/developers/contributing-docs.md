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

!!! note "Public site only at RC"
    Free GitHub Pages needs a **public** repo. PiHerder stays private until the **v0.5.0 RC** go-live; then: make repo public → Settings → Pages → **GitHub Actions** → Docs workflow deploys to `https://bjorngluck.github.io/piherder/`. Until then, preview with `mkdocs serve` only.

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
