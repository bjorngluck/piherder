# Move a service

!!! note "Availability"
    **Move a service** ships on `v1.4.0-dev` (will be **v1.4.0**; production Hub/`main` stay **1.3.0** until freeze). Behind `PIHERDER_SERVICE_MIGRATE` (default **off**). Host **lock / unlock** has no flag. Source remove + named-volume delete is optional and **off** unless you pick it. Public demo never copies.

## What this is

Move one **Docker Compose project** (same boundary as Stop all / Start all) from fleet host A to host B as one audited **Job**: lock hardware-bound stacks, preflight dest, stop source, copy dataset via the herder, start dest, retarget DNS **or** the NPM proxy backend, validate TLS/Kuma when those rows exist, rebind maps / templates / Kuma, then leftover policy.

PiHerder stays **SSH-first**. The herder is the staging hop (`/backups/_migrate/{job_id}`) — not a new host-to-host trust mesh.

## Why it exists

Operators already stop a stack, rsync trees, retarget a CNAME, restart Pi-hole, and start on another Pi. Those steps were never **one Job** with a preview of paths, bytes, and dest free space.

## Enable Move

1. Set `PIHERDER_SERVICE_MIGRATE=true` in `.env`.  
2. Recreate **web** (`docker compose up -d web`).  
3. **Operator+** only. Viewer **403**. Demo never opens the wizard.

Lock / unlock does **not** need this flag.

## Lock to this host (always on)

**Operator+** only.

1. Open **Docker** on the source host.  
2. Project **⋯** → **Lock to this host…**  
3. Pick a reason: **Hardware** (TPU / USB / Coral), **Operator**, or **Infrastructure**. Optional note (e.g. `Coral TPU on USB`).  
4. A **Locked** badge appears on the project and on the runtime stack panel. **Move** is disabled with the reason.  
5. **Unlock…** is a confirm.

**HAOS** hosts are always locked — they never appear as a migrate source or destination. You cannot lock/unlock individual HAOS “projects”; the host `os_type` is enough.

Audit: `service_host_lock` / `service_host_unlock` (project, reason, note — not secret bodies).

Use lock for Frigate + Coral, USB gadgets, or anything you must not relocate by accident.

## Move wizard

1. Unlock the project if it is locked.  
2. **⋯** → **Move to another host…**  
3. Pick a destination (other hosts with **Docker / containers** on; HAOS and the source are excluded). A **wait modal** runs while PiHerder SSH-probes dest (same overlay as other long tasks).  
4. Preflight lists **blocks** (hard stop) and **warnings**. Dataset lists paths, kinds, byte estimate, dest free space.  
5. **Project folder** is dest docker root + the same folder name (`/home/bjorn/docker/test` → `/home/bjorn/docker/test`). Change **Dest folder name** only if you want `test2`. Named volumes copy with the project. Extra host binds (paths **outside** the project folder) are a separate list — not the project itself.  
6. If the stack looks hardware-bound (`/dev/…`), tick the acknowledge checkbox (or lock it instead).  
7. Choose leftover (see below). Default is **leave source stopped**.  
8. **Move service** — danger confirm (downtime). **Remove source** also requires the extra checkbox and a stronger confirm.  
9. **JobHold** live log. Job type `service_migrate`. `Job.server_id` is the **source**; dest is in job details.

Audit on open: `service_migrate_preview`.

### Pipeline (default)

```text
preflight
  → compose stop (source)
  → rsync project + binds + named volumes → herder staging → dest
  → compose up -d (dest)
  → DNS CNAME → dest  or  NPM PUT forward_host → dest
  → rebind maps / Kuma / templates / clone cert targets
  → TLS (SNI = service FQDN) + Kuma last_state when rows exist
  → leftover
  → wipe staging (success) / keep staging (failure)
```

Default order is **dest up, then name/proxy flip** (clients keep hitting the stopped source until dest answers). The **project folder** is a verbatim rsync of every file and directory (including `.env`) onto dest docker root + folder name. A `~/…` bind (inspect `/home/piherder/open-webui-data`) is folded into that dest project as `./open-webui-data` and compose is rewritten. Host ports only if remapped. Named volumes = Mountpoint rsync.

