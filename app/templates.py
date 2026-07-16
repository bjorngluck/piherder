import json

from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from .services.app_settings import (
    get_app_timezone,
    format_datetime_in_app_tz,
    utc_isoformat,
)

templates = Jinja2Templates(directory="app/templates")

# Enable auto-reload so template changes (e.g. UI fixes to modals/logs) are picked up
# immediately when using volume mounts in Docker without restarting the container.
templates.env.auto_reload = True

# Make timezone selection from Settings apply globally in all templates
templates.env.globals["get_app_timezone"] = get_app_timezone
templates.env.globals["utc_isoformat"] = utc_isoformat


def ph_brand(extra_class: str = "") -> Markup:
    """Pi (ink / text color) + Herder (brand red) brand mark for UI copy."""
    cls = "ph-brand"
    if extra_class:
        cls = f"{cls} {extra_class}".strip()
    return Markup(
        f'<span class="{cls}" aria-label="PiHerder">'
        f'<span class="ph-brand-pi">Pi</span>'
        f'<span class="ph-brand-herder">Herder</span>'
        f"</span>"
    )


templates.env.globals["ph_brand"] = ph_brand


def _app_tz_filter(dt, fmt="%Y-%m-%d %H:%M"):
    """Render UTC-stored times in the Settings timezone (datetime or ISO string)."""
    return format_datetime_in_app_tz(dt, fmt)


templates.env.filters["app_tz"] = _app_tz_filter
templates.env.filters["utc_iso"] = utc_isoformat


def _fromjson_filter(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


templates.env.filters["fromjson"] = _fromjson_filter

# About / update banner (soft-fail; cached GitHub check)
from . import version_info as _vi
from .services import app_update as _app_update

templates.env.globals["app_version"] = _vi.get_app_version()
templates.env.globals["github_url"] = _vi.GITHUB_URL
templates.env.globals["docs_url"] = _vi.DOCS_URL
templates.env.globals["get_update_notice"] = _app_update.get_update_notice
