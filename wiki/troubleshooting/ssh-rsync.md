# SSH, rsync & dependencies

## What this is

Fixes when PiHerder **cannot log into a host**, key deploy fails, or enabled features lack **rsync / docker / apt** on the remote. Start from server **SSH access → Test connection**.

## Cannot connect

1. Hostname/IP and port correct?  
2. Firewall allows SSH from PiHerder host?  
3. **Test connection** on SSH access panel.  
4. Key deployed? Password only for bootstrap.  

## Key deploy fails

- Password session required if key not yet installed.  
- `authorized_keys` permissions on remote (`~/.ssh` 700, file 600).  
- SELinux/AppArmor rare edge cases on some distros.

## Backups: permission denied / rsync

- Non-root: need `sudo -n rsync` (passwordless) for protected paths.  
- Root/HAOS: plain rsync path auto-detected.  
- Path policy may **deny** the source — check allow/deny on Backups page.

## Docker commands fail after least-priv

- User in `docker` group? re-login / new session.  
- **Docker base dir** absolute path + Option B ACL if stacks under another home.  
- `~/docker` expands to wrong home after username switch.

## Dependency chips red

**SSH access → Check dependencies** (or **Test connection**) — install missing packages **on the host** yourself (PiHerder does not auto-install).
