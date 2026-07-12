from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlmodel import select, Session
import os

from .database import init_db, engine
from .models import Server, User
from .routers import auth as auth_router
from .routers import servers as servers_router
from .routers import audit as audit_router
from .routers import notifications as notifications_router
from .routers import push as push_router
from .routers import metrics as metrics_router
from .routers import api_v1 as api_v1_router
from .routers import integrations as integrations_router
from .routers import fleet_services as fleet_services_router
from .routers import templates_svc as templates_svc_router
from . import templates as templates_mod  # shared Jinja instance (avoids circular)
from .security.auth import get_optional_current_user, get_password_hash
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
                sync_herder_backup_schedule,
                sync_stack_health_schedule,
                sync_integrations_poll_schedule,
            )
            sync_all_server_cron_jobs(scheduler, HAS_SCHEDULER)
            sync_herder_backup_schedule(scheduler, HAS_SCHEDULER)
            sync_docker_inventory_schedule(scheduler, HAS_SCHEDULER)
            sync_stack_health_schedule(scheduler, HAS_SCHEDULER)
            sync_integrations_poll_schedule(scheduler, HAS_SCHEDULER)
        except Exception as e:
            print(f"Scheduler init skipped: {e}")

    # Seed builtin service templates into catalog (idempotent)
    try:
        from .services.service_templates import ensure_builtin_templates_in_db

        with Session(engine) as db:
            n = ensure_builtin_templates_in_db(db)
            if n:
                print(f"Service templates: updated {n} builtin catalog row(s)")
    except Exception as e:
        logger.warning("Service template seed skipped: %s", e)

    yield

    if HAS_SCHEDULER and scheduler and scheduler.running:
        scheduler.shutdown()


app = FastAPI(
    title="PiHerder",
    description=(
        "Self-hosted fleet manager. Interactive UI uses session cookies. "
        "Automation uses **Bearer API tokens** under `/api/v1` "
        "(admin-managed; see **docs/API.md** and Settings → API tokens)."
    ),
    version="0.2.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "api-v1",
            "description": (
                "Automation REST API. Auth: `Authorization: Bearer ph_…`. "
                "Scopes: `read`, `jobs`, `edit`, optional `feature:backup|os|docker`. "
                "Optional per-token IP/CIDR allowlist."
            ),
        },
        {"name": "auth", "description": "Browser login / account"},
        {"name": "servers", "description": "Fleet servers (UI + HTMX)"},
        {"name": "metrics", "description": "Prometheus scrape (/metrics)"},
    ],
)

# Optional CORS (opt-in allowlist). Default off — UI is same-origin; n8n/HA are server-side.
# CORS is not an auth layer: /api/v1 still requires Bearer + scopes + IP allowlist.
from .config import settings as _app_settings
from .services.cors_policy import apply_cors_middleware, parse_cors_origins

_cors_origins = parse_cors_origins(getattr(_app_settings, "CORS_ORIGINS", None))
if _cors_origins:
    apply_cors_middleware(app, _cors_origins)
    logger.info("CORS enabled for origins: %s", ", ".join(_cors_origins))

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
from .routers import settings as settings_router
from .services import scheduler as sched

app.include_router(auth_router.router, prefix="/auth", tags=["auth"])
app.include_router(servers_router.router, prefix="/servers", tags=["servers"])
app.include_router(audit_router.router, prefix="", tags=["audit"])
app.include_router(notifications_router.router, prefix="", tags=["notifications"])
app.include_router(push_router.router, prefix="", tags=["push"])
app.include_router(jobs_page_router.router, prefix="", tags=["jobs"])
app.include_router(metrics_router.router, prefix="", tags=["metrics"])
app.include_router(api_v1_router.router, prefix="/api/v1", tags=["api-v1"])
app.include_router(settings_router.router, prefix="", tags=["settings"])
app.include_router(integrations_router.router, prefix="", tags=["integrations"])
app.include_router(fleet_services_router.router, prefix="", tags=["fleet-services"])
app.include_router(templates_svc_router.router, prefix="", tags=["templates"])

# Re-export schedule helpers used by lifespan and other routers
schedule_backup_job = sched.schedule_backup_job
sync_herder_backup_schedule = sched.sync_herder_backup_schedule
schedule_herder_backup_job = sched.schedule_herder_backup_job
HERDER_SCHEDULE_JOB_ID = sched.HERDER_SCHEDULE_JOB_ID


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, user: User = Depends(get_optional_current_user)):
    # Lightweight fleet dashboard from DB (no SSH).
    servers = []
    fleet = None
    open_alerts = 0
    service_count = 0
    service_down = 0
    db = Session(engine)
    try:
        rows = list(db.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
        servers = [row.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}) for row in rows]
        if user:
            from .services.fleet_status import summarize_fleet
            from .services import notifications as notif_svc
            from .services.integrations import registry as integ_reg
            fleet = summarize_fleet(rows)
            try:
                open_alerts = notif_svc.open_count(db)
            except Exception:
                open_alerts = 0
            try:
                chips = integ_reg.fleet_service_chips(db)
                service_count = len(chips)
                service_down = sum(1 for c in chips if c.get("state") == "down")
            except Exception:
                service_count = 0
                service_down = 0
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
            "service_count": service_count,
            "service_down": service_down,
            "user": user,
            "pihole_url": getattr(_settings, "PIHOLE_URL", None),
            "lean_page": True,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}

