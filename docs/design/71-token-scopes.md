# Token scopes for destructive actions

Design for issue #71. This document does not change the token code.

## Current model

An API token is one row. The plaintext is shown once. The database stores a hash, a short prefix, and a comma-separated `scopes` string. Empty feature scopes mean every feature. A token can also carry an IP allow list and an expiry.

Capability scopes:

| Scope | What it allows |
|-------|----------------|
| `read` | List and read. Other capability scopes add `read` when they are saved |
| `jobs` | Queue host jobs such as backup, patch, reboot, and Docker prune |
| `edit` | Change host feature flags. Also the current gate for unused-volume prune |
| `files` | Host file list, read, write, mkdir, rename, and delete |
| `admin` | Read users, tokens, and the audit log. It is not implied by `read` |

Feature scopes are `feature:backup`, `feature:os`, and `feature:docker`. They narrow `jobs` and `edit`. They do not grant a capability on their own. A token with no `feature:*` scope is not feature-restricted. The host's own feature flags still apply.

Defaults for a new automation token are `read` and `jobs`. An MCP approval starts at `read`. Hosted MCP tools declare which capability they need. `prune_volumes` is an `edit` tool.

Unused-volume prune is `POST /api/v1/servers/{id}/docker/volumes`. `dry_run` true lists dangling volumes and deletes nothing. It requires `edit` (and `feature:docker` when the token is feature-restricted). `confirm` true runs `docker volume prune -f`. That path uses the same `edit` check. There is no separate opt-in. An `edit` token can therefore delete unused volumes. The same is true of hosted `prune_volumes` with `confirm` true.

`files` can already delete a host file. `jobs` can already queue a reboot, an OS patch, or a Docker prune job. Those are destructive, and they are outside this issue's first cut.

## Proposed change

Add one capability scope, `destructive`.

- `dry_run` true on the volume route stays `edit`.
- `confirm` true requires `destructive` as well as `edit`.
- Hosted `prune_volumes` with `confirm` true requires `destructive`. A dry run does not.
- Saving a token does not add `destructive` because some other scope is present. `edit` does not imply it. `admin` does not imply it.
- Existing rows are left as they are. An `edit` token issued before this change does not gain `destructive`.
- The settings checkbox is off unless the operator turns it on.
- An MCP consent screen lists `destructive` on its own line. Approving `edit` does not approve it.
- A token that sends `confirm` true without the scope gets 403. The body does not include the volume names.

`destructive` is not a feature scope. `feature:docker` still applies when the token is feature-restricted.

## Migration

No column change. `scopes` stays a comma-separated string. `normalize_scopes` accepts `destructive` and does not insert it into `DEFAULT_SCOPES` or `MCP_DEFAULT_SCOPES`.

Existing tokens keep working for `edit`, including volume dry runs. They start failing the confirm path on the day the check ships. That is the point of the change. Operators who want confirm must edit the token and turn the scope on. The plaintext is not shown again. There is no rewrite job and no expiry of current tokens.

OAuth access tokens that were minted as API tokens follow the same rule. A refresh does not add `destructive`. The operator approves it in a new consent, or turns it on for that token in Settings.

Docs, the scope help text, and the hosted tool description name the extra scope on the confirm path. The stdio adapter and the Home Assistant plugin do not list `prune_volumes` today. When they do, they must send the scope through and must not hide confirm behind `edit`.

## Open questions

1. Is the scope name `destructive`, or a narrower name such as `volumes:prune`? A narrow name matches this issue. A wide name can cover later deletes without another scope.
2. Should `files` delete, the Docker prune job, stack removal, and host reboot move under the same scope later, or stay on `files` and `jobs`?
3. Should a feature-restricted token need both `destructive` and `feature:docker`, which this design says yes to?
4. Should the confirm path record the scope name on the audit row, so a later review can see that the token had opted in?
5. The adapter and the plugin are other repositories. This design does not change them. Who updates those clients in the same release as the herder check?
