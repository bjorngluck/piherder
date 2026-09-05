# Move a service

!!! note "Availability"
    **Move a service** is **v1.4.0 pending sign-off** on `v1.4.0-dev` (Hub/`main` stay **1.3.0** until tag). Behind `PIHERDER_SERVICE_MIGRATE` (default **off**). Host **lock / unlock** has no flag. Source remove + named-volume delete is optional and **off** unless you pick it. Public demo never copies. User notes: [RELEASE_v1.4.0](https://github.com/bjorngluck/piherder/blob/v1.4.0-dev/docs/RELEASE_v1.4.0.md). Technical: [PLAN_v1.4.0](https://github.com/bjorngluck/piherder/blob/v1.4.0-dev/docs/PLAN_v1.4.0.md).

## What this is

Move one **Docker Compose project** (same boundary as Stop all / Start all) from fleet host A to host B as one audited **Job**: lock hardware-bound stacks, preflight dest, stop source, copy dataset via the herder, start dest, retarget DNS **or** the NPM proxy backend, validate TLS/Kuma when those rows exist, rebind maps / templates / Kuma / Grafana container chips, then leftover policy.

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
3. Pick a destination (other hosts with **Docker / containers** on; HAOS and the source are excluded). A **wait modal** runs for dest pick, port change, and **Recheck** (SSH probe).  
4. Preflight lists **blocks** (hard stop) and **warnings**. Dataset lists paths, kinds, byte estimate, dest free space.  
5. **Project folder** is dest docker root + the same folder name (`/home/bjorn/docker/test` → `/home/bjorn/docker/test`). Change **Dest folder name** only if you want `test2`. Named volumes copy with the project. Extra host binds (paths **outside** the project folder) are a separate list — not the project itself.  
6. If the stack looks hardware-bound (`/dev/…`), tick the acknowledge checkbox (or lock it instead).  
7. If preflight lists NPM names with **no fabric DNS row**, optional **Adopt into fabric** (default off) — see below.  
8. Choose leftover (see below). Default is **leave source stopped**.  
9. **Move service** — danger confirm (downtime). **Remove source** also requires the extra checkbox and a stronger confirm.  
10. **JobHold** live log stays open with **Succeeded** or **Failed** until you Close (does not vanish). Job type `service_migrate`. `Job.server_id` is the **source**; dest is in job details. Copy / dest-up fail offers **Start source stack**.

Audit on open: `service_migrate_preview`.

### Pipeline (default)

```text
preflight
  → compose stop (source)
  → rsync project + binds + named volumes → herder staging → dest
  → compose up -d (dest)
  → DNS CNAME → dest  or  NPM PUT forward_host → dest
  → rebind maps / Kuma / Grafana container chips / templates / clone cert targets
  → TLS (SNI = service FQDN) + Kuma last_state when rows exist
  → leftover
  → wipe staging (success) / keep staging (failure)
```

Default order is **dest up, then name/proxy flip** (clients keep hitting the stopped source until dest answers). The **project folder** is a verbatim rsync of every file and directory (including `.env`) onto dest docker root + folder name, then **chown** to whoever owns dest docker root (`/home/bjorn/docker` → `bjorn`, not fleet SSH `piherder` and not `root:root`). A `~/…` bind (inspect `/home/piherder/open-webui-data`) is folded into that dest project as `./open-webui-data` and compose is rewritten. Host ports only if remapped (does **not** apply with `network_mode: host`). Named volumes = Mountpoint rsync. Host sockets (`/var/run/docker.sock`) stay as dest-host binds — they are not rsync’d.

Direct TLS rows (`via_proxy` off): **CNAME → dest DNS name**, then both Pi-holes **Restart DNS**. NPM-fronted rows: public CNAME stays on NPM; the job **PUT**s `forward_host` (and `forward_port` only if you remapped that published host port). An NPM **proxy-host binding** for the compose project is enough — a fabric DNS row is not required (e.g. `ai.hacknow.info` → Open WebUI). Optional **Adopt into fabric** (default off) adds those NPM names as `via_proxy` rows so they show on the DNS list; it does **not** create certs or rewrite Pi-hole CNAMEs. Preflight still uses the **NPM poll cache**; unmatched hosts are a block. Poll NPM before moving.

**Moving the NPM edge itself** (the compose project whose public name is the NPM base URL, e.g. `nginx.hacknow.info`): every proxied service stays **CNAME → that alias**. Only the alias is rewritten to dest (`nginx.hacknow.info` → dest host DNS name). The job does **not** rewrite `ai.hacknow.info` onto dest. Pi-hole admin URLs that go through this proxy are reached on the **LAN backend** from the last NPM poll (`forward_host:forward_port`) so cutover is not stuck on a dead edge FQDN. A retry after a failed DNS sync still retargets the alias (fabric may already show dest).

### Dual-host exclusive

A migrate **and** a backup or stack-mutating job cannot run at the same time on the **source or dest**. Second start → **409**. Same lane as Stop all / Deploy / template deploy, plus backup.

### Blocks (cannot move yet)

| Check | Typical cause |
|-------|----------------|
| Source / dest is HAOS | Appliance, not a compose suitcase |
| Project locked | Unlock, or keep it on this host |
| Dest Docker off | Enable **Docker / containers** on dest |
| Same host | Pick a different Pi |
| Dest already has that project name | Recheck **SSHs dest**. Block only if dest has **running** containers for that name. A leftover dest folder / `created` containers from a failed Move is a warning — Move overwrites the folder and removes those ghosts |
| Could not SSH dest | Recheck could not see dest live. Cached dest inventory is **not** used. Fix SSH, Recheck |
| Arch mismatch | `uname -m` differs (e.g. aarch64 vs x86_64) — rebuild images yourself |
| Dest docker base not writable | Fleet SSH user cannot write `docker_base_dir` |
| Dest / herder disk | Free space below payload + margin (512 MiB or 15%) |
| Absolute bind outside docker base | Default dest path is **inside the dest project folder** (`./basename`). Override only if you want another path, or skip copy. Host sockets/devices (`docker.sock`, `/dev/…`) are **not copied** — dest binds dest’s own path |
| Inventory mount path truncated (`…`) | `docker ps` ellipsis, not a real directory — Move inspects containers for full Source. Refresh Docker on source if it still blocks |
| Published port clash | Dest is **currently listening** on that host port (`ss` + docker). Remap dest host port. Ghost leftover dest-project ports are ignored. **Host network** stacks cannot remap — dest must have the same port free |
| Busy job | Backup or stack mutate running on **source or dest** |
| Direct DNS, dest has no DNS name | Set dest **DNS name** for CNAME retarget |
| `via_proxy` and no NPM | Enable an NPM integration |
| NPM proxy host unmatched | FQDN not in last NPM poll — poll NPM, or the proxy host is missing |

### Warnings (informational)

| Check | Meaning |
|-------|---------|
| Leftover dest compose containers | Failed previous Move left `created`/exited containers. Move removes them before dest up |
| Dest folder not empty | No running dest stack — Move overwrites that folder (`rsync --delete`) |
| `/dev/…` mounts | Hardware-looking binds (TPU / USB) — lock the project if it must stay |
| Host network / privileged | Dest binds the **same host ports**; wizard remap does not apply. Tick the acknowledge checkbox (or lock instead) |
| Host socket (`docker.sock`) | Not a folder — dest uses dest’s `/var/run/docker.sock` (Uptime Kuma Docker monitor) |
| External DNS checklist | Cloudflare / other still on the fabric row |
| Kuma looks IP-based | Monitor hostname will not be rewritten (no Kuma write API) |
| Arch / disk / writable unknown | SSH probe failed — refresh connectivity and retry |
| NPM edge | This project *is* the reverse proxy. Public names CNAME to its alias and stay; only the alias follows dest |
| NPM proxy-host binding (no fabric row) | Move will still PUT `forward_host`. Optional **Adopt into fabric** adds a `via_proxy` DNS row (no cert, no Pi-hole rewrite) |

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
- **NPM-fronted:** public name still on NPM; proxy-host `forward_host` is dest. A fabric DNS row is **not** required if an NPM **proxy-host binding** exists for the project.  
- If you ticked **Adopt into fabric**: those NPM names appear on the DNS list as `via_proxy` (backend = dest). Pi-hole CNAMEs are unchanged.  
- **NPM edge move:** `nginx…` (or whatever the NPM hostname is) CNAME → dest host; `ai…` / other proxied names still CNAME to that alias.  
- Maps / stack panel / template deployment / Kuma **service** bind / Grafana **container** dashboard chips follow dest. Host metrics/logs Grafana chips stay.  
- TLS probe used SNI = service FQDN when a cert is linked.  
- Source leftover matches what you picked.  
- Staging dir under `/backups/_migrate/{job_id}` is gone on success.

## Failure

Validate red (TLS mismatch, Kuma down) **does not auto-roll back**. Dest may already be up with DNS/NPM flipped. Fix dest yourself. Staging is **kept** on failure until you dismiss the job / it ages out.

| Fail | State | JobHold |
|------|--------|---------|
| Stop / copy | Source stopped (or stop failed), dest untouched | **Start source stack** |
| Dest up | Source stopped, dest partial, DNS/NPM **unchanged** | **Start source stack** |
| NPM PUT / DNS | Dest up, proxy or names maybe stale | Fix dest / poll NPM; no auto-revert |
| Validate | Dest up, names already flipped | No **Start source** (would dual-run) |

**Start source stack** queues the existing Docker **Start all** job on the source project. It is not a full migrate rollback.

## What it does not do

- Auto-rollback, live (zero-downtime) copy, cross-arch image rebuild  
- Moving PiHerder itself, or the Pi-hole you are using to migrate  
- Deleting dest volumes, or wiping extra binds outside the project folder  
- Remapping published ports on **host-network** stacks (the process still binds the source host port on dest)  
- Copying host sockets/devices (`/var/run/docker.sock`, `/dev/…`) — dest binds dest’s own path  
- Rewriting IP-based Kuma monitors (checklist only)  
- Moving Grafana **host metrics / logs** chips (only **container/project** dashboard binds follow dest)  
- Auto-creating fabric DNS rows (optional **Adopt into fabric** checkbox, default off)  
- Flipping names **before** dest is up (`dns_then_start` — not built)  
- Full NPM proxy create/delete/SSL (backend `forward_host` only)  
- ACME-in-herder  

## Related

- [Docker overview](overview.md)  
- [HAOS hosts](../day-to-day/haos-hosts.md)  
- [Network maps / DNS fabric](../integrations/dns-fabric.md)  
- [NPM](../integrations/npm.md)  
- [Uptime Kuma](../integrations/uptime-kuma.md)  
- [Grafana](../integrations/grafana.md) — container chips follow dest  
- [Certificates](../integrations/certificates.md)  
- [Jobs, audit & notifications](../day-to-day/jobs-audit-notifications.md)  
- [Backups](../day-to-day/backups.md) — same rsync privilege as named-volume Mountpoints  
- [Env reference](../operations/env-reference.md) — `PIHERDER_SERVICE_MIGRATE`  
- [Operator scenarios](../getting-started/operator-scenarios.md#journey-move)  
