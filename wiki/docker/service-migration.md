# Move a service

!!! note "Availability"
    **Move a service** (lock, preflight, copy, dest up, DNS/NPM, validate, rebind, leftover down) ships on `v1.4.0-dev` (will be **v1.4.0**). Behind `PIHERDER_SERVICE_MIGRATE` (default **off**). Source remove + volume delete is still Should / off.

## What this is

Move a **Docker Compose project** from one fleet host to another as one audited flow: lock hardware-bound stacks so they cannot move, then **preflight** the dest (HAOS, ports, disk, DNS / NPM, busy jobs) before any copy.

PiHerder stays **SSH-first**. The herder is the staging hop when copy lands — not a new host-to-host trust mesh.

## Why it exists

Operators already stop a stack, rsync trees, retarget a CNAME, restart Pi-hole, and start on another Pi. Those steps were never **one Job** with a preview of paths, bytes, and dest free space.

## Lock to this host (always on)

No env flag. **Operator+** only.

1. Open **Docker** on the source host.  
2. Project **⋯** → **Lock to this host…**  
3. Pick a reason: **Hardware** (TPU / USB / Coral), **Operator**, or **Infrastructure**. Optional note (e.g. `Coral TPU on USB`).  
4. A **Locked** badge appears on the project and on the runtime stack panel.  
5. **Unlock…** is a confirm.

**HAOS** hosts are always locked — they never appear as a migrate source or destination. You cannot lock/unlock individual HAOS “projects”; the host `os_type` is enough.

Audit: `service_host_lock` / `service_host_unlock` (project, reason, note — not secret bodies).

Use lock for Frigate + Coral, USB gadgets, or anything you must not relocate by accident.

## Move wizard

Master enable: `PIHERDER_SERVICE_MIGRATE=true` in `.env`, then recreate **web**. Default **off**. Public demo never opens Move. Viewer **403**.

1. Unlock the project if it is locked.  
2. **⋯** → **Move to another host…**  
3. Pick a destination (other hosts with **Docker / containers** on; HAOS and the source are excluded).  
4. Preflight lists **blocks** (hard stop) and **warnings**.  
5. If green, **Move service** confirms downtime, then a **Job** stops the source, copies via the herder (`/backups/_migrate/{job_id}`), `docker compose up -d` on dest, then **direct** CNAMEs → dest DNS name (both Pi-holes `restartdns`) or **NPM** `forward_host` → dest (public CNAME stays on NPM). Maps / Kuma / template rows follow dest. TLS and Kuma are checked when those rows exist (failure does **not** auto-roll back).  
6. Leftover: **leave source stopped** (default) or **`compose down`** (volumes kept). Hardware-looking `/dev` mounts require an acknowledge checkbox (or lock the project instead).

Named Docker volumes copy as **rsync of the volume Mountpoint** (same privilege as backing up `/var/lib/docker/volumes`). Relative binds ride with the project tree. Absolute binds **outside** the docker base dir are a preflight block, not a silent copy.

Audit on open: `service_migrate_preview`.

### Blocks (cannot move yet)

| Check | Typical cause |
|-------|----------------|
| Source / dest is HAOS | Appliance, not a compose suitcase |
| Project locked | Unlock, or keep it on this host |
| Dest Docker off | Enable **Docker / containers** on dest |
| Same host | Pick a different Pi |
| Dest already has that project name | Rename or remove the dest stack first |
| Arch mismatch | `uname -m` differs (e.g. aarch64 vs x86_64) — rebuild images yourself |
| Dest docker base not writable | Fleet SSH user cannot write `docker_base_dir` |
| Dest / herder disk | Free space below payload + margin (512 MiB or 15%) |
| Absolute bind outside docker base | e.g. `/mnt/media` — not a silent copy |
| Published port clash | Dest already publishes the same host port |
| Busy job | Backup or stack mutate running on **source or dest** |
| Direct DNS, dest has no DNS name | Set dest **DNS name** for CNAME retarget |
| `via_proxy` and no NPM | Enable an NPM integration |
| NPM proxy host unmatched | FQDN not in last NPM poll — poll NPM, or the proxy host is missing |

### Warnings (informational)

| Check | Meaning |
|-------|---------|
| `/dev/…` mounts | Hardware-looking binds (TPU / USB) — lock the project if it must stay |
| External DNS checklist | Cloudflare / other still on the fabric row |
| Kuma looks IP-based | Monitor hostname will not be rewritten (no Kuma write API) |
| Arch / disk / writable unknown | SSH probe failed — refresh connectivity and retry |

Direct TLS rows (`via_proxy` off): **CNAME → dest DNS name**, then both Pi-holes **Restart DNS**. NPM-fronted rows: public CNAME stays on NPM; the job **PUT**s `forward_host` (and keeps port/SSL). Preflight still uses the **NPM poll cache**; unmatched hosts are a block.

## What it does not do yet

- Auto-rollback, live (zero-downtime) copy, cross-arch image rebuild  
- Source project + volume **delete** (Should; extra danger confirm later)  
- Moving PiHerder itself, or the Pi-hole you are using to migrate  

## Related

- [Docker overview](overview.md)  
- [HAOS hosts](../day-to-day/haos-hosts.md)  
- [Network maps / DNS fabric](../integrations/dns-fabric.md)  
- [NPM](../integrations/npm.md)  
- [Env reference](../operations/env-reference.md) — `PIHERDER_SERVICE_MIGRATE`  
