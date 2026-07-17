# Add a server

## What this is

A **server** in PiHerder is one fleet host (Raspberry Pi, Debian/Ubuntu box, or specialised OS like HAOS) that the control plane reaches over **SSH**. Everything else — backups, apt, Docker, templates — hangs off this record.

## Why it exists

Without a server record you have no place to store the encrypted SSH key, feature flags, schedules, or job history. Adding a server is the bridge from “I have a Pi on the LAN” to “PiHerder can act on it safely and repeatedly.”

## When to use it

- First host after install  
- Every new Pi / VM / metal box you want under the same dashboard  
- Replacing a host: often **add new** then [remove](remove-server.md) the old one after cutover  

<figure class="ph-figure" markdown>
  ![Servers list](../assets/screenshots/server-list.png)
  <figcaption>Servers list with bulk action bar for checks, upgrades, and backups.</figcaption>
</figure>

---

## End-to-end: first Pi (happy path)

1. **Servers → Add server** — name people will recognise, hostname or IP, SSH port/user.  
2. **Generate a keypair** (recommended) or upload a private key you already use.  
3. Optionally store a **one-time SSH password** only to bootstrap key deploy.  
4. Save → open the server → **SSH access**.  
5. **Deploy key** → **Test connection** until login succeeds.  
6. **Check dependencies** for features you plan to enable (rsync / docker / apt).  
7. Optional: **Least-priv user** on Pi OS / Ubuntu so day-to-day is not root.  
8. **Edit → Features** — enable Backups / OS patch / Docker only as needed.  
9. Clear any stored SSH password once key-only works.  
10. Confirm the host on the [Dashboard](dashboard-and-services.md).

**Done when:** Test connection succeeds; dependency chips match enabled features; password bootstrap is gone.

---

## Steps (reference)

1. **Servers → Add server** (or equivalent CTA).  
2. Enter **name**, **hostname/IP**, SSH port/user.  
3. **Generate** a keypair (recommended) or upload a private key.  
4. Optionally store a **one-time SSH password** only to bootstrap key deploy.  
5. Save, open the server → **SSH access**.

<figure class="ph-figure" markdown>
  ![SSH access panel](../assets/screenshots/ssh-access.png)
  <figcaption>SSH access: deploy key, test, rotate, least-priv, dependency chips.</figcaption>
</figure>

## SSH access panel

| Action | What it does | Why |
|--------|----------------|-----|
| **Test connection** | Verifies key (or password) login, then refreshes **host dependency** probes when login succeeds | Proves the path before you queue jobs |
| **Check dependencies** | Probes `rsync` / docker / apt for **enabled** features only | Failures become hints, not silent job fails later |
| **Deploy key** | Installs public key into `authorized_keys`; verifies key-only login | Stops depending on passwords |
| **Rotate key** | New keypair, deploy, swap only after verify succeeds | Safe rotation if a key may have leaked |
| **Least-priv user** | Optional `piherder` user + limited sudoers (Pi OS / Ubuntu) | Limits blast radius of the herder account |

Dependency chips on the server page are **read-only** snapshots; re-check from **SSH access** (onboarding lives there).

!!! tip "Clear stored passwords"
    After key auth works, clear any stored SSH password so secrets stay keys-only (encrypted at rest with `PIHERDER_MASTER_KEY`).

### Least-privilege user (Debian / Pi OS / Ubuntu)

**Why:** Running every job as your personal `pi`/`ubuntu` user mixes human logins with automation. A dedicated user with narrow sudoers is easier to reason about and revoke.

- Creates e.g. `piherder` with key-only login  
- Optional `docker` group  
- Sudoers for rsync/test and optional apt/reboot (`visudo -cf` before install)  
- **Run on host** re-points `ssh_username` after verify  
- **HAOS / specialised:** instructions only — not automated  

### Docker base dir (Option B)

If stacks live under another user’s home (e.g. `/home/bjorn/docker`):

1. Set **Docker base dir** to that **absolute** path (not `~/docker` after switching to the `piherder` user).  
2. Run the **Option B ACL script** from SSH access so the service user can traverse the tree.

`~/docker` expands to the **SSH** user’s home and breaks restart/build/logs after re-pointing to `piherder`.

## Server detail layout

After onboarding, the server page uses the shared **ops-hero** plus equal **destination cards** (desktop grid): **Backups**, **Docker**, **Services**, optional **Grafana** / **SSH (Uptime Kuma)**, and **Host status** (⋯ actions). Host dependency chips stay above as a snapshot; full SSH onboarding stays under **SSH access**. Child pages (Backups, Docker, Services) reuse the same hero width and card rhythm.

## Feature flags

**Edit → Features** — enable only what you need:

| Flag | Unlocks | Why a flag |
|------|---------|------------|
| Backups | rsync backup/restore UI + schedules | Hosts without files to protect stay quiet |
| OS patch | apt check/apply | Skips apt probes on non-Debian hosts |
| Docker / containers | Docker page, container patch, templates deploy targets | HAOS or bare metal without Docker stay simple |

Disabled features are **hard-hidden** from dest cards and ⋯ menus.

On the **Servers** list, bulk actions (check/upgrade OS, check/patch containers, backup) only queue hosts with the matching flag enabled — see [Bulk actions](updates-and-patching.md#bulk-actions-servers-list).

## Schedules

**Edit → Schedules** — update **checks** (safe) and optional **apply** (real upgrades). See [Updates & patching](updates-and-patching.md).

**Why start with checks only:** scheduled apply is powerful; quiet weekly checks build trust before you automate upgrades.

## Host dependency check

After key deploy / least-priv / test, PiHerder stores a dependency snapshot. Failures show install/privilege **hints only** — nothing is auto-installed on the remote (so a production host never gets surprise packages).

## Host status / diagnostics

From server detail **Host status** (⋯) or related chips, PiHerder can show a short **system info** snapshot over SSH (OS/kernel, reboot-pending, disk free — cached briefly). This is read-only diagnostics, not continuous monitoring (use Kuma for uptime).

<figure class="ph-figure" markdown>
  ![Server detail](../assets/screenshots/server-detail.png)
  <figcaption>Server detail with status chips and feature cards.</figcaption>
</figure>

## Planned post-RC (onboarding)

After **v0.5.0 RC**, a **wizard-driven add-host** path and richer **bootstrap scripts** (create `piherder` user before first connect, hostname, Pi-hole A handoff, optional first-boot enrollment with a token) are planned. Web SSH console is last and highest bar.

See [FEATURE_PLAN_HOST_LIFECYCLE.md](https://github.com/bjorngluck/piherder/blob/main/docs/FEATURE_PLAN_HOST_LIFECYCLE.md) (phases P2 / P4 / P5). Until then, use the steps above.

## Related

- [Remove a server](remove-server.md) — UI teardown + optional host cleanup  
- [Backups](backups.md) · [Updates](updates-and-patching.md) · [Docker](../docker/overview.md)  
- Journey A: [Operator scenarios](../getting-started/operator-scenarios.md#journey-a)  
