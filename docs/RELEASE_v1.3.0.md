# PiHerder v1.3.0

**Status:** **Draft** — feature freeze 2026-08-22 · **not tagged** (Hub / `latest` remain **1.2.0**)  
**Git branch:** `v1.3.0-dev`  
**Package / image version at tag:** `1.3.0` (still **1.2.0** on the branch until the bump)  
**Theme:** Operator-owned policy · scale lists · fleet + privileged SSH · opt-in command audit · history Reports · confined Host Files (flag off)  
**Baseline:** [v1.2.0](RELEASE_v1.2.0.md)  
**Plan:** [PLAN_v1.3.0.md](PLAN_v1.3.0.md) · honesty **§0a**  
**QA:** [QA_v1.3.0.md](QA_v1.3.0.md) (maintainer — not the operator wiki)

This file is the operator-facing freeze note. **Do not** describe 1.3 Files as list/get/put-only, or say “WinSCP is deferred” as if there is no file manager. Tag text at ship time should keep this section.

---

## Where the plan bent (not broken)

Security architecture held. Original **scope** was rewritten in-train. Full table: [PLAN §0a](PLAN_v1.3.0.md#0a-where-the-plan-bent-not-broken).

### Host Files (Stream F)

**Kickoff** was thin: confined **list/get/put**, dest only, no mkdir/delete as Must.

**Freeze shipped a real manager** (kill switch still **`PIHERDER_HOST_FILES=false`**): browse, upload/download with progress (default 512 MiB, Settings up to 32 GiB), mkdir, rename/move, recursive delete, UTF-8 edit, zip/unzip (zip **on the host**), chmod/chown, name + content search, image/hex preview, folder upload, thin Docker volumes + `docker cp`. Hero: **Limited access** (fleet) / **Elevated access** (privileged).

That is product expansion. It is **not** a WinSCP clone (no dual-pane, no zmodem, not a backup job, not the compose editor).

**Architecture that still held:**

| Control | 1.3 behaviour |
|---------|----------------|
| Jail | Fleet: docker project folder or home, never `/`. Privileged: `/` minus `/proc` `/sys` `/dev` `/run`. `..` / zip-slip refused |
| Identities | Fleet vs privileged stay separate; jobs stay on fleet |
| Secrets | `.env` / PEM / keys **list**; open / edit / download / preview / content-search need 2FA |
| API | Token scope `files` is fleet list/get/put + limited mkdir/rename/empty-delete. Privileged verbs stay **UI + step-up** |
| Flag / demo | Default **off**. Demo never exposes a real tree |
| RAM | Stream chunks — not full-file loads on the herder |
| Audit | `host_file_*` path + bytes + hash — **never** bodies |

Richer Files **token** API is under consideration for **v1.4+**, not this tag.

### Reports (Stream N)

**Kickoff** was fleet-health **widgets** / a custom dashboard.

**Freeze rejected status portlets** and shipped **history Grafana cannot see** at `/reports` (backups dest, OS patches, LAN live-per-day, Docker deploys, console sessions). That is a better fit for data PiHerder already stores. It is **not** a second Grafana. Custom layout (**N3**) stays Cap.

### Command audit (Stream W-audit)

**Kickoff** was Discover → optional ship. **Promoted Deep the same day.**

Controls match the brief: default **off**, Fernet table, viewer denied, privileged **warns when off** (still allows), optional require-on-every-session, demo never stores.

**Redaction is heuristic and imperfect.** Password prompts and some tokens are stripped; `read -s`, editors, `sudo`, and secrets pasted as arguments can still land in the log. Do not treat a transcript as secret-free. Wiki: [Web SSH console](../wiki/day-to-day/web-ssh-console.md#command-audit-v13).

---

## What else is in 1.3 (unchanged from the signed streams)

| Stream | Operator-facing |
|--------|-----------------|
| **P + T** | Settings → Security: password rules, force-2FA scope + grace 0–60 days, step-up windows. Unlink / passkey revoke accept any enrolled 2FA |
| **W-cfg** | Settings → Console: idle / max / slots / ticket / park / bind / scrollback. Kill switch stays env `PIHERDER_SSH_CONSOLE` |
| **W-id** | Fleet + privileged SSH identities; console **Connect as…**; Settings who may elevate |
| **L** | Pager + search on Servers, Docker stacks, discovery list |
| **A** | Alert policy (severity / mute / debounce) + map/discovery types |
| **Settings hub** | General cards + Edit modals (phone sheets) |

**Still Cap (not this tag):** host `tmux`/`screen`, fine-grained roles, custom dashboard, CSP nonces, ACME-in-herder, extra branding.

**v1.4 (not this tag):** service migration host→host.

---

## Freeze bugfix: web after host reboot

`web` had no Compose `restart` policy (Docker default **`no`**). After a host reboot, **db / redis / celery / caddy** came back (`unless-stopped`) and the UI stayed down until someone started `piherder-web`. **web** now matches the rest of the stack. Recreate once: `docker compose up -d`. Confirm: `docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' piherder-web` → `unless-stopped`. Docker itself must be enabled at boot.

---

## Flags (defaults)

| Env | Default | Notes |
|-----|---------|--------|
| `PIHERDER_SSH_CONSOLE` | **false** | Live PTY |
| `PIHERDER_HOST_FILES` | **false** | Files manager |
| Command audit | **off** (Settings) | Heuristic redaction |

---

## Upgrade (when tagged)

1. Full DR self-backup. Keep `PIHERDER_MASTER_KEY`.  
2. Pull image / checkout tag. Alembic **`040_ssh_identities`** · **`041_console_transcripts`**.  
3. Compose no longer injects defaulted `PIHERDER_SSH_CONSOLE_*` — Settings apply unless you lock env.  
4. Smoke: Security · Console · Reports · optional Files if you turn the flag on.

Operator how-to: [wiki upgrades](../wiki/operations/upgrades.md). Maintainer clicks: [QA_v1.3.0.md](QA_v1.3.0.md).

---

*Draft until tag. Hub `1.2.0` / `1.2` / `latest` stay production until then.*
