"""Fleet-wide Jobs / task list (audit-style feed)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import select

from .. import templates as templates_mod
from ..database import get_session
from ..models import Job, Server, User
from ..security.auth import get_current_user
from ..services import jobs as job_service
from ..services.app_settings import format_datetime_in_app_tz

router = APIRouter()

PER_PAGE_CHOICES = (10, 20, 50)


def _parse_date_start(s: str | None):
    if not s or not str(s).strip():
        return None
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d")
    except Exception:
        return None


def _parse_date_end(s: str | None):
    """Inclusive end-of-day for date_to filter."""
    d = _parse_date_start(s)
    if d is None:
        return None
    return d + timedelta(days=1) - timedelta(microseconds=1)


def _clamp_per_page(raw) -> int:
    try:
        n = int(raw or 20)
    except Exception:
        n = 20
    if n in PER_PAGE_CHOICES:
        return n
    return min(PER_PAGE_CHOICES, key=lambda x: abs(x - n))


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    user: User = Depends(get_current_user),
    server_id: Optional[str] = None,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    active_only: Optional[str] = None,
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 20,
):
    """Task list across the fleet — similar feed layout to Audit."""
    servers_list: list = []
    rows: list = []
    active_count = 0
    total = 0
    distinct_types: list = []
    distinct_statuses = ["pending", "running", "success", "failed", "cancelled"]
    sid: int | None = None
    only_active = active_only in ("1", "on", "true", "yes")
    per_page = _clamp_per_page(per_page)
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    df = _parse_date_start(date_from)
    dt = _parse_date_end(date_to)

    try:
        with next(get_session()) as s:
            servers_list = list(s.exec(select(Server).order_by(Server.name)).all())
            name_map = {srv.id: srv.name for srv in servers_list}
            if server_id and str(server_id).strip():
                try:
                    sid = int(server_id)
                except ValueError:
                    sid = None
            total = job_service.count_jobs(
                s,
                server_id=sid,
                status=None if only_active else (status or None),
                job_type=job_type or None,
                active_only=only_active,
                date_from=df,
                date_to=dt,
            )
            total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
            if page > total_pages:
                page = total_pages
            offset = (page - 1) * per_page
            jobs = job_service.list_jobs(
                s,
                server_id=sid,
                status=None if only_active else (status or None),
                job_type=job_type or None,
                active_only=only_active,
                date_from=df,
                date_to=dt,
                limit=per_page,
                offset=offset,
            )
            active_count = job_service.count_jobs(
                s, server_id=sid, active_only=True, date_from=df, date_to=dt
            )
            recent = s.exec(select(Job).order_by(Job.created_at.desc()).limit(200)).all()
            distinct_types = sorted({j.job_type for j in recent if j.job_type})
            for j in jobs:
                d = job_service.job_public_dict(j, detail=True)
                d["server_name"] = name_map.get(j.server_id) if j.server_id else None
                when = j.finished_at or j.started_at or j.created_at
                d["when_display"] = format_datetime_in_app_tz(when) if when else "—"
                d["when_label"] = (
                    "Finished" if j.finished_at else "Started" if j.started_at else "Queued"
                )
                rows.append(d)
    except Exception:
        rows = []
        servers_list = []
        total = 0
        total_pages = 1
        page = 1

    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "title": "Jobs",
            "lean_page": True,
            "user": user,
            "jobs": rows,
            "servers": servers_list,
            "server_id": sid or "",
            "status": status or "",
            "job_type": job_type or "",
            "active_only": only_active,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "active_count": active_count,
            "job_types": distinct_types,
            "statuses": distinct_statuses,
            "type_labels": job_service.JOB_TYPE_LABELS,
            "page": page,
            "per_page": per_page,
            "per_page_choices": list(PER_PAGE_CHOICES),
            "total": total,
            "total_pages": total_pages,
        },
    )


@router.get("/jobs/{job_id}", response_class=JSONResponse)
async def job_detail_api(
    job_id: int,
    user: User = Depends(get_current_user),
):
    """JSON detail for jobs modal / API consumers."""
    with next(get_session()) as s:
        job = s.get(Job, job_id)
        if not job:
            return JSONResponse({"error": "not found"}, status_code=404)
        d = job_service.job_public_dict(job, detail=True)
        if job.server_id:
            srv = s.get(Server, job.server_id)
            d["server_name"] = srv.name if srv else None
        when = job.finished_at or job.started_at or job.created_at
        d["when_display"] = format_datetime_in_app_tz(when) if when else "—"
        return d


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    request: Request,
    job_id: int,
    user: User = Depends(get_current_user),
):
    """Cancel a pending/running job (Jobs screen or fetch).

    Returns JSON for ``Accept: application/json`` / XHR, else redirect to /jobs.
    """
    with next(get_session()) as s:
        job = s.get(Job, job_id)
        if not job:
            wants_json = _wants_json(request)
            if wants_json:
                return JSONResponse({"error": "not found"}, status_code=404)
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            job = job_service.cancel_job(s, job, user_id=user.id)
        except job_service.JobNotCancellable as e:
            if _wants_json(request):
                return JSONResponse(
                    {"error": e.message, "job": job_service.job_public_dict(job)},
                    status_code=409,
                )
            return RedirectResponse(
                f"/jobs?error=cancel&msg={quote(e.message)}",
                status_code=303,
            )
        d = job_service.job_public_dict(job, detail=True)
        if job.server_id:
            srv = s.get(Server, job.server_id)
            d["server_name"] = srv.name if srv else None

    if _wants_json(request):
        return JSONResponse({"ok": True, "job": d})
    return RedirectResponse(f"/jobs?cancelled={job_id}", status_code=303)


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    if (request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest":
        return True
    # fetch() from Jobs UI always sends this
    if (request.headers.get("x-piherder-cancel") or "") == "1":
        return True
    return False
