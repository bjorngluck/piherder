"""Integration CRUD helpers (credentials encrypt/decrypt, config JSON)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import Integration, IntegrationBinding, Server
from ...security.encryption import decrypt_str, encrypt_str
from . import grafana as gf
from . import npm as npm_mod
from . import pihole as ph
from . import uptime_kuma as kuma

logger = logging.getLogger(__name__)

TYPE_UPTIME_KUMA = "uptime_kuma"
TYPE_GRAFANA = "grafana"
TYPE_PIHOLE = "pihole"
TYPE_NPM = "npm"
ROLE_SSH = "ssh_reachability"
ROLE_SERVICE = "service"  # HTTP(s) / app / cert monitoring (Kuma)
ROLE_DASHBOARD = "dashboard"  # Grafana dashboard deep link per server
ROLE_PROXY_HOST = "proxy_host"  # NPM proxy host → server / docker scope
ROLE_PIHOLE_HOST = "pihole_host"  # Pi-hole instance → fleet host / docker scope

DEFAULT_PIHOLE_POLL_SEC = 120
DEFAULT_NPM_POLL_SEC = 120

# Grafana binding kinds (stored in external_meta_json.kind)
GRAFANA_KIND_METRICS = "metrics"  # host node exporter / host metrics
GRAFANA_KIND_CONTAINERS = "containers"  # cadvisor etc. host or per-container
GRAFANA_KIND_LOGS = "logs"  # host-level logs (Loki / log dashboard)
GRAFANA_KINDS = (
    GRAFANA_KIND_METRICS,
    GRAFANA_KIND_CONTAINERS,
    GRAFANA_KIND_LOGS,
)

DEFAULT_POLL_INTERVAL_SEC = 60
MIN_POLL_INTERVAL_SEC = 30
MAX_POLL_INTERVAL_SEC = 900
DEFAULT_GRAFANA_POLL_SEC = 120

# Sensible defaults — operators override to match their dashboards
DEFAULT_QT_HOST = "var-job={hostname_short}_exporter"
DEFAULT_QT_CONTAINER_HOST = "var-job={hostname_short}_cadvisor"
DEFAULT_QT_CONTAINER = "var-job={hostname_short}_cadvisor&var-container={container}"
DEFAULT_QT_LOGS = "var-host={hostname_short}"


def parse_config(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def dump_config(cfg: dict[str, Any]) -> str:
    return json.dumps(cfg, separators=(",", ":"))


def poll_interval_sec(integration: Integration) -> int:
    cfg = parse_config(integration.config_json)
    try:
        n = int(cfg.get("poll_interval_sec") or DEFAULT_POLL_INTERVAL_SEC)
    except (TypeError, ValueError):
        n = DEFAULT_POLL_INTERVAL_SEC
    return max(MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, n))


def tls_verify(integration: Integration) -> bool:
    cfg = parse_config(integration.config_json)
    v = cfg.get("tls_verify")
    if v is None:
        return True
    return bool(v)


def encrypt_credentials(api_key: str, username: str = "", password: str = "") -> str:
    payload = json.dumps(
        {
            "api_key": (api_key or "").strip(),
            "username": (username or "").strip(),
            "password": password or "",
        }
    )
    return encrypt_str(payload)


def decrypt_credentials(integration: Integration) -> dict[str, str]:
    """Return decrypted credential dict: api_key, optional username/password."""
    raw = integration.credentials_encrypted or ""
    if not raw:
        return {}
    try:
        plain = decrypt_str(raw)
        data = json.loads(plain)
        if isinstance(data, dict):
            return {
                "api_key": str(data.get("api_key") or ""),
                "username": str(data.get("username") or ""),
                "password": str(data.get("password") or ""),
            }
        return {"api_key": plain, "username": "", "password": ""}
    except Exception as e:
        logger.warning("Failed to decrypt integration credentials: %s", e)
        return {}


def decrypt_api_key(integration: Integration) -> str:
    return decrypt_credentials(integration).get("api_key") or ""


def encrypt_credentials_full(
    api_key: str,
    *,
    username: str = "",
    password: str = "",
    keep_from: Optional[Integration] = None,
) -> str:
    """Build encrypted credentials blob.

    Empty password on edit keeps previous password when keep_from is set.
    """
    prev = decrypt_credentials(keep_from) if keep_from else {}
    key = (api_key or "").strip() or prev.get("api_key") or ""
    user = (username or "").strip()
    if user == "" and keep_from is not None and username is None:
        user = prev.get("username") or ""
    pw = password
    if not pw and keep_from is not None:
        pw = prev.get("password") or ""
    payload = {
        "api_key": key,
        "username": user,
        "password": pw or "",
    }
    return encrypt_str(json.dumps(payload))


def has_credentials(integration: Integration) -> bool:
    return bool(integration.credentials_encrypted)


def has_kuma_login(integration: Integration) -> bool:
    c = decrypt_credentials(integration)
    return bool(c.get("username") and c.get("password"))


def list_integrations(session: Session, *, type_filter: Optional[str] = None) -> list[Integration]:
    q = select(Integration).order_by(Integration.name)
    if type_filter:
        q = q.where(Integration.type == type_filter)
    return list(session.exec(q).all())


def get_integration(session: Session, integration_id: int) -> Optional[Integration]:
    return session.get(Integration, integration_id)


def is_pihole_primary(integration: Integration) -> bool:
    return bool(parse_config(integration.config_json).get("is_primary"))


def set_pihole_primary_flags(
    session: Session, primary_id: int
) -> None:
    """Ensure only primary_id has is_primary=true among pihole integrations."""
    rows = list_integrations(session, type_filter=TYPE_PIHOLE)
    now = datetime.utcnow()
    for r in rows:
        cfg = parse_config(r.config_json)
        want = r.id == primary_id
        if bool(cfg.get("is_primary")) != want:
            cfg["is_primary"] = want
            r.config_json = dump_config(cfg)
            r.updated_at = now
            session.add(r)
    session.commit()


def create_pihole(
    session: Session,
    *,
    name: str,
    base_url: str,
    password: str,
    poll_interval_sec: int = DEFAULT_PIHOLE_POLL_SEC,
    tls_verify_flag: bool = True,
    enabled: bool = True,
    is_primary: bool = False,
) -> Integration:
    base = ph.normalize_base_url(base_url)
    pw = password or ""
    if not pw:
        raise ValueError("Pi-hole password is required")
    iv = max(MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec)))
    now = datetime.utcnow()
    # First pihole becomes primary if none marked yet
    existing = list_integrations(session, type_filter=TYPE_PIHOLE)
    if not existing:
        is_primary = True
    cfg = {
        "poll_interval_sec": iv,
        "tls_verify": bool(tls_verify_flag),
        "is_primary": bool(is_primary),
    }
    row = Integration(
        type=TYPE_PIHOLE,
        name=(name or "Pi-hole").strip() or "Pi-hole",
        base_url=base,
        enabled=enabled,
        config_json=dump_config(cfg),
        credentials_encrypted=encrypt_credentials("", password=pw),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    if is_primary and row.id:
        set_pihole_primary_flags(session, row.id)
        session.refresh(row)
    return row


def update_pihole(
    session: Session,
    integration: Integration,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    password: Optional[str] = None,
    poll_interval_sec: Optional[int] = None,
    tls_verify_flag: Optional[bool] = None,
    enabled: Optional[bool] = None,
    is_primary: Optional[bool] = None,
) -> Integration:
    if name is not None:
        integration.name = name.strip() or integration.name
    if base_url is not None and base_url.strip():
        integration.base_url = ph.normalize_base_url(base_url)
    if password is not None and password != "":
        prev = decrypt_credentials(integration)
        integration.credentials_encrypted = encrypt_credentials(
            "", password=password or prev.get("password") or ""
        )
    cfg = parse_config(integration.config_json)
    if poll_interval_sec is not None:
        cfg["poll_interval_sec"] = max(
            MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec))
        )
    if tls_verify_flag is not None:
        cfg["tls_verify"] = bool(tls_verify_flag)
    make_primary = False
    if is_primary is not None:
        cfg["is_primary"] = bool(is_primary)
        make_primary = bool(is_primary)
    integration.config_json = dump_config(cfg)
    if enabled is not None:
        integration.enabled = bool(enabled)
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    if make_primary and integration.id:
        set_pihole_primary_flags(session, integration.id)
        session.refresh(integration)
    return integration


def create_npm(
    session: Session,
    *,
    name: str,
    base_url: str,
    identity: str,
    password: str,
    poll_interval_sec: int = DEFAULT_NPM_POLL_SEC,
    tls_verify_flag: bool = True,
    enabled: bool = True,
) -> Integration:
    base = npm_mod.normalize_base_url(base_url)
    ident = (identity or "").strip()
    pw = password or ""
    if not ident or not pw:
        raise ValueError("NPM identity and password are required")
    iv = max(MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec)))
    now = datetime.utcnow()
    row = Integration(
        type=TYPE_NPM,
        name=(name or "Nginx Proxy Manager").strip() or "Nginx Proxy Manager",
        base_url=base,
        enabled=enabled,
        config_json=dump_config(
            {"poll_interval_sec": iv, "tls_verify": bool(tls_verify_flag)}
        ),
        credentials_encrypted=encrypt_credentials(
            "", username=ident, password=pw
        ),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_npm(
    session: Session,
    integration: Integration,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    identity: Optional[str] = None,
    password: Optional[str] = None,
    poll_interval_sec: Optional[int] = None,
    tls_verify_flag: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> Integration:
    if name is not None:
        integration.name = name.strip() or integration.name
    if base_url is not None and base_url.strip():
        integration.base_url = npm_mod.normalize_base_url(base_url)
    prev = decrypt_credentials(integration)
    new_user = prev.get("username") or ""
    new_pw = prev.get("password") or ""
    if identity is not None:
        new_user = identity.strip()
    if password is not None and password != "":
        new_pw = password
    if identity is not None or (password is not None and password != ""):
        integration.credentials_encrypted = encrypt_credentials(
            "", username=new_user, password=new_pw
        )
    cfg = parse_config(integration.config_json)
    if poll_interval_sec is not None:
        cfg["poll_interval_sec"] = max(
            MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec))
        )
    if tls_verify_flag is not None:
        cfg["tls_verify"] = bool(tls_verify_flag)
    integration.config_json = dump_config(cfg)
    if enabled is not None:
        integration.enabled = bool(enabled)
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


def pihole_password(integration: Integration) -> str:
    return decrypt_credentials(integration).get("password") or ""


def npm_credentials(integration: Integration) -> tuple[str, str]:
    c = decrypt_credentials(integration)
    return c.get("username") or "", c.get("password") or ""


def create_kuma(
    session: Session,
    *,
    name: str,
    base_url: str,
    api_key: str,
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
    tls_verify_flag: bool = True,
    enabled: bool = True,
    username: str = "",
    password: str = "",
) -> Integration:
    base = kuma.normalize_base_url(base_url)
    key = (api_key or "").strip()
    if not key:
        raise ValueError("API key is required")
    iv = max(MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec)))
    now = datetime.utcnow()
    row = Integration(
        type=TYPE_UPTIME_KUMA,
        name=(name or "Uptime Kuma").strip() or "Uptime Kuma",
        base_url=base,
        enabled=enabled,
        config_json=dump_config(
            {"poll_interval_sec": iv, "tls_verify": bool(tls_verify_flag)}
        ),
        credentials_encrypted=encrypt_credentials(key, username=username, password=password),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _set_qt(cfg: dict[str, Any], key: str, value: Optional[str], *, default: str = "") -> None:
    v = (value if value is not None else default) or ""
    v = v.strip()
    if v:
        cfg[key] = v
    else:
        cfg.pop(key, None)


def create_grafana(
    session: Session,
    *,
    name: str,
    base_url: str,
    api_key: str = "",
    poll_interval_sec: int = DEFAULT_GRAFANA_POLL_SEC,
    tls_verify_flag: bool = True,
    enabled: bool = True,
    query_template: str = "",
    query_template_container_host: str = "",
    query_template_container: str = "",
    query_template_logs: str = "",
) -> Integration:
    """Create Grafana integration. Service account token optional (deep links work without it)."""
    base = gf.normalize_base_url(base_url)
    iv = max(MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec)))
    now = datetime.utcnow()
    cfg: dict[str, Any] = {
        "poll_interval_sec": iv,
        "tls_verify": bool(tls_verify_flag),
    }
    _set_qt(cfg, "query_template", query_template, default=DEFAULT_QT_HOST)
    _set_qt(
        cfg,
        "query_template_container_host",
        query_template_container_host,
        default=DEFAULT_QT_CONTAINER_HOST,
    )
    _set_qt(
        cfg,
        "query_template_container",
        query_template_container,
        default=DEFAULT_QT_CONTAINER,
    )
    _set_qt(cfg, "query_template_logs", query_template_logs, default=DEFAULT_QT_LOGS)
    row = Integration(
        type=TYPE_GRAFANA,
        name=(name or "Grafana").strip() or "Grafana",
        base_url=base,
        enabled=enabled,
        config_json=dump_config(cfg),
        credentials_encrypted=encrypt_credentials((api_key or "").strip())
        if (api_key or "").strip()
        else None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_grafana(
    session: Session,
    integration: Integration,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    poll_interval_sec: Optional[int] = None,
    tls_verify_flag: Optional[bool] = None,
    enabled: Optional[bool] = None,
    query_template: Optional[str] = None,
    query_template_container_host: Optional[str] = None,
    query_template_container: Optional[str] = None,
    query_template_logs: Optional[str] = None,
    clear_token: bool = False,
) -> Integration:
    if name is not None:
        integration.name = name.strip() or integration.name
    if base_url is not None and base_url.strip():
        integration.base_url = gf.normalize_base_url(base_url)
    if clear_token:
        integration.credentials_encrypted = None
    elif api_key is not None and api_key.strip():
        integration.credentials_encrypted = encrypt_credentials(api_key.strip())
    cfg = parse_config(integration.config_json)
    if poll_interval_sec is not None:
        cfg["poll_interval_sec"] = max(
            MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec))
        )
    if tls_verify_flag is not None:
        cfg["tls_verify"] = bool(tls_verify_flag)
    if query_template is not None:
        _set_qt(cfg, "query_template", query_template)
    if query_template_container_host is not None:
        _set_qt(cfg, "query_template_container_host", query_template_container_host)
    if query_template_container is not None:
        _set_qt(cfg, "query_template_container", query_template_container)
    if query_template_logs is not None:
        _set_qt(cfg, "query_template_logs", query_template_logs)
    integration.config_json = dump_config(cfg)
    if enabled is not None:
        integration.enabled = bool(enabled)
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


def query_template(integration: Integration) -> str:
    return str(
        parse_config(integration.config_json).get("query_template") or DEFAULT_QT_HOST
    ).strip()


def query_template_container_host(integration: Integration) -> str:
    cfg = parse_config(integration.config_json)
    return str(
        cfg.get("query_template_container_host") or DEFAULT_QT_CONTAINER_HOST
    ).strip()


def query_template_container(integration: Integration) -> str:
    cfg = parse_config(integration.config_json)
    return str(cfg.get("query_template_container") or DEFAULT_QT_CONTAINER).strip()


def query_template_logs(integration: Integration) -> str:
    cfg = parse_config(integration.config_json)
    return str(cfg.get("query_template_logs") or DEFAULT_QT_LOGS).strip()


def preferred_display_names(integration: Integration) -> dict[str, str]:
    """Grafana dashboard UID → operator preferred chip label (integration config)."""
    raw = parse_config(integration.config_json).get("display_names")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        uid = str(k or "").strip()
        name = str(v or "").strip()
        if uid and name:
            out[uid] = name
    return out


def preferred_display_name(integration: Integration, uid: Optional[str]) -> str:
    """Preferred PiHerder label for a dashboard UID, or empty if unset."""
    u = (uid or "").strip()
    if not u:
        return ""
    return preferred_display_names(integration).get(u) or ""


def set_preferred_display_name(
    session: Session,
    integration: Integration,
    uid: str,
    display_name: str,
) -> Integration:
    """Persist preferred name for a dashboard UID on the integration (or clear if blank)."""
    u = (uid or "").strip()
    if not u:
        raise ValueError("Dashboard UID required")
    cfg = parse_config(integration.config_json)
    names = cfg.get("display_names")
    if not isinstance(names, dict):
        names = {}
    else:
        names = {str(k): str(v) for k, v in names.items() if str(k or "").strip()}
    custom = (display_name or "").strip()
    if custom:
        names[u] = custom
    else:
        names.pop(u, None)
        # drop empty-string keys
        names = {k: v for k, v in names.items() if str(v or "").strip()}
    if names:
        cfg["display_names"] = names
    else:
        cfg.pop("display_names", None)
    integration.config_json = dump_config(cfg)
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


def resolve_grafana_display_label(
    integration: Integration,
    binding: IntegrationBinding,
    *,
    meta: Optional[dict[str, Any]] = None,
) -> tuple[str, str, str]:
    """Return (label, preferred_or_override, grafana_title) for chips/UI.

    Preference: integration preferred name for UID → legacy binding label_override
    → Grafana title → external_label → UID.
    """
    meta = meta if meta is not None else parse_binding_meta(binding)
    uid = (binding.external_id or meta.get("uid") or "").strip()
    grafana_title = (meta.get("grafana_title") or meta.get("title") or "").strip()
    preferred = preferred_display_name(integration, uid)
    legacy = (meta.get("label_override") or "").strip()
    # If preferred is set it wins; legacy per-binding override only when no preferred
    override = preferred or legacy
    label = (
        override
        or grafana_title
        or (binding.external_label or "").strip()
        or uid
        or "dashboard"
    )
    return label, override, grafana_title


def normalize_grafana_kind(kind: Optional[str]) -> str:
    k = (kind or "").strip().lower()
    if k not in GRAFANA_KINDS:
        return GRAFANA_KIND_METRICS
    return k


def binding_grafana_kind(
    binding: Optional[IntegrationBinding] = None,
    *,
    meta: Optional[dict[str, Any]] = None,
    docker_project: str = "",
    docker_container: str = "",
) -> str:
    """Resolve Grafana binding kind for UI tabs / templates.

    Preference: explicit meta.kind → docker scope columns imply containers → metrics.
    (Older binds / poll overwrites often omitted kind; docker_* is the source of truth.)
    """
    meta = meta if meta is not None else (parse_binding_meta(binding) if binding else {})
    raw = (meta.get("kind") or "").strip().lower()
    if raw in GRAFANA_KINDS:
        return raw
    proj = (
        docker_project
        or (binding.docker_project if binding else None)
        or meta.get("docker_project")
        or ""
    )
    cont = (
        docker_container
        or (binding.docker_container if binding else None)
        or meta.get("docker_container")
        or ""
    )
    if str(proj).strip() or str(cont).strip():
        return GRAFANA_KIND_CONTAINERS
    scope = str(meta.get("scope") or "").strip().lower()
    if scope in ("container", "project", "docker"):
        return GRAFANA_KIND_CONTAINERS
    return GRAFANA_KIND_METRICS


def resolve_grafana_query_template(
    integration: Integration,
    *,
    kind: str = GRAFANA_KIND_METRICS,
    docker_project: str = "",
    docker_container: str = "",
    meta: Optional[dict[str, Any]] = None,
) -> str:
    """Pick query template: binding override → kind/scope default on integration."""
    meta = meta or {}
    override = str(meta.get("query_template") or "").strip()
    if override:
        return override
    kind = normalize_grafana_kind(kind) if kind else binding_grafana_kind(
        meta=meta,
        docker_project=docker_project,
        docker_container=docker_container,
    )
    # Re-infer if caller passed default metrics but docker scope says containers
    if kind == GRAFANA_KIND_METRICS and (
        (docker_project or meta.get("docker_project") or "").strip()
        or (docker_container or meta.get("docker_container") or "").strip()
    ):
        kind = GRAFANA_KIND_CONTAINERS
    cont = (docker_container or meta.get("docker_container") or "").strip()
    if kind == GRAFANA_KIND_LOGS:
        return query_template_logs(integration)
    if kind == GRAFANA_KIND_CONTAINERS:
        if cont:
            return query_template_container(integration)
        return query_template_container_host(integration)
    return query_template(integration)


def dashboards_from_cache(integration: Integration) -> list[dict[str, Any]]:
    return gf.dashboards_from_status(parse_last_status(integration))


def update_kuma(
    session: Session,
    integration: Integration,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    poll_interval_sec: Optional[int] = None,
    tls_verify_flag: Optional[bool] = None,
    enabled: Optional[bool] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Integration:
    if name is not None:
        integration.name = name.strip() or integration.name
    if base_url is not None and base_url.strip():
        integration.base_url = kuma.normalize_base_url(base_url)
    # Credential updates (api key / optional login)
    if (
        (api_key is not None and api_key.strip())
        or username is not None
        or (password is not None and password != "")
    ):
        prev = decrypt_credentials(integration)
        new_key = (api_key or "").strip() or prev.get("api_key") or ""
        new_user = prev.get("username") or ""
        if username is not None:
            new_user = username.strip()
        new_pw = prev.get("password") or ""
        if password is not None and password != "":
            new_pw = password
        if username is not None and username.strip() == "" and (password is None or password == ""):
            # Clear optional login
            new_user, new_pw = "", ""
        integration.credentials_encrypted = encrypt_credentials(
            new_key, username=new_user, password=new_pw
        )
    cfg = parse_config(integration.config_json)
    if poll_interval_sec is not None:
        cfg["poll_interval_sec"] = max(
            MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec))
        )
    if tls_verify_flag is not None:
        cfg["tls_verify"] = bool(tls_verify_flag)
    integration.config_json = dump_config(cfg)
    if enabled is not None:
        integration.enabled = bool(enabled)
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


def delete_integration(session: Session, integration: Integration) -> None:
    # Bindings cascade via FK when DB supports it; also delete explicitly for SQLite/tests
    binds = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration.id
            )
        ).all()
    )
    for b in binds:
        session.delete(b)
    session.delete(integration)
    session.commit()


def list_bindings(
    session: Session,
    *,
    integration_id: Optional[int] = None,
    server_id: Optional[int] = None,
    role: Optional[str] = None,
) -> list[IntegrationBinding]:
    q = select(IntegrationBinding)
    if integration_id is not None:
        q = q.where(IntegrationBinding.integration_id == integration_id)
    if server_id is not None:
        q = q.where(IntegrationBinding.server_id == server_id)
    if role is not None:
        q = q.where(IntegrationBinding.role == role)
    return list(session.exec(q).all())


def set_binding(
    session: Session,
    *,
    integration_id: int,
    server_id: int,
    external_id: str,
    role: str = ROLE_SSH,
    docker_project: Optional[str] = None,
    docker_container: Optional[str] = None,
    external_label: Optional[str] = None,
    external_meta: Optional[dict[str, Any]] = None,
    last_state: Optional[str] = None,
    last_message: Optional[str] = None,
    binding_id: Optional[int] = None,
) -> IntegrationBinding:
    ext = str(external_id or "").strip()
    if not ext:
        raise ValueError("Monitor id is required")
    if not session.get(Server, server_id):
        raise ValueError("Server not found")
    if not session.get(Integration, integration_id):
        raise ValueError("Integration not found")

    proj = (docker_project or "").strip() or None
    cont = (docker_container or "").strip() or None
    # role=service: docker_project optional
    #   - set → Docker project/container scope (shown on Docker page)
    #   - empty → host-level service (HAOS, bare metal, etc. — shown on server detail)
    # role=dashboard (Grafana): docker scope only for kind=containers
    if role == ROLE_SSH:
        proj, cont = None, None
    elif role in (ROLE_PROXY_HOST, ROLE_PIHOLE_HOST):
        # Optional docker scope for linking product → fleet host / service
        pass
    elif role == ROLE_DASHBOARD:
        kind_pre = normalize_grafana_kind(
            (external_meta or {}).get("kind") if external_meta else None
        )
        if kind_pre != GRAFANA_KIND_CONTAINERS:
            proj, cont = None, None
        # kind=containers: host overview (no project/container), project-only,
        # or specific container (project optional)
    elif role == ROLE_SERVICE and not proj:
        cont = None  # container only makes sense under a project

    now = datetime.utcnow()
    meta = dict(external_meta) if external_meta else {}
    if role in (ROLE_SERVICE, ROLE_PROXY_HOST, ROLE_PIHOLE_HOST):
        if proj:
            meta["docker_project"] = proj
            if cont:
                meta["docker_container"] = cont
            else:
                meta.pop("docker_container", None)
            meta["scope"] = "docker"
        else:
            meta.pop("docker_project", None)
            meta.pop("docker_container", None)
            meta["scope"] = "host"
            cont = None  # container only under project
    elif role == ROLE_DASHBOARD:
        kind = normalize_grafana_kind(meta.get("kind"))
        meta["kind"] = kind
        if kind == GRAFANA_KIND_CONTAINERS:
            if cont:
                meta["docker_project"] = proj or ""
                meta["docker_container"] = cont
                meta["scope"] = "container"
            elif proj:
                meta["docker_project"] = proj
                meta.pop("docker_container", None)
                meta["scope"] = "project"
            else:
                meta.pop("docker_project", None)
                meta.pop("docker_container", None)
                meta["scope"] = "host"
        else:
            meta.pop("docker_project", None)
            meta.pop("docker_container", None)
            meta["scope"] = "host"
    meta_s = json.dumps(meta) if meta else None

    def _scope_match(r: IntegrationBinding) -> bool:
        return (r.docker_project or None) == proj and (r.docker_container or None) == cont

    def _find_by_unique_scope() -> Optional[IntegrationBinding]:
        """Match DB unique index uq_integ_bind_scope (kind is meta-only, not in index)."""
        rows = list(
            session.exec(
                select(IntegrationBinding).where(
                    IntegrationBinding.integration_id == integration_id,
                    IntegrationBinding.server_id == server_id,
                    IntegrationBinding.role == role,
                    IntegrationBinding.external_id == ext,
                )
            ).all()
        )
        for r in rows:
            if _scope_match(r):
                return r
        return None

    existing: Optional[IntegrationBinding] = None
    by_id: Optional[IntegrationBinding] = None
    if binding_id is not None:
        by_id = session.get(IntegrationBinding, binding_id)
        if by_id and (
            by_id.integration_id != integration_id or by_id.role != role
        ):
            by_id = None

    if role == ROLE_SSH:
        # One SSH monitor per server per integration
        existing = session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration_id,
                IntegrationBinding.server_id == server_id,
                IntegrationBinding.role == role,
            )
        ).first()
    elif role in (ROLE_DASHBOARD, ROLE_SERVICE, ROLE_PROXY_HOST, ROLE_PIHOLE_HOST):
        # Prefer row already occupying the unique scope (proj/container).
        existing = _find_by_unique_scope()
        if by_id is not None:
            if existing is None:
                # Edit/move binding into a free scope — update the row in place
                existing = by_id
            elif existing.id != by_id.id:
                # Edit moved onto a scope that already has a row (e.g. clone target
                # already bound, or edit container to an existing one): keep the
                # scope row, drop the stale source binding.
                session.delete(by_id)
                session.flush()
            # else: by_id is the scope row already
        # Clone/new with no binding_id: existing is scope match or None → insert
    else:
        existing = by_id

    if existing:
        existing.external_id = ext
        existing.server_id = server_id
        existing.docker_project = proj
        existing.docker_container = cont
        existing.external_label = external_label
        if meta_s is not None:
            existing.external_meta_json = meta_s
        if last_state is not None:
            existing.last_state = last_state
        if last_message is not None:
            existing.last_message = last_message
        existing.updated_at = now
        session.add(existing)
        try:
            session.commit()
        except Exception:
            session.rollback()
            # Concurrent insert won the unique race — load and update that row
            other = _find_by_unique_scope()
            if not other:
                raise
            other.external_label = external_label
            if meta_s is not None:
                other.external_meta_json = meta_s
            if last_state is not None:
                other.last_state = last_state
            if last_message is not None:
                other.last_message = last_message
            other.updated_at = now
            session.add(other)
            session.commit()
            existing = other
        session.refresh(existing)
        if role == ROLE_SERVICE and not existing.logo_path:
            try:
                maybe_discover_logo(session, existing)
            except Exception:
                pass
        return existing

    row = IntegrationBinding(
        integration_id=integration_id,
        server_id=server_id,
        role=role,
        docker_project=proj,
        docker_container=cont,
        external_id=ext,
        external_label=external_label,
        external_meta_json=meta_s,
        last_state=last_state,
        last_message=last_message,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        # Unique race or missed match: merge into the existing scope row
        other = _find_by_unique_scope()
        if not other:
            raise ValueError(
                "A binding for this dashboard and scope already exists "
                "(same host / project / container)."
            )
        other.external_label = external_label
        if meta_s is not None:
            other.external_meta_json = meta_s
        if last_state is not None:
            other.last_state = last_state
        if last_message is not None:
            other.last_message = last_message
        other.updated_at = now
        session.add(other)
        session.commit()
        session.refresh(other)
        return other
    session.refresh(row)
    if role == ROLE_SERVICE:
        try:
            maybe_discover_logo(session, row)
        except Exception:
            pass
    return row


def apply_grafana_preferred_name(
    session: Session,
    *,
    integration_id: int,
    uid: str,
    display_name: str,
) -> list[IntegrationBinding]:
    """Set preferred display name for a dashboard UID on the integration.

    Primary UX: Inventory tab (one name per Grafana dashboard). Stored in
    config_json.display_names[uid] so **new** bindings and polls pick it up.
    Existing ROLE_DASHBOARD rows with that UID are synced (labels + meta).
    Blank display_name clears the preferred name for that UID.
    """
    integration = session.get(Integration, integration_id)
    if not integration or integration.type != TYPE_GRAFANA:
        raise ValueError("Grafana integration not found")

    u = (uid or "").strip()
    if not u:
        raise ValueError("Dashboard UID required")

    custom = (display_name or "").strip()
    set_preferred_display_name(session, integration, u, custom)
    session.refresh(integration)

    targets = list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration_id,
                IntegrationBinding.role == ROLE_DASHBOARD,
                IntegrationBinding.external_id == u,
            )
        ).all()
    )

    now = datetime.utcnow()
    updated: list[IntegrationBinding] = []
    for b in targets:
        meta = parse_binding_meta(b)
        if custom:
            meta["label_override"] = custom
            b.external_label = custom
        else:
            meta.pop("label_override", None)
            b.external_label = (
                (meta.get("grafana_title") or meta.get("title") or "").strip()
                or b.external_id
            )
        b.external_meta_json = json.dumps(meta)
        b.updated_at = now
        session.add(b)
        updated.append(b)
    session.commit()
    for b in updated:
        session.refresh(b)
    return updated


def apply_grafana_display_name(
    session: Session,
    *,
    integration_id: int,
    binding_id: int,
    display_name: str,
    apply_same_dashboard: bool = True,
) -> list[IntegrationBinding]:
    """Backward-compatible wrapper: resolve UID from binding then set preferred name."""
    del apply_same_dashboard
    row = session.get(IntegrationBinding, binding_id)
    if (
        not row
        or row.integration_id != integration_id
        or (row.role or "") != ROLE_DASHBOARD
    ):
        raise ValueError("Dashboard binding not found")
    return apply_grafana_preferred_name(
        session,
        integration_id=integration_id,
        uid=row.external_id or "",
        display_name=display_name,
    )


def clear_binding(
    session: Session,
    *,
    integration_id: int,
    server_id: int,
    role: str = ROLE_SSH,
    external_id: Optional[str] = None,
    binding_id: Optional[int] = None,
) -> bool:
    if binding_id is not None:
        row = session.get(IntegrationBinding, binding_id)
        if not row or row.integration_id != integration_id:
            return False
        session.delete(row)
        session.commit()
        return True
    q = select(IntegrationBinding).where(
        IntegrationBinding.integration_id == integration_id,
        IntegrationBinding.server_id == server_id,
        IntegrationBinding.role == role,
    )
    if external_id:
        q = q.where(IntegrationBinding.external_id == str(external_id).strip())
    row = session.exec(q).first()
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


def docker_inventory_options(session: Session, server_id: int) -> list[dict[str, Any]]:
    """Compose projects + containers for service-binding pickers."""
    server = session.get(Server, server_id)
    if not server:
        return []
    from .. import docker_inventory as inv_svc

    inv = inv_svc.parse_inventory(server) or {}
    out: list[dict[str, Any]] = []
    for p in inv.get("projects") or []:
        if not isinstance(p, dict):
            continue
        pname = (p.get("name") or "").strip()
        if not pname:
            continue
        containers = []
        for c in p.get("containers") or []:
            if not isinstance(c, dict):
                continue
            cname = (c.get("name") or c.get("compose_service") or "").strip()
            if cname:
                containers.append(
                    {
                        "name": cname,
                        "compose_service": c.get("compose_service") or "",
                        "running": bool(c.get("running")),
                    }
                )
        out.append({"name": pname, "path": p.get("path") or "", "containers": containers})
    return out


def service_bindings_for_server(session: Session, server_id: int) -> list[IntegrationBinding]:
    return list_bindings(session, server_id=server_id, role=ROLE_SERVICE)


def is_host_service_binding(binding: IntegrationBinding) -> bool:
    """True when service monitor is host-scoped (no Docker project)."""
    return not (binding.docker_project or "").strip()


def is_docker_service_binding(binding: IntegrationBinding) -> bool:
    return bool((binding.docker_project or "").strip())


def binding_to_chip(
    session: Session, binding: IntegrationBinding
) -> dict[str, Any]:
    integ = get_integration(session, binding.integration_id)
    open_url = binding_open_url(integ, binding) if integ else ""
    meta = parse_binding_meta(binding)
    logo = None
    if binding.logo_path and binding.id:
        logo = f"/services/logo/{binding.id}"
    return {
        "id": binding.id,
        "state": binding.last_state or "unknown",
        "label": binding.external_label or binding.external_id,
        "message": binding.last_message or "",
        "open_url": open_url,
        "integration_id": binding.integration_id,
        "server_id": binding.server_id,
        "checked_at": binding.last_checked_at,
        "cert_days": meta.get("cert_days_remaining"),
        "cert_valid": meta.get("cert_is_valid"),
        "url": meta.get("url") or meta.get("target") or "",
        "docker_project": binding.docker_project or "",
        "docker_container": binding.docker_container or "",
        "scope": "docker" if is_docker_service_binding(binding) else "host",
        "logo_url": logo,
        "has_logo": bool(binding.logo_path),
    }


def fleet_service_count(session: Session) -> int:
    """Count of role=service bindings across the fleet."""
    rows = list_bindings(session, role=ROLE_SERVICE)
    return len(rows)


def fleet_service_chips(session: Session) -> list[dict[str, Any]]:
    """All service bindings with server name for fleet Services grid."""
    from ...models import Server

    binds = list_bindings(session, role=ROLE_SERVICE)
    server_names: dict[int, str] = {}
    out: list[dict[str, Any]] = []
    for b in binds:
        chip = binding_to_chip(session, b)
        sid = b.server_id
        if sid not in server_names:
            srv = session.get(Server, sid)
            server_names[sid] = srv.name if srv else f"#{sid}"
        chip["server_name"] = server_names[sid]
        if is_docker_service_binding(b):
            loc = b.docker_project or ""
            if b.docker_container:
                loc = f"{loc} / {b.docker_container}"
            chip["location"] = loc
            chip["location_kind"] = "docker"
        else:
            chip["location"] = "Host service"
            chip["location_kind"] = "host"
        out.append(chip)
    out.sort(
        key=lambda c: (
            (c.get("label") or "").lower(),
            (c.get("server_name") or "").lower(),
        )
    )
    return out


def maybe_discover_logo(session: Session, binding: IntegrationBinding) -> bool:
    """If binding has URL and no logo, try favicon fetch. Returns True if saved."""
    if not binding or not binding.id or binding.logo_path:
        return False
    if binding.role != ROLE_SERVICE:
        return False
    meta = parse_binding_meta(binding)
    url = (meta.get("url") or meta.get("target") or "").strip()
    if not url.startswith("http"):
        return False
    try:
        from .. import service_logos as logos

        rel = logos.try_discover_and_save(binding.id, url)
        if rel:
            binding.logo_path = rel
            binding.updated_at = datetime.utcnow()
            session.add(binding)
            session.commit()
            session.refresh(binding)
            return True
    except Exception as e:
        logger.debug("logo discover skip: %s", e)
    return False


def host_service_chips_for_server(
    session: Session, server_id: int
) -> list[dict[str, Any]]:
    """Host-level HTTP/TLS services (HAOS, bare metal) for server detail."""
    out = []
    for b in service_bindings_for_server(session, server_id):
        if is_host_service_binding(b):
            out.append(binding_to_chip(session, b))
    return out


def all_service_chips_for_server(
    session: Session, server_id: int
) -> list[dict[str, Any]]:
    """All HTTP/TLS service bindings for a server (host + Docker), for Services page."""
    out = []
    for b in service_bindings_for_server(session, server_id):
        chip = binding_to_chip(session, b)
        # Location string for UI
        if is_docker_service_binding(b):
            loc = b.docker_project or ""
            if b.docker_container:
                loc = f"{loc} / {b.docker_container}"
            chip["location"] = loc
            chip["location_kind"] = "docker"
        else:
            chip["location"] = "Host service"
            chip["location_kind"] = "host"
        out.append(chip)
    # Host first, then docker by location name
    out.sort(
        key=lambda c: (
            0 if c.get("location_kind") == "host" else 1,
            (c.get("location") or "").lower(),
            (c.get("label") or "").lower(),
        )
    )
    return out


def kuma_index_for_server(session: Session, server_id: int) -> dict[str, Any]:
    """Maps for Docker UI: project name → chips, container name → chips."""
    binds = service_bindings_for_server(session, server_id)
    by_project: dict[str, list[dict[str, Any]]] = {}
    by_container: dict[str, list[dict[str, Any]]] = {}
    for b in binds:
        if not is_docker_service_binding(b):
            continue
        chip = binding_to_chip(session, b)
        meta = parse_binding_meta(b)
        proj = (b.docker_project or "").strip()
        cont = (b.docker_container or "").strip()
        if cont:
            by_container.setdefault(cont, []).append(chip)
            cs = (meta.get("compose_service") or "").strip()
            if cs and cs != cont:
                by_container.setdefault(cs, []).append(chip)
        elif proj:
            by_project.setdefault(proj, []).append(chip)
    return {"by_project": by_project, "by_container": by_container}


def parse_binding_meta(binding: IntegrationBinding) -> dict[str, Any]:
    if not binding.external_meta_json:
        return {}
    try:
        data = json.loads(binding.external_meta_json)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def binding_open_url(
    integration: Integration,
    binding: IntegrationBinding,
    *,
    server: Optional[Server] = None,
) -> str:
    if integration.type == TYPE_NPM:
        # Prefer open proxy hosts UI; id-specific deep links vary by NPM version
        return npm_mod.open_npm_url(integration.base_url, "/nginx/proxy")
    if integration.type == TYPE_PIHOLE:
        return ph.admin_url(integration.base_url, "/")
    if integration.type == TYPE_GRAFANA:
        meta = parse_binding_meta(binding)
        uid = (binding.external_id or meta.get("uid") or "").strip()
        slug = str(meta.get("slug") or "").strip()
        rel = str(meta.get("url") or "").strip()
        cont = (binding.docker_container or meta.get("docker_container") or "").strip()
        proj = (binding.docker_project or meta.get("docker_project") or "").strip()
        cs = str(meta.get("compose_service") or cont).strip()
        kind = binding_grafana_kind(
            binding, meta=meta, docker_project=proj, docker_container=cont
        )
        qt = resolve_grafana_query_template(
            integration,
            kind=kind,
            docker_project=proj,
            docker_container=cont,
            meta=meta,
        )
        hostname = ""
        name = ""
        ip = ""
        sid = str(binding.server_id or "")
        if server is not None:
            hostname = server.hostname or ""
            name = server.name or ""
            ip = server.ip_address or ""
            sid = str(server.id or sid)
        return gf.open_dashboard_url(
            integration.base_url,
            uid=uid,
            slug=slug,
            relative_url=rel,
            query_template=qt,
            hostname=hostname,
            name=name,
            ip_address=ip,
            server_id=sid,
            container=cont,
            project=proj,
            compose_service=cs,
        )
    meta = parse_binding_meta(binding)
    did = kuma.resolve_dashboard_id(
        external_id=binding.external_id or "",
        meta=meta,
    )
    return kuma.open_kuma_url(integration.base_url, dashboard_id=did)


def _grafana_chip_dict(
    integ: Integration,
    binding: IntegrationBinding,
    *,
    server: Optional[Server] = None,
) -> dict[str, Any]:
    meta = parse_binding_meta(binding)
    cont = (binding.docker_container or "").strip()
    proj = (binding.docker_project or "").strip()
    kind = binding_grafana_kind(binding, meta=meta, docker_project=proj, docker_container=cont)
    scope = str(
        meta.get("scope")
        or ("container" if cont else ("project" if proj else "host"))
    )
    open_url = binding_open_url(integ, binding, server=server)
    kind_label = {
        GRAFANA_KIND_METRICS: "Host metrics",
        GRAFANA_KIND_CONTAINERS: "Containers",
        GRAFANA_KIND_LOGS: "Logs",
    }.get(kind, kind)
    loc = ""
    if cont:
        loc = f"{proj}/{cont}" if proj else cont
    elif proj:
        loc = proj
    label, override, grafana_title = resolve_grafana_display_label(
        integ, binding, meta=meta
    )
    preferred = preferred_display_name(integ, binding.external_id)
    return {
        "id": binding.id,
        "state": binding.last_state or "linked",
        "label": label,
        "label_override": override,
        "preferred_name": preferred,
        "grafana_title": grafana_title,
        "message": binding.last_message or meta.get("folder_title") or "",
        "open_url": open_url,
        "integration_id": binding.integration_id,
        "integration_name": integ.name,
        "server_id": binding.server_id,
        "uid": binding.external_id,
        "checked_at": binding.last_checked_at,
        "kind": kind,
        "kind_label": kind_label,
        "scope": scope,
        "docker_project": proj,
        "docker_container": cont,
        "location": loc,
    }


def grafana_chips_for_server(
    session: Session,
    server_id: int,
    *,
    host_only: bool = True,
) -> list[dict[str, Any]]:
    """Dashboard deep-link chips for server detail.

    host_only=True (default): metrics, logs, and host-level container overview
    (no specific docker_container). Per-container chips live on the Docker page.
    """
    server = session.get(Server, server_id)
    out: list[dict[str, Any]] = []
    for b in list_bindings(session, server_id=server_id, role=ROLE_DASHBOARD):
        integ = get_integration(session, b.integration_id)
        if not integ or integ.type != TYPE_GRAFANA or not integ.enabled:
            continue
        if host_only and (b.docker_container or "").strip():
            continue
        chip = _grafana_chip_dict(integ, b, server=server)
        out.append(chip)
    kind_order = {
        GRAFANA_KIND_METRICS: 0,
        GRAFANA_KIND_CONTAINERS: 1,
        GRAFANA_KIND_LOGS: 2,
    }
    out.sort(
        key=lambda c: (
            kind_order.get(c.get("kind") or "", 9),
            (c.get("label") or "").lower(),
        )
    )
    return out


def grafana_index_for_server(session: Session, server_id: int) -> dict[str, Any]:
    """Maps for Docker UI: project → chips, container name → chips (kind=containers)."""
    server = session.get(Server, server_id)
    by_project: dict[str, list[dict[str, Any]]] = {}
    by_container: dict[str, list[dict[str, Any]]] = {}
    for b in list_bindings(session, server_id=server_id, role=ROLE_DASHBOARD):
        integ = get_integration(session, b.integration_id)
        if not integ or integ.type != TYPE_GRAFANA or not integ.enabled:
            continue
        meta = parse_binding_meta(b)
        if normalize_grafana_kind(meta.get("kind")) != GRAFANA_KIND_CONTAINERS:
            continue
        chip = _grafana_chip_dict(integ, b, server=server)
        cont = (b.docker_container or "").strip()
        proj = (b.docker_project or "").strip()
        if cont:
            by_container.setdefault(cont, []).append(chip)
            cs = str(meta.get("compose_service") or "").strip()
            if cs and cs != cont:
                by_container.setdefault(cs, []).append(chip)
        elif proj:
            by_project.setdefault(proj, []).append(chip)
    return {"by_project": by_project, "by_container": by_container}


def binding_message_from_monitor(mon: "kuma.KumaMonitor") -> str:
    parts: list[str] = []
    if mon.cert_is_valid is True and mon.cert_days_remaining is not None:
        parts.append(f"TLS {int(mon.cert_days_remaining)}d")
    elif mon.cert_is_valid is False:
        parts.append("TLS invalid")
    if mon.response_time_ms is not None:
        parts.append(f"{mon.response_time_ms:.0f} ms")
    if not parts:
        parts.append(mon.target_display())
    return " · ".join(parts)


def parse_last_status(integration: Integration) -> dict[str, Any]:
    if not integration.last_status_json:
        return {}
    try:
        data = json.loads(integration.last_status_json)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def monitors_from_cache(integration: Integration) -> list[dict[str, Any]]:
    data = parse_last_status(integration)
    mons = data.get("monitors") or []
    return mons if isinstance(mons, list) else []


def bindings_by_server(
    session: Session, *, role: str = ROLE_SSH
) -> dict[int, list[IntegrationBinding]]:
    """server_id → bindings (ssh role by default)."""
    rows = list_bindings(session, role=role)
    out: dict[int, list[IntegrationBinding]] = {}
    for b in rows:
        out.setdefault(b.server_id, []).append(b)
    return out
