"""Uptime Kuma adapter — API key + GET /metrics (Prometheus text).

Auth: HTTP Basic with empty username and API key as password
  curl -u ":$API_KEY" "$BASE/metrics"

No Socket.IO / username-password for H1.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# monitor_status{monitor_id="1",monitor_name="x",...} 1
_METRIC_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)"
    r"(?:\s+\d+)?\s*$"
)
_LABEL_PAIR = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')

STATUS_MAP = {
    0: "down",
    1: "up",
    2: "pending",
    3: "maintenance",
}

DEFAULT_TIMEOUT = 15.0


@dataclass
class KumaMonitor:
    id: str
    name: str
    type: str = ""
    hostname: str = ""
    port: str = ""
    url: str = ""
    status: str = "unknown"  # up|down|pending|maintenance|unknown
    status_raw: Optional[float] = None
    response_time_ms: Optional[float] = None
    # Numeric Kuma dashboard id for /dashboard/{id} (may differ from metrics key)
    dashboard_id: Optional[str] = None
    cert_days_remaining: Optional[float] = None
    cert_is_valid: Optional[bool] = None

    def target_display(self) -> str:
        if self.hostname and self.port:
            return f"{self.hostname}:{self.port}"
        if self.hostname:
            return self.hostname
        if self.url:
            return self.url
        return "—"

    def is_service_like(self) -> bool:
        """HTTP(s) / keyword / etc. — not plain SSH port checks."""
        t = (self.type or "").lower()
        if t in ("http", "keyword", "json-query", "grpc-keyword", "real-browser"):
            return True
        if t in ("port", "tcp", "ping", "dns", "docker", "steam", "mqtt", "rabbitmq"):
            # Port checks that aren't SSH still count as service monitors
            if t == "port" and str(self.port) in ("22", "2222"):
                return False
            if t in ("port", "tcp") and "ssh" in (self.name or "").lower():
                return False
            return t not in ("ping",)  # keep non-ssh ports as service-ish
        return bool(self.url)

    def is_ssh_like(self) -> bool:
        t = (self.type or "").lower()
        if t in ("port", "tcp") and str(self.port) in ("22", "2222"):
            return True
        if "ssh" in (self.name or "").lower() and t in ("port", "tcp", ""):
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "hostname": self.hostname,
            "port": self.port,
            "url": self.url,
            "status": self.status,
            "status_raw": self.status_raw,
            "response_time_ms": self.response_time_ms,
            "dashboard_id": self.dashboard_id,
            "cert_days_remaining": self.cert_days_remaining,
            "cert_is_valid": self.cert_is_valid,
            "target": self.target_display(),
            "is_ssh_like": self.is_ssh_like(),
            "is_service_like": self.is_service_like(),
        }


@dataclass
class KumaPollResult:
    ok: bool
    monitors: list[KumaMonitor] = field(default_factory=list)
    error: str = ""
    raw_metric_lines: int = 0

    def to_status_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "monitor_count": len(self.monitors),
            "monitors": [m.to_dict() for m in self.monitors],
            "raw_metric_lines": self.raw_metric_lines,
        }


def normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("Base URL is required")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL must be http(s)://host[:port]")
    return u


def metrics_url(base_url: str) -> str:
    return urljoin(normalize_base_url(base_url) + "/", "metrics")


def _parse_labels(label_blob: str | None) -> dict[str, str]:
    if not label_blob:
        return {}
    out: dict[str, str] = {}
    for m in _LABEL_PAIR.finditer(label_blob):
        key = m.group(1)
        val = m.group(2).replace(r"\"", '"').replace(r"\\", "\\").replace(r"\n", "\n")
        out[key] = val
    return out


def _clean_label(val: str | None) -> str:
    """Kuma often emits hostname/port as the string 'null'."""
    v = (val or "").strip()
    if not v or v.lower() in ("null", "none", "undefined"):
        return ""
    return v


def monitor_key_from_labels(labels: dict[str, str]) -> str:
    """Stable external id for a monitor.

    Newer Kuma Prometheus export often **omits monitor_id** and only labels
    monitor_name / type / url / hostname / port. Prefer numeric id when present;
    otherwise use monitor_name (unique in a typical Kuma install).
    """
    mid = _clean_label(labels.get("monitor_id"))
    if mid:
        return mid
    name = _clean_label(labels.get("monitor_name"))
    if name:
        return name
    # Last resort composite
    parts = [
        _clean_label(labels.get("monitor_type")),
        _clean_label(labels.get("monitor_hostname")),
        _clean_label(labels.get("monitor_port")),
        _clean_label(labels.get("monitor_url")),
    ]
    composite = "|".join(p for p in parts if p)
    return composite or ""


def parse_prometheus_metrics(text: str) -> list[KumaMonitor]:
    """Parse Kuma /metrics text into monitor rows.

    Keys by monitor_id when present, else monitor_name (real-world Kuma export).
    """
    by_id: dict[str, KumaMonitor] = {}

    def _ensure(labels: dict[str, str]) -> Optional[KumaMonitor]:
        mid = monitor_key_from_labels(labels)
        if not mid:
            return None
        hostname = _clean_label(labels.get("monitor_hostname"))
        port = _clean_label(labels.get("monitor_port"))
        url = _clean_label(labels.get("monitor_url"))
        # Placeholder https:// with empty host is useless noise from Kuma port checks
        if url in ("https://", "http://"):
            url = ""
        mon = by_id.get(mid)
        if mon is None:
            mon = KumaMonitor(
                id=mid,
                name=_clean_label(labels.get("monitor_name")) or f"Monitor {mid}",
                type=_clean_label(labels.get("monitor_type")),
                hostname=hostname,
                port=port,
                url=url,
            )
            by_id[mid] = mon
        else:
            if not mon.name and labels.get("monitor_name"):
                mon.name = _clean_label(labels.get("monitor_name")) or mon.name
            if not mon.type and labels.get("monitor_type"):
                mon.type = _clean_label(labels.get("monitor_type"))
            if not mon.hostname and hostname:
                mon.hostname = hostname
            if not mon.port and port:
                mon.port = port
            if not mon.url and url:
                mon.url = url
        return mon

    line_count = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE.match(line)
        if not m:
            continue
        line_count += 1
        name = m.group("name")
        labels = _parse_labels(m.group("labels"))
        try:
            value = float(m.group("value"))
        except ValueError:
            continue

        if name == "monitor_status":
            mon = _ensure(labels)
            if mon:
                mon.status_raw = value
                try:
                    mon.status = STATUS_MAP.get(int(value), "unknown")
                except (TypeError, ValueError):
                    mon.status = "unknown"
                # When metrics include numeric monitor_id, that is the dashboard id
                mid = _clean_label(labels.get("monitor_id"))
                if mid and mid.isdigit():
                    mon.dashboard_id = mid
        elif name == "monitor_response_time":
            mon = _ensure(labels)
            if mon and value >= 0:
                mon.response_time_ms = value
        elif name == "monitor_cert_days_remaining":
            mon = _ensure(labels)
            if mon:
                mon.cert_days_remaining = value
        elif name == "monitor_cert_is_valid":
            mon = _ensure(labels)
            if mon:
                try:
                    mon.cert_is_valid = bool(int(value))
                except (TypeError, ValueError):
                    mon.cert_is_valid = None

    # Stable sort: name then id
    monitors = sorted(by_id.values(), key=lambda x: (x.name.lower(), x.id))
    return monitors


def fetch_metrics(
    base_url: str,
    api_key: str,
    *,
    tls_verify: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> KumaPollResult:
    """Fetch and parse Kuma /metrics. Raises nothing — returns ok/error."""
    key = (api_key or "").strip()
    if not key:
        return KumaPollResult(ok=False, error="API key is required")

    try:
        url = metrics_url(base_url)
    except ValueError as e:
        return KumaPollResult(ok=False, error=str(e))

    try:
        with httpx.Client(timeout=timeout, verify=tls_verify, follow_redirects=True) as client:
            # Kuma: Basic auth with empty username, password = API key
            resp = client.get(url, auth=("", key))
    except httpx.TimeoutException:
        return KumaPollResult(ok=False, error=f"Timeout contacting {url}")
    except httpx.HTTPError as e:
        return KumaPollResult(ok=False, error=f"HTTP error: {e}"[:300])

    if resp.status_code in (401, 403):
        return KumaPollResult(
            ok=False,
            error=f"Auth failed ({resp.status_code}) — check API key and that API keys are enabled in Kuma",
        )
    if resp.status_code != 200:
        return KumaPollResult(
            ok=False,
            error=f"Unexpected status {resp.status_code} from /metrics",
        )

    body = resp.text or ""
    monitors = parse_prometheus_metrics(body)
    raw_lines = sum(1 for ln in body.splitlines() if ln.strip() and not ln.startswith("#"))
    return KumaPollResult(
        ok=True,
        monitors=monitors,
        error="",
        raw_metric_lines=raw_lines,
    )


def _host_tokens(value: str) -> set[str]:
    """Expand hostname into comparable tokens (fqdn, short name, lowercased)."""
    v = (value or "").strip().lower()
    if not v:
        return set()
    out = {v}
    # short name before first dot
    if "." in v:
        out.add(v.split(".", 1)[0])
    return out


def suggest_monitor_for_server(
    monitors: list[KumaMonitor],
    *,
    hostname: str = "",
    ip_address: str = "",
    ssh_port: int = 22,
) -> Optional[KumaMonitor]:
    """Best-effort match: hostname/IP + port, prefer type port + SSH-ish names."""
    host_candidates: set[str] = set()
    host_candidates |= _host_tokens(hostname)
    host_candidates |= _host_tokens(ip_address)
    host_candidates.discard("")
    port_s = str(ssh_port or 22)

    def score(m: KumaMonitor) -> int:
        s = 0
        mh = (m.hostname or "").strip().lower()
        mp = str(m.port or "").strip()
        mtype = (m.type or "").lower()
        mon_hosts = _host_tokens(mh)
        name_l = (m.name or "").lower()

        # Exact host match (incl. short vs FQDN)
        if mon_hosts & host_candidates:
            s += 10
        if mp == port_s:
            s += 5
        if mtype in ("port", "tcp"):
            s += 3
        # Name contains a host token (e.g. "RPI5-1 SSH")
        for h in host_candidates:
            if h and len(h) >= 3 and h in name_l:
                s += 4
                break
        if "ssh" in name_l:
            s += 2
        return s

    ranked = sorted(monitors, key=score, reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    # Need a host-related signal (exact host or name token), not only "any SSH on :22"
    sc = score(best)
    if sc < 12:
        return None
    return best


def open_kuma_url(
    base_url: str,
    *,
    dashboard_id: str | None = None,
    monitor_id: str | None = None,
) -> str:
    """Deep link to Kuma UI.

    Prefer numeric dashboard id → ``{base}/dashboard/{id}`` (Kuma 1.x path).
    Falls back to instance home when id unknown (metrics-only has names on 1.23).
    """
    try:
        base = normalize_base_url(base_url)
    except ValueError:
        return (base_url or "").strip()
    did = (dashboard_id or monitor_id or "").strip()
    if did.isdigit():
        return f"{base}/dashboard/{did}"
    return base


def resolve_dashboard_id(
    mon: Optional[KumaMonitor] = None,
    *,
    external_id: str = "",
    meta: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Pick best numeric dashboard id from monitor cache / binding meta."""
    meta = meta or {}
    for cand in (
        meta.get("dashboard_id"),
        getattr(mon, "dashboard_id", None) if mon else None,
        external_id if str(external_id).isdigit() else None,
        getattr(mon, "id", None) if mon and str(getattr(mon, "id", "")).isdigit() else None,
    ):
        s = str(cand or "").strip()
        if s.isdigit():
            return s
    return None


