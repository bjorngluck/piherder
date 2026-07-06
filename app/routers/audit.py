from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import select
from typing import Optional
from ..database import get_session
from ..models import AuditLog, Server, User
from ..security.auth import get_current_user
from .. import templates as templates_mod
from ..services.audit_format import format_audit_entry

router = APIRouter()


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
):
    all_users: list = []
    servers_list: list = []
    logs_data: list = []
    distinct_actions: list = []
    distinct_statuses: list = []
    hide_incomplete = hide_noise not in ("0", "false", "no")

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
            if date_from:
                query = query.where(AuditLog.started_at >= date_from)
            if date_to:
                query = query.where(AuditLog.started_at <= date_to)
            logs = s.exec(query.limit(200)).all()

            user_ids = {l.user_id for l in logs if l.user_id}
            user_map = {}
            if user_ids:
                for u in s.exec(select(User).where(User.id.in_(list(user_ids)))):
                    user_map[u.id] = u.email

            servers_list = list(s.exec(select(Server).order_by(Server.name)).all())
            all_users = list(s.exec(select(User).order_by(User.email)).all())

            recent = s.exec(select(AuditLog).order_by(AuditLog.started_at.desc()).limit(300)).all()
            distinct_actions = sorted({l.action for l in recent if l.action})
            distinct_statuses = sorted({l.status for l in recent if l.status})

            for l in logs:
                d = l.model_dump()
                d["user_email"] = user_map.get(l.user_id) if l.user_id else None
                d["server_name"] = (
                    next((srv.name for srv in servers_list if srv.id == l.server_id), None)
                    if l.server_id else None
                )
                logs_data.append(format_audit_entry(d))

            if hide_incomplete:
                logs_data = [row for row in logs_data if not row.get("is_noise")]
            logs_data = logs_data[:100]
    except Exception:
        logs_data = []
        servers_list = []
        all_users = []

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
            "actions": distinct_actions or [
                "backup_request", "backup_queued", "backup_running", "backup",
                "server_create", "server_update", "server_password_set", "server_password_clear",
                "server_ssh_key_viewed", "server_backup_config", "server_backup_source_add",
                "server_backup_source_remove", "server_move", "reboot",
                "retention", "backup_stop", "herder_backup", "herder_restore",
            ],
            "hide_noise": hide_incomplete,
        }
    )
