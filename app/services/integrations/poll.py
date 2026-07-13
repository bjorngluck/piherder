"""Poll integrations and apply binding status + notifications."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ...database import engine
from ...models import Integration, IntegrationBinding, Server
from .. import notifications as notif_svc
from . import grafana as gf
from . import registry as reg
from . import uptime_kuma as kuma

logger = logging.getLogger(__name__)

_local_locks: dict[int, threading.Lock] = {}
_local_guard = threading.Lock()


def _process_lock(integration_id: int) -> threading.Lock:
    with _local_guard:
        if integration_id not in _local_locks:
            _local_locks[integration_id] = threading.Lock()
        return _local_locks[integration_id]


def _redis_lock(integration_id: int, ttl: int = 45) -> tuple[Any, Optional[str]]:
    """Return (redis_client_or_None, token_or_None). token set if lock acquired."""
    try:
        import redis

        url = (
            os.getenv("CELERY_BROKER_URL")
            or os.getenv("REDIS_URL")
            or "redis://localhost:6379/0"
        )
        client = redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        key = f"piherder:integration_poll:{int(integration_id)}"
        token = f"{time.time()}:{os.getpid()}"
        if client.set(key, token, nx=True, ex=ttl):
            return client, token
        return client, None
    except Exception:
        return None, None


def _redis_unlock(client: Any, integration_id: int, token: str) -> None:
    if not client or not token:
        return
    try:
        key = f"piherder:integration_poll:{int(integration_id)}"
        if client.get(key) == token:
            client.delete(key)
    except Exception:
        pass


def poll_integration(
    integration_id: int,
    *,
    notify: bool = True,
    session: Optional[Session] = None,
) -> dict[str, Any]:
    """Poll one integration; update cache + bindings. Returns summary dict."""
    own_session = session is None
    db = session or Session(engine)
    rclient = None
    rtoken = None
    try:
        # Cross-worker single-flight
        rclient, rtoken = _redis_lock(integration_id)
        if rclient is not None and rtoken is None:
            return {"ok": False, "skipped": True, "error": "poll already in progress"}

        lock = _process_lock(integration_id)
        if not lock.acquire(blocking=False):
            return {"ok": False, "skipped": True, "error": "poll already in progress"}
        try:
            return _poll_unlocked(db, integration_id, notify=notify)
        finally:
            lock.release()
    finally:
        if rtoken:
            _redis_unlock(rclient, integration_id, rtoken)
        if own_session:
            db.close()


def _poll_unlocked(db: Session, integration_id: int, *, notify: bool) -> dict[str, Any]:
    integration = db.get(Integration, integration_id)
    if not integration:
        return {"ok": False, "error": "integration not found"}
    if not integration.enabled:
        return {"ok": False, "error": "integration disabled"}

    if integration.type == reg.TYPE_UPTIME_KUMA:
        return _poll_kuma(db, integration, notify=notify)
    if integration.type == reg.TYPE_GRAFANA:
        return _poll_grafana(db, integration, notify=notify)

    return {"ok": False, "error": f"unsupported type {integration.type}"}


def _poll_kuma(db: Session, integration: Integration, *, notify: bool) -> dict[str, Any]:
    creds = reg.decrypt_credentials(integration)
    api_key = creds.get("api_key") or ""
    result = kuma.fetch_metrics(
        integration.base_url,
        api_key,
        tls_verify=reg.tls_verify(integration),
    )
    # Optional: resolve numeric dashboard ids via Kuma login (Socket.IO)
    id_map: dict[str, str] = {}
    if result.ok and creds.get("username") and creds.get("password"):
        id_map = kuma.fetch_dashboard_id_map(
            integration.base_url,
            creds["username"],
            creds["password"],
            tls_verify=reg.tls_verify(integration),
        )
        if id_map:
            kuma.apply_dashboard_id_map(result.monitors, id_map)

    now = datetime.utcnow()
    status_payload = result.to_status_json()
    status_payload["polled_at"] = now.isoformat() + "Z"
    status_payload["dashboard_ids_resolved"] = len(id_map)

    integration.last_status_json = json.dumps(status_payload)
    integration.last_polled_at = now
    integration.last_error = (result.error or "")[:500] if not result.ok else None
    integration.updated_at = now
    db.add(integration)

    bindings = list(
        db.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration.id
            )
        ).all()
    )
    updated = 0
    for b in bindings:
        prev_meta = reg.parse_binding_meta(b)
        mon = kuma.find_monitor(
            result.monitors, b.external_id or "", meta=prev_meta
        )
        prev = (b.last_state or "").lower()
        if mon is None:
            new_state = "unknown"
            msg = "monitor missing from Kuma metrics"
            label = b.external_label
            # keep prior dashboard_id in meta
            meta = dict(prev_meta) if prev_meta else {}
        else:
            new_state = mon.status
            msg = reg.binding_message_from_monitor(mon)
            label = mon.name
            meta = mon.to_dict()
            # Preserve manually set dashboard_id if monitor lacks one
            if prev_meta.get("dashboard_id") and not meta.get("dashboard_id"):
                meta["dashboard_id"] = prev_meta["dashboard_id"]
            # Prefer resolved id from name map
            if id_map.get(mon.name):
                meta["dashboard_id"] = id_map[mon.name]
                mon.dashboard_id = id_map[mon.name]

        b.last_state = new_state
        b.last_message = msg
        b.last_checked_at = now
        b.external_label = label
        b.external_meta_json = json.dumps(meta)
        b.updated_at = now
        db.add(b)
        updated += 1

        if notify and b.role in (reg.ROLE_SSH, reg.ROLE_SERVICE):
            _notify_transition(db, integration, b, prev, new_state)

    db.commit()

    # Opportunistic favicon discovery for service bindings missing a logo
    try:
        for b in bindings:
            if b.role == reg.ROLE_SERVICE and not b.logo_path:
                reg.maybe_discover_logo(db, b)
    except Exception:
        pass
    return {
        "ok": result.ok,
        "error": result.error,
        "monitor_count": len(result.monitors),
        "bindings_updated": updated,
        "dashboard_ids_resolved": len(id_map),
        "integration_id": integration.id,
    }


def _poll_grafana(
    db: Session, integration: Integration, *, notify: bool
) -> dict[str, Any]:
    """Health check + optional dashboard inventory; refresh dashboard binding labels."""
    del notify  # Grafana H1: no down notifications on dashboards
    token = reg.decrypt_api_key(integration)
    result = gf.poll(
        integration.base_url,
        token,
        tls_verify=reg.tls_verify(integration),
    )
    now = datetime.utcnow()
    status_payload = result.to_status_json()
    status_payload["polled_at"] = now.isoformat() + "Z"

    integration.last_status_json = json.dumps(status_payload)
    integration.last_polled_at = now
    integration.last_error = (result.error or "")[:500] if not result.ok else None
    integration.updated_at = now
    db.add(integration)

    by_uid = {d.uid: d for d in result.dashboards}
    bindings = list(
        db.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.integration_id == integration.id,
                IntegrationBinding.role == reg.ROLE_DASHBOARD,
            )
        ).all()
    )
    updated = 0
    instance_state = "up" if result.ok else "down"
    for b in bindings:
        prev_meta = reg.parse_binding_meta(b)
        kind = reg.binding_grafana_kind(b, meta=prev_meta)
        dash = by_uid.get((b.external_id or "").strip())
        if dash:
            meta = dash.to_dict()
            # Preserve operator scope / kind (poll inventory must not reclassify)
            meta["kind"] = kind
            if prev_meta.get("query_template"):
                meta["query_template"] = prev_meta["query_template"]
            if prev_meta.get("scope"):
                meta["scope"] = prev_meta["scope"]
            elif (b.docker_container or "").strip():
                meta["scope"] = "container"
            elif (b.docker_project or "").strip():
                meta["scope"] = "project"
            else:
                meta["scope"] = "host"
            if b.docker_project:
                meta["docker_project"] = b.docker_project
            if b.docker_container:
                meta["docker_container"] = b.docker_container
            # Grafana title for reference; integration preferred name / override wins
            meta["grafana_title"] = dash.title
            preferred = reg.preferred_display_name(integration, b.external_id)
            legacy = (prev_meta.get("label_override") or "").strip()
            override = preferred or legacy
            if override:
                meta["label_override"] = override
                b.external_label = override
            else:
                meta.pop("label_override", None)
                b.external_label = dash.title
            b.external_meta_json = json.dumps(meta)
            b.last_message = dash.folder_title or result.version or "dashboard"
        else:
            # Keep prior meta; ensure kind is recorded for UI tabs
            if not prev_meta.get("kind"):
                prev_meta["kind"] = kind
                b.external_meta_json = json.dumps(prev_meta)
            b.last_message = (
                f"Grafana {result.version}" if result.ok else (result.error or "unreachable")
            )[:500]
        b.last_state = instance_state if result.ok else "down"
        b.last_checked_at = now
        b.updated_at = now
        db.add(b)
        updated += 1

    db.commit()
    return {
        "ok": result.ok,
        "error": result.error,
        "dashboard_count": len(result.dashboards),
        "monitor_count": len(result.dashboards),
        "bindings_updated": updated,
        "version": result.version,
        "integration_id": integration.id,
    }


def _notify_transition(
    db: Session,
    integration: Integration,
    binding: IntegrationBinding,
    prev: str,
    new_state: str,
) -> None:
    fp = f"kuma_down:{integration.id}:{binding.role}:{binding.external_id}"
    server = db.get(Server, binding.server_id)
    server_name = server.name if server else f"server {binding.server_id}"
    mon_label = binding.external_label or binding.external_id
    kind = "SSH" if binding.role == reg.ROLE_SSH else "Service"

    if new_state == "down" and prev != "down":
        notif_svc.upsert_notification(
            db,
            fingerprint=fp,
            type="integration_monitor_down",
            title=f"{server_name}: {kind} monitor down",
            body=f"Uptime Kuma «{mon_label}» is down ({integration.name}).",
            link_url=f"/servers/{binding.server_id}" if binding.server_id else "/integrations",
            severity="critical",
            server_id=binding.server_id,
            payload={
                "integration_id": integration.id,
                "external_id": binding.external_id,
                "role": binding.role,
            },
        )
    elif new_state == "up" and prev == "down":
        notif_svc.resolve_by_fingerprint(db, fp)
    elif new_state != "down" and prev == "down":
        # recovered to pending/maintenance/unknown — resolve critical down
        notif_svc.resolve_by_fingerprint(db, fp)


def poll_all_enabled(*, notify: bool = True) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with Session(engine) as db:
        rows = list(
            db.exec(
                select(Integration).where(
                    Integration.enabled == True,  # noqa: E712
                    Integration.type.in_([reg.TYPE_UPTIME_KUMA, reg.TYPE_GRAFANA]),
                )
            ).all()
        )
        ids = [r.id for r in rows if r.id is not None]
    for iid in ids:
        try:
            results.append(poll_integration(iid, notify=notify))
        except Exception as e:
            logger.warning("poll integration %s failed: %s", iid, e)
            results.append({"ok": False, "integration_id": iid, "error": str(e)[:200]})
    return results


def test_connection(integration: Integration) -> Any:
    """Live test without persisting (caller may persist)."""
    if integration.type == reg.TYPE_GRAFANA:
        return gf.poll(
            integration.base_url,
            reg.decrypt_api_key(integration),
            tls_verify=reg.tls_verify(integration),
        )
    return kuma.fetch_metrics(
        integration.base_url,
        reg.decrypt_api_key(integration),
        tls_verify=reg.tls_verify(integration),
    )
