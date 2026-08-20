# Host Files

## What this is

A **jailed SFTP file manager** on each SSH host: browse, upload/download (progress, 512 MiB), create folders, rename, **move**, **edit** UTF-8 text, **zip / unzip**, **chmod / chown**, **search** (names and file contents), **preview** images / hex, delete files or folder trees, and a thin **Docker** helper (named volumes + `docker cp` into the current folder). It is not WinSCP, not a backup job, and not the [compose editor](../docker/compose-edit.md) (no version history, no deploy).

**Where:** host overview dest-card **Files** → `/servers/{id}/files`. Pin with ★ / jump host like Docker. Kill switch **`PIHERDER_HOST_FILES`** (default **off**). Operator+ only. Viewer never. Demo never.

The page is a **file-manager window**: folder tree on the left, file list on the right. Click a **row** to select (Zip / Move / Delete). **Tap or click the name** to open a folder, edit text, or preview an image. On a phone, **long-press** selects and shows actions. Right-click or **⋯** for the rest. Drag files **or folders** onto the list (or a tree folder) to upload. Phone hides the tree.

The herder **keeps one SFTP session per host/identity for ~75 seconds idle** so folder clicks are not a new SSH handshake each time. Transfers use **1 MiB** buffers on a **dedicated SFTP connection** (browse stays on the pooled session). Do not prefetch/pipeline whole files — that stalled around ~12 MiB. Caddy must **not gzip** `application/octet-stream` and uses `flush_interval -1` so the browser download bar can move. Upload progress is two-phase: send to PiHerder, then write on the host. Traffic still goes browser → herder → host (not a raw LAN `scp`).

## End-to-end: drop a sidecar

1. Set `PIHERDER_HOST_FILES=true` in `.env` / compose and restart **web**.  
2. Open a host → dest-card **Files** (operator). Jail is `docker_base_dir` when Docker is on, else that user’s home.  
3. Click folders in the tree, or **tap the folder name** in the list. **Upload** or drop `config.yml` (confirm if the name exists). **Folder** uploads a local directory tree.  
4. Tap `config.yml` to edit — same monospace gutter / wrap / Tab indent as the [compose editor](../docker/compose-edit.md). **Ctrl/Cmd+S** saves (512 KiB UTF-8 cap).  
5. Optional: **Connect as… Privileged** (same elevate role as console) + Passkey (TOTP fallback) — for paths outside the fleet jail (HAOS `/mnt/data`, Docker volume `_data`).  
6. Check **Audit** for `host_file_put` (path, bytes, sha256 — never the body).

## Why it exists

The web console is a PTY. Dropping a Frigate `config.yml`, a compose sidecar, or pulling a large log still meant `scp` / FileZilla. Files is that transfer, inside PiHerder, using the same SSH identities as the rest of the product.

## When to use it

