# Agent notes

These notes are for coding agents working in this repository. Operator setup stays in [CONTRIBUTING.md](CONTRIBUTING.md) and the wiki.

## Public-repo hygiene

Anything committed here is public. Use generic names in code, tests, docs, and commit messages.

- Hosts: `host-a`, `host-b`, `host-a.example.com`, `example.com`.
- Addresses: RFC 5737 documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`).
- Do not use real fleet hostnames, real addresses, container ids, or internal domains.
- Do not use `.local` or `.internal` hostnames in examples.
- Do not add em dashes or en dashes in new prose. Use a period, a comma, or parentheses.

## Current train

`v1.14.0-dev` is the open train. Package stays `1.13.0`. Nothing is built. `v1.13.0` is the current release. Host tool bootstrap is `POST /servers/{id}/host-deps/bootstrap` and is not on `/api/v1` or `/mcp`.

## Opening a version train

Before locking `docs/PLAN_vN.md`, list every living backlog item and wait for a vote: Must, Should, Discover, Later, or Out. Do not assign tiers first. Open issues that the current release already implements stay off the new train. The rest of the open issues go on the ballot. Items removed on 2026-10-27 stay off future ballots. That list is in [docs/PLAN_v1.14.0.md](docs/PLAN_v1.14.0.md). A pull request, release notes, tag, and Hub publish wait until asked.

## Opening a version pull request (maintainer)

`gh` on a maintainer machine may not be logged in. That is not a blocker and it is not the release path. Do not stop to run `gh auth login`.

[v1.5.0 pull request #5](https://github.com/bjorngluck/piherder/pull/5) was opened with the Grok GitHub connector, tool `github__create_pull_request`. The catalog name is `github__create_pull_request`, not `grok_com_github__create_pull_request`.

1. Write `docs/PR_vN.md` (title, base `main`, head `vN-dev`, test plan, ship steps). Push the train branch first.
2. Discover the tool (`search_tool` query `github__create_pull_request`) and call it. Fields: `owner` `bjorngluck`, `repo` `piherder`, `title`, `head`, `base` `main`, `draft` true until the QA gates in `docs/QA_vN.md` are closed, `body` the text of `docs/PR_vN.md`.
3. Leave the pull request unmerged. Merge, the `v*` tag, the GitHub Release, and the Hub push are a separate ask. PiHerder does not file a GitHub Issue for a version.

If `search_tool` returns no `github__` tool, the connector is not attached to the session. A fresh `grok -p` session can have the same gap (seen 2026-09-21 while opening v1.6). There is no local GitHub token to fall back on. Do not invent an Issue, and do not claim the pull request exists.
