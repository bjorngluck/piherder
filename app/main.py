from fastapi import FastAPI, Request, Depends, Form, BackgroundTasks, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlmodel import select, Session
from typing import Optional
import os
import json
from datetime import datetime
from pathlib import Path

from .database import init_db, get_session, engine
from .models import Server, AuditLog
from .config import settings
from .routers import auth as auth_router
from .routers import servers as servers_router
from .routers import audit as audit_router
from .routers import notifications as notifications_router
from .routers import push as push_router
from .routers import metrics as metrics_router
from .routers import api_v1 as api_v1_router
from . import templates as templates_mod  # shared Jinja instance (avoids circular)
from .security.auth import get_current_user, get_optional_current_user, get_password_hash, get_admin_user
from .models import User
import logging

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema: Alembic is the source of truth (idempotent upgrade to head).
    # create_all remains a safety net for brand-new DBs if migrate is skipped.
    try:
        from .db_migrate import run_alembic_upgrade

        run_alembic_upgrade()
    except Exception as e:
        print(f"Alembic migration warning: {e}")
        try:
            init_db()
        except Exception as e2:
            print(f"init_db fallback failed: {e2}")
    else:
        # Ensure metadata tables exist even if migration only adds columns
        try:
            init_db()
        except Exception as e:
            print(f"init_db after migrate: {e}")

    try:
        from .services.jobs import cleanup_stale_backup_jobs
        with Session(engine) as db:
            cleanup_stale_backup_jobs(db)
    except Exception as e:
        logger.warning(f"Stale job cleanup skipped: {e}")

    # Create default admin user if none exist (for first-time login)
    try:
        db = Session(engine)
        try:
            existing = db.exec(select(User)).first()
            if not existing:
                default_email = "admin@example.com"
                default_pass = "admin"
                user = User(
                    email=default_email,
                    hashed_password=get_password_hash(default_pass),
                    role="admin",
                )
                db.add(user)
                db.commit()
                print(f"Created default admin: {default_email} / {default_pass}  -- CHANGE THIS IMMEDIATELY!")
            else:
                print(f"Default admin check: user {existing.email} already exists")
        finally:
            db.close()
    except Exception as e:
        print(f"Could not create default user: {e}")

    # Web Push: ensure VAPID keys exist (env override or auto-generate once into DB)
    try:
        from .services.push import ensure_vapid_keys

        with Session(engine) as db:
            creds = ensure_vapid_keys(db)
            if creds:
                print(f"Web Push VAPID ready (source={creds.source})")
            else:
                print("Web Push VAPID not available (generation failed or py_vapid missing)")
    except Exception as e:
        logger.warning("VAPID ensure skipped: %s", e)

    # Start APScheduler (per-server schedules + PiHerder self-backup)
    if HAS_SCHEDULER and scheduler and not scheduler.running:
        scheduler.start()
        try:
            from .services.scheduler import (
                sync_all_server_cron_jobs,
                sync_docker_inventory_schedule,
            )
            sync_all_server_cron_jobs(scheduler, HAS_SCHEDULER)
            sync_herder_backup_schedule(scheduler, HAS_SCHEDULER)
            sync_docker_inventory_schedule(scheduler, HAS_SCHEDULER)
        except Exception as e:
            print(f"Scheduler init skipped: {e}")

    yield

    if HAS_SCHEDULER and scheduler and scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="PiHerder", lifespan=lifespan)

# Onboarding redirects (must change password / force 2FA)
from .security.auth import OnboardingRedirect


@app.exception_handler(OnboardingRedirect)
async def onboarding_redirect_handler(request: Request, exc: OnboardingRedirect):
    return RedirectResponse(url=exc.location, status_code=303)


# Static files (vendored JS for offline support + any other assets).
# Always ensure the directory exists so /static/* never 404s.
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Serve favicon.ico (generated from logo) so browser probes get a real icon (the <link> in base.html uses the PNG)
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import FileResponse
    return FileResponse("app/static/favicon.ico", media_type="image/x-icon")