| Use Files | Use something else |
|-----------|-------------------|
| Sidecar / YAML / log sitting next to a stack | [Compose editor](../docker/compose-edit.md) for `docker-compose.yml` / `.env` tabs with history + deploy. `.env` is redacted there until step-up. |
| One-off upload/download up to **512 MiB** (env can raise to 2 GiB) | [Backups](backups.md) for scheduled trees |
| Zip a folder (save on the host or download) | — |
| Peek at a PNG / hex of a binary | Full media gallery (not in Files) |
| Copy a file out of a container into the jail | Service migration copy engine ([v1.4](https://github.com/bjorngluck/piherder/blob/v1.3.0-dev/docs/PLAN_v1.4.0.md)) |
| HAOS `/mnt/data` via **privileged** Connect as… | Cert deploy (PEM paste) · template deploy |

## Actions

| Action | How |
|--------|-----|
| **Edit** | Tap/click the **name**, or **Edit**. Overlay: line numbers, YAML-ish colours, Wrap, Tab indent, **Ctrl/Cmd+S**. Binary / larger than **512 KiB** opens **Preview** instead. Close warns if unsaved. |
| **Preview** | Images in-page (8 MiB). Other binaries: hex/ASCII peek + Download. |
| **Zip** | Select → **Zip**. Modal: **name**, **Save on the host** (this folder) or **Download**. Optional **delete the selected files** after success (confirm). Caps: 2000 files, depth 24, same byte cap as upload. |
| **Extract** | Select a `.zip` or tap it. Extracts **into the current folder**. `..` / absolute members refused (zip-slip). Existing names replaced after confirm. |
| **Delete** | Selection (files and folders). Folders go with their contents. Jail root cannot be deleted. |
| **Permissions** | Selection → **Permissions**. Octal + rwx checkboxes; **owner/group names** (`pi`, `www-data`) or numeric ids. Listing shows names when `getent` works. Recursive option for folders. Jail root cannot be changed. Fleet may chmod files it owns; **chown** and chmod of files you do not own need **privileged**. If that SSH user is not root: `sudo -n chmod` / `sudo -n chown` (no password prompt — add NOPASSWD, or Connect as root). HAOS root often has no sudo — plain chmod/chown is tried too. |
| **Search** | Toolbar box or **Ctrl/Cmd+F**. Case-insensitive **names** from the current folder down. Tick **Contents** to grep UTF-8 text (512 KiB/file, 80 content hits). Secret-ish files skipped in content search until 2FA unlock. Caps: 200 name hits, 2000 scanned. |
| **Move** | Select → **Move** (or drag onto a tree folder). Same jail, SFTP rename. Cannot move a folder into itself. Existing files confirm replace. |
| **Folder upload** | **Folder** or drop a local directory. Parents created (`mkdir -p`). `..` refused. Cap 2000 files. |
| **Secrets** | `.env`, `*.pem`, and key files still **list**. Open / edit / download / preview / content-search needs the same 2FA grant as privileged Files (Passkey preferred). Operators can unlock on **fleet** without being allowed to elevate. Prefer the compose editor when you need **redaction**. |
| **Docker** | When Docker is on: **Docker** lists named volumes (open `_data` if that path is in this jail — usually **privileged** `/`) and **Copy from container** (`docker cp` into the current folder). Container paths must not contain `..`. |
| **Select** | Click the **row** (not the name). Checkbox, Shift-click, Ctrl/Cmd-click, header checkbox, **Ctrl/Cmd+A**. Phone: **long-press** selects and opens actions. |

## Identities (Connect as…)

Default **fleet** (least-priv). Optional **privileged** — same Settings **who may elevate** as the console. Step-up is **Passkey first** when you have one enrolled; authenticator TOTP is the fallback unless Settings requires passkey. An existing console grant cookie also unlocks privileged Files **and** secret-ish files. Jobs stay on fleet. API is **fleet only**.

| Identity | Jail |
|----------|------|
| Fleet | `docker_base_dir` when Docker is on, else that user’s home (HAOS often `/root`). Never `/`. `.ssh` and OS trees (`/etc`, `/proc`, …) blocked. |
| Privileged | `/` minus `/proc` `/sys` `/dev` `/run`. The privileged key’s OS rights are the real ACL (root ⇒ almost anything). |

HAOS is **in**. SSH add-on SFTP works where that user can write. Fleet home may be too tight for `/mnt/data` — use privileged.

## Overwrite and delete

Upload onto an existing name: browser **confirm**, then tmp+rename (same as compose writes). Delete always confirms; folders are recursive. Unzip and zip-and-delete also confirm.

## API

Token scope **`files`** (not on by default). Fleet identity only. List / download / upload / mkdir / rename / delete-empty. Edit, zip, unzip, chmod, recursive delete, preview, Docker helpers, and privileged stay in the browser (tokens have no 2FA). A richer Files API is **under consideration for v1.4+**. See [API.md](https://github.com/bjorngluck/piherder/blob/v1.3.0-dev/docs/API.md).

## Env

| Variable | Default | Notes |
|----------|---------|--------|
| `PIHERDER_HOST_FILES` | `false` | Master enable. Compose injects this. |
| `PIHERDER_HOST_FILES_MAX_BYTES` | 512 MiB | Optional lock; ceiling 2 GiB. Do **not** put a default in compose. Also caps zip contents, unzipped size, and preview-adjacent transfers. |

Large uploads stream through the herder (O(chunk) RAM) with an upload progress bar, then “Writing to host…”. If you front PiHerder with nginx, raise `client_max_body_size`. Caddy in the bundle has no small body cap.

Stale-data **Cleanup** does not purge Files (there is no Files table). Audit rows (`host_file_*`) follow normal Audit retention.

## Audit

| Action | Details (never the file body) |
|--------|-------------------------------|
| `host_file_list` | identity, directory, entry count |
| `host_file_get` / `host_file_put` | identity, path, bytes, sha256, overwrite on put; zip download/save, unzip, docker cp, and text save count as get/put |
| `host_file_mkdir` / `rename` / `delete` | identity, paths; recursive delete includes file/dir counts; move is rename |
| `host_file_chmod` | identity, names count, mode, owner, group, recursive, changed, whether sudo was used |

Failures store `status=error` and a reason code (`secret_confirm` when 2FA is required for a secret-ish file).

## Related

- [Web SSH console](web-ssh-console.md) — privileged is console **and** Files  
- [Compose edit](../docker/compose-edit.md) — redacted `.env` / history / deploy  
- [Docker overview](../docker/overview.md)  
- [HAOS hosts](haos-hosts.md)  
- [API tokens](../operations/api-tokens.md)  
- [Settings](../operations/settings.md) — Files is **not** a Settings card  
