"""In-app notification center — actionable alerts separate from AuditLog."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Any, Sequence

from sqlmodel import Session, select

from ..models import Notification, User
from ..config import settings
from . import alert_policy as apol

logger = logging.getLogger(__name__)

_LAST_NOTIFIED_KEY = "_last_notified_at"


def _parse_payload(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None


def upsert_notification(
    session: Session,
    *,
    fingerprint: str,
    type: str,
    title: str,
    body: Optional[str] = None,
    link_url: Optional[str] = None,
    severity: str = "warning",
    server_id: Optional[int] = None,
    user_id: Optional[int] = None,
    payload: Optional[dict] = None,
    notify_channels: bool = True,
) -> Optional[Notification]:
    """Create or refresh an open notification for this fingerprint.

    Policy (mute / severity / debounce / re-alert) is applied here so emitters
    stay dumb. ``notify_channels=False`` still writes the inbox row (nmap
    per-device) without webhook/email/push.

    If a dismissed/resolved row exists for the same fingerprint and the condition
    is still true, re-open a new open row only when no open row exists and the
    debounce window has elapsed.
    """
    try:
        policy = apol.effective(type)
    except Exception as e:
        logger.debug("alert policy lookup failed: %s", e)
        policy = apol.EffectivePolicy(
            type_id=type,
            category="other",
            label=type,
            enabled=True,
            severity=None,
            debounce_minutes=0,
            realert_hours=0,
        )
    if not policy.enabled:
        resolve_by_fingerprint(session, fingerprint)
        return None

    severity = apol.resolve_severity(policy, severity)
    now = datetime.utcnow()

    existing = session.exec(
        select(Notification).where(
            Notification.fingerprint == fingerprint,
            Notification.status == "open",
        )
    ).first()

    if existing:
        merged = _parse_payload(existing.payload)
        if payload is not None:
            merged.update(payload)
        existing.title = title
        existing.body = body
        existing.link_url = link_url
        existing.severity = severity
        existing.type = type
        existing.server_id = server_id if server_id is not None else existing.server_id
        existing.user_id = user_id if user_id is not None else existing.user_id
        existing.updated_at = now
        did_notify = False
        if notify_channels and policy.realert_hours > 0:
            stamp = _parse_iso(merged.get(_LAST_NOTIFIED_KEY)) or existing.created_at
            if stamp is None or (now - stamp) >= timedelta(hours=policy.realert_hours):
                _fire_channels(
                    session,
                    existing,
                    severity=severity,
                    title=title,
                    body=body,
                    link_url=link_url,
                    type=type,
                )
                merged[_LAST_NOTIFIED_KEY] = now.isoformat() + "Z"
                did_notify = True
        if payload is not None or did_notify:
            existing.payload = json.dumps(merged) if merged else existing.payload
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    if policy.debounce_minutes > 0:
        closed = session.exec(
            select(Notification)
            .where(
                Notification.fingerprint == fingerprint,
                Notification.status.in_(["dismissed", "resolved"]),  # type: ignore[attr-defined]
            )
            .order_by(Notification.updated_at.desc())
        ).first()
        if closed is not None:
            closed_at = closed.dismissed_at or closed.resolved_at or closed.updated_at
            if closed_at and (now - closed_at) < timedelta(minutes=policy.debounce_minutes):
                return None

    merged = dict(payload) if payload else {}
    if notify_channels:
        merged[_LAST_NOTIFIED_KEY] = now.isoformat() + "Z"
    payload_s = json.dumps(merged) if merged else None

    n = Notification(
        fingerprint=fingerprint,
        type=type,
        title=title,
        body=body,
        link_url=link_url,
        severity=severity,
        server_id=server_id,
        user_id=user_id,
        payload=payload_s,
        status="open",
        created_at=now,
        updated_at=now,
    )
    session.add(n)
    session.commit()
    session.refresh(n)

    if notify_channels:
        _fire_channels(
            session,
            n,
            severity=severity,
            title=title,
            body=body,
            link_url=link_url,
            type=type,
        )

    return n


def _fire_channels(
    session: Session,
    notification: Notification,
    *,
    severity: str,
    title: str,
    body: Optional[str],
    link_url: Optional[str],
    type: str,
) -> None:
    category = apol.category_of(type)
    if severity in ("warning", "critical", "info"):
        msg = f"[{severity}] {title}" + (f": {body}" if body else "")
        _maybe_webhook(
            msg,
            severity=severity,
            link_url=link_url,
            notif_type=type,
            category=category,
        )
        _maybe_email(
            severity=severity,
            title=title,
            body=body,
            link_url=link_url,
            notif_type=type,
            category=category,
        )
    _maybe_push(session, notification)


def resolve_by_fingerprint(session: Session, fingerprint: str) -> int:
    """Mark all open notifications with this fingerprint as resolved.

    Sends Web Push on auto-resolve (B09) for each closed row, using the same
    type preferences as the original alert (title: "Resolved: …").
    """
    rows = list(
        session.exec(
            select(Notification).where(
                Notification.fingerprint == fingerprint,
                Notification.status == "open",
            )
        ).all()
    )
    now = datetime.utcnow()
    for n in rows:
        n.status = "resolved"
        n.resolved_at = now
        n.updated_at = now
        session.add(n)
    if rows:
        session.commit()
        for n in rows:
            _maybe_push_resolved(session, n)
    return len(rows)


def dismiss(session: Session, notification_id: int, user: User | None = None) -> bool:
    """Mark an open notification dismissed. Idempotent if already closed."""
    n = session.get(Notification, notification_id)
    if not n:
        return False
    if n.status != "open":
        # Already resolved/dismissed — treat as success so UI forms don't 404
        return True
    n.status = "dismissed"
    n.dismissed_at = datetime.utcnow()
    n.updated_at = n.dismissed_at
    session.add(n)
    session.commit()
    return True


def dismiss_all(session: Session, user: User | None = None) -> int:
    rows = list(
        session.exec(select(Notification).where(Notification.status == "open")).all()
    )
    now = datetime.utcnow()
    for n in rows:
        n.status = "dismissed"
        n.dismissed_at = now
        n.updated_at = now
        session.add(n)
    if rows:
        session.commit()
    return len(rows)


def list_notifications(
    session: Session,
    *,
    status: Optional[str] = "open",
    type: Optional[str] = None,
    server_id: Optional[int] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Notification]:
    q = select(Notification).order_by(Notification.updated_at.desc())
    if status:
        q = q.where(Notification.status == status)
    if type:
        q = q.where(Notification.type == type)
    if server_id is not None:
        q = q.where(Notification.server_id == server_id)
    if severity and severity in apol.SEVERITIES:
        q = q.where(Notification.severity == severity)
    cat = (category or "").strip()
    if cat:
        if cat == "other":
            known = list(apol.catalog_type_ids())
            if known:
                q = q.where(Notification.type.notin_(known))  # type: ignore[attr-defined]
        else:
            ids = apol.types_in_category(cat)
            if ids:
                q = q.where(Notification.type.in_(ids))  # type: ignore[attr-defined]
            else:
                return []
    if offset:
        q = q.offset(max(0, int(offset)))
    return list(session.exec(q.limit(limit)).all())


def dismiss_matching(
    session: Session,
    user: User | None = None,
    *,
    type: Optional[str] = None,
    server_id: Optional[int] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
) -> int:
    """Dismiss open rows matching Alerts filters (not the whole inbox)."""
    del user
    rows = list_notifications(
        session,
        status="open",
        type=type,
        server_id=server_id,
        severity=severity,
        category=category,
        limit=5000,
    )
    now = datetime.utcnow()
    n = 0
    for row in rows:
        if row.status != "open":
            continue
        row.status = "dismissed"
        row.dismissed_at = now
        row.updated_at = now
        session.add(row)
        n += 1
    if n:
        session.commit()
    return n


def open_count(session: Session) -> int:
    rows = session.exec(
        select(Notification).where(Notification.status == "open")
    ).all()
    return len(list(rows))


def mark_read(session: Session, notification_id: int) -> bool:
    n = session.get(Notification, notification_id)
    if not n:
        return False
    if not n.read_at:
        n.read_at = datetime.utcnow()
        session.add(n)
        session.commit()
    return True


def _maybe_webhook(
    message: str,
    *,
    severity: str = "warning",
    link_url: Optional[str] = None,
    notif_type: Optional[str] = None,
    category: Optional[str] = None,
) -> None:
    try:
        from . import alert_channels as ch

        extra: dict[str, Any] = {"link_url": link_url or ""}
        if notif_type:
            extra["type"] = notif_type
        if category:
            extra["category"] = category
        ch.send_webhook(
            message,
            event="notification",
            severity=severity,
            extra=extra,
            notif_type=notif_type,
            category=category,
        )
    except Exception as e:
        logger.debug("Notification webhook failed: %s", e)


def _maybe_email(
    *,
    severity: str,
    title: str,
    body: Optional[str] = None,
    link_url: Optional[str] = None,
    notif_type: Optional[str] = None,
    category: Optional[str] = None,
) -> None:
    try:
        from . import alert_channels as ch

        ch.maybe_email_notification(
            severity=severity,
            title=title,
            body=body,
            link_url=link_url,
            notif_type=notif_type,
            category=category,
        )
    except Exception as e:
        logger.debug("Notification email failed: %s", e)


def _maybe_push(session: Session, notification: Notification) -> None:
    """Best-effort Web Push; never break the in-app notification path."""
    try:
        from .push import send_for_notification

        send_for_notification(session, notification)
    except Exception as e:
        logger.debug("Web push dispatch failed: %s", e)


def _maybe_push_resolved(session: Session, notification: Notification) -> None:
    """Best-effort Web Push when an alert auto-resolves (B09)."""
    try:
        from .push import send_for_resolved_notification

        send_for_resolved_notification(session, notification)
    except Exception as e:
        logger.debug("Web push resolve dispatch failed: %s", e)


# --- Domain helpers used by check jobs ---

def notify_os_updates(
    session: Session,
    server_id: int,
    server_name: str,
    updates_count: int,
    reboot_pending: bool,
    phased_count: int = 0,
) -> None:
    """Alert only on *actionable* upgrades (updates_count).

    Ubuntu phased packages (listed but not installable yet) are visibility-only —
    they must not keep a warning open after a successful patch with 0 upgrades.
    """
    fp_os = f"os_updates:server:{server_id}"
    fp_reboot = f"reboot_pending:server:{server_id}"
    link = f"/servers/{server_id}"

    if updates_count and updates_count > 0:
        body = f"{updates_count} package(s) ready to install"
        if phased_count and phased_count > 0:
            body += f" · {phased_count} deferred (phased)"
        upsert_notification(
            session,
            fingerprint=fp_os,
            type="os_updates",
            title=f"OS updates on {server_name}",
            body=body,
            link_url=link,
            severity="warning",
            server_id=server_id,
            payload={
                "updates_count": updates_count,
                "phased_count": phased_count or 0,
            },
        )
    else:
        # Phased-only or clean — clear actionable alert
        resolve_by_fingerprint(session, fp_os)

    if reboot_pending:
        upsert_notification(
            session,
            fingerprint=fp_reboot,
            type="reboot_pending",
            title=f"Reboot pending on {server_name}",
            body="Kernel or system packages require a reboot",
            link_url=link,
            severity="warning",
            server_id=server_id,
        )
    else:
        resolve_by_fingerprint(session, fp_reboot)


def notify_container_updates(
    session: Session,
    server_id: int,
    server_name: str,
    projects: list[str],
) -> None:
    fp = f"container_updates:server:{server_id}"
    link = f"/servers/{server_id}/docker"
    if projects:
        names = ", ".join(projects[:8])
        extra = f" (+{len(projects) - 8} more)" if len(projects) > 8 else ""
        upsert_notification(
            session,
            fingerprint=fp,
            type="container_updates",
            title=f"Container image updates on {server_name}",
            body=f"{len(projects)} project(s): {names}{extra}",
            link_url=link,
            severity="warning",
            server_id=server_id,
            payload={"projects": projects},
        )
    else:
        resolve_by_fingerprint(session, fp)


def notify_backup_failed(
    session: Session,
    server_id: int,
    server_name: str,
    message: str,
) -> None:
    upsert_notification(
        session,
        fingerprint=f"backup_failed:server:{server_id}",
        type="backup_failed",
        title=f"Backup failed: {server_name}",
        body=(message or "Backup job failed")[:400],
        link_url=f"/servers/{server_id}/backups",
        severity="critical",
        server_id=server_id,
    )


def resolve_backup_failed(session: Session, server_id: int) -> None:
    """Close open backup-failed alerts for this server (after a successful run)."""
    resolve_by_fingerprint(session, f"backup_failed:server:{int(server_id)}")


# --- Certificate deploy / verify (service-level targets) ---


def cert_deploy_failed_fingerprint(target_id: int) -> str:
    return f"cert_deploy_failed:target:{int(target_id)}"


def cert_verify_failed_fingerprint(target_id: int) -> str:
    return f"cert_verify_failed:target:{int(target_id)}"


def notify_cert_deploy_failed(
    session: Session,
    *,
    target_id: int,
    cert_id: int,
    cert_name: str,
    server_id: int | None,
    server_name: str,
    service_label: str,
    message: str,
) -> None:
    """Open/refresh alert when SSH deploy to a service target fails."""
    label = (service_label or "").strip() or f"target #{target_id}"
    host = (server_name or "").strip() or (f"server #{server_id}" if server_id else "host")
    upsert_notification(
        session,
        fingerprint=cert_deploy_failed_fingerprint(target_id),
        type="cert_deploy_failed",
        title=f"Cert deploy failed: {label}",
        body=(
            f"{cert_name} → {host}: {(message or 'deploy failed')[:320]}"
        )[:400],
        link_url=f"/certificates/{int(cert_id)}",
        severity="critical",
        server_id=server_id,
        payload={
            "target_id": int(target_id),
            "cert_id": int(cert_id),
            "kind": "deploy",
        },
    )


def resolve_cert_deploy_failed(session: Session, target_id: int) -> None:
    """Close deploy-failed alert after a successful follow-up deploy."""
    resolve_by_fingerprint(session, cert_deploy_failed_fingerprint(target_id))


def notify_cert_verify_failed(
    session: Session,
    *,
    target_id: int,
    cert_id: int,
    cert_name: str,
    server_id: int | None,
    server_name: str,
    service_label: str,
    message: str,
    status: str = "failed",
) -> None:
    """Open/refresh alert when host fingerprint or TLS URL probe fails.

    *status* ``partial`` → warning (files OK, live TLS mismatch);
    other failures → critical.
    """
    label = (service_label or "").strip() or f"target #{target_id}"
    host = (server_name or "").strip() or (f"server #{server_id}" if server_id else "host")
    sev = "warning" if (status or "") == "partial" else "critical"
    title = (
        f"Cert verify partial: {label}"
        if sev == "warning"
        else f"Cert verify failed: {label}"
    )
    upsert_notification(
        session,
        fingerprint=cert_verify_failed_fingerprint(target_id),
        type="cert_verify_failed",
        title=title,
        body=(
            f"{cert_name} → {host}: {(message or 'fingerprint/TLS check failed')[:320]}"
        )[:400],
        link_url=f"/certificates/{int(cert_id)}",
        severity=sev,
        server_id=server_id,
        payload={
            "target_id": int(target_id),
            "cert_id": int(cert_id),
            "kind": "verify",
            "status": status,
        },
    )


def resolve_cert_verify_failed(session: Session, target_id: int) -> None:
    """Close verify-failed alert after fingerprint/TLS check succeeds."""
    resolve_by_fingerprint(session, cert_verify_failed_fingerprint(target_id))


def resolve_cert_target_alerts(session: Session, target_id: int) -> None:
    """Resolve deploy + verify alerts for a target (e.g. target deleted)."""
    resolve_cert_deploy_failed(session, target_id)
    resolve_cert_verify_failed(session, target_id)


# --- Map / discovery (Stream A) ---


def nmap_new_fingerprint(device_id: int) -> str:
    return f"nmap_new:{int(device_id)}"


def nmap_digest_fingerprint(integration_id: int) -> str:
    return f"nmap_new_digest:{int(integration_id)}"


def nmap_offline_fingerprint(device_id: int) -> str:
    return f"nmap_offline:{int(device_id)}"


def nmap_device_map_url(device_id: int) -> str:
    return f"/dns/physical?focus=n:host-d-{int(device_id)}#map"


def nmap_new_list_url(integration_id: int) -> str:
    return f"/integrations/{int(integration_id)}?tab=devices&state=new"


def notify_nmap_new_device(
    session: Session,
    *,
    device_id: int,
    integration_id: int,
    label: str,
    ip: str = "",
) -> None:
    """In-app only — channels go through the per-scan digest."""
    name = (label or ip or f"device #{device_id}").strip()
    body = ip.strip() if ip and ip.strip() != name else "New device on LAN"
    upsert_notification(
        session,
        fingerprint=nmap_new_fingerprint(device_id),
        type="nmap_new_device",
        title=f"New device: {name}",
        body=body[:400],
        link_url=nmap_device_map_url(device_id),
        severity="warning",
        payload={"device_id": int(device_id), "integration_id": int(integration_id)},
        notify_channels=False,
    )


def notify_nmap_new_digest(
    session: Session,
    *,
    integration_id: int,
    count: int,
    sample_names: Sequence[str] | None = None,
) -> None:
    if count <= 0:
        return
    samples = [s for s in (sample_names or []) if s][:5]
    extra = f" ({', '.join(samples)})" if samples else ""
    more = f" +{count - len(samples)} more" if count > len(samples) and samples else ""
    upsert_notification(
        session,
        fingerprint=nmap_digest_fingerprint(integration_id),
        type="nmap_new_device",
        title=f"{count} new device(s) on LAN",
        body=(f"{count} new discovery record(s){extra}{more}")[:400],
        link_url=nmap_new_list_url(integration_id),
        severity="warning",
        payload={"integration_id": int(integration_id), "count": int(count)},
        notify_channels=True,
    )


def resolve_nmap_new_device(session: Session, device_id: int) -> None:
    resolve_by_fingerprint(session, nmap_new_fingerprint(device_id))


def resolve_nmap_new_digest_if_clear(session: Session, integration_id: int) -> None:
    """Drop the digest when this integration has no remaining ``new`` devices."""
    try:
        from ..models import NmapDevice

        remaining = session.exec(
            select(NmapDevice).where(
                NmapDevice.integration_id == int(integration_id),
                NmapDevice.state == "new",
            )
        ).first()
    except Exception:
        return
    if remaining is None:
        resolve_by_fingerprint(session, nmap_digest_fingerprint(integration_id))


def notify_nmap_device_offline(
    session: Session,
    *,
    device_id: int,
    integration_id: int,
    label: str,
    ip: str = "",
) -> None:
    name = (label or ip or f"device #{device_id}").strip()
    upsert_notification(
        session,
        fingerprint=nmap_offline_fingerprint(device_id),
        type="nmap_device_offline",
        title=f"Device offline: {name}",
        body=(f"{ip} not seen in recent LAN scans" if ip else "Not seen in recent LAN scans")[:400],
        link_url=nmap_device_map_url(device_id),
        severity="info",
        payload={"device_id": int(device_id), "integration_id": int(integration_id)},
    )


def resolve_nmap_device_offline(session: Session, device_id: int) -> None:
    resolve_by_fingerprint(session, nmap_offline_fingerprint(device_id))


def map_infra_fingerprint(slot: str) -> str:
    return f"map_infra:{(slot or '').strip() or 'unknown'}"


def notify_map_infra_down(
    session: Session,
    *,
    slot: str,
    label: str,
    message: str = "",
) -> None:
    title_slot = "Gateway" if slot == "gateway" else ("WAN" if slot == "wan" else slot)
    upsert_notification(
        session,
        fingerprint=map_infra_fingerprint(slot),
        type="map_infra_down",
        title=f"{title_slot} down: {label}",
        body=(message or f"Uptime Kuma reports {title_slot} down")[:400],
        link_url="/dns/physical#map",
        severity="warning",
        payload={"slot": slot},
    )


def resolve_map_infra(session: Session, slot: str) -> None:
    resolve_by_fingerprint(session, map_infra_fingerprint(slot))
