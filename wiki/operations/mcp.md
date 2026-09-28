# Agents (MCP)

## What this is

PiHerder speaks [MCP](https://modelcontextprotocol.io) on the **same host and port** as the web app. The path is **`/mcp`**. Mint a `ph_` API token, paste the URL and a Bearer header into Cursor, Claude Code, Codex, or Grok. There is no second process to run.

The older stdio program [bjorngluck/piherder-mcp](https://github.com/bjorngluck/piherder-mcp) is still there for an air-gapped laptop that cannot open HTTP to the herder. It is optional. It is not in the PiHerder image.

## Why hosted

Operators were starting `uvx` on every agent machine and keeping `PIHERDER_URL` in sync. The herder is already up. `POST /mcp` uses the token API that already exists.

**Transport:** [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports) (the remote MCP transport Cursor, Claude Code, VS Code, and Codex use in 2026). The server is **stateless**. Each call is one `POST` and a JSON body. `GET /mcp` returns **405** — there is no long-lived SSE listen channel. A client that only speaks the old HTTP+SSE transport should use the stdio fallback.

**Auth:** the same `Authorization: Bearer ph_…` token as `/api/v1`. Same scopes, same IP allowlist, same expiry. The token is a header. It is not a query parameter. There is **no MCP OAuth** in this cut. Clients that ignore a static Bearer header and only complete an OAuth login should use stdio.

PiHerder does not advertise an OAuth discovery document. A `401` is `WWW-Authenticate: Bearer` with no `resource_metadata` URL, so Cursor keeps the header you pasted instead of opening a browser login.

## Mint

Admin → Settings → **API management** → Create new token → **MCP agent**.

The name fills in as `mcp-…` and only **read** stays checked. Add **jobs**, **edit**, or **files** if this agent should do more than look. Leave the IP allowlist empty for a roaming laptop.

After create or rotate, the banner shows the secret **once** and a **hosted** client config (URL + Bearer). Copy that. The collapsed **Local / air-gapped** block is `uvx piherder-mcp` if you need it. The plaintext is not shown again.

Set `PIHERDER_PUBLIC_URL` so the snippet uses your real origin (include `:8443` when that is how you publish). If it is empty, the snippet uses `https://piherder.example.com`, the copy block starts with a warning, and the Settings banner says the host is a placeholder. Replace it before pasting. An empty public URL is easy to miss: a browser `Origin` is then allowed only when it matches the request `Host`.

**Browser Origin.** Clients that omit `Origin` (Cursor, Codex, Claude Code, curl) are unchanged. `Origin: null` is rejected. An `http` or `https` `Origin` must match the request `Host` or the host in `PIHERDER_PUBLIC_URL`. A Bearer token does not bypass that. Non-http origins (an editor’s app scheme) are allowed. Production should set `PIHERDER_PUBLIC_URL` to the origin operators actually open.

## Client config

Replace the host. Keep the secret in the client’s secret store. Do not commit it. Do not put it in the URL.

**Cursor and Grok** — `.cursor/mcp.json` (Grok imports this file when Cursor MCP import is on):

```json
{
  "mcpServers": {
    "piherder": {
      "type": "http",
      "url": "https://piherder.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ph_…"
      }
    }
  }
}
```

`type` is `http` so Claude Code accepts the same block. Cursor uses `url` and sends the header.

**Claude Code** — project `.mcp.json` or the user `mcpServers` entry, same JSON. Claude Desktop’s remote connector UI often wants OAuth; if it will not take a Bearer header, use the stdio fallback below.

**Codex** — `~/.codex/config.toml`. The env var is the **raw** `ph_…` secret. Codex adds `Bearer` itself.

```toml
[mcp_servers.piherder]
url = "https://piherder.example.com/mcp"
bearer_token_env_var = "PIHERDER_TOKEN"
```

```bash
export PIHERDER_TOKEN='ph_…'
```

Call the herder through the published HTTPS port (Caddy **8443**, or **8888** for HTTP), not an unpublished app port, so the token’s IP allowlist sees your address.

## What a token can do

Tools match the public stdio adapter. Nothing from that list is deferred. Tools appear only for scopes on the token. A token **without `read` fails closed**: initialize returns an error and no tools are listed.

| Scope | Tools |
|-------|--------|
| `read` | `health`, `summary`, `list_servers`, `get_server`, `inventory`, `services`, `list_jobs`, `get_job`. Snapshots already in the database. |
| `jobs` | `trigger_job` for `backup`, `retention`, `os_patch`, `container_patch`, `os_update_check`, `container_update_check`. |
| `edit` | `set_features` for `backup`, `os_patch`, and `docker`. |
| `files` | `list_files`, `read_file`, `write_file`, `mkdir`, `rename_file`, `delete_file` in the fleet jail. |

`feature:*` scopes and the host’s feature flags still apply. A second start of an exclusive job returns **409** with the job that is already running. The tool result includes `http_status` and `already_active` (including a backup that is already running). Poll `get_job`. Do not start another.

`write_file` and `mkdir` require `p`, the same as the stdio adapter. `p` is the jail-relative directory. `""` is the jail root.

The bearer jobs POST accepts more types than this tool list (`host_reboot`, compose stack actions, `template_deploy`, `template_redeploy`). Those are not MCP tools. File bodies returned to the agent are capped around **256 KiB**.

Read tools set `readOnlyHint`. `set_features`, `trigger_job`, `write_file`, `mkdir`, `rename_file`, and `delete_file` set `destructiveHint`.

## What stays out

SSH, the web console, Move, undo, compose stack actions, template deploy, nmap, DNS, certificates, Settings, token create/revoke, and stale data cleanup. Privileged Files, zip, chmod, and recursive delete stay in the browser. The public demo is not a target (API tokens are off there, so `/mcp` is too).

## Local / air-gapped fallback

On the computer that runs the agent, when that computer cannot reach `/mcp`:

```bash
export PIHERDER_URL='https://piherder.example.com'
export PIHERDER_TOKEN='ph_…'
uvx piherder-mcp
```

If the package is not on PyPI yet:

```bash
uvx --from git+https://github.com/bjorngluck/piherder-mcp.git piherder-mcp
```

**Cursor** stdio shape:

```json
{
  "mcpServers": {
    "piherder": {
      "command": "uvx",
      "args": ["piherder-mcp"],
      "env": {
        "PIHERDER_URL": "https://piherder.example.com",
        "PIHERDER_TOKEN": "ph_…"
      }
    }
  }
}
```

`${PIHERDER_TOKEN}` in older samples is a human placeholder. Many hosts do **not** expand it. Paste the real secret, or set it in the environment the host already inherits.

The tool list is the same. A token without `read` makes the stdio process exit on stderr.

## Operating note

Copy into the client that needs a short rule (Cursor rule, Grok skill, `CLAUDE.md`, Codex `AGENTS.md`):

Call `summary` before changing anything. `trigger_job` only for the six types. On **409**, poll `get_job`. Files stay in the fleet jail. Do not invent SSH, Move, or a console. A token without `jobs`, `edit`, or `files` has no such tool.

## Known follow-up

The Settings create and rotate redirect still puts the new secret in `token_secret` on the query string so the one-time banner can render. That predates hosted MCP. The page removes it from the address bar on copy. It can still show up in the access log for that redirect. The MCP URL does not carry the token. Moving that flash off the query string is a separate change.

## Related

- [API tokens](api-tokens.md) · [Host Files](../day-to-day/host-files.md) · [Jobs](../day-to-day/jobs-audit-notifications.md)
- [Home Assistant](../integrations/home-assistant.md) is a different client (HACS, runs on HA).
- Public demo: [demo site](demo-site.md) — no API tokens, no MCP
