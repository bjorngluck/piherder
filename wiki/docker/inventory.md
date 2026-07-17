# Docker inventory cache

## What this is

Compose/container lists shown on the Docker page come from a **database snapshot**, refreshed in the background — not a blocking full SSH inventory on every page open.

## Why it exists

Walking every project with full mount `du` over SSH on each navigation makes the UI feel broken on a busy Pi. Snapshot + background refresh keeps the page fast; **Force refresh** and expand-time `du` cover accuracy when you need it.

| Behaviour | Detail |
|-----------|--------|
| Storage | Per-server `docker_inventory_*` columns |
| Open Docker page | Renders **last snapshot** immediately |
| Refresh | Background L1 collect (no expensive mount `du` on list path) |
| Triggers | Stale on open, after mutations, fleet job ~every **10 minutes**, **Force refresh** |
| Stale UI | Banner “Inventory as of …” / “Refreshing…”; last good list kept |
| Feature gate | Only servers with Docker feature on |

### Container expand

Full mount paths + per-path host `du` run on **expand** (`GET …/docker/container/mounts`). List stays fast.

!!! tip "Looks wrong after host-side changes?"
    Use **Force refresh** on the Docker page.

## Related

- [Docker overview](overview.md)  
