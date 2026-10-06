"""Opt-in direct backup. The host sends its own files to a saved copy.

rclone reads the original files in place. For a non-root backup user it
runs as root, from a binary installed for that run only. Nothing is copied
on the host first. Hosts that do not opt in still rsync onto /backups.
Restore from Drive, OneDrive, or the NAS is not built here.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..models import BackupDestination, Server
from .backup_replicate import (
    _open_destination_rclone,
    _redact,
    credentials_saved,
    destination_name,
    discard_rclone_config,
    host_folder_name,
    parse_selection,
    paths_for_scope,
)
from .ssh import get_ssh_client, run_command

logger = logging.getLogger(__name__)

# Fixed path so least-privilege sudoers can allow it. Root-owned, copied
# for the run, and removed at the end. Not a standing install.
SUDO_RCLONE = "/var/lib/piherder/rclone"

NO_DEST_MSG = (
    "Select a destination for this host. Add Google Drive, OneDrive, "
    "or a LAN share under Settings → PiHerder backup first."
)
SELECT_DEST_MSG = (
    "Select a destination. Choose Google Drive, OneDrive, or a LAN share."
)

DIRECT_PROVIDERS = ("drive", "onedrive", "smb")


def dest_name(item: dict) -> str:
    src = str(item.get("source") or "")
    return str(item.get("dest_name") or Path(src).name or "root")


def herder_rclone_bin() -> str:
    found = shutil.which("rclone")
    if found and os.path.isfile(found):
        return found
    if os.path.isfile("/usr/bin/rclone"):
        return "/usr/bin/rclone"
    raise FileNotFoundError("rclone is not installed in this image")


def rewrite_service_account(text: str, remote_sa: str) -> tuple[str, str | None]:
    """Point service_account_file at the path on the host. Return the herder path too."""
    local_sa = None
    lines: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("service_account_file = "):
            local_sa = line.split("=", 1)[1].strip()
            lines.append("service_account_file = " + remote_sa)
        else:
            lines.append(line)
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return body, local_sa


def remote_sync_command(
    binary: str,
    *,
    config_path: str,
    source: str,
    remote_path: str,
    excludes: list[str],
    use_trash: bool,
    use_sudo: bool,
) -> str:
    argv = ["sudo", "-n", binary] if use_sudo else [binary]
    argv.extend(
        [
            "sync",
            source,
            f"dest:{remote_path}",
            "--config",
            config_path,
            "--stats-one-line",
            "--stats",
            "0",
        ]
    )
    if use_trash:
        argv.append("--drive-use-trash")
    for rel in excludes:
        pattern = rel.rstrip("/")
        if not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
            continue
        argv.extend(["--exclude", pattern if "." in Path(pattern).name else f"{pattern}/**"])
    return " ".join(shlex.quote(part) for part in argv)


def sudo_rclone_install_command(user_bin: str) -> str:
    """Install the binary root-owned so sudo can read the original files.

    The backup user cannot rewrite it. The directory stays root-owned.
    """
    return (
        "sudo -n rsync -a --chown=root:root --chmod=755 --mkpath "
        + shlex.quote(user_bin)
        + " "
        + shlex.quote(SUDO_RCLONE)
    )


def root_read_required_message() -> str:
    """Shown when sudo rsync works but sudo cannot run the staged rclone."""
    return (
        "A direct copy reads the original files as root and does not make a second copy. "
        f"Allow {SUDO_RCLONE} in the least-privilege script from SSH access, then apply it on the host."
    )


def sudo_rclone_cleanup_command(empty_dir: str) -> str:
    """Delete only the staged rclone binary under /var/lib/piherder."""
    src = empty_dir.rstrip("/") + "/"
    return (
        "sudo -n rsync -a --delete --include=rclone --exclude='*' "
        + shlex.quote(src)
        + " /var/lib/piherder/"
    )


def _excludes_under(base: str, skipped: list[str]) -> list[str]:
    prefix = base.rstrip("/") + "/"
    found: list[str] = []
    for skip in skipped:
        if skip.startswith(prefix):
            found.append(skip[len(prefix):])
    return found


def planned_syncs(
    server: Server,
    sources: list[dict],
    rels: list[str],
    skipped: list[str],
) -> list[dict[str, Any]]:
    """Host paths to send for the ticked backup-drive paths."""
    from .backup_replicate import _under

    folder = host_folder_name(server)
    plans: list[dict[str, Any]] = []
    for item in sources:
        if not item.get("enabled", True) or not item.get("source"):
            continue
        name = dest_name(item)
        base = f"{folder}/{name}" if folder else name
        src = str(item["source"]).rstrip("/")
        whole = any(rel == base or _under(base, rel) for rel in rels)
        if whole:
            plans.append(
                {
                    "source": item["source"],
                    "local": src,
                    "rel": base,
                    "excludes": _excludes_under(base, skipped),
                }
            )
            continue
        for child in rels:
            if _under(child, base) and child != base:
                extra = child[len(base) + 1:]
                plans.append(
                    {
                        "source": item["source"],
                        "local": f"{src}/{extra}",
                        "rel": child,
                        "excludes": _excludes_under(child, skipped),
                    }
                )
    return plans


def _index_rows(rows: list[Server]) -> dict[str, Server]:
    index: dict[str, Server] = {}
    for row in rows:
        if not getattr(row, "backup_direct", False):
            continue
        if not getattr(row, "backup_enabled", False):
            continue
        folder = host_folder_name(row)
        if folder:
            index[folder] = row
    return index


def load_direct_index() -> dict[str, Server]:
    try:
        from ..database import engine

        with Session(engine) as session:
            session.expire_on_commit = False
            rows = session.exec(
                select(Server).where(Server.backup_direct == True)  # noqa: E712
            ).all()
            index = _index_rows(list(rows))
            for row in index.values():
                _ = (
                    row.backup_paths,
                    row.ssh_username,
                    row.hostname,
                    row.backup_path_rules,
                    row.id,
                    row.name,
                )
                session.expunge(row)
            return index
    except Exception:
        logger.warning("direct host lookup failed", exc_info=True)
        return {}


def partition_checked(
    checked: list[str],
    hosts: list[Server] | None = None,
) -> tuple[list[str], list[tuple[Server, list[str]]]]:
    """Herder paths, then (direct host, ticked paths under that host)."""
    index = _index_rows(hosts) if hosts is not None else load_direct_index()
    local: list[str] = []
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for rel in checked:
        top = rel.split("/", 1)[0]
        server = index.get(top)
        if server is None:
            local.append(rel)
            continue
        if top not in grouped:
            order.append(top)
            grouped[top] = []
        grouped[top].append(rel)
    return local, [(index[name], grouped[name]) for name in order]


def _index_from_session(session: Session) -> dict[str, Server]:
    try:
        rows = session.exec(
            select(Server).where(Server.backup_direct == True)  # noqa: E712
        ).all()
    except Exception:
        logger.warning("direct host lookup failed", exc_info=True)
        return {}
    return _index_rows(list(rows))


def path_is_direct(session: Session, rel: str) -> bool:
    top = (rel or "").strip().strip("/").split("/", 1)[0]
    if not top:
        return False
    return top in _index_from_session(session)


def merge_directory(
    session: Session,
    rel: str,
    disk_rows: list[dict] | None,
) -> list[dict] | None:
    """Disk rows plus a direct host that has no tree on /backups.

    None means the path is missing and it is not a direct host.
    """
    from .backup_replicate import _clean_rel

    try:
        cleaned = _clean_rel(rel)
    except ValueError:
        return None
    index = _index_from_session(session)
    top = cleaned.split("/", 1)[0] if cleaned else ""
    if disk_rows is None:
        if not cleaned and not index:
            return None
        if cleaned and top not in index:
            return None
    rows = [dict(row) for row in (disk_rows or [])]
    names = {str(row.get("name") or "") for row in rows}
    if not cleaned:
        for row in rows:
            if str(row.get("name") or "") in index:
                row["direct"] = True
    rows.extend(_virtual_rows(cleaned, index, names))
    return rows


def _virtual_rows(cleaned: str, index: dict[str, Server], names: set[str]) -> list[dict]:
    rows: list[dict] = []
    if not cleaned:
        for folder in sorted(index):
            if folder in names:
                continue
            rows.append(_direct_entry(folder, folder))
        return rows
    top = cleaned.split("/", 1)[0]
    server = index.get(top)
    if server is None or cleaned != top:
        return rows
    for item in server.get_backup_sources():
        if not item.get("enabled", True):
            continue
        name = dest_name(item)
        if not name or name in names:
            continue
        rows.append(_direct_entry(name, f"{top}/{name}"))
    return rows


def _direct_entry(name: str, path: str) -> dict:
    return {
        "name": name,
        "path": path,
        "is_dir": True,
        "size": 0,
        "size_h": "",
        "mtime": 0,
        "direct": True,
    }


def selected_direct_providers(values) -> list[str]:
    """Keep Drive, OneDrive, and SMB, in the order they were posted."""
    chosen: list[str] = []
    for item in values or []:
        provider = str(item or "").strip().lower()
        if provider in DIRECT_PROVIDERS and provider not in chosen:
            chosen.append(provider)
    return chosen


def parse_direct_targets(raw: str | None) -> list[str]:
    """Providers this host sends to. Empty or missing means none."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return selected_direct_providers(data)


