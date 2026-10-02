# Release sync — piherder, piherder-mcp, piherder-ha

The three production repos name each other. A tag in one repo is not done until the other two READMEs show that version. Do this in the same turn as the tag. Do not wait for a later prompt.

Local clones:

- `/home/bjorn/docker/piherder`
- `/home/bjorn/piherder-mcp`
- `/home/bjorn/piherder-ha`

## Before the tag

1. The README that will be published names **this** version as the current package. PyPI shows the README that was inside the upload. A later commit does not change that page.
2. Do not write that PyPI, `uvx`, or Shields "still serves" the previous version, or that the old package remains current "until the tag is pushed."
3. Version badges are hand-typed: `badge/release-v1.8.1`, `badge/pypi-v0.3.1`, `badge/adapter-v0.3.1`, `badge/plugin-v0.5.0`, `badge/MCP-v0.3.1`, `badge/PiHerder-v1.8.1`. Do not use `img.shields.io/pypi/v/` or `img.shields.io/github/v/release/`. Those URLs stay on the previous version for hours, including on the PyPI page.
4. Each README's badge row names the other two production versions.
5. From this repo, after `git fetch` in all three clones:

```bash
python3 scripts/check_release_sync.py
```

That reads `origin/main` of each clone. It checks the version constants, the hand-typed badges, and that hosted `MCP_JOB_TYPES` and the adapter `JOB_TYPES` are the same set. Fix what it prints. Then tag.

A new job type ships on the herder first. Tag the adapter only after `origin/main` of the herder accepts that type. Tagging the adapter first makes `uvx` call a herder that returns 400.

## After the tag

Run:

```bash
python3 scripts/check_release_sync.py --shipped
```

`--shipped` also requires the newest `v*` tag on each `origin/main` to equal that repo's version constant. If this tag changed a version the other READMEs display, update those READMEs, push them, fetch, and run the check again before calling the release done.

The plugin stays a separate HACS repo. It is not inside the PiHerder image.
