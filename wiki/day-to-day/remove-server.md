# Remove a server

**Where:** Server detail → **Remove from PiHerder** (danger zone).

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

- Server detail → **Copy script** / **Download .sh**  
- Or **SSH access → Host cleanup script**  
- Repo: `scripts/cleanup-piherder-user.sh`

```bash
# On the host
sudo bash cleanup-piherder-user.sh                 # sudoers + docker group; keep user
USER_NAME=piherder REMOVE_USER=1 sudo -E bash cleanup-piherder-user.sh
DRY_RUN=1 sudo -E bash cleanup-piherder-user.sh    # preview
```

Does not remove Docker projects or data. Does not remove the server from the UI — do that separately if still listed.