def configured_destinations(session: Session) -> list[BackupDestination]:
    """Saved Drive, OneDrive, and SMB rows, in that order."""
    rows = session.exec(
        select(BackupDestination).where(BackupDestination.enabled == True)  # noqa: E712
    ).all()
    by_provider: dict[str, BackupDestination] = {}
    for row in rows:
        if row.provider in DIRECT_PROVIDERS and credentials_saved(row):
            by_provider[row.provider] = row
    return [by_provider[provider] for provider in DIRECT_PROVIDERS if provider in by_provider]


def configured_destination_choices(session: Session) -> list[dict[str, str]]:
    return [
        {"provider": row.provider, "label": destination_name(row.provider)}
        for row in configured_destinations(session)
    ]


def direct_where_label(raw: str | None, choices: list[dict[str, str]]) -> str:
    wanted = parse_direct_targets(raw)
    labels = {item["provider"]: item["label"] for item in choices}
    names = [labels.get(provider) or destination_name(provider) for provider in wanted]
    return ", ".join(names) if names else "no destination chosen"


def followup_destinations(session: Session, server: Server) -> list[BackupDestination]:
    """Saved destinations this direct host pushes to on Backup now."""
    wanted = set(parse_direct_targets(getattr(server, "backup_direct_targets", None)))
    if not wanted:
        return []
    return [dest for dest in configured_destinations(session) if dest.provider in wanted]


