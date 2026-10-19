# History sweep (v1.12)

**2026-10-19.** The working tree on `v1.12.0-dev` no longer carries the home path, the home subnet, or an internal hostname. The public docs host, the public demo host, and the demo login stay. The trusted-proxy ranges stay. This note does not rewrite published commits. No force-push.

A yes rewrites the blobs below. A no leaves them.

| Class | Where it still is |
|-------|-------------------|
| Home path | `origin/main` `1e03f266c0df6281ffcc599a1afdd76349da52c4`. Tags `v0.2.0` through `v1.11.0` (including `v1.1.1` and `v1.8.1`) |
| Home subnet | Same `origin/main`. Tags `v0.5.0` through `v1.11.0` (including `v1.1.1` and `v1.8.1`). Not on `v0.2.0`, `v0.3.0`, or `v0.4.0` |
| Internal hostname | Same `origin/main`. Every tag in the home-path row except `v0.5.0` |

The push denylist is `.github/workflows/push-denylist.yml`. Patterns stay in the GitHub secret `PIHERDER_PUSH_DENYLIST`, one literal per line. Until that secret is set, the workflow prints that the manual grep remains and exits 0. It does not echo the secret or the matched line. Do not put the patterns in the repo.