def find_monitor(
    monitors: list[KumaMonitor],
    external_id: str,
    *,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[KumaMonitor]:
    """Match binding external_id (name or numeric) to a cached monitor."""
    ext = str(external_id or "").strip()
    if not ext:
        return None
    meta = meta or {}
    did = str(meta.get("dashboard_id") or "").strip()
    by_id = {m.id: m for m in monitors}
    if ext in by_id:
        return by_id[ext]
    for m in monitors:
        if m.name == ext:
            return m
        if did and m.dashboard_id == did:
            return m
        if did and m.id == did:
            return m
        if ext.isdigit() and (m.dashboard_id == ext or m.id == ext):
            return m
    return None


def apply_dashboard_id_map(
    monitors: list[KumaMonitor], name_to_id: dict[str, str]
) -> None:
    """Attach numeric dashboard ids from a name→id map (e.g. Socket.IO sync)."""
    for mon in monitors:
        if mon.dashboard_id and str(mon.dashboard_id).isdigit():
            continue
        for key in (mon.name, mon.id):
            if key in name_to_id:
                mon.dashboard_id = str(name_to_id[key])
                break


def fetch_dashboard_id_map(
    base_url: str,
    username: str,
    password: str,
    *,
    tls_verify: bool = True,
    timeout: float = 20.0,
) -> dict[str, str]:
    """Optional Socket.IO login to resolve monitor name → dashboard id.

    Requires ``uptime-kuma-api``. Returns {} on failure / missing dep.
    """
    user = (username or "").strip()
    pw = password or ""
    if not user or not pw:
        return {}
    try:
        from uptime_kuma_api import UptimeKumaApi  # type: ignore
    except ImportError:
        logger.info("uptime-kuma-api not installed; skip dashboard id sync")
        return {}
    try:
        url = normalize_base_url(base_url)
        api = UptimeKumaApi(url, timeout=timeout, ssl_verify=tls_verify)
        try:
            api.login(user, pw)
            rows = api.get_monitors() or []
        finally:
            try:
                api.disconnect()
            except Exception:
                pass
        out: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = row.get("id")
            name = (row.get("name") or "").strip()
            if mid is None:
                continue
            sid = str(mid)
            if name:
                out[name] = sid
            out[sid] = sid
        return out
    except Exception as e:
        logger.warning("Kuma dashboard id sync failed: %s", e)
        return {}
