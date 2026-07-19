from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
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
from .routers import certificates as certificates_router
from .routers import fleet_services as fleet_services_router
from .routers import templates_svc as templates_svc_router
from .routers import dns as dns_router
from . import templates as templates_mod  # shared Jinja instance (avoids circular)
from .security.auth import get_optional_current_user
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

    # First boot: no default user. Empty DB → open /auth/register creates the admin;
    # registration then closes unless ALLOW_OPEN_REGISTRATION=true.
    try:
        with Session(engine) as db:
            existing = db.exec(select(User)).first()
            if not existing:
                print(
                    "No users yet — open the UI and register the first admin account. "
                    "Self-registration closes after that (unless ALLOW_OPEN_REGISTRATION=true)."
                )
            else:
                print(f"Users present (example: {existing.email})")
    except Exception as e:
        logger.warning("User bootstrap check skipped: %s", e)

    # Warn on insecure production defaults (dev compose still works)
    try:
        from .config import settings as _cfg

        if (_cfg.SECRET_KEY or "").strip() in ("", "dev-secret-change-in-prod"):
            print(
                "WARNING: SECRET_KEY is the default/dev value — set a long random "
                "SECRET_KEY in .env before production use."
            )
        if not (_cfg.METRICS_TOKEN or "").strip():
            print(
                "NOTE: METRICS_TOKEN is unset — GET /metrics is open on the app port. "
                "Set a bearer token (or firewall) for production scrapes."
            )
    except Exception as e:
        logger.warning("Startup config checks skipped: %s", e)

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
                sync_stale_data_cleanup_schedule,
                sync_stack_health_schedule,
                sync_integrations_poll_schedule,
                sync_cert_renew_schedule,
                sync_template_drift_schedule,
                sync_nmap_schedules,
            )
            sync_all_server_cron_jobs(scheduler, HAS_SCHEDULER)
            sync_herder_backup_schedule(scheduler, HAS_SCHEDULER)
            sync_stale_data_cleanup_schedule(scheduler, HAS_SCHEDULER)
            sync_docker_inventory_schedule(scheduler, HAS_SCHEDULER)
            sync_stack_health_schedule(scheduler, HAS_SCHEDULER)
            sync_integrations_poll_schedule(scheduler, HAS_SCHEDULER)
            sync_cert_renew_schedule(scheduler, HAS_SCHEDULER)
            sync_template_drift_schedule(scheduler, HAS_SCHEDULER)
            sync_nmap_schedules(scheduler, HAS_SCHEDULER)
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

    # Optional GitHub release check (soft-fail; cached for About + banner)
    try:
        from .services.app_update import schedule_startup_check

        schedule_startup_check(delay_sec=20.0)
    except Exception as e:
        logger.debug("Update check schedule skipped: %s", e)

    yield

    if HAS_SCHEDULER and scheduler and scheduler.running:
        scheduler.shutdown()


from .version_info import APP_VERSION as _APP_VERSION

