# Docker on hosts

When **Docker / containers** is enabled for a server, PiHerder can list compose projects, view logs, edit multi-file compose, build, and redeploy. The host Docker page uses the shared **ops-hero** (project counts / health) consistent with other host pages.

<figure class="ph-figure" markdown>
  ![Server detail Docker card](../assets/screenshots/server-detail.svg)
  <figcaption>Docker dest card on server detail. <span class="ph-wireframe-badge">wireframe</span></figcaption>
</figure>

## Prerequisites

1. Feature flag **Docker / containers** on.  
2. Remote `docker` usable by the SSH user (group/socket).  
3. **Docker base dir** set correctly (absolute path if using least-priv).  
4. Dependency check green for docker — [SSH troubleshooting](../troubleshooting/ssh-rsync.md).

## What you can do

| Action | Notes |
|--------|--------|
| Browse projects / containers | From inventory snapshot ([Inventory](inventory.md)) |
| Logs | Per container / service (live stream on full log page) |
| Container start / stop / restart | Row actions on the stack |
| Quick edit / Full editor | ⋯ menu — modal vs multi-file page — [Compose edit](compose-edit.md) |
| Multi-file compose edit | compose + override + `.env` + Dockerfile |
| Version history | Snapshots; rollback |
| Build / redeploy | Wait for job / progress UI |
| Check updates vs Deploy | Pull-only vs pull+up as **Jobs** — [Updates](../day-to-day/updates-and-patching.md) |
| Cleanup unused | List dangling images / exited containers (escaped HTML); optional prune |
| New project wizard | Create a stack on the host |
| Template-managed stacks | Badge + gated full editor — [Templates](../service-templates/overview.md) |

## Template vs free-form stacks

| Kind | Edit path |
|------|-----------|
| **Template-managed** | Desired state / redeploy on deployment page; compose editor gated |
| **Free-form** | Full compose multi-file editor |

## Related

- [Inventory cache](inventory.md)  
- [Compose edit & deploy](compose-edit.md)  
- [Service templates](../service-templates/overview.md)  