def rels_for_push(destination: BackupDestination, server: Server) -> tuple[list[str], list[str]]:
    """Ticked folder when the host is in the tree. Otherwise the whole host."""
    folder = host_folder_name(server)
    checked, skipped = parse_selection(destination.selection_json)
    scoped, scoped_skip = paths_for_scope(checked, skipped, folder)
    if scoped:
        return scoped, scoped_skip
    under = [item for item in skipped if item == folder or item.startswith(folder + "/")]
    return [folder], under


def run_direct_backup(
    server: Server,
    sources_override: list[dict] | None = None,
    job_id: int | None = None,
) -> dict:
    """Push this host's sources. Does not create a tree under /backups."""
    from . import backup as backup_mod
    from .backup_path_policy import filter_allowed_sources, parse_rules
    from .demo import demo_mode

    hostname = server.hostname
    if job_id:
        backup_mod._active_job_id[hostname] = job_id
    if demo_mode():
        backup_mod._clear_progress(hostname)
        backup_mod._active_job_id.pop(hostname, None)
        return {"server": hostname, "direct": True, "error": "Demo does not upload", "results": []}

    sources = sources_override if sources_override is not None else server.get_backup_sources()
    rules = parse_rules(getattr(server, "backup_path_rules", None))
    sources, rejected = filter_allowed_sources(sources, rules)
    results: list[dict] = []
    for bad in rejected:
        results.append(
            {
                "source": bad.get("source"),
                "error": bad.get("error") or "Denied by path policy",
                "skipped": True,
                "rc": 1,
            }
        )
        backup_mod._set_progress(
            hostname,
            log_line=f"Denied by policy: {bad.get('source')} — {bad.get('error')}",
        )
    enabled = [item for item in sources if item.get("enabled", True)]
    if not enabled and not results:
        backup_mod._clear_progress(hostname)
        backup_mod._active_job_id.pop(hostname, None)
        return {"server": hostname, "direct": True, "error": "No backup sources on this host", "results": []}

    from ..database import engine

    with Session(engine) as session:
        session.expire_on_commit = False
        dests = followup_destinations(session, server)
        for dest in dests:
            session.expunge(dest)
    if not dests:
        backup_mod._clear_progress(hostname)
        backup_mod._active_job_id.pop(hostname, None)
        return {"server": hostname, "direct": True, "error": NO_DEST_MSG, "results": results}

    backup_mod._set_progress(hostname, current="preparing", log_line="Sending files straight to the copy")
    lock = backup_mod._get_backup_lock(int(server.id or 0))
    with lock:
        for dest in dests:
            rels, skipped = rels_for_push(dest, server)
            batch = push_server(
                server,
                dest,
                rels,
                skipped,
                sources=enabled,
                own_lock=False,
            )
            results.extend(batch)
    backup_mod._clear_progress(hostname)
    backup_mod._active_job_id.pop(hostname, None)
    return {"server": hostname, "direct": True, "results": results}