app = FastAPI(
    title="PiHerder",
    description=(
        "Self-hosted fleet manager. Interactive UI uses session cookies. "
        "Automation uses **Bearer API tokens** under `/api/v1` "
        "(admin-managed; see **docs/API.md** and Settings → API tokens)."
    ),
    version=_APP_VERSION,
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


class ClientIpMiddleware(BaseHTTPMiddleware):
    """Bind resolved client IP (Caddy XFF / peer) for AuditLog + rate limits."""

    async def dispatch(self, request: Request, call_next):
        from .services.request_ip import (
            client_ip_from_request,
            reset_request_client_ip,
            set_request_client_ip,
        )

        ip = client_ip_from_request(request)
        token = set_request_client_ip(ip)
        # Expose on request.state for handlers that prefer explicit access
        try:
            request.state.client_ip = ip
        except Exception:
            pass
        try:
            return await call_next(request)
        finally:
            reset_request_client_ip(token)


app.add_middleware(ClientIpMiddleware)

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
# Resolve from package dir — cwd-relative "app/static" breaks under pytest / some deploys.
from pathlib import Path as _Path

_STATIC_DIR = _Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _static_file(name: str) -> _Path:
    return _STATIC_DIR / name


# Serve favicon.ico (generated from logo) so browser probes get a real icon (the <link> in base.html uses the PNG)
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import FileResponse

    return FileResponse(_static_file("favicon.ico"), media_type="image/x-icon")


# Root-scoped service worker + manifest for PWA (scope must be /)
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    from fastapi.responses import FileResponse

    return FileResponse(
        _static_file("sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
async def web_manifest():
    from fastapi.responses import FileResponse

    return FileResponse(
        _static_file("manifest.webmanifest"),
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
app.include_router(certificates_router.router, prefix="", tags=["certificates"])
app.include_router(fleet_services_router.router, prefix="", tags=["fleet-services"])
app.include_router(templates_svc_router.router, prefix="", tags=["templates"])
app.include_router(dns_router.router, prefix="", tags=["dns"])
from .routers import about as about_router

app.include_router(about_router.router, prefix="", tags=["about"])

# Re-export schedule helpers used by lifespan and other routers
schedule_backup_job = sched.schedule_backup_job
sync_herder_backup_schedule = sched.sync_herder_backup_schedule
schedule_herder_backup_job = sched.schedule_herder_backup_job
HERDER_SCHEDULE_JOB_ID = sched.HERDER_SCHEDULE_JOB_ID


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, user: User = Depends(get_optional_current_user)):
    # Lightweight fleet dashboard from DB (no SSH, no full fabric SVG).
    servers = []
    fleet = None
    open_alerts = 0
    service_count = 0
    service_down = 0
    down_services: list = []
    network_pulse: dict = {}
    db = Session(engine)
    try:
        rows = list(db.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
        servers = [row.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}) for row in rows]
        if user:
            from .services.fleet_status import summarize_fleet
            from .services import notifications as notif_svc
            from .services.integrations import registry as integ_reg
            from .models import ServiceDnsRecord

            fleet = summarize_fleet(rows)
            try:
                open_alerts = notif_svc.open_count(db)
            except Exception:
                open_alerts = 0
            try:
                chips = integ_reg.fleet_service_chips(db)
                service_count = len(chips)
                downs = [c for c in chips if c.get("state") == "down"]
                service_down = len(downs)
                down_services = downs[:8]
            except Exception:
                service_count = 0
                service_down = 0
                down_services = []
            # Cheap fabric counts for the network showcase (no SVG layout).
            # "via NPM" = proxy hosts from NPM integrations (matches Catalog/NPM
            # detail). Not DNS via_proxy flags — the edge hostname itself is often
            # an NPM host but via_proxy=false (CNAME to self).
            try:
                recs = list(db.exec(select(ServiceDnsRecord)).all())
                npm_proxy_hosts = 0
                npm_seen = False
                for integ in integ_reg.list_integrations(
                    db, type_filter=integ_reg.TYPE_NPM
                ):
                    npm_seen = True
                    st = integ_reg.parse_last_status(integ)
                    if st.get("proxy_host_count") is not None:
                        npm_proxy_hosts += int(st.get("proxy_host_count") or 0)
                    else:
                        npm_proxy_hosts += len(st.get("proxy_hosts") or [])
                via_npm = (
                    npm_proxy_hosts
                    if npm_seen
                    else sum(1 for r in recs if getattr(r, "via_proxy", False))
                )
                network_pulse = {
                    "mapped_names": len(recs),
                    "via_npm": via_npm,
                    "hosts_named": sum(
                        1 for s in rows if (getattr(s, "dns_name", None) or "").strip()
                    ),
                    "hosts_total": len(rows),
                }
            except Exception:
                network_pulse = {
                    "mapped_names": 0,
                    "via_npm": 0,
                    "hosts_named": 0,
                    "hosts_total": len(rows),
                }
    except Exception:
        servers = []
        fleet = None
    finally:
        db.close()
    from .config import settings as _settings
    pihole_url = getattr(_settings, "PIHOLE_URL", None)
    pihole_integrations = []
    try:
        from .services.integrations import registry as integ_reg2
        from .services.integrations import pihole as ph_mod

        with Session(engine) as db2:
            ph_rows = integ_reg2.list_integrations(db2, type_filter=integ_reg2.TYPE_PIHOLE)
            for r in ph_rows:
                st = integ_reg2.parse_last_status(r)
                pihole_integrations.append(
                    {
                        "id": r.id,
                        "name": r.name,
                        "admin_url": ph_mod.admin_url(r.base_url),
                        "is_primary": integ_reg2.is_pihole_primary(r),
                        "ok": st.get("ok"),
                        "queries": st.get("queries"),
                        "percent_blocked": st.get("percent_blocked"),
                    }
                )
            if pihole_integrations:
                # Prefer primary admin URL over env fallback for quick link
                primary = next(
                    (p for p in pihole_integrations if p.get("is_primary")),
                    pihole_integrations[0],
                )
                pihole_url = primary.get("admin_url") or pihole_url
    except Exception:
        pihole_integrations = []
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
            "down_services": down_services,
            "network_pulse": network_pulse,
            "user": user,
            "pihole_url": pihole_url,
            "pihole_integrations": pihole_integrations,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}

