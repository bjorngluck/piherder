# Static assets for PiHerder (offline / air-gapped support)

All JavaScript dependencies are vendored locally so PiHerder works in
environments with **zero internet access** (very common for secure Pi fleets).

## Vendoring (recommended way)

Run this from the project root:

```bash
bash scripts/vendor_cdns.sh
```

This downloads the exact versions used by the templates into `app/static/`:
- `tailwind.js`   (Tailwind Play – supports the runtime config in base.html)
- `htmx.min.js`
- `alpine.min.js`

## Automatic in Docker builds

The [Dockerfile](../Dockerfile) runs `scripts/vendor_cdns.sh` automatically
during `docker build`.

**The build will fail hard** if a valid `tailwind.js` cannot be obtained.
This is intentional (especially after open-sourcing) so that people who
build the image themselves do not accidentally get a degraded UI.

If the build fails with a message about missing `tailwind.js`:
- Make sure the build machine has internet.
- Temporarily whitelist `cdn.tailwindcss.com` if you use Pi-hole.
- Or pre-download the file on another machine and include it in the build context.

Once a successful image is built, it is fully self-contained and works
offline / air-gapped.

## Fallback when JS is missing

`base.html` contains a large hand-maintained `<style>` block that emulates
the Tailwind classes actually used by the UI (cards, buttons with proper
`h-7` sizing, modals, flex layouts, colors, etc.). The Docker Management
page and most of the app remain usable even without the JS files.

## Do not commit the JS files

They are listed in `.gitignore` because they are generated assets.
Re-run the vendor script (or rebuild the image) when you want fresh copies.

## Updating versions

Edit `scripts/vendor_cdns.sh` and re-run. Also update the `<script src>`
tags in `app/templates/base.html` if you change filenames.

## Common Problems

### SSL / Certificate errors (especially with Tailwind)

```
curl: (60) SSL: no alternative certificate subject name matches target hostname 'cdn.tailwindcss.com'
```

This is very common when:
- Running behind a corporate proxy that does SSL inspection
- Using certain container / CI environments
- Outdated CA certificates

**Quick fixes:**

```bash
# Most common case for this project (you probably run Pi-hole):
# Temporarily whitelist "cdn.tailwindcss.com" in Pi-hole, then:
bash scripts/vendor_cdns.sh

# Force insecure (corporate proxy / bad CA):
VENDOR_INSECURE=1 bash scripts/vendor_cdns.sh

# Manual download (bypass everything):
curl -kL -o app/static/tailwind.js https://cdn.tailwindcss.com
```

The script now detects Pi-hole blocking and will warn you. Once downloaded, the
`tailwind.js` file can be copied to air-gapped machines.

Once you have a *good* `tailwind.js` (should be > 250kB and start with real JavaScript, not HTML), put it in `app/static/` before building.

You can transfer a good copy from another machine:
```bash
# On a machine that can reach the real CDN:
curl -L -o tailwind.js https://cdn.tailwindcss.com
scp tailwind.js user@your-pi:~/docker/piherder/app/static/
```

After placing a good file, rebuild:
```bash
docker compose build --no-cache
docker compose up -d
```
