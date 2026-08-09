# Publishing a PiHerder image (Docker Hub / GHCR)

**Status:** Docker Hub **live** — [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) (public). Multi-arch **linux/amd64 + linux/arm64**. Production line **v1.1.1**.  
**Related:** [ADMIN](https://piherder-docs.hacknow.info/operations/upgrades/) · [wiki publish page](https://piherder-docs.hacknow.info/developers/publish-image/) · live docs: https://piherder-docs.hacknow.info/

Official compose pulls the published image:

```bash
docker compose up -d
# optional pin:
# PIHERDER_IMAGE=bjorngluck/piherder:1.1.1 docker compose up -d
```

**Dependency pins:** the image installs from committed `requirements.lock.txt` (`pip install --require-hashes`). Bump deps with `./scripts/refresh-lockfiles.sh` before a release build so Hub tags match the lockfile in the git tag.

---

## 1. Docker Hub account (one-time)

1. Create / sign in: [https://hub.docker.com](https://hub.docker.com)  
2. Confirm email; pick username (planned image: **`bjorngluck/piherder`** — username must own that namespace).  
3. **Account Settings → Security → New Access Token**
   - Description: `piherder-publish`
   - Permissions: **Read, Write, Delete** (or Read & Write)
   - Copy the token once (treat like a password)
4. **Create repository** (if it does not exist):
   - Name: `piherder`
   - Visibility: **Public** (matches public GitHub; software is **MIT**)
   - Short description: e.g. *Self-hosted Raspberry Pi / Linux fleet manager (backups, patch, Docker, templates)*
   - Full description: link to GitHub + docs:

     ```text
     https://github.com/bjorngluck/piherder
     Docs: https://piherder-docs.hacknow.info/
     License: MIT
     ```

5. On the build machine:

   ```bash
   docker login -u bjorngluck
   # password = Access Token (not your Hub password)
   ```

### Optional: GHCR instead of / in addition to Hub

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u bjorngluck --password-stdin
# image: ghcr.io/bjorngluck/piherder
```

---

## 2. Image names & tags

| Registry | Image |
|----------|--------|
| Docker Hub | `bjorngluck/piherder` |
| GHCR | `ghcr.io/bjorngluck/piherder` |

| Tag | Meaning |
|-----|---------|
| `1.1.1` | Immutable release (match git tag `v1.1.1`) |
| `1.1.0` | Prior 1.1 patch pin (still valid) |
| `1.1` | Rolling minor |
| `1.0` | Prior production minor (optional pin) |
| `0.9.0` | Prior line (historical) |
| `0.9` | Optional rolling minor |
| `latest` | Current stable RC/release |
| `main` / `*-dev` | Optional CI/dev only — avoid as default for operators |

---

## 3. Multi-arch build & push

Arm64 matters for Raspberry Pi hosts running the herder itself.

```bash
# From repo root, after docker login
export IMAGE=bjorngluck/piherder
export VERSION=1.1.1   # match release

docker buildx create --use --name piherder-builder --driver docker-container 2>/dev/null || true
docker buildx use piherder-builder
docker buildx inspect --bootstrap
# On arm64 hosts: ensure QEMU for amd64
# docker run --privileged --rm tonistiigi/binfmt --install all

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:1.1" \
  -t "${IMAGE}:latest" \
  --push \
  .
```

Single-arch test (local only):

```bash
docker build -t bjorngluck/piherder:local .
```

---

## 4. Compose with a published image

Official `docker-compose.yml` already uses:

```yaml
image: ${PIHERDER_IMAGE:-bjorngluck/piherder:latest}
```

for `web` and `celery-worker` when not building locally. Keep the same env vars and volumes.  
Install docs: https://piherder-docs.hacknow.info/getting-started/install/

To develop against local source again, temporarily restore `build: .` or point `PIHERDER_IMAGE` at a locally tagged build.

---

## 5. Checklist before a release push

- [x] Multi-arch `docker buildx` push to Docker Hub (`1.0.0` / `1.0` / `latest`)  
- [x] Hardened `.dockerignore` (no backups/certs/local data/git)  
- [x] `pyproject.toml` version matches image tag  
- [x] `pytest` unit pack green (fail-under 55; v1.0 suites)  
- [x] Manual smoke: auth recovery, trusted device, avatar, fleet surfaces (operator freeze)  
- [x] Git tag + [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md)  
- [x] Hub public repo + docs/GitHub links  
- [ ] Optional: GitHub Release UI notes + GHCR mirror  
- [x] [SECURITY.md](../SECURITY.md) still accurate  

---

## 6. Secrets (never commit)

| Secret | Where |
|--------|--------|
| Docker Hub access token | Local `docker login` or CI secret `DOCKERHUB_TOKEN` |
| Hub username | `DOCKERHUB_USERNAME` |

Do **not** put Hub passwords in the git repo or wiki.

---

## GitHub Actions (later)

Optional CI job on tag `v*`:

1. `docker/login-action` with Hub secrets  
2. `docker/build-push-action` with `platforms: linux/amd64,linux/arm64`  
3. Tags from git tag  

Add when account + token exist and first manual push has worked once.


---

## v1.1.1 publish checklist (maintainer)

- [x] `pyproject.toml` = `1.1.1`
- [x] [RELEASE_v1.1.1.md](RELEASE_v1.1.1.md) finalized · Status **Tagged**
- [x] Fix: `run_in_threadpool` import in `server_ssh.py`
- [x] Multi-arch push: `1.1.1` / `1.1` / `latest` (amd64 + arm64) · digest `sha256:5a769302e8285d997438740a469284c6fa51f507868515d2e00bb3cd14701349`
- [x] Git tag `v1.1.1`

### Prior: v1.1.0

- [x] `APP_VERSION` / `pyproject.toml` = `1.1.0`
- [x] [RELEASE_v1.1.0.md](RELEASE_v1.1.0.md) finalized · Status **Tagged**
- [x] Unit / Docs / E2E green (PR #1 + `main` merge)
- [x] Multi-arch push: `1.1.0` / `1.1` / `latest` (amd64 + arm64) · digest `sha256:7256a9cea6ba403b33c28dd74d7176c3c6e890850419efd70e7b7fb02ad725c1`
- [x] Git tag `v1.1.0`

### Prior: v1.0.0

- [x] Tagged and published (`1.0.0` / `1.0` / `latest` at the time)