"""Prometheus scrape endpoint (optional bearer token)."""
from __future__ import annotations

import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session

from ..config import settings
from ..database import get_session
from ..services import metrics as metrics_svc

router = APIRouter(tags=["metrics"])


def _token_ok(provided: str, expected: str) -> bool:
    """Constant-time compare for bearer tokens (pad to equal length via secrets)."""
    if not expected:
        return True
    a = provided.encode("utf-8")
    b = expected.encode("utf-8")
    # secrets.compare_digest requires equal length; fall back when lengths differ
    if len(a) != len(b):
        # still run a dummy compare to reduce timing leak on length branch
        secrets.compare_digest(a, a)
        return False
    return secrets.compare_digest(a, b)


@router.get("/metrics")
async def prometheus_metrics(
    request: Request,
    session: Session = Depends(get_session),
):
    expected = (settings.METRICS_TOKEN or "").strip()
    if expected:
        auth = request.headers.get("authorization") or ""
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif request.headers.get("x-metrics-token"):
            token = (request.headers.get("x-metrics-token") or "").strip()
        if not _token_ok(token, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    body = metrics_svc.metrics_body(session)
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
