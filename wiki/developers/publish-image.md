# Publish multi-arch image

Multi-arch images on **Docker Hub**: [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) (**v1.6.0** production, `linux/amd64` + `linux/arm64`). Full maintainer checklist: [`docs/PUBLISH_IMAGE.md`](https://github.com/bjorngluck/piherder/blob/main/docs/PUBLISH_IMAGE.md).

## Hub listing checklist

1. Description + overview  
2. Logo / screenshots  
3. Link to [RELEASE notes](https://github.com/bjorngluck/piherder/blob/main/docs/RELEASE_v1.6.0.md)  
4. Link description to GitHub + [these docs](https://piherder-docs.hacknow.info/)

## Tags

| Tag | Meaning |
|-----|---------|
| `1.6.0` | Immutable release |
| `1.6` | Rolling minor |
| `1.5.0` | Prior 1.5 pin |
| `1.5` | Prior rolling minor |
| `1.4.0` | Prior 1.4 pin |
| `1.4` | Prior rolling minor |
| `1.3.0` | Prior 1.3 pin |
| `1.3` | Prior rolling minor |
| `1.2.0` | Prior 1.2 pin |
| `1.2` | Prior rolling minor |
| `1.1.1` | Prior 1.1 pin |
| `1.1` | Prior rolling minor |
| `latest` | Current stable |

Images: `bjorngluck/piherder` (optional later: `ghcr.io/bjorngluck/piherder`).

**v1.6.0** manifest list: `sha256:cdf88c70099f78830943e6529f05eff1b83bb5565e7b12f71ebf0877c7b018a8` (`1.6.0` / `1.6` / `latest`).

## Multi-arch build example

```bash
export IMAGE=bjorngluck/piherder
export VERSION=1.6.0

docker buildx create --use --name piherder-builder --driver docker-container 2>/dev/null || true
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:1.6" \
  -t "${IMAGE}:latest" \
  --push .
```

## Operators (compose)

```bash
# PIHERDER_IMAGE=bjorngluck/piherder:1.4.0 docker compose up -d
```

See [Install](../getting-started/install.md) · [Upgrades](../operations/upgrades.md).
