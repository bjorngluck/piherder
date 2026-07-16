"""Shared helpers for /servers/* routers."""
from __future__ import annotations

import logging
import threading
from urllib.parse import quote

logger = logging.getLogger("piherder.servers")


def server_redirect(server_id: int, **params: str) -> str:
    url = f"/servers/{server_id}"
    if params:
        qs = "&".join(
            f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v is not None
        )
        if qs:
            url = f"{url}?{qs}"
    return url


def safe_close_ssh(client, timeout: float = 2.0) -> None:
    """Close SSH without blocking forever (common after host reboot starts)."""
    if client is None:
        return

    def _close():
        try:
            client.close()
        except Exception:
            pass

    t = threading.Thread(target=_close, daemon=True)
    t.start()
    t.join(timeout)
