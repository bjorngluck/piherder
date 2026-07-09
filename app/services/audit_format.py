"""Human-readable audit log summaries for the UI."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .backup import human_size, backup_succeeded, effective_backup_status
from .herder_backup import format_datetime_in_app_tz

_JOB_STARTED = re.compile(r"^Job #\d+ started$")

_ACTION_LABELS = {
    "backup": "Backup complete",
    "backup_request": "Backup requested",
    "backup_queued": "Backup queued",
    "backup_running": "Backup running",
    "backup_stop": "Stopped",
    "server_create": "Server added",
    "server_update": "Server updated",
    "server_password_set": "Password set",
    "server_password_clear": "Password cleared",
    "server_ssh_key_viewed": "SSH key viewed",
    "server_ssh_key_deployed": "SSH key deployed",
    "server_ssh_key_rotated": "SSH key rotated",
    "server_ssh_user_provisioned": "SSH user provisioned",
    "server_ssh_test": "SSH test",
    "server_backup_config": "Backup config",
    "server_backup_source_add": "Backup source added",
    "server_backup_source_remove": "Backup source removed",
    "server_move": "Server reordered",
    "server_reorder": "Server list reordered",
    "reboot": "Reboot",
    "retention": "Retention",
    "herder_backup": "PiHerder backup",
    "herder_restore": "PiHerder restore",
    "container_patch": "Containers",
    "os_patch": "OS patch",
    "os_update_check": "OS update check",
    "container_update_check": "Container update check",
    "server_os_check_schedule": "OS check schedule",
    "server_container_check_schedule": "Container check schedule",
    "server_os_apply_schedule": "OS apply schedule",
    "server_container_apply_schedule": "Container apply schedule",
    "backup_restore": "Backup restore",
    "user_role_changed": "User role changed",
    "user_created": "User created",
    "user_deleted": "User deleted",
    "diagnostics": "Diagnostics",
    "user_profile_updated": "Profile updated",
    "user_email_changed": "Email changed",
    "user_password_changed": "Password changed",
    "user_avatar_updated": "Avatar updated",
    "user_2fa_enabled": "2FA enabled",
    "user_2fa_disabled": "2FA disabled",
    "user_2fa_backup_regenerated": "2FA backup codes",
    "user_trusted_device_revoked": "Trusted device revoked",
}


def _parse_json(text: str | None) -> Any:
    if not text or not str(text).strip():
        return None
    t = str(text).strip()
    if not (t.startswith("{") or t.startswith("[")):
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def _source_failed(r: dict) -> bool:
    if r.get("skipped"):
        return False
    if r.get("error"):
        return True
    return int(r.get("rc", 0)) != 0


def _backup_summary(data: dict) -> str:
    if data.get("error"):
        err = str(data["error"])
        if "Superseded" in err:
            return "Superseded by newer backup"
        return err[:140]

    results = data.get("results") or []
    if not results:
        host = data.get("server")
        return f"Backup to {host}" if host else "Backup completed"

    failed = [r for r in results if _source_failed(r)]
    ok = len(results) - len(failed)
    if failed:
        first = failed[0]
        err = first.get("error") or f"rsync exit {first.get('rc', '?')}"
        src = first.get("source", "source")
        suffix = f" (+{len(failed) - 1} more)" if len(failed) > 1 else ""
        return f"{src}: {err}{suffix}"[:140]

    total = sum(int(r.get("size_bytes") or 0) for r in results)
    parts = [f"{len(results)} source{'s' if len(results) != 1 else ''}"]
    if total:
        parts.append(human_size(total))
    return " · ".join(parts)


def _duration(started: datetime | str | None, finished: datetime | str | None) -> str | None:
    if not started or not finished:
        return None
    try:
        if isinstance(started, str):
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        else:
            started_dt = started
        if isinstance(finished, str):
            finished_dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        else:
            finished_dt = finished
        secs = max(0, int((finished_dt - started_dt).total_seconds()))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    except Exception:
        return None


def _details_meta(details: str) -> dict:
    parsed = _parse_json(details)
    return parsed if isinstance(parsed, dict) else {}


def is_noise_entry(log: dict) -> bool:
    """Incomplete / superseded runs that clutter the audit view."""
    action = log.get("action") or ""
    status = log.get("status") or ""
    details = (log.get("details") or "").strip()
    snippet = (log.get("output_snippet") or "").strip()

    if action == "backup" and status == "running":
        return True

    # Stuck OS patch rows left "running" with no output (e.g. worker crash)
    if action == "os_patch" and status == "running" and not snippet:
        return True

    if action != "backup":
        return False

    if status in ("failed", "running") and not snippet and _JOB_STARTED.match(details):
        return True

    data = _parse_json(snippet)
    if isinstance(data, dict) and "Superseded" in str(data.get("error", "")):
        return True
    return False


def _os_patch_modal_body(parsed: dict) -> str:
    """Human-readable audit modal for OS patch (summary + optional apt log tail)."""
    lines: list[str] = []
    summary = (parsed.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")
    server = parsed.get("server")
    if server:
        lines.append(f"Server: {server}")
    steps = parsed.get("steps")
    if steps:
        lines.append(f"Steps: {', '.join(str(s) for s in steps)}")
    if parsed.get("needs_reboot"):
        lines.append("Reboot: required")
    elif "needs_reboot" in parsed:
        lines.append("Reboot: not required")
    if parsed.get("phased_deferred"):
        lines.append("Note: some packages deferred (Ubuntu phasing)")
    if parsed.get("error"):
        lines.append(f"Error: {parsed['error']}")

    results = parsed.get("results") or []
    if results:
        lines.append("")
        lines.append("Step results:")
        for r in results:
            step = r.get("step") or "?"
            if r.get("error"):
                lines.append(f"  {step}: ERROR {r['error']}")
            else:
                lines.append(f"  {step}: exit {r.get('rc', '?')}")

    post = parsed.get("post_check")
    if isinstance(post, dict):
        lines.append("")
        lines.append("After patch recheck:")
        if post.get("actionable_count") is not None:
            lines.append(f"  ready: {post.get('actionable_count')}")
        if post.get("phased_count"):
            lines.append(f"  phased: {post.get('phased_count')}")
        if "reboot_pending" in post:
            lines.append(f"  reboot_pending: {post.get('reboot_pending')}")

    tail = parsed.get("log_tail") or []
    if tail:
        lines.append("")
        lines.append("--- apt log (tail) ---")
        lines.extend(str(x) for x in tail)

    if not lines:
        return json.dumps(parsed, indent=2)
    return "\n".join(lines)


def format_audit_entry(log: dict) -> dict:
    """Enrich an audit log dict with display-friendly fields."""
    action = log.get("action") or ""
    status = log.get("status") or ""
    details = (log.get("details") or "").strip()
    snippet = (log.get("output_snippet") or "").strip()
    parsed = _parse_json(snippet)
    meta = _details_meta(details)

    action_label = _ACTION_LABELS.get(action, action.replace("_", " ").title())
    summary = details or "—"
    status_display = status

    if action in ("backup_request", "backup_queued", "backup_running"):
        job_id = meta.get("job_id")
        src = meta.get("source_filter") or "all sources"
        msg = meta.get("message") or ""
        prefix = f"Job #{job_id} · {src}" if job_id else src
        summary = f"{prefix} — {msg}" if msg else prefix
        if action == "backup_queued":
            status_display = "queued"

    elif action == "backup":
        if status == "running":
            summary = "Backup in progress…"
        elif isinstance(parsed, dict):
            summary = _backup_summary(parsed)
            status_display = effective_backup_status(status, parsed)
        elif status == "failed" and not snippet:
            summary = "Did not finish"
            status_display = "cancelled"
        elif status == "success":
            summary = _backup_summary(parsed) if isinstance(parsed, dict) else "Completed"
            status_display = effective_backup_status(status, parsed if isinstance(parsed, dict) else snippet)

    elif action == "backup_stop":
        summary = "Backup stopped by user"
        status_display = "stopped"

    elif action.startswith("server_"):
        msg = meta.get("message") or ""
        if action == "server_create":
            auth = meta.get("auth_method", "")
            host = meta.get("hostname", "")
            summary = f"{meta.get('name', 'Server')} ({host})" + (f" · {auth}" if auth else "")
        elif action == "server_ssh_key_viewed":
            summary = msg or "Public key viewed"
        elif action == "server_ssh_key_deployed":
            summary = msg or "Public key installed on host"
        elif action == "server_ssh_key_rotated":
            summary = msg or "Keypair rotated"
        elif action == "server_ssh_user_provisioned":
            nu = meta.get("new_username") or ""
            summary = msg or (f"User {nu}" if nu else "Least-priv user")
        elif action == "server_ssh_test":
            summary = msg or ("OK" if status == "success" else "Failed")
        elif action == "server_password_set":
            summary = msg or "SSH password stored (encrypted)"
        elif action == "server_password_clear":
            summary = msg or "SSH password removed"
        elif action == "server_backup_source_add":
            summary = f"Added {meta.get('source', 'source')}"
        elif action == "server_backup_source_remove":
            summary = f"Removed {meta.get('source', 'source')}"
        elif action == "server_backup_config":
            summary = msg or "Backup settings changed"
        elif action == "server_update":
            fields = meta.get("fields") or []
            summary = f"Updated: {', '.join(fields)}" if fields else (msg or "Settings changed")
        elif action == "server_move":
            summary = msg or f"Moved {meta.get('direction', '')}".strip()
        else:
            summary = msg or action.replace("server_", "").replace("_", " ").title()

    elif action == "reboot":
        summary = meta.get("message") or details[:140] if details else "Reboot"

    elif action == "herder_backup":
        if isinstance(parsed, dict) and parsed.get("path"):
            summary = f"Archive {str(parsed['path']).split('/')[-1]}"
        elif status == "failed":
            summary = (snippet or details or "Backup failed")[:140]

    elif action == "herder_restore":
        if isinstance(parsed, dict):
            n = parsed.get("restored_servers", 0)
            summary = f"Restored {n} server{'s' if n != 1 else ''}"
            if parsed.get("restored_audit"):
                summary += f" · {parsed['restored_audit']} audit entries"
        else:
            summary = details or "Restore run"

    elif action == "backup_restore":
        if isinstance(parsed, dict):
            src = parsed.get("source") or "?"
            dry = parsed.get("dry_run")
            summary = parsed.get("summary") or (
                f"{'Dry-run restore' if dry else 'Restore'} · {src}"
            )
        else:
            summary = details or "Backup restore"

    elif action == "os_patch":
        if status == "running" and not snippet:
            summary = details if details else "OS patch in progress…"
        elif isinstance(parsed, dict):
            summary = (parsed.get("summary") or "").strip()
            if not summary:
                # Build from results when older jobs lacked summary
                parts = []
                for r in parsed.get("results") or []:
                    step = r.get("step") or "?"
                    if r.get("error"):
                        parts.append(f"{step} ✗")
                    elif int(r.get("rc", 1)) != 0:
                        parts.append(f"{step} rc={r.get('rc')}")
                    else:
                        parts.append(f"{step} ✓")
                summary = " · ".join(parts) if parts else "OS patch"
                if parsed.get("needs_reboot"):
                    summary += " · reboot needed"
                if parsed.get("phased_deferred"):
                    summary += " · phased deferral"
            if not summary:
                # Finished details look like "Job #214 · upgrade ✓ · autoremove ✓"
                if details and " · " in details:
                    summary = details.split(" · ", 1)[1][:140]
                else:
                    summary = (snippet or details or action_label)[:140]
        else:
            # Legacy Python-repr snippets or plain error text
            raw = (snippet or details or action_label).strip()
            if raw.startswith("{") and "results" in raw:
                summary = "OS patch (see details)"
            elif details and " · " in details and not snippet:
                summary = details.split(" · ", 1)[1][:140]
            else:
                summary = raw[:140]

    elif action in ("retention", "container_patch", "diagnostics"):
        summary = (snippet or details or action_label)[:140]

    modal_body = snippet or details or "(no additional output)"
    if action == "os_patch" and isinstance(parsed, dict):
        modal_body = _os_patch_modal_body(parsed)
    elif isinstance(parsed, (dict, list)):
        modal_body = json.dumps(parsed, indent=2)

    started = log.get("started_at")
    finished = log.get("finished_at")
    started_display = format_datetime_in_app_tz(started, "%b %d %H:%M") if started else "—"
    finished_display = format_datetime_in_app_tz(finished, "%b %d %H:%M") if finished else None

    out = {
        **log,
        "action_label": action_label,
        "summary": summary,
        "status_display": status_display,
        "is_noise": is_noise_entry(log),
        "duration": _duration(started, finished),
        "modal_body": modal_body,
        "started_at_display": started_display,
        "finished_at_display": finished_display,
    }
    for k in ("started_at", "finished_at"):
        if k in out and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    return out