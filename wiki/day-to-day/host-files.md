# Host Files

## What this is

A **jailed SFTP browser** on each host: list one folder, download, upload (with a progress bar), create a folder, rename, delete (file or **empty** folder). It is not WinSCP, not the compose text editor, and not a backup job.

**Where:** host overview dest-card **Files** → `/servers/{id}/files`. Pin with ★ / jump host like Docker. Kill switch **`PIHERDER_HOST_FILES`** (default **off**). Operator+ only. Viewer never. Demo never.

The page is a **file-manager window**: folder tree on the left, file list on the right (like Files / Explorer / Finder). Double-click a folder to open it, double-click a file to download, right-click or **⋯** for rename/delete. Drag files onto the list to upload; new items appear in the same window (no full reload). Phone hides the tree and shows the list.

## End-to-end: drop a sidecar

1. Set `PIHERDER_HOST_FILES=true` in `.env` / compose and restart **web**.  
2. Open a host → dest-card **Files** (operator). Jail is `docker_base_dir` when Docker is on, else that user’s home.  
3. Click folders in the tree or double-click them in the list. **Upload** or drop `config.yml` into the current folder (confirm if the name exists).  
4. Optional: **Connect as… Privileged** (same elevate role as console) + TOTP, or reuse an existing console grant cookie — for paths outside the fleet jail (HAOS `/mnt/data`).  
5. Check **Audit** for `host_file_put` (path, bytes, sha256 — never the body).

## Why it exists

The web console is a PTY. Dropping a Frigate `config.yml`, a compose sidecar, or pulling a large log still meant `scp` / FileZilla. Files is that transfer, inside PiHerder, using the same SSH identities as the rest of the product.

## When to use it

| Use Files | Use something else |
|-----------|-------------------|
| Sidecar / YAML / log sitting next to a stack | [Compose editor](../docker/compose-edit.md) for `docker-compose.yml` / `.env` tabs (`.env` is redacted there until step-up) |
| One-off upload/download up to **512 MiB** (env can raise to 2 GiB) | [Backups](backups.md) for scheduled trees |
| HAOS `/mnt/data` via **privileged** Connect as… | Cert deploy (PEM paste) · template deploy |

`.env`, `*.pem`, and `id_rsa` **are listed and downloadable**. Wiki-warn only this freeze — treat Files as a byte pipe. Prefer the compose editor when you need redaction.

## Identities (Connect as…)

Default **fleet** (least-priv). Optional **privileged** — same Settings **who may elevate** as the console. Step-up is **Passkey first** (same as console) when you have one enrolled; authenticator TOTP is the fallback unless Settings requires passkey. An existing console grant cookie also unlocks privileged Files. Jobs stay on fleet. API is **fleet only**.

| Identity | Jail |
|----------|------|
| Fleet | `docker_base_dir` when Docker is on, else that user’s home (HAOS often `/root`). Never `/`. `.ssh` and OS trees (`/etc`, `/proc`, …) blocked. |
| Privileged | `/` minus `/proc` `/sys` `/dev` `/run`. The privileged key’s OS rights are the real ACL (root ⇒ almost anything). |

HAOS is **in**. SSH add-on SFTP works where that user can write. Fleet home may be too tight for `/mnt/data` — use privileged.

## Overwrite and delete

Upload onto an existing name: browser **confirm**, then tmp+rename (same as compose writes). Delete always confirms. Directories must be empty. No recursive delete, zip, chmod, or in-browser edit this freeze.

## API

Token scope **`files`** (not on by default). Fleet identity only. See [API.md](https://github.com/bjorngluck/piherder/blob/v1.3.0-dev/docs/API.md). Privileged Files stays in the browser (tokens have no 2FA).

## Env

| Variable | Default | Notes |
|----------|---------|--------|
| `PIHERDER_HOST_FILES` | `false` | Master enable. Compose injects this. |
| `PIHERDER_HOST_FILES_MAX_BYTES` | 512 MiB | Optional lock; ceiling 2 GiB. Do **not** put a default in compose. |

Large uploads stream through the herder (O(chunk) RAM) with an upload progress bar, then “Writing to host…”. If you front PiHerder with nginx, raise `client_max_body_size`. Caddy in the bundle has no small body cap.

Stale-data **Cleanup** does not purge Files (there is no Files table). Audit rows (`host_file_*`) follow normal Audit retention.

## Audit

| Action | Details (never the file body) |
|--------|-------------------------------|
| `host_file_list` | identity, directory, entry count |
| `host_file_get` / `host_file_put` | identity, path, bytes, sha256, overwrite on put |
| `host_file_mkdir` / `rename` / `delete` | identity, paths |

Failures store `status=error` and a reason code.

## Related

- [Web SSH console](web-ssh-console.md) — privileged is console **and** Files  
- [Docker overview](../docker/overview.md)  
- [HAOS hosts](haos-hosts.md)  
- [Settings](../operations/settings.md) — Files is **not** a Settings card  
