"""About page + optional force update-check."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import templates as templates_mod
from .. import version_info as vi
from ..models import User
from ..security.auth import get_current_user
from ..services import app_update

router = APIRouter(tags=["about"])


@router.get("/about", response_class=HTMLResponse)
async def about_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    force = (request.query_params.get("check") or "").strip() in ("1", "true", "yes")
    notice = app_update.get_update_notice(force=force)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="about.html",
        context={
            "title": "About",
            "user": user,
            "app_version": vi.get_app_version(),
            "about_tagline": vi.ABOUT_TAGLINE,
            "about_story": vi.ABOUT_STORY,
            "github_url": vi.GITHUB_URL,
            "github_releases_url": vi.GITHUB_RELEASES_URL,
            "docs_url": vi.DOCS_URL,
            "sponsor_url": vi.SPONSOR_URL,
            "license_name": vi.LICENSE_NAME,
            "license_url": vi.LICENSE_URL,
            "update_notice": notice,
            "check_forced": force,
        },
    )


@router.post("/about/check-update")
async def about_check_update(user: User = Depends(get_current_user)):
    del user
    app_update.refresh_update_check(force=True)
    return RedirectResponse("/about?check=1", status_code=303)
