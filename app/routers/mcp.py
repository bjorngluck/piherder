"""Hosted MCP (Streamable HTTP) on the same origin as the herder UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from ..database import get_session
from ..services.mcp_hosted import handle_mcp_http

router = APIRouter(include_in_schema=False)


@router.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"])
@router.api_route("/mcp/", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"])
async def hosted_mcp(request: Request, session: Session = Depends(get_session)):
    """Stateless Streamable HTTP MCP. Auth: ``Authorization: Bearer ph_…``."""
    return await handle_mcp_http(request, session)
