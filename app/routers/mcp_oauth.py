"""MCP OAuth discovery, dynamic registration, consent, and token exchange."""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import User
from ..security.auth import (
    LoginRequired,
    MCP_OAUTH_RETURN_COOKIE,
    OnboardingRedirect,
    cookie_auth_kwargs,
    get_current_user,
)
from ..services import mcp_oauth as oauth
from ..services.api_tokens import SCOPE_HELP
from .. import templates as templates_mod

router = APIRouter(include_in_schema=False)


def _oauth_error(exc: oauth.OAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.error, "error_description": exc.description},
        headers={"Cache-Control": "no-store"},
    )


def _json(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers={"Cache-Control": "no-store"})


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource(request: Request):
    return _json(oauth.protected_resource_metadata(request))


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
async def authorization_server(request: Request):
    return _json(oauth.authorization_server_metadata(request))


@router.post("/mcp/oauth/register")
async def register(request: Request, session: Session = Depends(get_session)):
    try:
        payload = await request.json()
    except Exception:
        return _oauth_error(oauth.OAuthError("invalid_client_metadata", "JSON body required."))
    if not isinstance(payload, dict):
        return _oauth_error(oauth.OAuthError("invalid_client_metadata", "JSON object required."))
    uris = payload.get("redirect_uris")
    if not isinstance(uris, list):
        return _oauth_error(oauth.OAuthError("invalid_client_metadata", "redirect_uris is required."))
    try:
        row = oauth.register_client(
            session,
            client_name=str(payload.get("client_name") or "MCP agent"),
            redirect_uris=[str(item) for item in uris],
        )
    except oauth.OAuthError as exc:
        return _oauth_error(exc)
    issued = int(row.created_at.timestamp()) if row.created_at else 0
    return _json(
        {
            "client_id": row.client_id,
            "client_name": row.client_name,
            "redirect_uris": json_uris(row.redirect_uris_json),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_id_issued_at": issued,
        },
        status=201,
    )


def json_uris(raw: str) -> list[str]:
    import json

    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _return_target(request: Request) -> str:
    target = "/mcp/oauth/authorize"
    if request.url.query:
        target = target + "?" + request.url.query
    return target[:2000]


def _login_redirect(request: Request, location: str) -> RedirectResponse:
    response = RedirectResponse(location, status_code=303)
    response.set_cookie(
        MCP_OAUTH_RETURN_COOKIE,
        _return_target(request),
        **cookie_auth_kwargs(max_age=600),
    )
    return response


def _ready_admin(request: Request, session: Session) -> tuple[User | None, RedirectResponse | None]:
    from fastapi import HTTPException

    try:
        user = get_current_user(request, None, session)
    except LoginRequired:
        return None, _login_redirect(request, "/auth/login")
    except OnboardingRedirect as exc:
        return None, _login_redirect(request, exc.location)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None, _login_redirect(request, "/auth/login")
        return None, HTMLResponse(str(exc.detail or "Forbidden"), status_code=exc.status_code)
    if not oauth.is_admin(user):
        return None, None
    return user, None


@router.get("/mcp/oauth/authorize", response_class=HTMLResponse)
async def authorize(request: Request, session: Session = Depends(get_session)):
    if oauth._demo():
        return HTMLResponse("The public demo does not sign in agents.", status_code=403)
    client_id = (request.query_params.get("client_id") or "").strip()
    redirect_uri = (request.query_params.get("redirect_uri") or "").strip()
    challenge = (request.query_params.get("code_challenge") or "").strip()
    method = (request.query_params.get("code_challenge_method") or "").strip()
    response_type = (request.query_params.get("response_type") or "").strip()
    state = request.query_params.get("state") or ""
    resource = (request.query_params.get("resource") or "").strip()
    scope_raw = request.query_params.get("scope") or ""
    client = oauth.get_client(session, client_id)
    if (
        response_type != "code"
        or method != "S256"
        or not challenge
        or not state
        or not client
        or redirect_uri not in oauth._client_redirects(client)
        or not oauth.resource_matches(request, resource)
    ):
        return HTMLResponse("This agent sign-in request is not valid.", status_code=400)
    user, bounce = _ready_admin(request, session)
    if bounce is not None:
        return bounce
    if user is None:
        return HTMLResponse("Admin role required.", status_code=403)
    scopes = oauth.parse_oauth_scopes(scope_raw)
    ticket = oauth.consent_token(
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        scopes=scopes,
        state=state,
        resource=resource,
    )
    page = _consent_page(
        request,
        client_name=client.client_name,
        redirect_uri=redirect_uri,
        scopes=scopes,
        error="",
    )
    page.set_cookie(oauth.CONSENT_COOKIE, ticket, **cookie_auth_kwargs(max_age=600))
    return page


