# History sweep (v1.12)

**2026-10-19.** Published history was rewritten and force-pushed. The home path is now `/home/user`. The home subnet is `192.0.2.0/24`. Internal hostnames now use `example.com`. The public docs host, the public demo host, and the demo login stay. The trusted-proxy ranges stay.

`v1.12.0-dev` tip `6e7ffb69f08a33aecf9dff50037a98101cf3de78` has the same tree as the commit before the rewrite. `main` is `086400e119039047af64ac8810cfe1de3f6e446e`. Tags `v0.2.0` through `v1.11.0` (including `v1.1.1` and `v1.8.1`) moved with that rewrite. So did `gh-pages`, `v1.10.0-dev`, `v1.11.0-dev`, `cursor/v190-operator-qa-d71a`, and `dependabot/uv/uv-security-b9ed79a806`.

`main` branch protection rejects force-push again. Pull requests required, one review, and the pytest, playwright, and build checks are unchanged.

GitHub can keep unreachable objects, and a pull-request ref can still point at a pre-rewrite commit, until GitHub garbage-collects them. Local branches that were never on origin still hold the old objects. Do not push those.

The push denylist is `.github/workflows/push-denylist.yml`. Patterns stay in the GitHub secret `PIHERDER_PUSH_DENYLIST`, one literal per line. Until that secret is set, the workflow prints that the manual grep remains and exits 0. It does not echo the secret or the matched line. Do not put the patterns in the repo.