def push_groups(
    destination: BackupDestination,
    groups: list[tuple[Server, list[str]]],
    skipped: list[str],
) -> tuple[list[str], list[str]]:
    """Copy now and a destination schedule. One host push per direct folder."""
    copied: list[str] = []
    errors: list[str] = []
    for server, rels in groups:
        rows = push_server(server, destination, rels, skipped, own_lock=True)
        sent = False
        for row in rows:
            label = str(row.get("rel") or row.get("source") or host_folder_name(server))
            if row.get("error"):
                errors.append(f"{label}: {row['error']}"[:500])
            elif row.get("skipped"):
                continue
            else:
                copied.append(label)
                sent = True
        if not sent and not any(row.get("error") for row in rows):
            errors.append(f"{host_folder_name(server) or server.hostname}: nothing to send")
    return copied, errors


def push_server(
    server: Server,
    destination: BackupDestination,
    rels: list[str],
    skipped: list[str],
    *,
    sources: list[dict] | None = None,
    own_lock: bool = True,
) -> list[dict]:
    from .demo import demo_mode
    from .server_job_lock import release_server_lock, try_acquire_server_lock

    folder = host_folder_name(server) or server.hostname
    if demo_mode():
        return [{"source": folder, "rel": folder, "error": "Demo does not upload"}]
    token = None
    if own_lock:
        if not getattr(server, "id", None):
            return [{"source": folder, "rel": folder, "error": "Host is missing an id"}]
        token = try_acquire_server_lock(
            "backup",
            int(server.id),
            holder=f"direct-{getattr(destination, 'id', None) or 'copy'}",
        )
        if not token:
            return [
                {
                    "source": folder,
                    "rel": folder,
                    "error": "Another backup is running on this host",
                }
            ]
    try:
        if own_lock:
            from . import backup as backup_mod

            with backup_mod._get_backup_lock(int(server.id)):
                return _push_unlocked(
                    server,
                    destination,
                    rels,
                    skipped,
                    sources=sources,
                    lock_token=token,
                )
        return _push_unlocked(
            server,
            destination,
            rels,
            skipped,
            sources=sources,
            lock_token=None,
        )
    finally:
        if token and getattr(server, "id", None):
            release_server_lock("backup", int(server.id), token)
            from . import backup as backup_mod

            backup_mod._clear_progress(server.hostname)


