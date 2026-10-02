"""Settings actions for the fleet copy of /backups (Drive or SMB)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import BackupDestination, User
from ..security.auth import (
    cookie_auth_kwargs,
    cookie_delete_kwargs,
    create_access_token,
    decode_token_payload,
    get_admin_user,
)
from ..services import backup_replicate as copies
from ..services.input_validation import ValidationError, safe_cron

router = APIRouter()

COPY_DRAFT_COOKIE = "ph_copy_draft"
# Validation and sign-in failures. The edit sheet stays open for these.
FORM_ERRORS = frozenset({
    "cron",
    "client",
    "folder",
    "provider",
    "host",
    "share",
    "user",
    "password",
    "path",
    "domain",
    "origin",
    "state",
    "google",
})


def _redirect(query: str = "", provider: str = "") -> RedirectResponse:
    url = "/herder-backups?tab=backup"
    extra = []
    if query:
        extra.append(query)
    if provider in ("drive", "smb"):
        extra.append("copy_provider=" + provider)
    if extra:
        url = url + "&" + "&".join(extra)
    return RedirectResponse(url, status_code=303)


def _draft_fields(
    *,
    provider: str,
    schedule_cron: str,
    after_host_backup: str,
    drive_folder: str = "",
    client_id: str = "",
    client_secret: str = "",
    host: str = "",
    share: str = "",
    smb_path: str = "",
    username: str = "",
    password: str = "",
    domain: str = "",
) -> dict[str, str]:
    """Submitted edit fields. Secrets stay out of the redirect URL."""
    return {
        "cd": "1",
        "provider": provider if provider in ("drive", "smb") else "drive",
        "schedule_cron": schedule_cron or "",
        "after_host_backup": "1" if after_host_backup in ("1", "on", "true") else "",
        "drive_folder": drive_folder or "",
        "client_id": client_id or "",
        "client_secret": client_secret or "",
        "host": host or "",
        "share": share or "",
        "smb_path": smb_path or "",
        "username": username or "",
        "password": password or "",
        "domain": domain or "",
    }


def _redirect_keeping_draft(code: str, draft: dict[str, str]) -> RedirectResponse:
    provider = draft.get("provider") or ""
    response = _redirect("copy_error=" + code, provider)
    token = create_access_token(draft, expires_delta=timedelta(minutes=10))
    response.set_cookie(
        COPY_DRAFT_COOKIE,
        token,
        **cookie_auth_kwargs(max_age=600),
    )
    return response


def read_copy_draft(request: Request) -> dict[str, str] | None:
    """One-shot form draft. None when the cookie is missing or not ours."""
    raw = request.cookies.get(COPY_DRAFT_COOKIE) or ""
    if not raw:
        return None
    payload = decode_token_payload(raw)
    if not payload or str(payload.get("cd") or "") != "1":
        return None
    return {key: str(payload.get(key) or "") for key in _draft_fields(provider="drive", schedule_cron="", after_host_backup="")}


def _blank_destination(provider: str) -> BackupDestination:
    now = datetime.utcnow()
    return BackupDestination(
        name="LAN NAS / SMB" if provider == "smb" else "Google Drive",
        provider=provider,
        enabled=True,
        selection_json=copies.selection_json([], []),
        created_at=now,
        updated_at=now,
    )


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
        return "provider"
    try:
        cron = safe_cron(schedule_cron, field="schedule", allow_empty=True) or ""
    except ValidationError:
        return "cron"
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
        return str(exc) if str(exc) in ("folder", "client") else "client"
    return None


def _apply_smb_form(
    dest,
    *,
    schedule_cron: str,
    after_host_backup: str,
    host: str,
    share: str,
    smb_path: str,
    username: str,
    password: str,
    domain: str,
):
    try:
        cron = safe_cron(schedule_cron, field="schedule", allow_empty=True) or ""
    except ValidationError:
        return "cron"
    try:
        copies.store_smb(
            dest,
            host=host,
            share=share,
            path=smb_path,
            username=username,
            password=password,
            domain=domain,
            schedule=cron or None,
            after_host_backup=after_host_backup in ("1", "on", "true"),
        )
    except ValueError as exc:
        return (
            str(exc)
            if str(exc) in ("host", "share", "path", "user", "password", "domain")
            else "password"
        )
    return None


def _copy_row(session: Session, provider: str):
    """Saved destination only. Listing must not recreate a row Remove just deleted."""
    try:
        key = copies.normalize_provider(provider)
    except ValueError:
        return None
    row = copies.find_destination(session, key)
    if row is None or not copies.has_saved_destination(row):
        return None
    return row


@router.get("/backup-copies/list")
async def list_copy_dir(
    p: str = "",
    provider: str = "drive",
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    dest = _copy_row(session, provider)
    if dest is None:
        return JSONResponse({"ok": False, "error": "That destination is not saved."}, status_code=400)
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
    dest = _copy_row(session, str((body or {}).get("provider") or "drive"))
    if dest is None:
        return JSONResponse({"ok": False, "error": "That destination is not saved."}, status_code=400)
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
    host: str = Form(""),
    share: str = Form(""),
    smb_path: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    domain: str = Form(""),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    draft = _draft_fields(
        provider=provider,
        schedule_cron=schedule_cron,
        after_host_backup=after_host_backup,
        drive_folder=drive_folder,
        client_id=client_id,
        client_secret=client_secret,
        host=host,
        share=share,
        smb_path=smb_path,
        username=username,
        password=password,
        domain=domain,
    )
    try:
        key = copies.normalize_provider(provider)
    except ValueError:
        return _redirect_keeping_draft("provider", draft)
    draft["provider"] = key
    dest = copies.find_destination(session, key) or _blank_destination(key)
    if key == "smb":
        failed = _apply_smb_form(
            dest,
            schedule_cron=schedule_cron,
            after_host_backup=after_host_backup,
            host=host,
            share=share,
            smb_path=smb_path,
            username=username,
            password=password,
            domain=domain,
        )
    else:
        failed = _apply_drive_form(
            dest,
            provider=key,
            schedule_cron=schedule_cron,
            after_host_backup=after_host_backup,
            drive_folder=drive_folder,
            client_id=client_id,
            client_secret=client_secret,
        )
    if failed:
        session.rollback()
        return _redirect_keeping_draft(failed, draft)
    if dest.id is None:
        session.add(dest)
    _commit_drive(session, dest)
    return _redirect("copy_saved=1", key)


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
    draft = _draft_fields(
        provider="drive",
        schedule_cron=schedule_cron,
        after_host_backup=after_host_backup,
        drive_folder=drive_folder,
        client_id=client_id,
        client_secret=client_secret,
    )
    dest = copies.find_destination(session, "drive") or _blank_destination("drive")
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
        session.rollback()
        return _redirect_keeping_draft(failed, draft)
    if dest.id is None:
        session.add(dest)
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
    dest = copies.get_or_create(session, "drive")
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
    dest = copies.get_or_create(session, "drive")
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


@router.post("/backup-copies/remove")
async def remove_copy_destination(
    provider: str = Form("drive"),
    confirm: str = Form(""),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Settings only. Not a job, token, or MCP action. Remote files stay."""
    from ..services.audit_write import make_audit_log
    from ..services.demo import DemoBlocked

    try:
        key = copies.normalize_provider(provider)
    except ValueError:
        return _redirect("copy_error=provider")
    try:
        summary = copies.remove_destination(session, key, confirm=confirm)
    except DemoBlocked:
        from ..services.demo import http_403_if_demo

        http_403_if_demo("settings_write")
        raise
    except ValueError as exc:
        code = str(exc) if str(exc) in ("confirm", "missing", "provider") else "confirm"
        return _redirect("copy_error=" + code, key)
    session.add(
        make_audit_log(
            user_id=user.id,
            action="backup_destination_remove",
            status="success",
            details=json.dumps(
                {
                    "provider": summary.get("provider") or key,
                    "remote_files": "kept",
                }
            ),
        )
    )
    session.commit()
    return _redirect("copy_removed=1", key)


@router.post("/backup-copies/test")
async def test_copy_account(
    provider: str = Form("drive"),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    del user
    try:
        key = copies.normalize_provider(provider)
    except ValueError:
        return _redirect("copy_error=provider")
    dest = _copy_row(session, key)
    if dest is None:
        return _redirect("copy_error=missing", key)
    result = copies.probe(dest)
    code = result.get("code") or "rclone"
    if result.get("ok") == "1":
        return _redirect("copy_test=ok", key)
    return _redirect("copy_test=" + code, key)


@router.post("/backup-copies/run")
async def run_copy_now(
    provider: str = Form("drive"),
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    try:
        key = copies.normalize_provider(provider)
    except ValueError:
        return _redirect("copy_error=provider")
    dest = _copy_row(session, key)
    if dest is None:
        return _redirect("copy_error=missing", key)
    job = copies.enqueue(session, dest, user_id=user.id)
    if job.status == "failed":
        try:
            err = json.loads(job.details or "{}").get("error") or "failed"
        except Exception:
            err = "failed"
        return _redirect("copy_error=" + str(err)[:80], key)
    return RedirectResponse(f"/jobs?highlight={job.id}", status_code=303)