# Root-scoped service worker + manifest for PWA (scope must be /)
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    from fastapi.responses import FileResponse

    return FileResponse(
        "app/static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
async def web_manifest():
    from fastapi.responses import FileResponse

    return FileResponse(
        "app/static/manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


from .routers import jobs_page as jobs_page_router

app.include_router(auth_router.router, prefix="/auth", tags=["auth"])
app.include_router(servers_router.router, prefix="/servers", tags=["servers"])
app.include_router(audit_router.router, prefix="", tags=["audit"])
app.include_router(notifications_router.router, prefix="", tags=["notifications"])
app.include_router(push_router.router, prefix="", tags=["push"])
app.include_router(jobs_page_router.router, prefix="", tags=["jobs"])
app.include_router(metrics_router.router, prefix="", tags=["metrics"])
app.include_router(api_v1_router.router, prefix="/api/v1", tags=["api-v1"])

# Scheduler helpers extracted
from .services import scheduler as sched


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, user: User = Depends(get_optional_current_user)):
    # Lightweight fleet dashboard from DB (no SSH).
    servers = []
    fleet = None
    open_alerts = 0
    db = Session(engine)
    try:
        rows = list(db.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
        servers = [row.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}) for row in rows]
        if user:
            from .services.fleet_status import summarize_fleet
            from .services import notifications as notif_svc
            fleet = summarize_fleet(rows)
            try:
                open_alerts = notif_svc.open_count(db)
            except Exception:
                open_alerts = 0
    except Exception:
        servers = []
        fleet = None
    finally:
        db.close()
    from .config import settings as _settings
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "title": "PiHerder Dashboard",
            "servers": servers,
            "fleet": fleet,
            "open_alerts": open_alerts,
            "user": user,
            "pihole_url": getattr(_settings, "PIHOLE_URL", None),
            "lean_page": True,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


# Schedule helpers moved to services/scheduler.py (re-exported for use in lifespan/herder UI)
schedule_backup_job = sched.schedule_backup_job
sync_herder_backup_schedule = sched.sync_herder_backup_schedule
schedule_herder_backup_job = sched.schedule_herder_backup_job
HERDER_SCHEDULE_JOB_ID = sched.HERDER_SCHEDULE_JOB_ID


# ------------------------------
# PiHerder self-backup (config/keys + optional audit) + restore
# ------------------------------

@app.get("/herder-backups", response_class=HTMLResponse)
async def herder_backups_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from .services import herder_backup as hb
    from .services import api_tokens as tok_svc
    from .security.auth import user_role, ROLE_ADMIN
    from .config import settings as _settings
    backups = hb.list_backups()
    tz_choices = hb.get_available_timezones()
    cfg = hb.load_herder_config()
    schedule_status = "disabled"
    next_run = None
    if HAS_SCHEDULER and scheduler and cfg.get("schedule_enabled"):
        job = scheduler.get_job(HERDER_SCHEDULE_JOB_ID)
        if job:
            schedule_status = "enabled"
            nr = getattr(job, "next_run_time", None)
            if nr:
                next_run = hb.format_datetime_in_app_tz(nr)

    api_token_rows = []
    if user_role(user) == ROLE_ADMIN:
        try:
            api_token_rows = [
                tok_svc.token_public_dict(t)
                for t in tok_svc.list_api_tokens(session, include_revoked=True)
            ]
        except Exception:
            api_token_rows = []

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="herder_backups.html",
        context={
            "title": "Settings",
            "user": user,
            "backups": backups,
            "herder_backup_dir": str(hb.HERDER_BACKUP_DIR),
            "herder_config": cfg,
            "tz_choices": tz_choices,
            "schedule_status": schedule_status,
            "schedule_next_run": next_run,
            "api_tokens": api_token_rows,
            "is_admin": user_role(user) == ROLE_ADMIN,
            "new_api_token_secret": request.query_params.get("token_secret"),
            "new_api_token_name": request.query_params.get("token_name"),
        }
    )


