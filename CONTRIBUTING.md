# Contributing to PiHerder

Thanks for your interest in PiHerder! The project is maintained by **Bjorn Gluck** as the primary owner and approver, but contributions are very welcome now that it is open source.

## How collaboration works

| You can | Maintainer |
|---------|------------|
| Open **Issues** (bugs, features, questions) | Triages, labels, prioritizes |
| Discuss in Issues / Discussions | Decides roadmap fit |
| Open **Pull Requests** | Reviews, requests changes, merges or closes |
| Fork and experiment | Fully encouraged under [MIT License](LICENSE) |

**Only the maintainer merges to `main`** and cuts releases. Opening a PR is a proposal.

## License

All contributions are offered under the **[MIT License](LICENSE)**. By submitting a PR you confirm you have the right to contribute the code and agree it may be distributed under the MIT license.

## Getting Started

- Read the [README](README.md) and [SPEC.md](SPEC.md) for project goals and design principles.
- Latest release: [v0.6.0](docs/RELEASE_v0.6.0.md) (RC2). Product next: **v0.7.0** wizard + screenshots — [PLAN_v0.6.0.md](docs/PLAN_v0.6.0.md) (frozen).
- Look for issues tagged `good first issue` or `help wanted`.

## Documentation

Operator docs live in **`wiki/`** (MkDocs → [operator docs](https://piherder-docs.hacknow.info/)). Maintainer release notes live in **`docs/RELEASE_v*.md`**.

**Strategy (summary):**

| Do | Don’t |
|----|--------|
| Update the **existing** wiki page when a feature ships | Fork a full wiki per minor version |
| Prefer **same PR** as the code for operator-facing changes | Leave docs for “later” on UX/API changes |
| Note *Available from **vX.Y.Z*** only where versions differ | Invent parallel guides for every release |
| Put upgrade/breaking narrative in **RELEASE** notes at tag time | Put full PLAN/SPEC checklists in the operator nav |

Full conventions, screenshot workflow, and **v1.0.0 docs freeze** expectations:  
[wiki/developers/contributing-docs.md](wiki/developers/contributing-docs.md) (also on the live docs site under **Developers → Contributing docs**).

## Issues

**Good issues include:**
- Clear title and description
- Expected vs actual behavior
- Version / commit / environment details
- Steps to reproduce
- Relevant logs (redact secrets: master key, tokens, private keys)

Security issues: see [SECURITY.md](SECURITY.md) — do not open public issues for vulnerabilities.

Feature requests are welcome and will be considered against the roadmap.

## Pull Requests

1. Fork the repo and create a branch.
2. Prefer small, focused PRs.
3. Include a clear description of what and why; link related issues.
4. Update tests and **documentation** where relevant (wiki + RELEASE notes for user-visible change).
5. Run `pytest` locally or via CI; for wiki edits run `mkdocs build --strict` when you can.
6. Be responsive to review comments.

### Dependencies

Dependency versions are locked for reproducible builds. After changes:
- Run `./scripts/refresh-lockfiles.sh` (requires [uv](https://docs.astral.sh/uv/))
- Commit all updated lock files.

### What we look for
- Fits project scope and principles (auditable actions, encrypted secrets, Compose-first, etc.)
- Clean code, tests for new behavior, updated docs
- No unnecessary dependency changes without discussion

## Questions & Community

Open an Issue (or use Discussions when enabled).

Thank you for helping make PiHerder better!
