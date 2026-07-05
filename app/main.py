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
from . import templates as templates_mod  # shared Jinja instance (avoids circular)
from .security.auth import get_current_user, get_optional_current_user, get_password_hash
from .models import User
import logging

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pycron
    from datetime import datetime
    scheduler = AsyncIOScheduler()
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if alembic not used (dev convenience)
    init_db()

    # Ensure new columns exist (pragmatic for dev, until full migrations)
    # This runs on every web startup so schema is always up to date
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'server' AND column_name = 'backup_schedule'
                    ) THEN
                        ALTER TABLE server ADD COLUMN backup_schedule VARCHAR;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'server' AND column_name = 'backup_dest_root'
                    ) THEN
                        ALTER TABLE server ADD COLUMN backup_dest_root VARCHAR;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'server' AND column_name = 'backup_folder_name'
                    ) THEN
                        ALTER TABLE server ADD COLUMN backup_folder_name VARCHAR;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'server' AND column_name = 'last_backup_at'
                    ) THEN
                        ALTER TABLE server ADD COLUMN last_backup_at TIMESTAMP;
                    END IF;
                END $$;
            """))
            conn.commit()
    except Exception as e:
        print(f"Schema update warning (non-fatal): {e}")

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
                    hashed_password=get_password_hash(default_pass)
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

    # Start APScheduler (per-server schedules + herder manual only for now)
    if HAS_SCHEDULER and scheduler and not scheduler.running:
        scheduler.start()
        try:
            with Session(engine) as db:
                for server in db.exec(select(Server).order_by(Server.sort_order, Server.name)).all():
                    if server.backup_enabled and server.backup_schedule:
                        try:
                            cron = server.backup_schedule.strip()
                            parts = cron.split()
                            if len(parts) == 5:
                                trigger = CronTrigger(
                                    minute=parts[0], hour=parts[1],
                                    day=parts[2], month=parts[3], day_of_week=parts[4]
                                )
                                scheduler.add_job(
                                    func=schedule_backup_job,
                                    trigger=trigger,
                                    args=[server.id],
                                    id=f"backup_{server.id}",
                                    replace_existing=True,
                                    name=f"Backup {server.name}"
                                )
                        except Exception as e:
                            print(f"Failed schedule for server {server.id}: {e}")
        except Exception as e:
            print(f"Scheduler init skipped: {e}")

    yield

    if HAS_SCHEDULER and scheduler and scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="PiHerder", lifespan=lifespan)

# Static files (vendored JS for offline support + any other assets).
# Always ensure the directory exists so /static/* never 404s.
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Silence noisy favicon.ico probes (we serve favicon.svg via <link> in base.html)
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


app.include_router(auth_router.router, prefix="/auth", tags=["auth"])
app.include_router(servers_router.router, prefix="/servers", tags=["servers"])


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, user: User = Depends(get_optional_current_user)):
    # Lightweight server count for dashboard.
    servers = []
    db = Session(engine)
    try:
        servers = [row.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}) for row in db.exec(select(Server).order_by(Server.sort_order, Server.name)).all()]
    except Exception:
        servers = []
    finally:
        db.close()
    from .config import settings as _settings
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"title": "PiHerder Dashboard", "servers": servers, "user": user, "pihole_url": getattr(_settings, "PIHOLE_URL", None)}
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


def schedule_backup_job(server_id: int):
    """Called by APScheduler for automated backups."""
    if not HAS_SCHEDULER:
        return
    logger.debug(f"[SCHEDULER] Running scheduled backup for server {server_id}")
    try:
        db = Session(engine)
        try:
            server = db.get(Server, server_id)
            if server and server.backup_enabled:
                from .services import backup as backup_svc
                result = backup_svc.run_backup(server)
                logger.debug(f"[SCHEDULER] Backup result for {server_id}: {result.get('results', [])}")
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[SCHEDULER] Error in scheduled backup for {server_id}: {e}")


def schedule_herder_backup_job():
    """Global scheduled PiHerder self-backup (config + keys + optional audit)."""
    if not HAS_SCHEDULER:
        return
    logger.debug("[SCHEDULER] Running scheduled herder self-backup")
    try:
        from .services import herder_backup as hb
        cfg = hb.load_herder_config()
        mode = cfg.get("schedule_mode", "config_only")
        include_audit = (mode == "full")
        config_only = (mode != "full")
        path = hb.create_herder_backup(include_audit=include_audit, config_only=config_only)
        logger.debug(f"[SCHEDULER] Herder backup written: {path}")
        # Also audit it
        try:
            from .models import AuditLog
            with Session(engine) as s:
                al = AuditLog(
                    user_id=None,
                    server_id=None,
                    action="herder_backup",
                    status="success",
                    details=f"Scheduled self-backup ({mode}): {getattr(path, 'name', path)}",
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
                s.add(al)
                s.commit()
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"[SCHEDULER] Herder backup error: {e}")


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    user: User = Depends(get_current_user),
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    server_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
):
    try:
        with next(get_session()) as s:
            query = select(AuditLog).order_by(AuditLog.started_at.desc())
            if search:
                query = query.where(
                    (AuditLog.action.contains(search)) |
                    (AuditLog.details.contains(search)) |
                    (AuditLog.output_snippet.contains(search))
                )
            if server_id and server_id.strip():
                try:
                    query = query.where(AuditLog.server_id == int(server_id))
                except ValueError:
                    pass
            if user_id and user_id.strip():
                try:
                    query = query.where(AuditLog.user_id == int(user_id))
                except ValueError:
                    pass
            if status:
                query = query.where(AuditLog.status == status)
            if action:
                query = query.where(AuditLog.action == action)
            # Note: for date filter, simple string compare for demo; in real use datetime parse
            if date_from:
                query = query.where(AuditLog.started_at >= date_from)
            if date_to:
                query = query.where(AuditLog.started_at <= date_to)
            logs = s.exec(query.limit(100)).all()

            # Resolve usernames and make serializable
            user_ids = {l.user_id for l in logs if l.user_id}
            user_map = {}
            if user_ids:
                for u in s.exec(select(User).where(User.id.in_(list(user_ids)))):
                    user_map[u.id] = u.email

            servers_list = list(s.exec(select(Server).order_by(Server.name)).all())

            # For filter dropdowns (all users + distinct actions/statuses from recent data or full)
            all_users = list(s.exec(select(User).order_by(User.email)).all())
            # Collect distinct actions/statuses seen (limit query for perf)
            distinct_actions = sorted({l.action for l in logs if l.action})
            distinct_statuses = sorted({l.status for l in logs if l.status})

            logs_data = []
            for l in logs:
                d = l.model_dump()
                for k in ("started_at", "finished_at"):
                    if k in d and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                d["user_email"] = user_map.get(l.user_id) if l.user_id else None
                d["server_name"] = next((srv.name for srv in servers_list if srv.id == l.server_id), None) if l.server_id else None
                logs_data.append(d)
    except Exception:
        logs_data = []
        servers_list = []
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "title": "Audit Log",
            "logs": logs_data,
            "user": user,
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "server_id": server_id,
            "servers": servers_list,
            "user_id": user_id,
            "users": all_users,
            "status": status,
            "statuses": distinct_statuses or ["success", "failed", "running"],
            "action": action,
            "actions": distinct_actions or ["backup", "container_patch", "os_patch", "retention", "reboot", "backup_stop"],
        }
    )


# ------------------------------
# PiHerder self-backup (config/keys + optional audit) + restore
# ------------------------------

@app.get("/herder-backups", response_class=HTMLResponse)
async def herder_backups_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    from .config import settings as _settings
    backups = hb.list_backups()
    tz_choices = hb.get_available_timezones()
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="herder_backups.html",
        context={
            "title": "Settings",
            "user": user,
            "backups": backups,
            "herder_backup_schedule": getattr(_settings, "HERDER_BACKUP_SCHEDULE", None),
            "herder_config": hb.load_herder_config(),
            "tz_choices": tz_choices,
        }
    )


@app.post("/herder-backups/run")
async def trigger_herder_backup(
    backup_mode: str = Form("config_only"),
    background_tasks: BackgroundTasks = ...,
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    include_audit = (mode == "full")
    config_only = (mode == "config_only")
    # For "Run now" we execute directly (consistent with schedule path) so we can
    # respect the mode. Create proper audit entry.
    with next(get_session()) as s:
        audit = AuditLog(
            user_id=user.id,
            server_id=None,
            action="herder_backup",
            status="running",
            details="Manual self-backup triggered",
        )
        s.add(audit)
        s.commit()
        s.refresh(audit)

        try:
            path = hb.create_herder_backup(include_audit=include_audit, config_only=config_only)
            summary = json.dumps({"path": str(path)})
            audit.status = "success"
            audit.output_snippet = summary
            audit.finished_at = datetime.utcnow()
            s.add(audit)
            s.commit()
        except Exception as e:
            audit.status = "failed"
            audit.output_snippet = str(e)[:2000]
            audit.finished_at = datetime.utcnow()
            s.add(audit)
            s.commit()
            raise

    return RedirectResponse("/herder-backups", status_code=303)


@app.post("/herder-backups/restore")
async def restore_herder_backup(
    archive: str = Form(""),
    restore_file: UploadFile = File(None),
    restore_audit: bool = Form(False),
    dry_run: bool = Form(False),
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

        res = hb.restore_herder_backup(archive_to_use, restore_audit=restore_audit, dry_run=dry_run)

        with next(get_session()) as s:
            al = AuditLog(
                user_id=user.id,
                server_id=None,
                action="herder_restore",
                status="success",
                details=json.dumps(res),
            )
            s.add(al)
            s.commit()
        return RedirectResponse(f"/herder-backups?restored=1&dry={int(dry_run)}", status_code=303)
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
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    # Do not touch timezone here — it is managed separately via /herder-backups/timezone
    cfg = {"keep": max(1, min(100, keep)), "schedule_mode": schedule_mode}
    # merge without clobbering existing tz
    existing = hb.load_herder_config()
    if "timezone" in existing:
        cfg["timezone"] = existing["timezone"]
    hb.save_herder_config(cfg)
    return RedirectResponse("/herder-backups?config_saved=1", status_code=303)


@app.post("/herder-backups/timezone")
async def save_timezone(
    timezone: str = Form("UTC"),
    user: User = Depends(get_current_user),
):
    from .services import herder_backup as hb
    hb.set_app_timezone(timezone)
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

