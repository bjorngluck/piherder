from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlmodel import select, func

from .. import templates as templates_mod
from ..database import get_session
from ..models import ApiToken, AuditLog, Server, User
from ..security.auth import ROLE_OPERATOR, get_current_user, get_operator_user, role_at_least
from ..services.audit_format import format_audit_entry, format_actor_label
from ..services.audit_write import make_audit_log
from ..services.app_settings import calendar_today_in_app_tz
from ..services import list_query as lq

router = APIRouter()

PER_PAGE_CHOICES = lq.PER_PAGE_CHOICES


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


def _apply_audit_filters(
    query,
    *,
    search,
    server_id,
    user_id,
    api_token_id,
    status,
    action,
    date_from,
    date_to,
):
    if search:
        query = query.where(
            (AuditLog.action.contains(search))
            | (AuditLog.details.contains(search))
            | (AuditLog.output_snippet.contains(search))
            | (AuditLog.api_token_name.contains(search))
            | (AuditLog.client_ip.contains(search))
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
    if api_token_id and str(api_token_id).strip():
        try:
            query = query.where(AuditLog.api_token_id == int(api_token_id))
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
    api_token_id: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    hide_noise: Optional[str] = "1",
    page: int = 1,
    per_page: int = 20,
):
    all_users: list = []
    api_tokens_list: list = []
    servers_list: list = []
    logs_data: list = []
    distinct_actions: list = []
    distinct_statuses: list = []
    pulse: dict = {
        "success": 0,
        "failed": 0,
        "running": 0,
        "other": 0,
        "sample": 0,
        "by_action": [],
    }
    hide_incomplete = hide_noise not in ("0", "false", "no")
    per_page = lq.per_page_from_request(request)
    page = lq.page_from_request(request)
    total = 0
    total_pages = 1
    filter_token_label: str | None = None

    try:
        with next(get_session()) as s:
            servers_list = list(s.exec(select(Server).order_by(Server.name)).all())
            all_users = list(s.exec(select(User).order_by(User.email)).all())
            api_tokens_list = list(
                s.exec(select(ApiToken).order_by(ApiToken.name, ApiToken.id)).all()
            )
            user_map = {}
            for u in all_users:
                label = (u.display_name or "").strip() or u.email
                if u.display_name and u.email:
                    label = f"{u.display_name} ({u.email})"
                user_map[u.id] = label

            token_map = {t.id: t.name for t in api_tokens_list if t.id is not None}

            if api_token_id and str(api_token_id).strip():
                try:
                    tid = int(api_token_id)
                    name = token_map.get(tid)
                    filter_token_label = f"{name} (#{tid})" if name else f"#{tid}"
                except ValueError:
                    filter_token_label = None

            recent = s.exec(select(AuditLog).order_by(AuditLog.started_at.desc()).limit(300)).all()
            distinct_actions = sorted({l.action for l in recent if l.action})
            distinct_statuses = sorted({l.status for l in recent if l.status})
            # Pulse stats from recent window (not the current page alone)
            from ..services.audit_format import action_label as audit_action_label

            pulse = {
                "success": 0,
                "failed": 0,
                "running": 0,
                "other": 0,
                "sample": len(recent),
                "by_action": [],
            }
            action_counts: dict[str, int] = {}
            for l in recent:
                st = (l.status or "").lower()
                if st == "success":
                    pulse["success"] += 1
                elif st == "failed":
                    pulse["failed"] += 1
                elif st in ("running", "pending"):
                    pulse["running"] += 1
                else:
                    pulse["other"] += 1
                act = (l.action or "other").strip() or "other"
                action_counts[act] = action_counts.get(act, 0) + 1
            # Top action types for breakdown chart
            ranked = sorted(action_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            max_c = ranked[0][1] if ranked else 1
            pulse["by_action"] = [
                {
                    "id": act,
                    "label": audit_action_label(act),
                    "count": n,
                    "pct": round(100.0 * n / max(1, len(recent)), 1),
                    "bar": round(100.0 * n / max(1, max_c), 1),
                }
                for act, n in ranked[:10]
            ]

            filt = dict(
                search=search,
                server_id=server_id,
                user_id=user_id,
                api_token_id=api_token_id,
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
                    d["user_label"] = None
                # Prefer snapshotted name; fall back to live token name
                tok_id = getattr(l, "api_token_id", None)
                tok_name = getattr(l, "api_token_name", None) or (
                    token_map.get(tok_id) if tok_id else None
                )
                d["api_token_id"] = tok_id
                d["api_token_name"] = tok_name
                d["actor_label"] = format_actor_label(
                    user_label=d.get("user_label"),
                    api_token_id=tok_id,
                    api_token_name=tok_name,
                )
                d["server_name"] = (
                    next((srv.name for srv in servers_list if srv.id == l.server_id), None)
                    if l.server_id
                    else None
                )
                # Public demo: never show real visitor IPs to other shared users
                # (column *and* details/snippet body — console writes ``ip=…``).
                try:
                    from ..services.demo import scrub_audit_client_ip, scrub_audit_text

                    d["client_ip"] = scrub_audit_client_ip(
                        d.get("client_ip"), for_display=True
                    )
                    d["details"] = scrub_audit_text(d.get("details"), for_display=True)
                    d["output_snippet"] = scrub_audit_text(
                        d.get("output_snippet"), for_display=True
                    )
                except Exception:
                    pass
                row = format_audit_entry(d)
                tid = row.get("transcript_id")
                row["transcript_readable"] = bool(tid) and role_at_least(user, ROLE_OPERATOR)
                return row

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
        api_tokens_list = []
        total = 0
        total_pages = 1
        page = 1

    resp = templates_mod.templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "title": "Audit Log",
            "logs": logs_data,
            "user": user,
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "app_date_today": calendar_today_in_app_tz(),
            "server_id": server_id,
            "servers": servers_list,
            "user_id": user_id,
            "users": all_users,
            "api_token_id": api_token_id,
            "api_tokens": api_tokens_list,
            "filter_token_label": filter_token_label,
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
                "server_features_updated",
            ],
            "hide_noise": hide_incomplete,
            "page": page,
            "per_page": per_page,
            "per_page_choices": list(PER_PAGE_CHOICES),
            "total": total,
            "total_pages": total_pages,
            "pulse": pulse,
        },
    )
    return lq.attach_per_page_cookie(resp, per_page)


