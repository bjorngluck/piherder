"""Pi-hole v6 REST adapter — stats, local DNS/CNAME, gravity & system actions.

Auth: POST /api/auth {"password": "…"} → session.sid + session.csrf
Headers: X-FTL-SID / sid cookie; X-CSRF-Token when required.

API base: {origin}/api  (admin UI lives at {origin}/admin/)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20.0
GRAVITY_TIMEOUT = 600.0


@dataclass
class PiholeSession:
    base_url: str  # origin without trailing slash
    sid: str
    csrf: str = ""
    tls_verify: bool = True

    @property
    def api_root(self) -> str:
        return f"{self.base_url.rstrip('/')}/api"

    def headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "X-FTL-SID": self.sid,
        }
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return h

    def cookies(self) -> dict[str, str]:
        return {"sid": self.sid}


@dataclass
class PiholeStats:
    ok: bool
    error: Optional[str] = None
    queries: int = 0
    blocked: int = 0
    percent_blocked: float = 0.0
    domains_on_lists: int = 0
    active_clients: int = 0
    version: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_status_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "queries": self.queries,
            "blocked": self.blocked,
            "percent_blocked": self.percent_blocked,
            "domains_on_lists": self.domains_on_lists,
            "active_clients": self.active_clients,
            "version": self.version,
            "monitor_count": 1 if self.ok else 0,
        }


def normalize_base_url(url: str) -> str:
    """Return origin (scheme://host[:port]) suitable for admin + API links."""
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("Base URL is required")
    # Allow pasting full admin URLs
    for suffix in ("/admin/gravity", "/admin/settings", "/admin", "/api"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL must be http(s)://host[:port]")
    return f"{parsed.scheme}://{parsed.netloc}"


def admin_url(base_url: str, path: str = "") -> str:
    base = normalize_base_url(base_url) if base_url else ""
    p = (path or "").strip()
    if not p:
        return f"{base}/admin/" if base else ""
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if not p.startswith("/"):
        p = "/" + p
    if not p.startswith("/admin"):
        p = "/admin" + p
    return f"{base}{p}"


def login(
    base_url: str,
    password: str,
    *,
    tls_verify: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> PiholeSession:
    origin = normalize_base_url(base_url)
    pw = password or ""
    if not pw:
        raise ValueError("Pi-hole password is required")
    url = f"{origin}/api/auth"
    with httpx.Client(verify=tls_verify, timeout=timeout) as client:
        r = client.post(url, json={"password": pw})
        if r.status_code >= 400:
            raise RuntimeError(f"Pi-hole auth failed HTTP {r.status_code}: {r.text[:200]}")
        data = r.json() if r.content else {}
    session = data.get("session") if isinstance(data, dict) else None
    if not isinstance(session, dict):
        # Some builds nest differently
        session = data if isinstance(data, dict) else {}
    sid = str(session.get("sid") or data.get("sid") or "").strip()
    csrf = str(session.get("csrf") or data.get("csrf") or "").strip()
    if not sid:
        raise RuntimeError("Pi-hole auth response missing session sid")
    return PiholeSession(base_url=origin, sid=sid, csrf=csrf, tls_verify=tls_verify)


def logout(sess: PiholeSession, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    try:
        with httpx.Client(verify=sess.tls_verify, timeout=timeout) as client:
            client.delete(
                f"{sess.api_root}/auth",
                headers=sess.headers(),
                cookies=sess.cookies(),
            )
    except Exception as e:
        logger.debug("pihole logout skip: %s", e)


def _get_json(sess: PiholeSession, path: str, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    url = f"{sess.api_root}{path}"
    with httpx.Client(verify=sess.tls_verify, timeout=timeout) as client:
        r = client.get(url, headers=sess.headers(), cookies=sess.cookies())
        if r.status_code == 401:
            raise RuntimeError("Pi-hole unauthorized")
        if r.status_code >= 400:
            raise RuntimeError(f"Pi-hole GET {path} HTTP {r.status_code}: {r.text[:200]}")
        if not r.content:
            return {}
        return r.json()


def _request(
    sess: PiholeSession,
    method: str,
    path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    json_body: Any = None,
) -> httpx.Response:
    url = f"{sess.api_root}{path}"
    with httpx.Client(verify=sess.tls_verify, timeout=timeout) as client:
        r = client.request(
            method.upper(),
            url,
            headers=sess.headers(),
            cookies=sess.cookies(),
            json=json_body,
        )
        return r


def _dig(data: Any, *keys: str, default: Any = None) -> Any:
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


def parse_stats_payload(data: Any) -> PiholeStats:
    """Map v6 summary/padd shapes into dashboard tiles."""
    if not isinstance(data, dict):
        return PiholeStats(ok=False, error="invalid stats payload")

    # Prefer nested summary objects used by FTL
    queries_obj = data.get("queries") if isinstance(data.get("queries"), dict) else {}
    gravity = data.get("gravity") if isinstance(data.get("gravity"), dict) else {}
    clients = data.get("clients") if isinstance(data.get("clients"), dict) else {}

    total = (
        queries_obj.get("total")
        or data.get("dns_queries_today")
        or data.get("queries_total")
        or data.get("total_queries")
        or 0
    )
    blocked = (
        queries_obj.get("blocked")
        or data.get("ads_blocked_today")
        or data.get("blocked_queries")
        or data.get("queries_blocked")
        or 0
    )
    try:
        total_i = int(total or 0)
        blocked_i = int(blocked or 0)
    except (TypeError, ValueError):
        total_i, blocked_i = 0, 0

    pct = (
        queries_obj.get("percent_blocked")
        or data.get("ads_percentage_today")
        or data.get("percent_blocked")
    )
    try:
        pct_f = float(pct) if pct is not None else (
            (100.0 * blocked_i / total_i) if total_i else 0.0
        )
    except (TypeError, ValueError):
        pct_f = 0.0

    domains = (
        gravity.get("domains_being_blocked")
        or data.get("domains_being_blocked")
        or data.get("gravity_size")
        or data.get("domains_on_lists")
        or 0
    )
    try:
        domains_i = int(domains or 0)
    except (TypeError, ValueError):
        domains_i = 0

    active = (
        clients.get("active")
        or data.get("unique_clients")
        or data.get("active_clients")
        or data.get("clients_active")
        or 0
    )
    try:
        active_i = int(active or 0)
    except (TypeError, ValueError):
        active_i = 0

    version = str(
        _dig(data, "version", "core", "local", "version", default="")
        or data.get("version")
        or ""
    )

    return PiholeStats(
        ok=True,
        queries=total_i,
        blocked=blocked_i,
        percent_blocked=round(pct_f, 1),
        domains_on_lists=domains_i,
        active_clients=active_i,
        version=version if isinstance(version, str) else str(version),
        raw=data if isinstance(data, dict) else {},
    )


def fetch_stats(
    base_url: str,
    password: str,
    *,
    tls_verify: bool = True,
) -> PiholeStats:
    try:
        sess = login(base_url, password, tls_verify=tls_verify)
    except Exception as e:
        return PiholeStats(ok=False, error=str(e)[:300])
    try:
        data = None
        last_err = None
        for path in ("/stats/summary", "/padd", "/stats"):
            try:
                data = _get_json(sess, path)
                if data:
                    break
            except Exception as e:
                last_err = e
                continue
        if not data:
            return PiholeStats(ok=False, error=str(last_err or "no stats endpoint")[:300])
        st = parse_stats_payload(data)
        # version endpoint optional
        try:
            ver = _get_json(sess, "/info/version")
            if isinstance(ver, dict):
                v = (
                    _dig(ver, "version", "core", "local", "version")
                    or _dig(ver, "version", "ftl", "local", "version")
                    or ver.get("version")
                )
                if v:
                    st.version = str(v)
        except Exception:
            pass
        return st
    except Exception as e:
        return PiholeStats(ok=False, error=str(e)[:300])
    finally:
        logout(sess)


def list_dns_hosts(sess: PiholeSession) -> list[dict[str, str]]:
    """Return [{ip, domain}] from config dns.hosts."""
    data = _get_json(sess, "/config/dns/hosts")
    return _parse_host_entries(data)


def list_dns_cnames(sess: PiholeSession) -> list[dict[str, str]]:
    data = _get_json(sess, "/config/dns/cnameRecords")
    # fallback path style
    if not data:
        try:
            data = _get_json(sess, "/config")
            data = _dig(data, "config", "dns", "cnameRecords") or data
        except Exception:
            pass
    return _parse_cname_entries(data)


def _parse_host_entries(data: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    # Shapes: {"config":{"dns":{"hosts":["1.2.3.4 name"]}}} or list
    hosts = data
    if isinstance(data, dict):
        hosts = (
            _dig(data, "config", "dns", "hosts")
            or data.get("hosts")
            or data.get("dns.hosts")
            or data.get("value")
            or []
        )
    if not isinstance(hosts, list):
        return entries
    for item in hosts:
        if isinstance(item, str):
            parts = item.split()
            if len(parts) >= 2:
                entries.append({"ip": parts[0], "domain": parts[1], "raw": item})
        elif isinstance(item, dict):
            ip = str(item.get("ip") or item.get("address") or "").strip()
            dom = str(item.get("domain") or item.get("name") or "").strip()
            if ip and dom:
                entries.append({"ip": ip, "domain": dom, "raw": f"{ip} {dom}"})
    return entries


def _parse_cname_entries(data: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    rows = data
    if isinstance(data, dict):
        rows = (
            _dig(data, "config", "dns", "cnameRecords")
            or data.get("cnameRecords")
            or data.get("value")
            or []
        )
    if not isinstance(rows, list):
        return entries
    for item in rows:
        if isinstance(item, str):
            # domain,target[,ttl]
            parts = [p.strip() for p in item.replace(" ", ",").split(",") if p.strip()]
            if len(parts) >= 2:
                entries.append(
                    {
                        "domain": parts[0],
                        "target": parts[1],
                        "raw": item,
                    }
                )
        elif isinstance(item, dict):
            dom = str(item.get("domain") or item.get("name") or "").strip()
            tgt = str(item.get("target") or item.get("cname") or "").strip()
            if dom and tgt:
                entries.append({"domain": dom, "target": tgt, "raw": f"{dom},{tgt}"})
    return entries


def encode_host_path(ip: str, domain: str) -> str:
    return quote(f"{(ip or '').strip()} {(domain or '').strip()}", safe="")


def encode_cname_path(domain: str, target: str) -> str:
    # UI uses domain,target
    return quote(f"{(domain or '').strip()},{(target or '').strip()}", safe="")


def add_dns_host(sess: PiholeSession, ip: str, domain: str) -> None:
    path = f"/config/dns/hosts/{encode_host_path(ip, domain)}"
    r = _request(sess, "PUT", path)
    if r.status_code >= 400:
        raise RuntimeError(f"add host failed HTTP {r.status_code}: {r.text[:200]}")


def delete_dns_host(sess: PiholeSession, ip: str, domain: str) -> None:
    path = f"/config/dns/hosts/{encode_host_path(ip, domain)}"
    r = _request(sess, "DELETE", path)
    if r.status_code >= 400 and r.status_code != 404:
        raise RuntimeError(f"delete host failed HTTP {r.status_code}: {r.text[:200]}")


def add_dns_cname(sess: PiholeSession, domain: str, target: str) -> None:
    path = f"/config/dns/cnameRecords/{encode_cname_path(domain, target)}"
    r = _request(sess, "PUT", path)
    if r.status_code >= 400:
        raise RuntimeError(f"add cname failed HTTP {r.status_code}: {r.text[:200]}")


def delete_dns_cname(sess: PiholeSession, domain: str, target: str) -> None:
    path = f"/config/dns/cnameRecords/{encode_cname_path(domain, target)}"
    r = _request(sess, "DELETE", path)
    if r.status_code >= 400 and r.status_code != 404:
        raise RuntimeError(f"delete cname failed HTTP {r.status_code}: {r.text[:200]}")


def run_action(
    sess: PiholeSession,
    action: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run system action: gravity | restartdns | flush/network | flush/logs.

    Returns response text (may be empty).
    """
    action = (action or "").strip().lower()
    path_map = {
        "gravity": "/action/gravity",
        "restartdns": "/action/restartdns",
        "restart_dns": "/action/restartdns",
        "flush_network": "/action/flush/network",
        "flush/network": "/action/flush/network",
        "flush_logs": "/action/flush/logs",
    }
    path = path_map.get(action)
    if not path:
        raise ValueError(f"Unknown Pi-hole action: {action}")
    t = GRAVITY_TIMEOUT if action == "gravity" else timeout
    r = _request(sess, "POST", path, timeout=t)
    if r.status_code >= 400:
        # try alternate without prefix for some builds
        raise RuntimeError(f"action {action} HTTP {r.status_code}: {r.text[:300]}")
    return (r.text or "")[:8000]


def summarize_instances(status_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Fleet summary: sum queries/blocked/clients; % from totals; domains from primary."""
    queries = blocked = clients = 0
    primary_domains = 0
    ok_n = 0
    for st in status_list:
        if not isinstance(st, dict):
            continue
        if st.get("ok"):
            ok_n += 1
        try:
            queries += int(st.get("queries") or 0)
            blocked += int(st.get("blocked") or 0)
            clients += int(st.get("active_clients") or 0)
        except (TypeError, ValueError):
            pass
        if st.get("is_primary"):
            try:
                primary_domains = int(st.get("domains_on_lists") or 0)
            except (TypeError, ValueError):
                primary_domains = 0
    if not primary_domains and status_list:
        try:
            primary_domains = int(status_list[0].get("domains_on_lists") or 0)
        except (TypeError, ValueError):
            primary_domains = 0
    pct = round(100.0 * blocked / queries, 1) if queries else 0.0
    return {
        "queries": queries,
        "blocked": blocked,
        "percent_blocked": pct,
        "domains_on_lists": primary_domains,
        "active_clients": clients,
        "instance_count": len(status_list),
        "ok_count": ok_n,
    }
