# Screenshots

Real UI captures live here. Wiki pages reference them like:

```markdown
![Dashboard](../assets/screenshots/dashboard.png)
```

Wireframe SVGs (`*.svg`) are legacy placeholders; wiki pages use real PNGs. You can delete unused SVGs once no external link points at them.

## Release policy

| Release | Screenshot bar |
|---------|----------------|
| **v0.6.0–v0.7.0** | Historical — PNG pack deferred / prose-first |
| **v0.8.0 RC3** (tagged) | Full pack landed — [RELEASE_v0.8.0.md](../../../docs/RELEASE_v0.8.0.md) |
| **v0.9.0** (tagged) | Operator recapture pack for 0.9 chrome — [RELEASE_v0.9.0.md](../../../docs/RELEASE_v0.9.0.md) |
| **v1.0.0** (tagged) | Full pack in place (operator-confirmed) — [PLAN_v1.0.0.md §8.2](../../../docs/PLAN_v1.0.0.md) · [RELEASE_v1.0.0.md](../../../docs/RELEASE_v1.0.0.md) |
| **v1.1.0** | Freeze pack landed — [RELEASE_v1.1.0.md](../../../docs/RELEASE_v1.1.0.md) |
| **v1.2.0** | **Current production** — QA **Pass** + screenshot pack **landed** 2026-08-18 (`login-sso`, `settings-sso`, `account-sso`, `account-passkeys`, `console-popup`, `settings-self-backup`). [QA](../../operations/qa-v1.2.0.md) · [RELEASE](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.2.0.md) |

**Owner:** operator fleet testing (not CI). Replace PNGs in this directory; captions note when a figure may lag. After dropping files: `mkdocs build --strict`.

!!! tip "Production screenshots (v1.2)"
    Capture from rebuilt **`v1.2.0-dev`** / image **`1.2.0`**: `docker compose build web && docker compose up -d web`.  
    App code is **not** bind-mounted in prod compose — stale containers = stale chrome in PNGs.  
    About / footer (signed in) should show **1.2.0**.

---

## v1.2.0 — what to update

Use this section to close the 1.2 pack. Mark rows done locally as you replace files. Wire **new** filenames into the wiki page in the **same commit** as the PNG.

Existing 1.1 PNGs stay valid unless a row below says recapture. Do **not** redo certs / maps / LAN unless chrome actually drifted.

### Must capture — new 1.2 surfaces (no PNG yet)

