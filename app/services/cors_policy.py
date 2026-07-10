"""Optional CORS policy for browser clients calling /api/v1 from other origins.

Default: disabled. Automation (n8n, Home Assistant, curl) is server-to-server and
does not need CORS.

CORS is a browser gate only — never a substitute for:
  - Bearer token auth
  - scope checks
  - per-token IP allowlists
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware


def parse_cors_origins(raw: Optional[str]) -> List[str]:
    """Parse comma/newline-separated exact origins. Empty / * alone → no CORS."""
    if not raw or not str(raw).strip():
        return []
    out: List[str] = []
    for part in str(raw).replace(";", ",").replace("\n", ",").split(","):
        o = part.strip().rstrip("/")
        if not o:
            continue
        # Reject wildcard — too broad for token APIs
        if o == "*":
            continue
        # Require absolute origin-ish form (scheme://host[:port])
        if "://" not in o:
            continue
        if o not in out:
            out.append(o)
    return out


def apply_cors_middleware(app: FastAPI, origins: Iterable[str]) -> None:
    """Attach Starlette CORSMiddleware with a tight allowlist.

    allow_credentials=False: API uses Authorization Bearer, not cookies.
    That avoids reflecting credentials cross-origin for the session UI.
    """
    allow = list(origins)
    if not allow:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        expose_headers=[],
        max_age=600,
    )
