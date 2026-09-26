# Agents (MCP)

## What this is

A small program an operator runs **on the computer that runs the agent** (Cursor, Grok Build, Claude, or Codex). It speaks [MCP](https://modelcontextprotocol.io) over **stdio** and calls your herder’s existing Bearer API.

It is **not in the PiHerder image**, and **v1.6.0 does not ship it**. The program is [bjorngluck/piherder-mcp](https://github.com/bjorngluck/piherder-mcp) **0.1.0** (same shape as the Home Assistant plugin). Maintainer contract: [PLAN_v1.7.0.md](https://github.com/bjorngluck/piherder/blob/v1.7.0-dev/docs/PLAN_v1.7.0.md).

The herder does not grow an MCP port. Create the token in Settings → **API management**, then point the agent process at that herder.

## Why it exists

Cursor, Grok, Claude, and Codex can already call HTTP. A shared tool list keeps those four on one process. The package is not on PyPI yet, so the command is `uvx --from git+https://github.com/bjorngluck/piherder-mcp.git piherder-mcp`, with `PIHERDER_URL` and `PIHERDER_TOKEN` in that client’s env. The token is never committed. Samples below use `${PIHERDER_TOKEN}`.

Grok Build also loads a project `.cursor/mcp.json` when Cursor MCP import is on (the default), so one Cursor file covers Grok on that checkout. Codex does not read that file. Claude uses its own `mcpServers` block or a project `.mcp.json`.

## What a token can do

Tools appear only for scopes on the token. Startup reads `GET /api/v1/health`. A token without `read` fails closed. Details and curl equivalents: [API tokens](api-tokens.md).

| Scope | Tools |
|-------|--------|
| `read` | Health, fleet summary, servers, Docker inventory, service chips, jobs (list and detail). Snapshots already in the database. |
| `jobs` | Start `backup`, `retention`, `os_patch`, `container_patch`, `os_update_check`, `container_update_check`. `host_reboot` is a herder and Home Assistant job, not a tool here. |
| `edit` | Toggle `backup`, `os_patch`, and `docker` on a host. |
| `files` | Fleet-jail list, read, write, mkdir, rename, and delete a file or an empty directory. |

`feature:*` scopes and the host’s feature flags still apply. A second start of an exclusive job returns **409** with the job that is already running. The agent should poll that job.

File bodies returned to the agent are capped around **256 KiB**.

## What stays out

SSH, the web console, Move, undo, compose stack actions, template deploy, nmap, DNS, certificates, Settings, and token create/revoke. Privileged Files, zip, chmod, and recursive delete stay in the browser. There is no remote HTTP MCP listener on the herder. The public demo is not a target.

## Client samples

Same command in each client. Replace the URL. Keep the token in the client’s secret store or an env var you do not commit.

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "piherder": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/bjorngluck/piherder-mcp.git", "piherder-mcp"],
      "env": {
        "PIHERDER_URL": "https://piherder.example.com",
        "PIHERDER_TOKEN": "${PIHERDER_TOKEN}"
      }
    }
  }
}
```

**Grok Build** — `.grok/config.toml` (or rely on the Cursor file above):

```toml
[mcp_servers.piherder]
command = "uvx"
args = ["--from", "git+https://github.com/bjorngluck/piherder-mcp.git", "piherder-mcp"]
env = { PIHERDER_URL = "https://piherder.example.com", PIHERDER_TOKEN = "${PIHERDER_TOKEN}" }
```

**Claude Desktop / Claude Code** — `mcpServers.piherder` with the same `command`, `args`, and `env`, or a project `.mcp.json` in that shape.

**Codex** — `~/.codex/config.toml`:

```toml
[mcp_servers.piherder]
command = "uvx"
args = ["--from", "git+https://github.com/bjorngluck/piherder-mcp.git", "piherder-mcp"]
env = { PIHERDER_URL = "https://piherder.example.com", PIHERDER_TOKEN = "${PIHERDER_TOKEN}" }
```

## Instruction template

One short operating note, copied into the client that needs it:

- Cursor rule `.cursor/rules/piherder.mdc`
- Grok skill `skills/piherder/SKILL.md` (same text; Grok also reads the Cursor rule)
- A short copy in Claude’s `CLAUDE.md` and Codex’s `AGENTS.md`

The note says: call the fleet summary before changing anything; start only the six job types; on **409** poll the existing job; files stay in the fleet jail; do not invent SSH, Move, or a console; a token without `jobs`, `edit`, or `files` has no such tool.

## Related

- [API tokens](api-tokens.md) · [Host Files](../day-to-day/host-files.md) · [Jobs](../day-to-day/jobs-audit-notifications.md)
- [Home Assistant](../integrations/home-assistant.md) is a different client (HACS, runs on HA). A `read` token is sensors and the fleet card. `jobs` and `edit` add the operator cards. `host_reboot` is not an MCP tool.
- Public demo: [demo site](demo-site.md) — no API tokens, no MCP
