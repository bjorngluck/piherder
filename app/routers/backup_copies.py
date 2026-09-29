"""Settings actions for the fleet Drive copy of /backups."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import User
from ..security.auth import get_admin_user
from ..services import backup_replicate as copies
from ..services.input_validation import ValidationError, safe_cron

router = APIRouter()


def _redirect(query: str = "") -> RedirectResponse:
    url = "/herder-backups?tab=backup"
    if query:
        url = f"{url}&{query}"
    return RedirectResponse(url, status_code=303)


@router.get("/backup-copies/list")
async def list_copy_dir(
    p: str = "",
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    dest = copies.get_or_create(session)
    checked, skipped = copies.parse_selection(dest.selection_json)
    try:
        rows = copies.list_directory(p)
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    for row in rows:
        row["state"] = copies.selection_state(row["path"], checked, skipped)
        row["included"] = row["state"] == "on"
    parent = (p or "").strip().strip("/")
    state = copies.selection_state(parent, checked, skipped)
    return {
        "ok": True,
        "path": parent,
        "state": state,
        "included": state == "on",
        "entries": rows,
    }


@router.post("/backup-copies/toggle")
async def toggle_copy_path(
    request: Request,
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    try:
        body = await request.json()
    except Exception:
        body = {}
    changes = (body or {}).get("changes")
    if not isinstance(changes, list):
        changes = [{"path": (body or {}).get("path"), "on": (body or {}).get("on")}]
    dest = copies.get_or_create(session)
    checked, skipped = copies.parse_selection(dest.selection_json)
    try:
        for change in changes:
            if not isinstance(change, dict):
                continue
            checked, skipped = copies.apply_toggle(
                checked, skipped, str(change.get("path") or ""), bool(change.get("on"))
            )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    dest.selection_json = copies.selection_json(checked, skipped)
    from datetime import datetime

    dest.updated_at = datetime.utcnow()
    session.add(dest)
    session.commit()
    return {"ok": True, "checked": checked, "skipped": skipped}


@router.post("/backup-copies/config")
async def save_copy_config(
    provider: str = Form("drive"),
    schedule_cron: str = Form(""),
    after_host_backup: str = Form(""),
    account_email: str = Form(""),
    private_key: str = Form(""),
    drive_folder: str = Form(""),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    dest = copies.get_or_create(session)
    if (provider or "drive").strip() != "drive":
        return _redirect("copy_error=provider")
    try:
        cron = safe_cron(schedule_cron, field="schedule", allow_empty=True) or ""
    except ValidationError:
        return _redirect("copy_error=cron")
    try:
        folder = copies.clean_drive_folder(drive_folder)
    except ValueError:
        return _redirect("copy_error=folder")
    dest.schedule = cron or None
    dest.after_host_backup = after_host_backup in ("1", "on", "true")
    try:
        cfg = json.loads(dest.config_json or "{}")
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["remote_dir"] = folder
    cfg["shared_with_me"] = True
    dest.config_json = json.dumps(cfg)
    email = (account_email or "").strip()
    key = (private_key or "").strip()
    saved = copies.credential_public(dest)
    saved_email = saved.get("email") or ""
    if key or (email and email != saved_email):
        if not key:
            if saved.get("kind") != "service_account":
                return _redirect("copy_error=key")
            try:
                previous = json.loads(copies.decrypt_token(dest))
            except Exception:
                return _redirect("copy_error=key")
            key = previous.get("private_key") or ""
        try:
            dest.credentials_encrypted = copies.encrypt_account(email, key)
        except ValueError as exc:
            code = str(exc) if str(exc) in ("email", "key") else "account"
            return _redirect("copy_error=" + code)
    from datetime import datetime

    dest.updated_at = datetime.utcnow()
    session.add(dest)
    session.commit()
    try:
        from ..main import HAS_SCHEDULER, scheduler
        from ..services.scheduler import sync_backup_copy_schedule

        sync_backup_copy_schedule(scheduler, HAS_SCHEDULER)
    except Exception:
        pass
    return _redirect("copy_saved=1")


@router.post("/backup-copies/test")
async def test_copy_account(
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    dest = copies.get_or_create(session)
    result = copies.probe(dest)
    code = result.get("code") or "rclone"
    if result.get("ok") == "1":
        return _redirect("copy_test=ok")
    return _redirect("copy_test=" + code)


@router.post("/backup-copies/run")
async def run_copy_now(
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    dest = copies.get_or_create(session)
    job = copies.enqueue(session, dest, user_id=user.id)
    if job.status == "failed":
        try:
            err = json.loads(job.details or "{}").get("error") or "failed"
        except Exception:
            err = "failed"
        return _redirect("copy_error=" + str(err)[:80])
    return RedirectResponse(f"/jobs?highlight={job.id}", status_code=303)
