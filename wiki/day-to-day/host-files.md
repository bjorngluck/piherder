# Host Files

## What this is

A **jailed SFTP browser** on each host: list one folder, download, upload (with a progress bar), create a folder, rename, **edit text**, **zip / unzip**, **chmod / chown**, and delete (files or **folders with contents**). It is not WinSCP and not a backup job.

**Where:** host overview dest-card **Files** → `/servers/{id}/files`. Pin with ★ / jump host like Docker. Kill switch **`PIHERDER_HOST_FILES`** (default **off**). Operator+ only. Viewer never. Demo never.

The page is a **file-manager window**: folder tree on the left, file list on the right (like Files / Explorer / Finder). Click a **row** to select (for Zip / Move / Delete). **Tap or click the name** to open a folder or edit a file — no double-tap. On a phone, **long-press** selects and shows actions. Right-click or **⋯** for the rest. Drag files **or folders** onto the list (or a tree folder) to upload. Search names from the current folder. Move with **Move** or drag onto the tree. Destructive actions use a confirm modal; a loading overlay covers the window until the list refreshes. Phone hides the tree and shows the list.

The herder **keeps one SFTP session per host/identity for ~75 seconds idle** so folder clicks are not a new SSH handshake each time. The next few subfolders are prefetched into the tree. Transfers use **1 MiB** buffers on a **dedicated SFTP connection** (browse stays on the pooled session). Do not prefetch/pipeline whole files — that stalled around ~12 MiB. Caddy must **not gzip** `application/octet-stream` and uses `flush_interval -1` so the browser download bar can move. Upload progress is two-phase: send to PiHerder, then write on the host. Traffic still goes browser → herder → host (not a raw LAN `scp`).

## End-to-end: drop a sidecar

1. Set `PIHERDER_HOST_FILES=true` in `.env` / compose and restart **web**.  
2. Open a host → dest-card **Files** (operator). Jail is `docker_base_dir` when Docker is on, else that user’s home.  
3. Click folders in the tree or double-click them in the list. **Upload** or drop `config.yml` into the current folder (confirm if the name exists).  
4. Double-click `config.yml` (or **Edit**) to open the in-page editor — same monospace gutter / wrap / Tab indent as the [compose editor](../docker/compose-edit.md). **Ctrl/Cmd+S** saves (512 KiB UTF-8 cap).  
5. Optional: **Connect as… Privileged** (same elevate role as console) + Passkey (TOTP fallback) — for paths outside the fleet jail (HAOS `/mnt/data`).  
6. Check **Audit** for `host_file_put` (path, bytes, sha256 — never the body).

## Why it exists

The web console is a PTY. Dropping a Frigate `config.yml`, a compose sidecar, or pulling a large log still meant `scp` / FileZilla. Files is that transfer, inside PiHerder, using the same SSH identities as the rest of the product.

## When to use it

| Use Files | Use something else |
|-----------|-------------------|
| Sidecar / YAML / log sitting next to a stack | [Compose editor](../docker/compose-edit.md) for `docker-compose.yml` / `.env` tabs (`.env` is redacted there until step-up; Files is **not** redacted) |
| One-off upload/download up to **512 MiB** (env can raise to 2 GiB) | [Backups](backups.md) for scheduled trees |
| Zip a folder to download, or extract a zip in the jail | Recursive upload of a local tree (drop files one folder at a time) |
| HAOS `/mnt/data` via **privileged** Connect as… | Cert deploy (PEM paste) · template deploy |

`.env`, `*.pem`, and `id_rsa` **are listed, downloadable, and editable** if they look like UTF-8 text. Wiki-warn only this freeze — treat Files as a byte pipe. Prefer the compose editor when you need redaction.

## Editor, zip, unzip, multi-select

