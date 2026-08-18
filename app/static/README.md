# Static assets for PiHerder (offline / air-gapped support)

JavaScript dependencies are vendored locally so PiHerder works with **zero
internet** after the image is built. Tailwind is a **committed compiled
stylesheet** — not Tailwind Play.

## Tailwind (compiled CSS)

Source: `css/tailwind-src.css` + `tailwind.config.js`  
Output (committed): `css/tailwind.css`

After you add new Tailwind **utility** class names in templates or `static/js`:

```bash
bash scripts/build-tailwind.sh
# commit app/static/css/tailwind.css
```

The script uses local `npx` or a one-shot `node:22` container. Image builds
do **not** need Node or `cdn.tailwindcss.com`.

Custom chrome (`btn`, `card`, `text-muted`, maps) stays in `css/themes.css`
and the ops/fabric sheets. Tailwind Preflight is **off** so it does not
reset those.

## Other JS (HTMX, Alpine, xterm)

```bash
bash scripts/vendor_cdns.sh
```

Downloads into `app/static/` (gitignored except xterm under `vendor/`):

- `htmx.min.js`
- `alpine.min.js`
- `vendor/xterm/*`

The Dockerfile runs this during `docker build`. HTMX/Alpine are gitignored;
xterm is typically committed so console works without a vendor step.

## Fallback

`base.html` still has a layout fallback `<style>` if `tailwind.css` fails to
load. Theme colors always come from `themes.css`.

## Updating versions

| Asset | How |
|-------|-----|
| Tailwind utilities | bump `tailwindcss@3.4.17` in `scripts/build-tailwind.sh`, rebuild, commit CSS |
| HTMX / Alpine / xterm | edit URLs in `scripts/vendor_cdns.sh` |