def _operator_transcript(session, user: User, transcript_id: int):
    from ..services import console_audit as ca
    from ..services.demo import http_403_if_demo

    http_403_if_demo("console_transcript")
    row = ca.load_transcript(session, int(transcript_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return row, ca


@router.get("/audit/console/{transcript_id}/json")
async def console_transcript_json(
    transcript_id: int,
    user: User = Depends(get_operator_user),
    session=Depends(get_session),
):
    row, ca = _operator_transcript(session, user, transcript_id)
    meta = ca.public_meta(row, readable=True)
    events = ca.decrypt_events(row) if meta.get("readable") else []
    session.add(
        make_audit_log(
            user_id=user.id,
            server_id=row.server_id,
            action="ssh_console_transcript_viewed",
            status="success",
            details=f"transcript_id={row.id} cmds={row.command_count}",
        )
    )
    session.commit()
    return JSONResponse({"ok": True, "transcript": meta, "events": events})


@router.get("/audit/console/{transcript_id}/download")
async def console_transcript_download(
    transcript_id: int,
    user: User = Depends(get_operator_user),
    session=Depends(get_session),
):
    row, ca = _operator_transcript(session, user, transcript_id)
    events = ca.decrypt_events(row)
    body = ca.events_as_text(events) or "(empty or purged)\n"
    session.add(
        make_audit_log(
            user_id=user.id,
            server_id=row.server_id,
            action="ssh_console_transcript_downloaded",
            status="success",
            details=f"transcript_id={row.id} cmds={row.command_count} bytes={row.byte_count}",
        )
    )
    session.commit()
    filename = f"console-{int(transcript_id)}.txt"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/audit/console/{transcript_id}", response_class=HTMLResponse)
async def console_transcript_page(
    request: Request,
    transcript_id: int,
    user: User = Depends(get_operator_user),
    session=Depends(get_session),
):
    row, ca = _operator_transcript(session, user, transcript_id)
    meta = ca.public_meta(row, readable=True)
    events = ca.decrypt_events(row) if meta.get("readable") else []
    session.add(
        make_audit_log(
            user_id=user.id,
            server_id=row.server_id,
            action="ssh_console_transcript_viewed",
            status="success",
            details=f"transcript_id={row.id} cmds={row.command_count}",
        )
    )
    session.commit()
    server_name = None
    if row.server_id:
        srv = session.get(Server, row.server_id)
        server_name = srv.name if srv else None
    actor = None
    if row.user_id:
        u = session.get(User, row.user_id)
        actor = (u.display_name or u.email) if u else None
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="audit_console.html",
        context={
            "title": f"Console transcript #{transcript_id}",
            "user": user,
            "row": row,
            "meta": meta,
            "events": events,
            "server_name": server_name,
            "actor": actor,
            "body_text": ca.events_as_text(events),
        },
    )
