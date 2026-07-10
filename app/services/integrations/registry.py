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
from . import uptime_kuma as kuma

logger = logging.getLogger(__name__)

TYPE_UPTIME_KUMA = "uptime_kuma"
TYPE_GRAFANA = "grafana"
ROLE_SSH = "ssh_reachability"
ROLE_SERVICE = "service"  # HTTP(s) / app / cert monitoring (Kuma)
ROLE_DASHBOARD = "dashboard"  # Grafana dashboard deep link per server

DEFAULT_POLL_INTERVAL_SEC = 60
MIN_POLL_INTERVAL_SEC = 30
MAX_POLL_INTERVAL_SEC = 900
DEFAULT_GRAFANA_POLL_SEC = 120


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
) -> Integration:
    """Create Grafana integration. Service account token optional (deep links work without it)."""
    base = gf.normalize_base_url(base_url)
    iv = max(MIN_POLL_INTERVAL_SEC, min(MAX_POLL_INTERVAL_SEC, int(poll_interval_sec)))
    now = datetime.utcnow()
    cfg: dict[str, Any] = {
        "poll_interval_sec": iv,
        "tls_verify": bool(tls_verify_flag),
    }
    qt = (query_template or "").strip()
    if qt:
        cfg["query_template"] = qt
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
        qt = query_template.strip()
        if qt:
            cfg["query_template"] = qt
        else:
            cfg.pop("query_template", None)
    integration.config_json = dump_config(cfg)
    if enabled is not None:
        integration.enabled = bool(enabled)
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


def query_template(integration: Integration) -> str:
    return str(parse_config(integration.config_json).get("query_template") or "").strip()


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
    # role=dashboard (Grafana): no docker scope
    if role in (ROLE_SSH, ROLE_DASHBOARD):
        proj, cont = None, None
    elif role == ROLE_SERVICE and not proj:
        cont = None  # container only makes sense under a project

    now = datetime.utcnow()
    meta = dict(external_meta) if external_meta else {}
    if role == ROLE_SERVICE:
        if proj:
            meta["docker_project"] = proj
            if cont:
                meta["docker_container"] = cont
            meta["scope"] = "docker"
        else:
            meta.pop("docker_project", None)
            meta.pop("docker_container", None)
            meta["scope"] = "host"
    meta_s = json.dumps(meta) if meta else None

    existing: Optional[IntegrationBinding] = None
    if binding_id is not None:
        existing = session.get(IntegrationBinding, binding_id)
        if existing and (
            existing.integration_id != integration_id or existing.role != role
        ):
            existing = None
    elif role == ROLE_SSH:
        # One SSH monitor per server per integration
        existing = session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration_id,
                IntegrationBinding.server_id == server_id,
                IntegrationBinding.role == role,
            )
        ).first()
    elif role == ROLE_DASHBOARD:
        # Unique dashboard uid per server per Grafana integration
        existing = session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration_id,
                IntegrationBinding.server_id == server_id,
                IntegrationBinding.role == role,
                IntegrationBinding.external_id == ext,
            )
        ).first()
    else:
        # Service: unique per project/container/monitor
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
            if (r.docker_project or None) == proj and (r.docker_container or None) == cont:
                existing = r
                break

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
        session.commit()
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
    session.commit()
    session.refresh(row)
    if role == ROLE_SERVICE:
        try:
            maybe_discover_logo(session, row)
        except Exception:
            pass
    return row


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
    if integration.type == TYPE_GRAFANA:
        meta = parse_binding_meta(binding)
        uid = (binding.external_id or meta.get("uid") or "").strip()
        slug = str(meta.get("slug") or "").strip()
        rel = str(meta.get("url") or "").strip()
        # Prefer binding override, else instance default query template
        qt = str(meta.get("query_template") or "").strip() or query_template(integration)
        hostname = ""
        name = ""
        ip = ""
        sid = str(binding.server_id or "")
        if server is None and binding.server_id:
            # Caller may not pass server; open without vars still works
            pass
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
        )
    meta = parse_binding_meta(binding)
    did = kuma.resolve_dashboard_id(
        external_id=binding.external_id or "",
        meta=meta,
    )
    return kuma.open_kuma_url(integration.base_url, dashboard_id=did)


def grafana_chips_for_server(session: Session, server_id: int) -> list[dict[str, Any]]:
    """Dashboard deep-link chips for server detail."""
    server = session.get(Server, server_id)
    out: list[dict[str, Any]] = []
    for b in list_bindings(session, server_id=server_id, role=ROLE_DASHBOARD):
        integ = get_integration(session, b.integration_id)
        if not integ or integ.type != TYPE_GRAFANA or not integ.enabled:
            continue
        open_url = binding_open_url(integ, b, server=server)
        meta = parse_binding_meta(b)
        out.append(
            {
                "id": b.id,
                "state": b.last_state or "linked",
                "label": b.external_label or b.external_id,
                "message": b.last_message or meta.get("folder_title") or "",
                "open_url": open_url,
                "integration_id": b.integration_id,
                "integration_name": integ.name,
                "server_id": b.server_id,
                "uid": b.external_id,
                "checked_at": b.last_checked_at,
            }
        )
    out.sort(key=lambda c: (c.get("label") or "").lower())
    return out


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
