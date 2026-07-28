# Publish image

Multi-arch images on **Docker Hub**: [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) (**v1.0.0** production, `linux/amd64` + `linux/arm64`). Full maintainer checklist: [`docs/PUBLISH_IMAGE.md`](https://github.com/bjorngluck/piherder/blob/main/docs/PUBLISH_IMAGE.md).

## One-time Hub setup

1. Account on [hub.docker.com](https://hub.docker.com)  
2. Create repo **`bjorngluck/piherder`** (public)  
3. Access token (Read/Write) → `docker login -u bjorngluck`  
4. Link description to GitHub + [these docs](https://piherder-docs.hacknow.info/)

## Tags

| Tag | Meaning |
|-----|---------|
| `1.0.0` | Immutable release |
| `1.0` | Rolling minor |
| `0.9` | Latest patch in line (optional) |
| `latest` | Current stable |

Images: `bjorngluck/piherder` (optional later: `ghcr.io/bjorngluck/piherder`).

## Multi-arch build example

```bash
export IMAGE=bjorngluck/piherder
export VERSION=1.0.0

docker buildx create --use --name piherder-builder --driver docker-container 2>/dev/null || true
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:0.9" \
  -t "${IMAGE}:latest" \
  --push \
  .
```

## Compose

Official compose already pulls the published image:

```bash
docker compose up -d
# pin:
# PIHERDER_IMAGE=bjorngluck/piherder:1.0.0 docker compose up -d
```