def _push_unlocked(
    server: Server,
    destination: BackupDestination,
    rels: list[str],
    skipped: list[str],
    *,
    sources: list[dict] | None,
    lock_token: str | None,
) -> list[dict]:
    from . import backup as backup_mod

    hostname = server.hostname
    folder = host_folder_name(server) or hostname
    use_sources = sources if sources is not None else server.get_backup_sources()
    plans = planned_syncs(server, use_sources, rels, skipped)
    if not plans:
        return [
            {
                "source": folder,
                "rel": folder,
                "error": "Nothing on this host matches the ticked folder",
            }
        ]
    label = destination_name(destination.provider)
    remote_root = _remote_root(destination)
    client = None
    local_config = ""
    token = secrets.token_hex(8)
    user_bin = f"/tmp/piherder-rclone-{token}"
    config_remote = f"/tmp/ph-rclone-{token}.conf"
    sa_remote = f"/tmp/ph-rclone-{token}.sa.json"
    empty_dir = f"/tmp/ph-empty-{token}"
    staged_as_root = False
    use_sudo = False
    binary = user_bin
    secret = ""
    refresh = ""
    try:
        herder_bin = herder_rclone_bin()
    except FileNotFoundError as exc:
        return [{"source": folder, "rel": folder, "error": str(exc)}]
    try:
        client = get_ssh_client(server)
        sftp = client.open_sftp()
        try:
            sftp.put(herder_bin, user_bin)
            sftp.chmod(user_bin, 0o700)
        finally:
            sftp.close()
        rc, out, err = run_command(client, shlex.quote(user_bin) + " version", timeout=30)
        if rc != 0:
            blob = f"{err or ''} {out or ''}".lower()
            if "exec format" in blob or "cannot execute" in blob:
                message = (
                    f"This herder's rclone cannot run on {hostname}. "
                    "The host and the herder need the same CPU architecture."
                )
            else:
                message = "rclone copied to the host did not start"
            return [{"source": folder, "rel": folder, "error": message}]
        user = (server.ssh_username or "").strip()
        if user and user.lower() != "root":
            rc, _out, err = run_command(
                client, sudo_rclone_install_command(user_bin), timeout=30
            )
            if rc != 0:
                return [
                    {
                        "source": folder,
                        "rel": folder,
                        "error": (err or "Could not install rclone for a root read")[:500],
                    }
                ]
            staged_as_root = True
            rc, _out, err = run_command(
                client, "sudo -n " + shlex.quote(SUDO_RCLONE) + " version", timeout=30
            )
            if rc != 0:
                return [{"source": folder, "rel": folder, "error": root_read_required_message()}]
            use_sudo = True
            binary = SUDO_RCLONE
        local_config, secret, refresh, use_trash = _open_destination_rclone(destination)
        with open(local_config, encoding="utf-8") as handle:
            rewritten, local_sa = rewrite_service_account(handle.read(), sa_remote)
        sftp = client.open_sftp()
        try:
            with sftp.open(config_remote, "w") as remote:
                remote.write(rewritten.encode("utf-8"))
            sftp.chmod(config_remote, 0o600)
            if local_sa and os.path.isfile(local_sa):
                sftp.put(local_sa, sa_remote)
                sftp.chmod(sa_remote, 0o600)
        finally:
            sftp.close()
        from .backup import _folder_exists_via_ssh

        results: list[dict] = []
        for plan in plans:
            src = plan["source"]
            if not _folder_exists_via_ssh(client, plan["local"], server.ssh_username or ""):
                backup_mod._set_progress(hostname, log_line=f"Skipped {src}: directory does not exist")
                results.append({"source": src, "rel": plan["rel"], "skipped": True, "reason": "missing"})
                continue
            remote_path = f"{remote_root}/{plan['rel']}"
            command = remote_sync_command(
                binary,
                config_path=config_remote,
                source=plan["local"],
                remote_path=remote_path,
                excludes=plan["excludes"],
                use_trash=use_trash,
                use_sudo=use_sudo,
            )
            backup_mod._set_progress(
                hostname,
                current=src,
                log_line=f"Sending {src} to {label}",
                force=True,
            )
            rc, err = _run_remote(
                client,
                command,
                hostname,
                int(server.id or 0),
                lock_token,
                [secret, refresh],
                heartbeat=f"Still sending {src}…",
            )
            if rc != 0:
                results.append(
                    {
                        "source": src,
                        "rel": plan["rel"],
                        "rc": rc,
                        "error": (err or "rclone failed")[:500],
                    }
                )
            else:
                results.append({"source": src, "rel": plan["rel"], "rc": 0, "direct": True})
        return results
    except Exception as exc:
        logger.warning("direct backup failed for %s: %s", hostname, exc)
        message = _redact(_redact(str(exc)[:500], secret), refresh)
        return [{"source": folder, "rel": folder, "error": message or "direct backup failed"}]
    finally:
        if client is not None:
            try:
                _cleanup_remote(
                    client,
                    user_bin=user_bin,
                    config_remote=config_remote,
                    sa_remote=sa_remote,
                    empty_dir=empty_dir,
                    sudo_installed=staged_as_root,
                )
            except Exception:
                logger.warning("direct backup cleanup failed for %s", hostname, exc_info=True)
            try:
                client.close()
            except Exception:
                pass
        if local_config:
            discard_rclone_config(local_config)
        from . import backup as backup_mod

        backup_mod._active_backup_channels.pop(hostname, None)


