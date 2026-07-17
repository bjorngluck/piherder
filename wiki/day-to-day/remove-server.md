# Remove a server

## What this is

**Remove server** deletes the host’s **control-plane record** from PiHerder: credentials, schedules, feature links, and UI presence. It is **not**, by default, a remote wipe of Docker stacks or the least-priv user on the machine.

## Why it exists

Hosts come and go (replacement Pi, decommission, renumber). Operators need a clear teardown that:

- Stops schedules and in-flight jobs for that host  
- Removes stored SSH secrets from the DB  
- Does **not** surprise-delete data on the remote disk  

Optional host cleanup is a **separate**, copy-pasteable script so you choose when the OS-side user goes away.

---

## End-to-end: retire a host

1. Finish or cancel active jobs on that server ([Jobs](jobs-audit-notifications.md)).  
2. Optional: take a last [backup](backups.md) if you still need files.  
3. Server detail → **Edit** → **Remove** tab.  
4. Type the **exact server name** to confirm.  
5. After remove, optionally run **Host cleanup** script on the machine as root if you used a least-priv `piherder` user.  
6. Confirm Dashboard / Servers list no longer shows the host; Audit history for past actions remains.

---

## Where

Server detail → **Edit** → **Remove** tab → **Remove server…**

## What happens

| Happens | Does **not** happen |
|---------|---------------------|
| Server row + stored SSH credentials removed from DB | No SSH / remote changes by default |
| Schedules unregistered; active jobs cancelled | Docker stacks, volumes, media untouched |
| Compose drafts in PiHerder deleted | Host `piherder` user / sudoers / keys left as-is |
| Jobs / audit / notifications unlinked (history kept) | Backup archives on the backup volume kept |

Confirm by typing the **exact server name**.

## Optional host cleanup

To drop the least-priv account **on the host** (as root):

- Edit → **Remove** tab → **Copy script** / **Download .sh**  
- Or **SSH access → Host cleanup script**  
- Repo: `scripts/cleanup-piherder-user.sh`

```bash
# On the host
sudo bash cleanup-piherder-user.sh                 # sudoers + docker group; keep user
USER_NAME=piherder REMOVE_USER=1 sudo -E bash cleanup-piherder-user.sh
DRY_RUN=1 sudo -E bash cleanup-piherder-user.sh    # preview
```

Does not remove Docker projects or data. Does not remove the server from the UI — do that separately if still listed.

## Related

- [Add a server](add-server.md)  
- [Self-backup](../operations/self-backup.md) — herder still holds other hosts after one is removed  
