# SSH, rsync & dependencies

## What this is

Fixes when PiHerder **cannot log into a host**, key deploy fails, or enabled features lack **rsync / docker / apt** on the remote. Start from server **SSH access → Test connection**.

## Cannot connect

1. Hostname/IP and port correct?  
2. Firewall allows SSH from PiHerder host?  
3. **Test connection** on SSH access panel.  
4. Key deployed? Password only for bootstrap.  
5. **Host-key mismatch?** After the first successful connect, PiHerder **pins** the remote SSH host key. A rebuild or a different machine at the same address is **refused** until you **reset the pin** on SSH access, then Test connection again.  

## Key deploy fails

- Password session required if key not yet installed.  
- `authorized_keys` permissions on remote (`~/.ssh` 700, file 600).  
- SELinux/AppArmor rare edge cases on some distros.

## Backups: permission denied / rsync

- Non-root: need `sudo -n rsync` (passwordless) for protected paths.  
- Root/HAOS: plain rsync path auto-detected.  
- **HAOS:** install the **rsync** package on the appliance; enable **Terminal & SSH** and deploy the PiHerder key. Host deps probe **`ha` CLI** (not apt) when HA updates is on — [HAOS hosts](../day-to-day/haos-hosts.md). 
- Path policy may **deny** the source — check allow/deny on Backups page.

## Backups: rsync code 23 (partial transfer)

Exit **23** means *some* files failed; volumes or other sources may still succeed. The job error should name the failing path (e.g. `readdir(...): Input/output error`).

| Symptom in error | Likely cause | What to do on the host |
| --- | --- | --- |
| `Input/output error` / `I/O error` on a path | Bad disk, failing mount, or ext4 **shutdown** after FS errors | `findmnt PATH`, `dmesg \| tail`, SMART (`smartctl -a`), unmount + `fsck`, remount or replace media |
| `Permission denied` | sudo / ownership | Passwordless `sudo -n rsync`, or root SSH |
| `vanished` (often code **24**; may surface as **23**) | Files deleted/moved while rsync ran — **common on Frigate/NVR and other busy media trees** | **v1.2 B-retry:** automatic retry + optional **soft-OK** ([backups troubleshooting](backups.md#vanished-files-busy-sources)). Still split high-churn paths when you need a strict snapshot. |

**Example (disk, not vanished):** a separate NVMe mount under the docker tree with ext4 **`shutdown`** and `ls` → I/O error must be repaired on the host before backups of that tree can succeed.

## Docker commands fail after least-priv

- User in `docker` group? re-login / new session.  
- **Docker base dir** absolute path + Option B ACL if stacks under another home.  
- `~/docker` expands to wrong home after username switch.

## Dependency chips red

**SSH access → Check dependencies** (or **Test connection**) — install missing packages **on the host** yourself (PiHerder does not auto-install).
