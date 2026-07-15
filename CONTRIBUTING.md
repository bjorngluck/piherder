# Contributing to PiHerder

Thanks for your interest. PiHerder is maintained by **Bjorn Gluck** as the sole project owner and approver.

## How collaboration works

| You can | Maintainer (Bjorn) |
|---------|---------------------|
| Open **Issues** (bugs, features, questions) | Triages, labels, prioritizes |
| Discuss in Issues / Discussions (when enabled) | Decides roadmap fit |
| Open **Pull Requests** for review | Reviews, requests changes, **merge or close** |
| Fork and experiment under [LICENSE](LICENSE) | Only merges into this repo |

**Only the maintainer merges to `main` and cuts releases.** Opening a PR is a proposal, not a commitment that it will land.

## License

Contributions are offered under the same terms as the project: **[PolyForm Noncommercial 1.0.0](LICENSE)**. By opening a PR you confirm you have the right to submit the change and agree it may be distributed under that license.

Commercial use of PiHerder is not granted by the public license; contact the author for a separate commercial grant.

## Documentation

- **Live wiki:** [https://bjorngluck.github.io/piherder/](https://bjorngluck.github.io/piherder/)  
- **Source:** [`wiki/`](wiki/) (MkDocs Material)

## Issues

**Good issues include:**

- Clear title  
- What you expected vs what happened  
- Version / commit / install path (compose tag, etc.)  
- Steps to reproduce  
- Logs (redact secrets: `PIHERDER_MASTER_KEY`, tokens, private keys)

**Security vulnerabilities:** do **not** open a public issue — see [SECURITY.md](SECURITY.md).

Feature requests are welcome; acceptance depends on roadmap fit ([SPEC.md](SPEC.md), [docs/ROADMAP_ECOSYSTEM.md](docs/ROADMAP_ECOSYSTEM.md)).

## Pull requests

1. **Fork** the repo (or branch if you have write access — most people won’t).  
2. Prefer small, focused PRs over large multi-topic changes.  
3. Describe **what** and **why**; link related Issues.  
4. Run what you can: `pytest`, and for docs `mkdocs build --strict`.  
5. Expect review comments; maintainers may edit or rework before merge.  
6. Do not expect merge without maintainer approval.

### Dependencies

Third-party versions are **locked** for reproducible RC/prod builds:

| File | Role |
|------|------|
| `pyproject.toml` | Declared mins / ranges |
| `uv.lock` | Full resolver lock (source of truth) |
| `requirements.lock.txt` | Pip pins + hashes (runtime + dev) — used by Docker & CI |
| `requirements.runtime.lock.txt` | Runtime-only pins (lean image option) |

After changing dependencies: run `./scripts/refresh-lockfiles.sh` (needs [uv](https://docs.astral.sh/uv/)) and commit **all** updated lock files. See [SECURITY.md](SECURITY.md) (Dependencies & supply chain).

### What we look for

- Fits project scope and design principles (auditable actions, secrets model, Compose-first)  
- No drive-by dependency or license changes without discussion; lockfiles updated when deps change  
- Tests for non-trivial behavior when practical  
- Docs updated when user-facing behavior changes  

### What may be closed without merge

- Large rewrites without prior Issue discussion  
- Features that conflict with locked decisions (e.g. default commercial secret backends)  
- PRs that strip copyright / license notices  
- Incomplete or unresponsive PR threads  

## Code of conduct (simple)

Be respectful. No harassment, spam, or bad-faith security disclosure. The maintainer may moderate Issues/PRs as needed.

## Questions

Open an Issue with the question label (or plain description). For private commercial licensing, contact the author outside the public tracker.