Direct TLS rows (`via_proxy` off): **CNAME → dest DNS name**, then both Pi-holes **Restart DNS**. NPM-fronted rows: public CNAME stays on NPM; the job **PUT**s `forward_host` (and `forward_port` only if you remapped that published host port). Preflight still uses the **NPM poll cache**; unmatched hosts are a block. Poll NPM before moving.

### Dual-host exclusive

A migrate **and** a backup or stack-mutating job cannot run at the same time on the **source or dest**. Second start → **409**. Same lane as Stop all / Deploy / template deploy, plus backup.

### Blocks (cannot move yet)

| Check | Typical cause |
|-------|----------------|
| Source / dest is HAOS | Appliance, not a compose suitcase |
| Project locked | Unlock, or keep it on this host |
| Dest Docker off | Enable **Docker / containers** on dest |
| Same host | Pick a different Pi |
| Dest already has that project name | Set **Project name / folder** on dest (lands as `docker_base/<new name>`). Named volumes prefixed with the source project are remapped |
| Arch mismatch | `uname -m` differs (e.g. aarch64 vs x86_64) — rebuild images yourself |
| Dest docker base not writable | Fleet SSH user cannot write `docker_base_dir` |
| Dest / herder disk | Free space below payload + margin (512 MiB or 15%) |
| Absolute bind outside docker base | Default dest path is **the same as source**. Change it to relocate (e.g. under dest docker base), or skip copy |
| Inventory mount path truncated (`…`) | `docker ps` ellipsis, not a real directory — Move inspects containers for full Source. Refresh Docker on source if it still blocks |
| Published port clash | Dest already publishes the same host port — set a free dest host port (compose `ports:` rewritten before dest up; NPM `forward_port` follows if it matched) |
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

## Source leftover

After a **green** move only:

| Choice | What happens on the **source** host | Dest |
|--------|-------------------------------------|------|
| **Leave stopped** (default) | Stack stays stopped; project dir and volumes stay on disk | Untouched |
| **`compose down`** | Containers/networks removed; **volumes kept** | Untouched |
| **Remove source** | `compose down`, then `docker volume rm` for **copied named volumes**, then delete the jailed project directory | **Never wiped** |

Remove is a second danger confirm plus checkbox. Preflight lists the project path and named volume names. Absolute binds **outside** the project folder are left on disk. Source cert deploy targets for that stack are **disabled** (dest clone stays). This cannot be undone from PiHerder — restore from backup if you still need the old copy.

## After a green move (check)

- Dest Docker inventory shows the project **running**.  
- **Direct:** CNAME target is dest `dns_name`; both Pi-holes restarted.  
- **NPM-fronted:** public name still on NPM; proxy-host `forward_host` is dest.  
- Maps / stack panel / template deployment / Kuma **service** bind follow dest.  
- TLS probe used SNI = service FQDN when a cert is linked.  
- Source leftover matches what you picked.  
- Staging dir under `/backups/_migrate/{job_id}` is gone on success.

## Failure

Validate red (TLS mismatch, Kuma down) **does not auto-roll back**. Dest may already be up with DNS/NPM flipped. Fix dest, or start the stack back on source yourself. Staging is **kept** on failure until you dismiss the job / it ages out.

Copy fail: source stopped, dest untouched, staging kept. Dest-up fail: DNS/NPM unchanged.

## What it does not do

- Auto-rollback, live (zero-downtime) copy, cross-arch image rebuild  
- Moving PiHerder itself, or the Pi-hole you are using to migrate  
- Deleting dest volumes, or wiping extra binds outside the project folder  
- Rewriting IP-based Kuma monitors (checklist only)  
- Full NPM proxy create/delete/SSL (backend `forward_host` only)  
- ACME-in-herder  

## Related

- [Docker overview](overview.md)  
- [HAOS hosts](../day-to-day/haos-hosts.md)  
- [Network maps / DNS fabric](../integrations/dns-fabric.md)  
- [NPM](../integrations/npm.md)  
- [Uptime Kuma](../integrations/uptime-kuma.md)  
- [Certificates](../integrations/certificates.md)  
- [Jobs, audit & notifications](../day-to-day/jobs-audit-notifications.md)  
- [Backups](../day-to-day/backups.md) — same rsync privilege as named-volume Mountpoints  
- [Env reference](../operations/env-reference.md) — `PIHERDER_SERVICE_MIGRATE`  
- [Operator scenarios](../getting-started/operator-scenarios.md#journey-move)  
