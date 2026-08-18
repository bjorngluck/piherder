# Publish multi-arch image

Multi-arch images on **Docker Hub**: [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) (**v1.1.1** production until **v1.2.0** is tagged, `linux/amd64` + `linux/arm64`). Full maintainer checklist: [`docs/PUBLISH_IMAGE.md`](https://github.com/bjorngluck/piherder/blob/main/docs/PUBLISH_IMAGE.md). Do **not** retag `latest` until [QA](../operations/qa-v1.2.0.md) is signed.

## Hub listing checklist

1. Description + overview  
2. Logo / screenshots  
3. Link to [RELEASE notes](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.1.1.md)  
4. Link description to GitHub + [these docs](https://piherder-docs.hacknow.info/)

## Tags

| Tag | Meaning |
|-----|---------|
| `1.1.1` | Immutable release |
| `1.1.0` | Prior 1.1 pin |
| `1.1` | Rolling minor |
| `1.0` | Prior production minor (optional pin) |
| `latest` | Current stable |

Images: `bjorngluck/piherder` (optional later: `ghcr.io/bjorngluck/piherder`).

## Multi-arch build example

```bash
export IMAGE=bjorngluck/piherder
export VERSION=1.1.1

docker buildx create --use --name piherder-builder --driver docker-container 2>/dev/null || true
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:1.1" \
  -t "${IMAGE}:latest" \
  --push .
```

## Operators (compose)

```bash
# PIHERDER_IMAGE=bjorngluck/piherder:1.1.1 docker compose up -d
```

See [Install](../getting-started/install.md) · [Upgrades](../operations/upgrades.md).