| Action | How |
|--------|-----|
| **Edit** | One file selected, or **tap/click the name**. Overlay reuses the compose editor feel: line numbers, YAML-ish colours, Wrap, Tab indent, **Ctrl/Cmd+S**. Binary / larger than **512 KiB** falls back to download. Close warns if unsaved. |
| **Zip** | Select → **Zip**. Modal asks for a **name**, then **Save on the host** (this folder) or **Download**. Optional **delete the selected files** after a successful zip (confirm). Caps: 2000 files, walk depth 24, same byte cap as upload. |
| **Extract** | Select one `.zip` (or double-click it). Extracts **into the current folder**. Paths with `..` or absolute members are **refused** (zip-slip). Existing names are replaced after confirm. |
| **Delete** | Selection (files and folders). Folders go with their contents. Jail root cannot be deleted. |
| **Permissions** | Selection → **Permissions**. Octal mode (and rwx checkboxes) plus **owner/group names** (`pi`, `www-data`) or numeric ids. Listing shows names when `getent` works. Recursive option when a folder is selected. Jail root cannot be changed. |
| **Search** | Box in the toolbar (or **Ctrl/Cmd+F**). Case-insensitive **name** match from the current folder downward (go to the jail root to search everything). Caps at 200 hits / 2000 scanned. Not file contents. |
| **Move** | Select items → **Move** (or drag onto a folder in the tree). Same jail only (SFTP rename). Cannot move a folder into itself. Existing files confirm replace. |
| **Folder upload** | **Folder** picks a local directory, or drag a folder onto the list / tree. Parents are created (`mkdir -p`). Paths with `..` are refused. Cap 2000 files. |
| **Select** | Click the **row** (not the name) to select. Checkbox, Shift-click, Ctrl/Cmd-click, header checkbox, **Ctrl/Cmd+A**. On a phone, **long-press** selects and opens actions. **Tap the name** (or folder in the tree) to open — no double-tap needed. |

chmod of files you own works on **fleet**. **chown** (and chmod of files you do not own) needs **privileged** Files. If that SSH user is not root, PiHerder runs `sudo -n chmod` / `sudo -n chown` (no password prompt). Add a NOPASSWD sudoers rule, or Connect as a root identity. HAOS root often has no sudo — plain `chmod`/`chown` is tried too.

Binary preview / `docker cp` stay out of this freeze.

## Identities (Connect as…)

Default **fleet** (least-priv). Optional **privileged** — same Settings **who may elevate** as the console. Step-up is **Passkey first** (same as console) when you have one enrolled; authenticator TOTP is the fallback unless Settings requires passkey. An existing console grant cookie also unlocks privileged Files. Jobs stay on fleet. API is **fleet only**.

| Identity | Jail |
|----------|------|
| Fleet | `docker_base_dir` when Docker is on, else that user’s home (HAOS often `/root`). Never `/`. `.ssh` and OS trees (`/etc`, `/proc`, …) blocked. |
| Privileged | `/` minus `/proc` `/sys` `/dev` `/run`. The privileged key’s OS rights are the real ACL (root ⇒ almost anything). |

HAOS is **in**. SSH add-on SFTP works where that user can write. Fleet home may be too tight for `/mnt/data` — use privileged.

## Overwrite and delete

Upload onto an existing name: browser **confirm**, then tmp+rename (same as compose writes). Delete always confirms; folders are recursive. Unzip also confirms, then tmp+rename per member.

## API

Token scope **`files`** (not on by default). Fleet identity only. List / download / upload / mkdir / rename / delete-empty. Edit, zip, unzip, and recursive delete stay in the browser. See [API.md](https://github.com/bjorngluck/piherder/blob/v1.3.0-dev/docs/API.md). Privileged Files stays in the browser (tokens have no 2FA).

## Env

| Variable | Default | Notes |
|----------|---------|--------|
| `PIHERDER_HOST_FILES` | `false` | Master enable. Compose injects this. |
| `PIHERDER_HOST_FILES_MAX_BYTES` | 512 MiB | Optional lock; ceiling 2 GiB. Do **not** put a default in compose. Also caps zip contents and unzipped size. |

Large uploads stream through the herder (O(chunk) RAM) with an upload progress bar, then “Writing to host…”. If you front PiHerder with nginx, raise `client_max_body_size`. Caddy in the bundle has no small body cap.

Stale-data **Cleanup** does not purge Files (there is no Files table). Audit rows (`host_file_*`) follow normal Audit retention.

## Audit

| Action | Details (never the file body) |
|--------|-------------------------------|
| `host_file_list` | identity, directory, entry count |
| `host_file_get` / `host_file_put` | identity, path, bytes, sha256, overwrite on put; zip download and unzip count as get/put |
| `host_file_mkdir` / `rename` / `delete` | identity, paths; recursive delete includes file/dir counts |
| `host_file_chmod` | identity, names count, mode, owner, group, recursive, changed, whether sudo was used |

Failures store `status=error` and a reason code.

## Related

- [Web SSH console](web-ssh-console.md) — privileged is console **and** Files  
- [Compose edit](../docker/compose-edit.md) — redacted `.env` / history / deploy  
- [Docker overview](../docker/overview.md)  
- [HAOS hosts](haos-hosts.md)  
- [Settings](../operations/settings.md) — Files is **not** a Settings card  
