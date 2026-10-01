"""Settings actions for the fleet Drive copy of /backups."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


def _public_origin(request: Request) -> str:
    from ..services.password_reset import configured_public_origin

    fixed = configured_public_origin()
    if fixed:
        return fixed
    parsed = urlparse(str(request.base_url))
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _apply_drive_form(
    dest,
    *,
    provider: str,
    schedule_cron: str,
    after_host_backup: str,
    drive_folder: str,
    client_id: str,
    client_secret: str,
):
    if (provider or "drive").strip() != "drive":
        return _redirect("copy_error=provider")
    try:
        cron = safe_cron(schedule_cron, field="schedule", allow_empty=True) or ""
    except ValidationError:
        return _redirect("copy_error=cron")
    try:
        folder = copies.clean_drive_folder(drive_folder)
        copies.store_oauth_client(
            dest,
            client_id=client_id,
            client_secret=client_secret,
            folder=folder,
            schedule=cron or None,
            after_host_backup=after_host_backup in ("1", "on", "true"),
        )
    except ValueError as exc:
        code = str(exc) if str(exc) in ("folder", "client") else "client"
        return _redirect("copy_error=" + code)
    return None


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


def _commit_drive(session: Session, dest) -> None:
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


@router.post("/backup-copies/config")
async def save_copy_config(
    provider: str = Form("drive"),
    schedule_cron: str = Form(""),
    after_host_backup: str = Form(""),
    drive_folder: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    dest = copies.get_or_create(session)
    failed = _apply_drive_form(
        dest,
        provider=provider,
        schedule_cron=schedule_cron,
        after_host_backup=after_host_backup,
        drive_folder=drive_folder,
        client_id=client_id,
        client_secret=client_secret,
    )
    if failed:
        return failed
    _commit_drive(session, dest)
    return _redirect("copy_saved=1")


@router.post("/backup-copies/google/start")
async def start_google_connect(
    request: Request,
    provider: str = Form("drive"),
    schedule_cron: str = Form(""),
    after_host_backup: str = Form(""),
    drive_folder: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    dest = copies.get_or_create(session)
    failed = _apply_drive_form(
        dest,
        provider=provider,
        schedule_cron=schedule_cron,
        after_host_backup=after_host_backup,
        drive_folder=drive_folder,
        client_id=client_id,
        client_secret=client_secret,
    )
    if failed:
        return failed
    _commit_drive(session, dest)
    session.refresh(dest)
    client = copies.oauth_client(dest)
    if not client["client_id"] or not client["client_secret"]:
        return _redirect("copy_error=client")
    origin = _public_origin(request)
    if not origin:
        return _redirect("copy_error=origin")
    state = copies.new_oauth_state()
    # Stay on this host. Chrome treats a form POST whose redirect chain
    # reaches Google as a forbidden form-action and saves the response as
    # start.json. /go returns a page; that page opens Google.
    response = RedirectResponse("/backup-copies/google/go", status_code=303)
    response.set_cookie(
        copies.OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=origin.startswith("https"),
        path="/",
    )
    return response


@router.get("/backup-copies/google/go")
async def google_connect_go(
    request: Request,
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    state = request.cookies.get(copies.OAUTH_STATE_COOKIE) or ""
    if not state:
        return _redirect("copy_error=state")
    origin = _public_origin(request)
    dest = copies.get_or_create(session)
    client = copies.oauth_client(dest)
    if not origin:
        return _redirect("copy_error=origin")
    if not client["client_id"] or not client["client_secret"]:
        return _redirect("copy_error=client")
    target = copies.google_auth_url(
        client["client_id"],
        copies.google_redirect_uri(origin),
        state,
    )
    from html import escape

    safe = escape(target, quote=True)
    page = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"refresh\" content=\"0;url={safe}\">"
        "<title>Continue to Google</title></head><body>"
        f"<p><a href=\"{safe}\">Continue to Google</a></p>"
        "</body></html>"
    )
    return HTMLResponse(page)


@router.get("/backup-copies/google/callback")
async def google_connect_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    if error or not code:
        return _redirect("copy_error=google")
    saved_state = request.cookies.get(copies.OAUTH_STATE_COOKIE) or ""
    if not saved_state or not state or saved_state != state:
        return _redirect("copy_error=state")
    origin = _public_origin(request)
    dest = copies.get_or_create(session)
    client = copies.oauth_client(dest)
    if not origin or not client["client_id"] or not client["client_secret"]:
        return _redirect("copy_error=client")
    try:
        token = copies.exchange_google_code(
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            code=code,
            redirect_uri=copies.google_redirect_uri(origin),
        )
    except ValueError:
        return _redirect("copy_error=google")
    email = copies.google_account_email(str(token.get("access_token") or ""))
    from ..security.encryption import encrypt_str

    plain = copies.pack_oauth_token(
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        refresh_token=str(token.get("refresh_token") or ""),
        access_token=str(token.get("access_token") or ""),
        email=email,
        expires_in=int(token["expires_in"]) if str(token.get("expires_in") or "").isdigit() else None,
    )
    dest.credentials_encrypted = encrypt_str(plain)
    _commit_drive(session, dest)
    response = _redirect("copy_saved=1")
    response.delete_cookie(copies.OAUTH_STATE_COOKIE, path="/")
    return response


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
