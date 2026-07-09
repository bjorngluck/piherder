from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import select, func

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Server, User
from ..security.auth import get_current_user
from ..services.audit_format import format_audit_entry

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


def _apply_audit_filters(query, *, search, server_id, user_id, status, action, date_from, date_to):
    if search:
        query = query.where(
            (AuditLog.action.contains(search))
            | (AuditLog.details.contains(search))
            | (AuditLog.output_snippet.contains(search))
        )
    if server_id and str(server_id).strip():
        try:
            query = query.where(AuditLog.server_id == int(server_id))
        except ValueError:
            pass
    if user_id and str(user_id).strip():
        try:
            query = query.where(AuditLog.user_id == int(user_id))
        except ValueError:
            pass
    if status:
        query = query.where(AuditLog.status == status)
    if action:
        query = query.where(AuditLog.action == action)
    df = _parse_date_start(date_from)
    dt = _parse_date_end(date_to)
    if df is not None:
        query = query.where(AuditLog.started_at >= df)
    if dt is not None:
        query = query.where(AuditLog.started_at <= dt)
    return query


@router.get("/audit", response_class=HTMLResponse)
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
    hide_noise: Optional[str] = "1",
    page: int = 1,
    per_page: int = 20,
):
    all_users: list = []
    servers_list: list = []
    logs_data: list = []
    distinct_actions: list = []
    distinct_statuses: list = []
    hide_incomplete = hide_noise not in ("0", "false", "no")
    per_page = _clamp_per_page(per_page)
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    total = 0
    total_pages = 1

    try:
        with next(get_session()) as s:
            servers_list = list(s.exec(select(Server).order_by(Server.name)).all())
            all_users = list(s.exec(select(User).order_by(User.email)).all())
            user_map = {}
            for u in all_users:
                label = (u.display_name or "").strip() or u.email
                if u.display_name and u.email:
                    label = f"{u.display_name} ({u.email})"
                user_map[u.id] = label

            recent = s.exec(select(AuditLog).order_by(AuditLog.started_at.desc()).limit(300)).all()
            distinct_actions = sorted({l.action for l in recent if l.action})
            distinct_statuses = sorted({l.status for l in recent if l.status})

            filt = dict(
                search=search,
                server_id=server_id,
                user_id=user_id,
                status=status,
                action=action,
                date_from=date_from,
                date_to=date_to,
            )

            def format_row(l: AuditLog) -> dict:
                d = l.model_dump()
                if l.user_id:
                    d["user_email"] = user_map.get(l.user_id)
                    d["user_label"] = user_map.get(l.user_id)
                else:
                    d["user_email"] = None
                    d["user_label"] = "system / scheduler"
                d["server_name"] = (
                    next((srv.name for srv in servers_list if srv.id == l.server_id), None)
                    if l.server_id
                    else None
                )
                return format_audit_entry(d)

            if hide_incomplete:
                # Noise is computed in Python — fetch a window, filter, then page
                base = _apply_audit_filters(
                    select(AuditLog), **filt
                ).order_by(AuditLog.started_at.desc())
                window = list(s.exec(base.limit(800)).all())
                formatted = [format_row(l) for l in window]
                formatted = [row for row in formatted if not row.get("is_noise")]
                total = len(formatted)
                total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
                if page > total_pages:
                    page = total_pages
                start = (page - 1) * per_page
                logs_data = formatted[start : start + per_page]
            else:
                count_q = _apply_audit_filters(select(func.count()).select_from(AuditLog), **filt)
                try:
                    total = int(s.exec(count_q).one())
                except Exception:
                    total = 0
                total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
                if page > total_pages:
                    page = total_pages
                offset = (page - 1) * per_page
                q = _apply_audit_filters(select(AuditLog), **filt).order_by(
                    AuditLog.started_at.desc()
                )
                page_rows = list(s.exec(q.offset(offset).limit(per_page)).all())
                logs_data = [format_row(l) for l in page_rows]
    except Exception:
        logs_data = []
        servers_list = []
        all_users = []
        total = 0
        total_pages = 1
        page = 1

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "title": "Audit Log",
            "lean_page": True,
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
            "actions": distinct_actions
            or [
                "backup",
                "os_patch",
                "container_patch",
                "user_created",
                "user_role_changed",
                "backup_restore",
            ],
            "hide_noise": hide_incomplete,
            "page": page,
            "per_page": per_page,
            "per_page_choices": list(PER_PAGE_CHOICES),
            "total": total,
            "total_pages": total_pages,
        },
    )