| Pri | Suggested file | Surface | Must show | Wire into |
|-----|----------------|---------|-----------|-----------|
| **P0** | `login-sso.png` | `/auth/login` | **Continue with Authentik** (or your display name) as primary; password form still visible (Require SSO **off**) | [SSO](../../account-security/sso-oidc.md) · [First login](../../getting-started/first-login.md) |
| **P0** | `settings-sso.png` | Settings → **General** → **SSO / OpenID Connect** | Enable + issuer + client id + group map UI + **redirect URI** shown. **Redact** client secret | [Settings](../../operations/settings.md) · [SSO](../../account-security/sso-oidc.md) |
| **P0** | `account-sso.png` | Account → **Connected accounts (SSO)** | Linked issuer/host; Link / Unlink (no secrets) | [SSO](../../account-security/sso-oidc.md) |
| **P0** | `account-passkeys.png` | Account → **Passkeys** | At least one named passkey + **Add passkey**. Optional: Passkeys & 2FA heading in frame | [2FA](../../account-security/two-factor.md) |
| **P0** | `console-popup.png` | Server detail → **Console** popup | After 2FA unlock: **+ Shell**, real host prompt, compact chrome. Flag **on** for this shot only | [Web SSH console](../../day-to-day/web-ssh-console.md) |
| **P0** | `settings-self-backup.png` | Settings → **PiHerder backup** | **Full DR** selected (pg_dump); archive list if you have one. No restore of a live fleet in the shot | [Self-backup](../../operations/self-backup.md) · [Settings](../../operations/settings.md) |
| **P1** | `login-2fa-passkey.png` | `/auth/2fa` after password or SSO | **Use passkey** + TOTP field (not passwordless) | [2FA](../../account-security/two-factor.md) |
| **P1** | `console-mobile.png` | Console on a **phone** width | Soft-key **row** (Tab / Esc / arrows), no leftover IME bar if you can; residual IME is [known](../../day-to-day/web-ssh-console.md#known-issues) | [Web SSH console](../../day-to-day/web-ssh-console.md) |
| **P2** | `login-require-sso.png` | Login with **Require SSO** on | Password form **hidden**; SSO button; do not leave this enabled on the QA box after the shot | [SSO](../../account-security/sso-oidc.md) |
| **P2** | `demo-banner.png` | Public demo dashboard | Shared-viewer **demo banner**. Capture on [piherder-demo](../../operations/demo-site.md), not the production fleet | [Public demo](../../operations/demo-site.md) |

### Must recapture — chrome changed in 1.2

| Pri | File(s) | 1.2 chrome to show |
|-----|---------|---------------------|
| **P0** | `account-2fa.png` | Still TOTP + backup-codes + trusted devices; **passkeys live on the same Account page** — either recapture a wider crop or rely on `account-passkeys.png` |
| **P0** | `add-server-wizard.png` | SSH user field defaults to **`pi`** (not `piherder`) |
| **P0** | `ssh-access.png` | **Pinned host key** fingerprint + **Reset pin** (after a Test connection) |
| **P1** | `add-server-wizard-done.png` | Spot-check: user shown as `pi@…` if that is the new host |
| **P1** | `dashboard.png`, `dashboard-dark.png` | Compiled Tailwind (no unstyled collapse); light **and** dark |
| **P1** | `server-detail.png` | **Console** action visible only if you leave the flag on for the shot; otherwise default **off** (no Console) is also honest |
| **P2** | `server-list.png` | Optional ⋯ **Console** if flag on; otherwise skip |

### Spot-check only (1.1 pack still good)

| File | Why skip unless broken |
|------|------------------------|
| Certs (`certificates-*.png`) | 1.1 deploy-target pack; no 1.2 headline |
| Maps (`dns-*.png`) | 1.1 icons / ports / direct-TLS story already captured; recapture `dns-logical.png` only if you want the **Use \<project\> for this path** chip in frame |
| LAN (`nmap-*.png`) | Unchanged story |
| Integrations Kuma / Grafana / Pi-hole / NPM / generic | Unchanged |
| Templates (`templates-*.png`) | Unchanged |
| `settings-alerts.png`, `settings-api.png`, `settings-status.png`, `settings-stale-cleanup.png` | Unchanged unless Status copy drifted |
| `backups-page.png`, `jobs-page.png` | Unchanged (vanished-file retry has no dedicated chrome) |
| `account-push.png`, `account-favourites.png`, `nav-host-jump.png` | Unchanged |
| HAOS shots | Spot-check only if HAOS UI drifted |
| `docker-*.png` | Compose **Build** has no dedicated shot unless you want one (`docker-build.png`, P2) |

---

## Default convention

| Default | Value |
|---------|--------|
| Theme | **Light** |
| Viewport | **Desktop** (~1400–1600px wide) |
| Variants | Only when the UI **story** changes |

Optional extras (not a full matrix):

| Suffix | Use |
|--------|-----|
| `*-dark.png` | One showcase (e.g. dashboard) |
| `*-mobile.png` | Layout differs (console soft keys, Hosts map, coverage) |

Do **not** capture every page in light×dark×mobile. See [Appearance](../../getting-started/appearance.md).

---

## Pre-capture checklist (operator)

1. Rebuild/restart **web** so templates + compiled CSS match `v1.2.0-dev` / tag. About shows **1.2.0**.  
2. Light theme · desktop width · redact hostnames/IPs if needed.  
3. **SSO:** Settings saved; login button visible; **do not** photograph the client secret (blank or `••••`).  
4. **Passkeys:** at least one enrolled key with a nickname; HTTPS / matching `PIHERDER_PUBLIC_URL`.  
5. **Console:** set `PIHERDER_SSH_CONSOLE=true` only for those shots, then decide whether production keeps it on (default **off**).  
6. **Host key:** Test connection once so the pin + fingerprint are real.  
7. **Full DR:** Settings → PiHerder backup shows Full mode; do not include download URLs that leak paths you care about.  
8. After saving PNGs: add `![…]` on the wiki pages in the table above · `mkdocs build --strict` · commit binaries + captions together.

**Do not include in frames:** client secrets, SMTP passwords, API tokens, PEM material, backup codes, live `database.dump` paths you would not publish.

---

## Expected chrome (do not document old UI)

### Carried from 1.0 / 1.1 (still required if you recapture those pages)

| Surface | Must show in PNGs |
|---------|-------------------|
| **Servers list** | No footer “Status from last update checks…” line |
| **Server detail** | Dest cards; **Network path** + **LAN discovery** side-by-side |
| **Certs** | Deploy-target language; top **Deploy**; wizard or compact target list |
| **Hosts map** | Kind icons; progressive ports if capturing expand |
| **Account 2FA** | Backup codes via **modal** (not query string); trusted device type + last IP + ✎ |
| **Footer (signed out)** | Brand only — **no** version until signed in |
| **Settings → Alerts / API** | SMTP + webhook; Try a token / docs links |

### New / changed in 1.2

| Surface | Must show in PNGs |
|---------|-------------------|
| **Login** | **Continue with {display name}** when SSO is enabled |
| **Settings → General** | **SSO / OpenID Connect** card (issuer, map, redirect URI) |
| **Account** | **Passkeys** list; **Connected accounts (SSO)** |
| **2FA step-up** | **Use passkey** after password **or** SSO (not passwordless) |
| **Console** | Popup or `/console`; step-up then **+ Shell**; mobile soft-key **row** if capturing phone |
| **PiHerder backup** | **Full DR** (`pg_dump`) vs Config only |
| **Add server** | Default SSH user **`pi`** |
| **SSH access** | **Pinned host key** + reset |

---

## Inventory — all PNGs

### 1.2 new (add file + wiki `![…]` when captured)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `login-sso.png` | Sign in | SSO button + password form |
| `login-2fa-passkey.png` | 2FA step-up | Use passkey + TOTP |
| `login-require-sso.png` *(optional)* | Require SSO | Password form hidden |
| `settings-sso.png` | Settings → General | SSO card; redact secret |
| `settings-self-backup.png` | Settings → PiHerder backup | Full DR |
| `account-passkeys.png` | Account → Passkeys | Named key + Add |
| `account-sso.png` | Account → Connected accounts | Link state |
| `console-popup.png` | Console popup | Unlocked PTY |
| `console-mobile.png` | Console phone | Soft keys |
| `demo-banner.png` *(optional)* | Public demo | Viewer banner only |

### Core fleet

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dashboard.png` | Home | Recapture if 1.2 chrome / compiled CSS |
| `dashboard-dark.png` | Home dark showcase | Recapture with light pair |
| `server-list.png` | Servers | Spot-check; Console ⋯ only if flag on |
| `server-detail.png` | Server detail | Recapture if Console CTA or 1.2 tabs show |
| `server-detail-haos.png` | HAOS host | Spot-check |
| `system-info-haos.png` | System info modal | Spot-check |
| `ssh-access.png` | SSH access | **Recapture:** pinned host key + reset |
| `add-server-wizard.png` | Add server | **Recapture:** default user **`pi`** |
| `add-server-wizard-done.png` | Wizard done | Spot-check `pi@host` |
| `backups-page.png` | Backups | Spot-check |
| `jobs-page.png` | Jobs | Spot-check |

### Docker & templates

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `docker-project-lifecycle.png` | Project ⋯ lifecycle | Spot-check |
| `templates-catalog.png` | Catalog → Templates | Spot-check |
| `templates-deploy.png` | Deploy wizard | Spot-check |
| `templates-deployment.png` | Deployment detail | Spot-check |
| `templates-from-host.png` *(optional)* | From host | Spot-check |

### Catalog / network / discovery

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `dns-hub.png` | `/dns` hub | Spot-check |
| `dns-physical.png` | Hosts map | Spot-check |
| `dns-physical-mobile.png` | Hosts map phone | Optional |
| `dns-logical.png` | Path map | Optional recapture if showing direct-TLS / **Use project** |
| `dns-stack-panel.png` | Stack panel | Spot-check |
| `dns-coverage.png` | Kuma coverage | Spot-check |
| `dns-host-ports-expand.png` | Hosts map ports | Spot-check |
| `nmap-overview.png` | LAN Overview | Spot-check |
| `nmap-devices.png` | Devices List | Spot-check |
| `nmap-network.png` | Devices Map | Spot-check |
| `nmap-schedules.png` | Schedules | Spot-check |
| `nmap-runs.png` | Runs | Spot-check |
| `nmap-server-embed.png` | Server LAN embed | Spot-check |

### Integrations & certs

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `integrations-kuma.png` | Kuma detail | Spot-check |
| `integrations-grafana.png` | Grafana | Spot-check |
| `integrations-pihole.png` | Pi-hole | Spot-check |
| `integrations-npm.png` | NPM | Spot-check |
| `integrations-generic.png` | Generic URL | Spot-check |
| `certificates-list.png` | Certs list | Spot-check |
| `certificates-setup.png` | Setup guide | Spot-check |
| `certificates-detail.png` | Cert detail | Spot-check |
| `certificates-deploy-wizard.png` | Wizard open | Spot-check |
| `certificates-edge-map.png` | Edge map card | Spot-check |
| `services-fleet.png` | `/services` | Spot-check |

### Settings & account (1.1 + 1.2)

| File | Page / topic | Capture notes |
|------|----------------|---------------|
| `settings-status.png` | Status tab | Spot-check |
| `settings-stale-cleanup.png` | Stale data cleanup | Spot-check |
| `settings-alerts.png` | Alerts | Spot-check |
| `settings-api.png` | API | Spot-check |
| `settings-sso.png` | **New 1.2** — SSO | See must-capture |
| `settings-self-backup.png` | **New 1.2** — Full DR | See must-capture |
| `account-push.png` | PWA / push | Spot-check |
| `account-2fa.png` | TOTP + trusted devices | Recapture or pair with `account-passkeys.png` |
| `account-passkeys.png` | **New 1.2** — Passkeys | See must-capture |
| `account-sso.png` | **New 1.2** — Connected accounts | See must-capture |
| `account-favourites.png` | ★ menu | Spot-check |
| `nav-host-jump.png` | Jump host | Spot-check |

### Optional residual

| File | Notes |
|------|--------|
| `ha-update-modal.png` | HA apply dialog |
| `jobs-live-log.png` | JobHold live log dedicated shot |
| `docker-logs-modal.png` | Logs modal with **All services** selected |
| `docker-build.png` *(optional 1.2)* | Compose **Build** stream — only if you want R3 in the wiki |

---

## Full QA pass — suggested sequence (v1.2)

1. **Login / identity** — `login-sso.png`; optional require-SSO; `/auth/2fa` passkey  
2. **Account** — passkeys; connected SSO; spot-check 2FA / trusted devices  
3. **Settings** — SSO card (redact secret); PiHerder backup **Full DR**  
4. **Onboard / SSH** — add-server default **`pi`**; SSH access **pinned host key**  
5. **Console** — desktop popup (flag on); optional phone soft keys; then restore your production flag  
6. **Shell** — dashboard light + dark if CSS/chrome drifted  
7. **Demo** *(optional)* — banner on the public demo only  
8. **Everything else** — only if a caption/PNG disagree  

Next train (not 1.2 screenshot blockers): per-host ACL · Host Files dest-card (flag off) · CSP nonces — [PLAN_v1.3.0.md](https://github.com/bjorngluck/piherder/blob/v1.3.0-dev/docs/PLAN_v1.3.0.md) (active).

---

## How to land screenshots

**Best practice: local git → commit → push** (binaries + markdown).

```bash
git checkout v1.2.0-dev && git pull
# optional: git checkout -b docs/screenshots-1.2

python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000

# Capture UI → save into wiki/assets/screenshots/
# Wire any *new* filenames into wiki pages (table above)
# Update captions only if the story changed

mkdocs build --strict
git add wiki/assets/screenshots/*.png wiki/**/*.md
git commit -m "docs(wiki): screenshot pack for v1.2.0"
git push
```

After merge to the docs deploy branch / tag, hard-refresh the live site.

### Checklist before commit

- [ ] PNG names match Markdown references  
- [ ] New 1.2 files have a wiki `![…]` (or stay unlinked only if still WIP)  
- [ ] Light desktop for defaults; dark/mobile only where planned  
- [ ] Sensitive hostnames/IPs redacted if needed  
- [ ] No real SMTP passwords, API tokens, client secrets, backup codes, or PEM material in frames  
- [ ] `mkdocs build --strict` passes  
- [ ] Captions no longer say “recapture in progress” for files you just replaced  

Full style guide: [Contributing docs](../../developers/contributing-docs.md).
