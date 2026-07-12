# Publish image

Multi-arch images on **Docker Hub** / GHCR (target **v0.5.0 RC**). Full maintainer checklist: [`docs/PUBLISH_IMAGE.md`](https://github.com/bjorngluck/piherder/blob/main/docs/PUBLISH_IMAGE.md).

## One-time Hub setup

1. Account on [hub.docker.com](https://hub.docker.com)  
2. Create repo **`bjorngluck/piherder`** (public)  
3. Access token (Read/Write) → `docker login -u bjorngluck`  
4. Link description to GitHub + [these docs](https://bjorngluck.github.io/piherder/)

## Tags

| Tag | Meaning |
|-----|---------|
| `0.5.0` | Immutable release |
| `0.5` | Latest patch in line (optional) |
| `latest` | Current stable |

Images: `bjorngluck/piherder` or `ghcr.io/bjorngluck/piherder`.

## Multi-arch build example

```bash
export IMAGE=bjorngluck/piherder
export VERSION=0.5.0

docker buildx create --use --name piherder-builder 2>/dev/null || true
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push \
  .
```

## Compose with a published image

```yaml
web:
  image: bjorngluck/piherder:0.5.0
celery-worker:
  image: bjorngluck/piherder:0.5.0
```

## Until published

```bash
docker compose up -d --build
```