def _consent_page(
    request: Request,
    *,
    client_name: str,
    redirect_uri: str,
    scopes: list[str],
    error: str,
) -> HTMLResponse:
    host = redirect_uri.split("?", 1)[0]
    optional = [
        {"name": name, "help": SCOPE_HELP.get(name, name), "checked": True}
        for name in scopes
        if name != "read"
    ]
    page = templates_mod.templates.TemplateResponse(
        request=request,
        name="mcp_oauth_consent.html",
        context={
            "title": "Allow this agent",
            "client_name": client_name,
            "redirect_host": host,
            "optional_scopes": optional,
            "error": error,
        },
    )
    page.headers["Cache-Control"] = "no-store"
    return page


@router.post("/mcp/oauth/consent")
async def consent(request: Request, session: Session = Depends(get_session)):
    if oauth._demo():
        return HTMLResponse("The public demo does not sign in agents.", status_code=403)
    user, bounce = _ready_admin(request, session)
    if bounce is not None:
        return bounce
    if user is None:
        return HTMLResponse("Admin role required.", status_code=403)
    pending = oauth.read_consent(request.cookies.get(oauth.CONSENT_COOKIE) or "")
    if not pending:
        return HTMLResponse("This sign-in request expired. Start it again from the agent.", status_code=400)
    form = await request.form()
    decision = str(form.get("decision") or "")
    redirect_uri = str(pending.get("redir") or "")
    state = str(pending.get("state") or "")
    if decision == "deny":
        url = oauth.with_query(redirect_uri, {"error": "access_denied", "state": state})
        return _continue_redirect(url)
    requested = [str(item) for item in (pending.get("scopes") or []) if str(item) in oauth.SCOPES]
    picked = {str(item) for item in form.getlist("scope")}
    granted = [name for name in requested if name in picked or name == "read"]
    if "read" not in granted:
        granted.insert(0, "read")
    granted = [name for name in granted if name in requested]
    if not granted:
        client = oauth.get_client(session, str(pending.get("cid") or ""))
        page = _consent_page(
            request,
            client_name=client.client_name if client else "MCP agent",
            redirect_uri=redirect_uri,
            scopes=requested or ["read"],
            error="Choose at least the read scope.",
        )
        return page
    try:
        code = oauth.issue_code(
            session,
            client_id=str(pending["cid"]),
            user_id=int(user.id),
            redirect_uri=redirect_uri,
            code_challenge=str(pending["cc"]),
            scopes=granted,
            resource=str(pending.get("resource") or ""),
        )
    except oauth.OAuthError as exc:
        return HTMLResponse(escape(exc.description), status_code=exc.status)
    from ..services.audit_write import make_audit_log

    session.add(
        make_audit_log(
            user_id=user.id,
            action="mcp_oauth_grant",
            details=f"MCP OAuth scopes {' '.join(granted)}",
        )
    )
    session.commit()
    url = oauth.with_query(redirect_uri, {"code": code, "state": state})
    response = _continue_redirect(url)
    response.delete_cookie(oauth.CONSENT_COOKIE, path="/")
    return response


def _continue_redirect(url: str) -> RedirectResponse:
    response = RedirectResponse("/mcp/oauth/continue", status_code=303)
    response.set_cookie(
        oauth.CONTINUE_COOKIE,
        oauth.continue_ticket(url),
        **cookie_auth_kwargs(max_age=120),
    )
    return response


@router.get("/mcp/oauth/continue", response_class=HTMLResponse)
async def continue_to_agent(request: Request):
    try:
        url = oauth.read_continue(request.cookies.get(oauth.CONTINUE_COOKIE) or "")
    except oauth.OAuthError as exc:
        return HTMLResponse(escape(exc.description), status_code=exc.status)
    safe = escape(url, quote=True)
    page = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<meta http-equiv=\"refresh\" content=\"0;url={safe}\">"
        "<title>Return to the agent</title></head><body>"
        f"<p><a href=\"{safe}\">Return to the agent</a></p>"
        "</body></html>"
    )
    response = HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie(oauth.CONTINUE_COOKIE, path="/")
    return response


@router.post("/mcp/oauth/token")
async def token(request: Request, session: Session = Depends(get_session)):
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in ctype:
            payload = await request.json()
            data = payload if isinstance(payload, dict) else {}
        else:
            form = await request.form()
            data = {str(k): str(v) for k, v in form.items()}
    except Exception:
        return _oauth_error(oauth.OAuthError("invalid_request", "Token request could not be read."))
    grant = str(data.get("grant_type") or "")
    try:
        if grant == "authorization_code":
            body = oauth.exchange_code(
                session,
                code=str(data.get("code") or ""),
                client_id=str(data.get("client_id") or ""),
                redirect_uri=str(data.get("redirect_uri") or ""),
                code_verifier=str(data.get("code_verifier") or ""),
            )
        elif grant == "refresh_token":
            body = oauth.refresh_grant(
                session,
                refresh_token=str(data.get("refresh_token") or ""),
                client_id=str(data.get("client_id") or ""),
            )
        else:
            raise oauth.OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token.")
    except oauth.OAuthError as exc:
        return _oauth_error(exc)
    return _json(body)
