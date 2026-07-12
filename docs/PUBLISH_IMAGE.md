# Publishing a PiHerder image (Docker Hub / GHCR)

**Status:** Process for multi-arch publish — target **v0.5.0 RC** (and later tags).  
**Related:** [ADMIN](https://bjorngluck.github.io/piherder/operations/upgrades/) · [wiki publish page](https://bjorngluck.github.io/piherder/developers/publish-image/) · live docs: https://bjorngluck.github.io/piherder/

Until an image is published, operators use:

```bash
docker compose up -d --build
```

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
   - Visibility: **Public** (matches public GitHub; PolyForm NC still applies to *software* terms — Hub is just distribution)
   - Short description: e.g. *Self-hosted Raspberry Pi / Linux fleet manager (backups, patch, Docker, templates)*
   - Full description: link to GitHub + docs:

     ```text
     https://github.com/bjorngluck/piherder
     Docs: https://bjorngluck.github.io/piherder/
     License: PolyForm Noncommercial 1.0.0
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
| `0.5.0` | Immutable release (match git tag `v0.5.0`) |
| `0.5` | Optional rolling minor |
| `latest` | Current stable RC/release |
| `0.5.0-dev` / `main` | Optional CI/dev only — avoid as default for operators |

---

## 3. Multi-arch build & push

Arm64 matters for Raspberry Pi hosts running the herder itself.

```bash
# From repo root, after docker login
export IMAGE=bjorngluck/piherder
export VERSION=0.5.0   # match release

docker buildx create --use --name piherder-builder 2>/dev/null || true
docker buildx inspect --bootstrap

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
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

Once published, operators can replace `build: .` with:

```yaml
web:
  image: bjorngluck/piherder:0.5.0
celery-worker:
  image: bjorngluck/piherder:0.5.0
```

Keep the same env vars and volumes as [docker-compose.yml](../docker-compose.yml).  
Install docs: https://bjorngluck.github.io/piherder/getting-started/install/

---

## 5. Checklist before a release push

- [ ] `pytest -q` green  
- [ ] Manual smoke: register, add server, backup, metrics, API token, template deploy  
- [ ] Git tag + [RELEASE notes](RELEASE_v0.4.0.md) (or `RELEASE_v0.5.0.md` at freeze)  
- [ ] `pyproject.toml` version matches image tag  
- [ ] Multi-arch `docker buildx` push to Docker Hub  
- [ ] Hub repo description + docs/GitHub links  
- [ ] Optional: GitHub Release + GHCR mirror  
- [ ] [SECURITY.md](../SECURITY.md) still accurate  

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
