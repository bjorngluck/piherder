# Publish multi-arch image

Multi-arch images on **Docker Hub**: [bjorngluck/piherder](https://hub.docker.com/r/bjorngluck/piherder) (**v1.8.1** production, `linux/amd64` + `linux/arm64`). Full maintainer checklist: [`docs/PUBLISH_IMAGE.md`](https://github.com/bjorngluck/piherder/blob/v1.8.1/docs/PUBLISH_IMAGE.md).

## Hub listing checklist

1. Description + overview  
2. Logo / screenshots  
3. Link to [RELEASE notes](https://github.com/bjorngluck/piherder/blob/v1.8.0/docs/RELEASE_v1.8.0.md)  
4. Link description to GitHub + [these docs](https://piherder-docs.hacknow.info/)

## Tags

| Tag | Meaning |
|-----|---------|
| `1.8.1` | Immutable patch |
| `1.8.0` | Prior 1.8 pin |
| `1.8` | Rolling minor |
| `1.7.0` | Prior 1.7 pin |
| `1.7` | Prior rolling minor |
| `1.6.0` | Prior 1.6 pin |
| `1.6` | Prior rolling minor |
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

**v1.8.1** manifest list: `sha256:289add1ce903c9cedf1bcff6a14b9d8e865284c2db22b17a99ea399693079f9a` (`1.8.1` / `1.8` / `latest`). Pin **1.8.0** remains `sha256:8ce50bbce758e622a996cd58557b0846e03cb02613b09471405222df620c2e89`. Pin **1.7.0** remains `sha256:174cb1313f6717d323211c8c899b30240e97f5097bd35770f7a6c4555de95270`.

## Multi-arch build example

```bash
export IMAGE=bjorngluck/piherder
export VERSION=1.8.1

docker buildx create --use --name piherder-builder --driver docker-container 2>/dev/null || true
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:1.8" \
  -t "${IMAGE}:latest" \
  --push .
```

## Operators (compose)

```bash
# PIHERDER_IMAGE=bjorngluck/piherder:1.4.0 docker compose up -d
```

See [Install](../getting-started/install.md) · [Upgrades](../operations/upgrades.md).