def _remote_root(destination: BackupDestination) -> str:
    from .backup_replicate import _remote_dir, smb_remote_root

    if destination.provider == "smb":
        return smb_remote_root(destination)
    return _remote_dir(destination)


def _run_remote(
    client,
    command: str,
    hostname: str,
    server_id: int,
    lock_token: str | None,
    redact_secrets: list[str],
    *,
    heartbeat: str,
) -> tuple[int, str]:
    from . import backup as backup_mod
    from .server_job_lock import refresh_server_lock

    _stdin, stdout, _stderr = client.exec_command(command)
    channel = stdout.channel
    backup_mod._active_backup_channels[hostname] = channel
    chunks: list[bytes] = []
    last_ping = time.time()
    last_refresh = last_ping
    try:
        while not channel.exit_status_ready():
            if channel.recv_stderr_ready():
                chunks.append(channel.recv_stderr(8192))
            if channel.recv_ready():
                channel.recv(8192)
            time.sleep(2)
            now = time.time()
            if now - last_ping >= 10:
                backup_mod._set_progress(hostname, log_line=heartbeat)
                last_ping = now
            if lock_token and server_id and now - last_refresh >= 30:
                refresh_server_lock("backup", server_id, lock_token)
                last_refresh = now
        while channel.recv_stderr_ready():
            chunks.append(channel.recv_stderr(8192))
        rc = int(channel.recv_exit_status())
    except Exception as exc:
        rc = 1
        chunks.append(str(exc).encode(errors="replace"))
    finally:
        backup_mod._active_backup_channels.pop(hostname, None)
    err = b"".join(chunks).decode(errors="replace")
    for secret in redact_secrets:
        err = _redact(err, secret)
    return rc, err[-2000:]


def _cleanup_remote(
    client,
    *,
    user_bin: str,
    config_remote: str,
    sa_remote: str,
    empty_dir: str,
    sudo_installed: bool,
) -> None:
    quoted = " ".join(shlex.quote(path) for path in (user_bin, config_remote, sa_remote))
    run_command(client, "rm -f " + quoted, timeout=20)
    if sudo_installed:
        run_command(client, "mkdir -p " + shlex.quote(empty_dir), timeout=15)
        run_command(client, sudo_rclone_cleanup_command(empty_dir), timeout=20)
        run_command(client, "rm -rf " + shlex.quote(empty_dir), timeout=15)
    _status, out, _err = run_command(
        client,
        "test -e " + shlex.quote(config_remote) + " && echo left || echo gone",
        timeout=15,
    )
    if "left" not in (out or ""):
        return
    blank = empty_dir + "-blank"
    run_command(client, ": > " + shlex.quote(blank), timeout=15)
    run_command(
        client,
        "sudo -n rsync -a --chmod=600 "
        + shlex.quote(blank)
        + " "
        + shlex.quote(config_remote),
        timeout=15,
    )
    run_command(
        client,
        "rm -f " + shlex.quote(blank) + " " + shlex.quote(config_remote),
        timeout=15,
    )