@app.post("/herder-backups/api-tokens")
async def create_api_token_form(
    name: str = Form(...),
    scope_read: Optional[str] = Form(None),
    scope_jobs: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    from .services import api_tokens as tok_svc
    from urllib.parse import quote

    scopes = []
    if scope_read in ("1", "on", "true"):
        scopes.append("read")
    if scope_jobs in ("1", "on", "true"):
        scopes.append("jobs")
    if not scopes:
        scopes = ["read", "jobs"]
    row, plain = tok_svc.create_api_token(
        session, name=name, created_by=user, scopes=scopes
    )
    # One-time reveal via query (same pattern as admin user invite; short-lived in browser history)
    return RedirectResponse(
        f"/herder-backups?token_created=1&token_name={quote(row.name)}"
        f"&token_secret={quote(plain)}",
        status_code=303,
    )


@app.post("/herder-backups/api-tokens/{token_id}/revoke")
async def revoke_api_token_form(
    token_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    from .services import api_tokens as tok_svc

    row = tok_svc.get_api_token(session, token_id)
    if row:
        tok_svc.revoke_api_token(session, row)
    return RedirectResponse("/herder-backups?token_revoked=1", status_code=303)


@app.post("/herder-backups/run")
async def trigger_herder_backup(
    backup_mode: str = Form("config_only"),
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    mode = backup_mode if backup_mode in ("config_only", "full") else "config_only"
    include_audit = (mode == "full")
    config_only = (mode != "full")
    with next(get_session()) as s:
        audit = AuditLog(
            user_id=user.id,
            server_id=None,
            action="herder_backup",
            status="running",
            details=f"Manual self-backup triggered ({mode})",
            started_at=datetime.utcnow(),
        )
        s.add(audit)
        s.commit()
        s.refresh(audit)

        try:
            path = hb.create_herder_backup(include_audit=include_audit, config_only=config_only)
            summary = json.dumps({"path": str(path), "mode": mode})
            audit.status = "success"
            audit.output_snippet = summary
            audit.finished_at = datetime.utcnow()
            s.add(audit)
            s.commit()
            return RedirectResponse(
                f"/herder-backups?backup_ok=1&file={path.name}",
                status_code=303,
            )
        except Exception as e:
            audit.status = "failed"
            audit.output_snippet = str(e)[:2000]
            audit.finished_at = datetime.utcnow()
            s.add(audit)
            s.commit()
            return RedirectResponse(f"/herder-backups?error={str(e)[:120]}", status_code=303)


@app.post("/herder-backups/restore")
async def restore_herder_backup(
    archive: str = Form(""),
    restore_file: UploadFile = File(None),
    restore_audit: Optional[str] = Form(None),
    dry_run: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    import shutil
    from datetime import datetime as dt
    tmp_path = None
    try:
        archive_to_use = archive
        if restore_file and restore_file.filename:
            # Always write upload temp to /tmp (writable) to avoid [Errno 13] on /herder_backups or fallback.
            # Use timestamped safe name to avoid odd extensions or collisions from client filename.
            orig = Path(restore_file.filename).name
            ts = dt.utcnow().strftime("%Y%m%d-%H%M%S")
            # ensure ends with reasonable tar ext
            if not orig.endswith((".tar.gz", ".tgz", ".tar")):
                safe_name = f".upload-piherder-{ts}-restore.tar.gz"
            else:
                safe_name = f".upload-{orig}"
            tmp_path = Path("/tmp") / safe_name
            with tmp_path.open("wb") as f:
                shutil.copyfileobj(restore_file.file, f)
            archive_to_use = str(tmp_path)

        if not archive_to_use:
            raise ValueError("Provide a server path or upload a file")

        do_audit = restore_audit in ("1", "on", "true")
        preview = dry_run in ("1", "on", "true")
        res = hb.restore_herder_backup(archive_to_use, restore_audit=do_audit, dry_run=preview)

        with next(get_session()) as s:
            al = AuditLog(
                user_id=user.id,
                server_id=None,
                action="herder_restore",
                status="success",
                details=json.dumps(res),
                output_snippet=json.dumps(res),
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
            s.add(al)
            s.commit()

        servers_n = res.get("would_restore_servers") if preview else res.get("restored_servers", 0)
        audit_n = res.get("would_restore_audit") if preview else res.get("restored_audit", 0)
        return RedirectResponse(
            f"/herder-backups?restored=1&dry={int(preview)}&servers={servers_n}&audit={audit_n}",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(f"/herder-backups?error={str(e)[:120]}", status_code=303)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


@app.get("/herder-backups/download")
async def download_herder_backup(path: str = "", name: str = "", user: User = Depends(get_current_user)):
    from fastapi.responses import FileResponse
    possible_roots = [Path(settings.HERDER_BACKUP_ROOT), Path("/backups"), Path("/backups/piherder_backups"), Path("/herder_backups")]
    if name:
        p = None
        for root in possible_roots:
            cand = root / name
            if cand.exists():
                p = cand
                break
        if p is None:
            raise HTTPException(404)
    else:
        p = Path(path)
    if not p.exists() or not any(str(p).startswith(str(r)) for r in possible_roots):
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.post("/herder-backups/config")
async def save_herder_config(
    keep: int = Form(10),
    schedule_mode: str = Form("config_only"),
    schedule_enabled: Optional[str] = Form(None),
    schedule_cron: str = Form("0 3 * * *"),
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    enabled = schedule_enabled in ("1", "on", "true")
    cron = (schedule_cron or "").strip() or "0 3 * * *"
    if enabled:
        try:
            hb.validate_cron_expression(cron)
        except ValueError as e:
            return RedirectResponse(f"/herder-backups?error={str(e)[:120]}", status_code=303)

    cfg = {
        "keep": max(1, min(100, keep)),
        "schedule_mode": schedule_mode if schedule_mode in ("config_only", "full") else "config_only",
        "schedule_enabled": enabled,
        "schedule_cron": cron,
    }
    hb.save_herder_config(cfg)
    sync_herder_backup_schedule(scheduler, HAS_SCHEDULER)
    return RedirectResponse("/herder-backups?config_saved=1", status_code=303)


@app.post("/herder-backups/security")
async def save_security_policy(
    force_2fa: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """Global security toggles (force 2FA, etc.)."""
    from .services import herder_backup as hb
    from .security.auth import user_role, ROLE_ADMIN

    if user_role(user) != ROLE_ADMIN:
        raise HTTPException(403, "Admin role required")
    hb.save_herder_config({"force_2fa": force_2fa in ("1", "on", "true")})
    return RedirectResponse("/herder-backups?security_saved=1", status_code=303)


@app.post("/herder-backups/update-checks")
async def save_update_check_defaults(
    os_check_global_enabled: Optional[str] = Form(None),
    os_check_cron: str = Form("0 0 * * *"),
    container_check_global_enabled: Optional[str] = Form(None),
    container_check_cron: str = Form("0 0 * * *"),
    update_check_jitter: Optional[str] = Form(None),
    apply_to_all: Optional[str] = Form(None),
    enable_feature_flags: Optional[str] = Form(None),
    enable_backups: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """Save global update-check defaults; optionally apply schedules to every eligible server."""
    from .services import herder_backup as hb
    from .services import update_check_config as ucc
    from .services.scheduler import sync_all_server_cron_jobs
    from .database import engine as _engine
    from sqlmodel import Session as _Session

    os_on = os_check_global_enabled in ("1", "on", "true")
    cont_on = container_check_global_enabled in ("1", "on", "true")
    jitter = update_check_jitter in ("1", "on", "true")
    do_apply = apply_to_all in ("1", "on", "true")
    # Default ON when apply_to_all (checkbox present); unchecked = omitted from form
    enable_flags = enable_feature_flags in ("1", "on", "true")
    enable_bak = enable_backups in ("1", "on", "true")
    os_cron = (os_check_cron or "").strip() or "0 0 * * *"
    cont_cron = (container_check_cron or "").strip() or "0 0 * * *"
    try:
        if os_on:
            hb.validate_cron_expression(os_cron)
        if cont_on:
            hb.validate_cron_expression(cont_cron)
    except ValueError as e:
        return RedirectResponse(f"/herder-backups?error={str(e)[:120]}", status_code=303)

    hb.save_herder_config({
        "os_check_global_enabled": os_on,
        "os_check_cron": os_cron,
        "container_check_global_enabled": cont_on,
        "container_check_cron": cont_cron,
        "update_check_jitter": jitter,
    })

    applied = {
        "os_applied": 0, "container_applied": 0, "servers_total": 0,
        "flags_os": 0, "flags_container": 0, "flags_backup": 0,
    }
    if do_apply:
        with _Session(_engine) as db:
            applied = ucc.apply_global_update_checks_to_all(
                db,
                os_enabled=os_on,
                os_cron=os_cron,
                container_enabled=cont_on,
                container_cron=cont_cron,
                jitter=jitter,
                only_patch_enabled=False,
                enable_feature_flags=enable_flags,
                enable_backups=enable_bak,
            )
        sync_all_server_cron_jobs(scheduler, HAS_SCHEDULER)

    return RedirectResponse(
        f"/herder-backups?update_checks_saved=1"
        f"&os={applied.get('os_applied', 0)}"
        f"&cont={applied.get('container_applied', 0)}"
        f"&total={applied.get('servers_total', 0)}"
        f"&flags_os={applied.get('flags_os', 0)}"
        f"&flags_cont={applied.get('flags_container', 0)}"
        f"&flags_bak={applied.get('flags_backup', 0)}"
        f"&applied={'1' if do_apply else '0'}",
        status_code=303,
    )


@app.post("/herder-backups/timezone")
async def save_timezone(
    timezone: str = Form("UTC"),
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    hb.set_app_timezone(timezone)
    sync_herder_backup_schedule(scheduler, HAS_SCHEDULER)
    return RedirectResponse("/herder-backups?config_saved=1", status_code=303)


@app.post("/herder-backups/delete")
async def delete_herder_backup(
    name: str = Form(...),
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    possible_roots = [Path(settings.HERDER_BACKUP_ROOT), Path("/backups"), Path("/backups/piherder_backups"), Path("/herder_backups")]
    deleted = False
    for root in possible_roots:
        p = root / name
        if p.exists() and str(p).startswith(str(root)) and p.suffix == ".gz":  # safety
            try:
                p.unlink()
                deleted = True
            except Exception:
                pass
            break
    return RedirectResponse("/herder-backups?deleted=1" if deleted else "/herder-backups?error=delete_failed", status_code=303)

