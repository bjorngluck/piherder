# Publishing a PiHerder image (H0)

**Status:** Process doc — run when Docker Hub / GHCR credentials are available.

## Recommended tags

| Tag | Meaning |
|-----|---------|
| `0.2.0` | Immutable release |
| `0.2` | Latest patch in 0.2 line (optional) |
| `latest` | Current stable |

**Image names (pick one registry):**

- Docker Hub: `bjorngluck/piherder`
- GHCR: `ghcr.io/bjorngluck/piherder`

## Multi-arch build (example)

```bash
# From repo root, after login: docker login  (or ghcr.io)
export IMAGE=bjorngluck/piherder
export VERSION=0.2.0

docker buildx create --use --name piherder-builder 2>/dev/null || true
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push \
  .
```

Arm64 matters for Raspberry Pi hosts running the herder itself.

## Compose with a published image

Once published, operators can replace `build: .` with:

```yaml
web:
  image: bjorngluck/piherder:0.2.0
  # …
celery-worker:
  image: bjorngluck/piherder:0.2.0
  # …
```

Keep the same env vars and volumes as [docker-compose.yml](../docker-compose.yml).

## Checklist before push

- [x] Git tag `v0.2.0` + [RELEASE_v0.2.0.md](RELEASE_v0.2.0.md)  
- [x] `pyproject.toml` version `0.2.0`  
- [x] SPEC / README version notes updated  
- [ ] `pytest -q` green (run before image build)  
- [ ] Manual smoke: register, add server, backup, metrics, create API token  
- [ ] Multi-arch `docker buildx` push to Docker Hub / GHCR  
- [ ] GitHub Release (optional; attach notes from RELEASE_v0.2.0.md)  
- [ ] `SECURITY.md` still accurate

## Until published

Default path remains:

```bash
docker compose up -d --build
```
