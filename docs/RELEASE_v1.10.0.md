# PiHerder v1.10.0

**5 October 2026.** Tag **[v1.10.0](https://github.com/bjorngluck/piherder/releases/tag/v1.10.0)**. Package **1.10.0**. Released.

A backup copy can go to OneDrive as well as Google Drive and a NAS share. The herder’s own self-backup archive can follow that copy. An agent can sign in to hosted `/mcp` in the browser. Reports can hide the six history cards from one row, and a template page lists the hosts that use it.

**Image:** [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) `1.10.0` · `1.10` · `latest` (amd64 + arm64). Manifest `sha256:0a1286cb0864e153ea1af6ba8b458175da051c8a54fc98d701539c08dcd99d04`. Pin `1.9.0` / `1.9` stays the previous image. Pins `1.8.1` / `1.8`, `1.8.0`, and `1.7.0` / `1.7` stay valid. Home Assistant plugin stays **0.5.0**. The installable agent adapter stays **0.3.1**. The public demo stays the **1.9.0** image.

Operator how-to: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Self-backup](https://piherder-docs.hacknow.info/operations/self-backup/) · [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/). Technical record: [PLAN_v1.10.0](PLAN_v1.10.0.md). Maintainer QA: [QA_v1.10.0](QA_v1.10.0.md).

---

## What’s new

### OneDrive is a backup copy you can select

Settings → PiHerder backup → **Copy the backup drive** → **OneDrive**. It is its own row beside Google Drive and a LAN share. **Test**, **Folders**, **Edit**, and **Remove** work the same way. **Test** lists the folder and does not copy. **Copy now**, a saved schedule, and the follow-up after a host backup can use this row. **Remove** deletes that row only. Files already in OneDrive stay there.

The copy lands in a folder on the default drive of the Microsoft account you sign in with. A Microsoft 365 personal plan already includes that storage. It does not include a directory for the app. A personal account with no Azure tenant lands in **Microsoft Services**, and App registrations will not open. Finish **Start free** at [azure.microsoft.com/free](https://azure.microsoft.com/free) with that same account, then register the app only when the directory is **Default Directory**. Microsoft may ask for a card. The app registration does not start a charge.

Register the app for **any organizational directory and personal Microsoft accounts**. On **Manifest**, set `requestedAccessTokenVersion` under `api` to `2` before you change that account type. Redirect URI is **Web**, and it is the URL shown in the PiHerder dialog. Paste the **Application (client) ID** and the secret **Value**. The **Secret ID** is not the secret. Graph permissions are delegated `User.Read`, `Files.ReadWrite`, and `offline_access`. The client secret and the sign-in stay encrypted. They are not written to the job log.

A work or school account that already has a directory skips the free signup and uses the same account type.

Wiki: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/).

### The NAS copy and the herder’s own archive

**Copy now**, the schedule, and the follow-up after a host backup were already in 1.9. This release signs that walk for a real SMB share and for Google Drive. A failed copy fails the copy job. It does not change the host’s last backup time.

**Also copy each new self-backup** is off until you check it, on Drive, OneDrive, or the share. After a successful self-backup, that one `.tar.gz` is copied to a `herder/` folder on the destination. **Copy off this host** sends an archive that is already on disk. The local file stays if the copy fails. Jobs calls that hop **Self-backup copy**. It does not take the **Copy now** slot, and a folder copy does not block it.

A self-backup that stays **pending** for 30 minutes is marked failed. That raises the critical **PiHerder self-backup failed** alert, linked to the job. A backup that is already **running** is left alone. The next **Run backup** can start after the pending row is failed.

The public demo does not upload.

Wiki: [Backups](https://piherder-docs.hacknow.info/day-to-day/backups/) · [Self-backup](https://piherder-docs.hacknow.info/operations/self-backup/).

### An agent can sign in in the browser

Hosted `POST /mcp` still accepts a pasted `ph_` token. It can also sign the agent in through the browser. PiHerder shows **Allow this agent**. You approve read, and jobs, edit, or files if you want those. The access token lasts one hour and starts with `ph_oa_`. It is listed under Settings → API management, so you can revoke it. It works on `POST /mcp` only. `/api/v1` rejects it. A pasted `ph_` token still works on `/api/v1`.

Cursor shows one HTTP server for each URL. A second entry with the same `/mcp` address does not appear. For the browser path, keep one entry and leave `Authorization` off. A pasted bearer header skips the sign-in. In Grok, set the URL with no header, then in `/mcps` press `r` and `i`.

`uvx piherder-mcp` does not use this sign-in. It still calls `/api/v1` with `PIHERDER_TOKEN`. Adapter **0.3.1** is unchanged. Move, undo, nmap, the console, token admin, down, and remove stay off the tool list. The public demo does not complete this sign-in.

Wiki: [Agents (MCP)](https://piherder-docs.hacknow.info/operations/mcp/).

### Reports can hide the six history cards

`/reports` has a **Cards** row for the same six history cards. Show or hide each one from that row. Pin, up and down, and per-card Hide stay. There are no new card types.

### A template page lists its fleet

Open a template. **On the fleet** lists the hosts and stacks recorded from that template. The catalog card shows how many stacks use it. A stack from a different template is not on that list.

---

## Defaults

| | Default |
|--|---------|
| Move a service | **on**. Set `PIHERDER_SERVICE_MIGRATE=false` to hide it |
| Google Drive copy | off until you connect an account |
| OneDrive copy | off until you connect an account |
| LAN share copy | off until you save a share |
| Copy each new self-backup | off until you check it on that destination |
| Agents (MCP) | off until you paste a token or approve a browser sign-in. No second container |
| Catalog in the nav | **on** |

---

## Upgrade from 1.9.0

There is one new database revision, for the agent sign-in tables. The image applies it on startup.

1. Take a full DR self-backup. Keep `PIHERDER_MASTER_KEY`.
2. Pull `bjorngluck/piherder:1.10.0` (or `1.10` / `latest`).
3. `docker compose pull && docker compose up -d`. Recreate **web** and **celery-worker**. The app code is not a folder on the host.
4. Confirm About / footer says **1.10.0**.
5. Move stays **on** unless `PIHERDER_SERVICE_MIGRATE=false`.
6. Home Assistant plugin **0.5.0** and `uvx piherder-mcp` **0.3.1** stay. Neither needs a new install for this release.
7. To copy to OneDrive, register the app as above, then **Connect Microsoft**. An account that connected under an older, wider Graph grant keeps that grant until you connect again.

---

## Honest limits

| | |
|--|--|
| OneDrive | The directory only holds the app. Files go to that account’s default drive. Personal accounts need the account type that includes personal Microsoft accounts, and token version `2`. |
| NAS and Drive copies | A second **Copy now** for the same destination still returns the copy that is already running. |
| Self-backup copy | Opt-in. The local `.tar.gz` stays. Restore still reads the local file, not Drive, OneDrive, or the share. |
| Agents | Browser sign-in is hosted `POST /mcp` only. `ph_oa_` is rejected on `/api/v1`. Stdio still uses a long-lived `ph_` token. |
| Reports | The six history cards only. No new card types. |
| Public demo | Stays **1.9.0** from `main`. It does not upload, and it does not complete agent sign-in. |
| Not this release | A host that writes straight to Drive, OneDrive, or the NAS, with nothing landing on `/backups` first. That decision is **v1.11.0**. |

---

## For the repository

`.github/dependabot.yml` turns on Dependabot security updates. Version-update pull requests stay off. The alerts that match PyJWT **2.15.1** and urllib3 **2.8.0** are closed.

[piherder-ha](https://github.com/bjorngluck/piherder-ha) and [piherder-mcp](https://github.com/bjorngluck/piherder-mcp) have Dependabot, a `SECURITY.md`, and `main` branch protection. Those files are not in this image.
