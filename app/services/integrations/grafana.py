"""Grafana adapter — service account token + health / dashboard search.

Auth: Authorization: Bearer <service_account_token>
  (token optional for open deep links only; health may work unauthenticated)

APIs used (read-only):
  GET /api/health
  GET /api/search?type=dash-db
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass
class GrafanaDashboard:
    uid: str
    title: str
    url: str = ""  # relative path from search API, e.g. /d/uid/slug
    tags: list[str] = field(default_factory=list)
    folder_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "id": self.uid,  # external_id alias for pickers
            "title": self.title,
            "name": self.title,
            "url": self.url,
            "tags": list(self.tags),
            "folder_title": self.folder_title,
        }


@dataclass
class GrafanaPollResult:
    ok: bool
    error: Optional[str] = None
    version: str = ""
    database: str = ""  # ok | fail | …
    commit: str = ""
    dashboards: list[GrafanaDashboard] = field(default_factory=list)

    def to_status_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "version": self.version,
            "database": self.database,
            "commit": self.commit,
            "dashboard_count": len(self.dashboards),
            "dashboards": [d.to_dict() for d in self.dashboards],
            # Alias so list UI can show a count similar to Kuma monitors
            "monitor_count": len(self.dashboards),
        }


def normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("Base URL is required")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL must be http(s)://host[:port]")
    return u


def open_grafana_url(base_url: str, path: str = "") -> str:
    base = (base_url or "").rstrip("/")
    p = (path or "").strip()
    if not p:
        return base or ""
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if not p.startswith("/"):
        p = "/" + p
    return urljoin(base + "/", p.lstrip("/")) if base else p


def dashboard_path(uid: str, slug: str = "") -> str:
    u = (uid or "").strip()
    if not u:
        return ""
    s = (slug or "").strip() or "dashboard"
    # Grafana accepts /d/{uid}/{slug}
    return f"/d/{quote(u, safe='')}/{quote(s, safe='-_.~')}"


def hostname_short(hostname: str = "", name: str = "") -> str:
    """First DNS label, lowercased — rpi5-1.example.com → rpi5-1.

    Falls back to a slug of the server display name.
    """
    h = (hostname or "").strip().lower()
    if h:
        return h.split(".", 1)[0]
    n = (name or "").strip().lower()
    if not n:
        return ""
    # RPI5-1 / "RPI 5" → rpi5-1 / rpi-5
    n = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    return n


def apply_query_template(
    template: str,
    *,
    hostname: str = "",
    name: str = "",
    ip_address: str = "",
    server_id: str = "",
    container: str = "",
    project: str = "",
    compose_service: str = "",
) -> str:
    """Replace placeholders in a Grafana query string template.

    Examples:
      var-job={hostname_short}_exporter
      var-job={hostname_short}_cadvisor&var-container={container}
      var-host={hostname_short}   (logs)

    Placeholders: {hostname}, {hostname_short}, {name}, {name_lower},
    {ip}, {ip_address}, {server_id}, {host},
    {container}, {docker_container}, {project}, {docker_project},
    {compose_service}
    """
    short = hostname_short(hostname, name)
    cont = (container or "").strip()
    proj = (project or "").strip()
    cs = (compose_service or cont or "").strip()
    vals = {
        "hostname": hostname or "",
        "hostname_short": short,
        "name": name or "",
        "name_lower": (name or "").strip().lower(),
        "ip": ip_address or "",
        "ip_address": ip_address or "",
        "server_id": server_id or "",
        "host": hostname or name or "",
        "container": cont,
        "docker_container": cont,
        "project": proj,
        "docker_project": proj,
        "compose_service": cs,
    }

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        return vals.get(key, m.group(0))

    raw = (template or "").strip()
    if not raw:
        return ""
    return _PLACEHOLDER.sub(_sub, raw)


def open_dashboard_url(
    base_url: str,
    *,
    uid: str,
    slug: str = "",
    relative_url: str = "",
    query_template: str = "",
    hostname: str = "",
    name: str = "",
    ip_address: str = "",
    server_id: str = "",
    container: str = "",
    project: str = "",
    compose_service: str = "",
) -> str:
    """Build absolute Grafana dashboard URL with optional query vars."""
    if relative_url and relative_url.startswith("/"):
        path = relative_url.split("?", 1)[0]
    else:
        path = dashboard_path(uid, slug=slug)
    if not path:
        return open_grafana_url(base_url)
    q = apply_query_template(
        query_template,
        hostname=hostname,
        name=name,
        ip_address=ip_address,
        server_id=server_id,
        container=container,
        project=project,
        compose_service=compose_service,
    )
    full = open_grafana_url(base_url, path)
    if q:
        sep = "&" if "?" in full else "?"
        # strip leading ? from template if operator included it
        q = q.lstrip("?")
        full = f"{full}{sep}{q}"
    return full


def _auth_headers(token: str) -> dict[str, str]:
    t = (token or "").strip()
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}


def fetch_health(
    base_url: str,
    token: str = "",
    *,
    tls_verify: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> GrafanaPollResult:
    """GET /api/health — works on most Grafana installs without auth."""
    try:
        base = normalize_base_url(base_url)
    except ValueError as e:
        return GrafanaPollResult(ok=False, error=str(e))

    url = f"{base}/api/health"
    try:
        with httpx.Client(
            timeout=timeout,
            verify=tls_verify,
            follow_redirects=True,
            headers=_auth_headers(token),
        ) as client:
            r = client.get(url)
        if r.status_code >= 400:
            return GrafanaPollResult(
                ok=False,
                error=f"HTTP {r.status_code} from /api/health",
            )
        data: dict[str, Any] = {}
        try:
            parsed = r.json()
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            data = {}
        # Grafana returns database: "ok" and version when healthy
        database = str(data.get("database") or "").strip()
        version = str(data.get("version") or "").strip()
        commit = str(data.get("commit") or "").strip()
        ok = database.lower() in ("ok", "") or r.status_code == 200
        if database.lower() == "fail":
            ok = False
        return GrafanaPollResult(
            ok=ok,
            version=version,
            database=database or ("ok" if ok else "unknown"),
            commit=commit,
            error=None if ok else "Grafana database not ok",
        )
    except httpx.TimeoutException:
        return GrafanaPollResult(ok=False, error="timeout contacting Grafana")
    except httpx.RequestError as e:
        return GrafanaPollResult(ok=False, error=f"request failed: {e}"[:300])
    except Exception as e:
        logger.exception("grafana health")
        return GrafanaPollResult(ok=False, error=str(e)[:300])


def fetch_dashboards(
    base_url: str,
    token: str,
    *,
    tls_verify: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    limit: int = 200,
) -> list[GrafanaDashboard]:
    """GET /api/search?type=dash-db — requires service account / API token."""
    t = (token or "").strip()
    if not t:
        return []
    try:
        base = normalize_base_url(base_url)
    except ValueError:
        return []

    url = f"{base}/api/search"
    params = {"type": "dash-db", "limit": str(limit)}
    try:
        with httpx.Client(
            timeout=timeout,
            verify=tls_verify,
            follow_redirects=True,
            headers=_auth_headers(t),
        ) as client:
            r = client.get(url, params=params)
        if r.status_code == 401 or r.status_code == 403:
            logger.info("grafana dashboard search unauthorized: %s", r.status_code)
            return []
        if r.status_code >= 400:
            logger.info("grafana dashboard search HTTP %s", r.status_code)
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        out: list[GrafanaDashboard] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            uid = str(item.get("uid") or "").strip()
            if not uid:
                continue
            title = str(item.get("title") or uid).strip()
            rel = str(item.get("url") or "").strip()
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            folder = str(item.get("folderTitle") or item.get("folder_title") or "").strip()
            out.append(
                GrafanaDashboard(
                    uid=uid,
                    title=title,
                    url=rel,
                    tags=[str(t) for t in tags],
                    folder_title=folder,
                )
            )
        out.sort(key=lambda d: (d.title or "").lower())
        return out
    except Exception as e:
        logger.warning("grafana fetch_dashboards: %s", e)
        return []


def poll(
    base_url: str,
    token: str = "",
    *,
    tls_verify: bool = True,
) -> GrafanaPollResult:
    """Health + optional dashboard inventory."""
    result = fetch_health(base_url, token, tls_verify=tls_verify)
    if result.ok and (token or "").strip():
        result.dashboards = fetch_dashboards(
            base_url, token, tls_verify=tls_verify
        )
    return result


def find_dashboard(
    dashboards: list[GrafanaDashboard], uid: str
) -> Optional[GrafanaDashboard]:
    key = (uid or "").strip()
    if not key:
        return None
    for d in dashboards:
        if d.uid == key:
            return d
    return None


def dashboards_from_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    raw = status.get("dashboards") or []
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict) and d.get("uid")]
