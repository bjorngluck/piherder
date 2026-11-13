"""Outbound alert channels — webhook (Wh-lite) + SMTP (H-lite).

Settings live in AppSetting (DR with herder backup). SMTP password is Fernet-
encrypted in the JSON blob. Env WEBHOOK_* remains a fallback when UI URL empty.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from ..config import settings as env_settings
from ..security.encryption import decrypt_str, encrypt_str
from . import app_settings as app_cfg
from .request_ip import parse_trusted_proxy_cidrs

logger = logging.getLogger(__name__)

# Private, loopback, and link-local ranges. Documentation ranges stay allowed.
# ipaddress.is_private on this interpreter also marks those documentation ranges.
_WEBHOOK_BLOCKED_NETS = tuple(
    ipaddress.ip_network(item)
    for item in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

SEVERITIES = ("info", "warning", "critical")
_SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


def _rank(sev: str) -> int:
    return _SEV_RANK.get((sev or "warning").lower(), 1)


def _note_delivery_failure(channel: str) -> None:
    from .delivery_metrics import note_alert_delivery_failure

    note_alert_delivery_failure(channel)


# ── Webhook (Wh-lite) ────────────────────────────────────────────────────────


def webhook_config() -> dict[str, Any]:
    cfg = app_cfg.load_settings()
    ui_url = (cfg.get("webhook_url") or "").strip()
    env_url = (env_settings.WEBHOOK_URL or "").strip()
    enabled = bool(cfg.get("webhook_enabled"))
    # Effective URL: UI when enabled+set, else env fallback when env set
    if enabled and ui_url:
        url = ui_url
        source = "settings"
    elif env_url:
        url = env_url
        source = "env"
        # env path is active even if UI toggle off (compose operators)
    else:
        url = ""
        source = "none"
    number = (cfg.get("webhook_number") or "").strip()
    if not number and source == "env":
        number = (env_settings.WEBHOOK_NUMBER or "").strip()
    recipients_raw = cfg.get("webhook_recipients") or ""
    if not recipients_raw and source == "env":
        recipients_raw = env_settings.WEBHOOK_RECIPIENTS or "[]"
    return {
        "enabled": enabled or bool(env_url),  # effective (UI or env)
        "ui_enabled": enabled,  # Settings checkbox state
        "url": url,
        "ui_url": ui_url,
        "source": source,
        "number": number,
        "recipients_raw": recipients_raw if isinstance(recipients_raw, str) else json.dumps(recipients_raw),
        "secret": (cfg.get("webhook_secret") or "").strip(),
        "events_notifications": bool(cfg.get("webhook_events_notifications", True)),
        "events_jobs": bool(cfg.get("webhook_events_jobs", True)),
        "events_backup": bool(cfg.get("webhook_events_backup", True)),
        "min_severity": (cfg.get("webhook_min_severity") or "warning").lower(),
        "notify_categories": cfg.get("webhook_notify_categories"),
        "has_ui_url": bool(ui_url),
        "env_fallback": bool(env_url) and not (enabled and ui_url),
    }


def _parse_recipients(raw: str) -> list:
    t = (raw or "").strip()
    if not t:
        return []
    try:
        data = json.loads(t)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return [x.strip() for x in t.split(",") if x.strip()]


def send_webhook(
    message: str,
    *,
    event: str = "notification",
    severity: str = "warning",
    extra: Optional[dict[str, Any]] = None,
    notif_type: Optional[str] = None,
    category: Optional[str] = None,
) -> dict[str, Any]:
    """POST JSON to configured webhook. event: notification|job|backup|test."""
    from . import alert_policy as apol

    wc = webhook_config()
    if not wc["url"]:
        return {"ok": False, "error": "webhook not configured"}
    if event == "notification" and not wc["events_notifications"]:
        return {"ok": False, "skipped": True, "error": "notifications disabled"}
    if event == "job" and not wc["events_jobs"]:
        return {"ok": False, "skipped": True, "error": "jobs disabled"}
    if event == "backup" and not wc["events_backup"]:
        return {"ok": False, "skipped": True, "error": "backup disabled"}
    if event == "notification" and _rank(severity) < _rank(wc["min_severity"]):
        return {"ok": False, "skipped": True, "error": "below min severity"}
    if event == "notification":
        allow = apol.parse_allowlist(wc.get("notify_categories"))
        cat = category or ((extra or {}).get("category") if extra else None)
        if not apol.category_allowed(cat, allow):
            return {"ok": False, "skipped": True, "error": "category filtered"}

    payload: dict[str, Any] = {
        "message": message,
        "event": event,
        "severity": severity,
        "number": wc["number"] or "",
        "recipients": _parse_recipients(wc["recipients_raw"]),
    }
    if extra:
        payload.update(extra)
    if notif_type and "type" not in payload:
        payload["type"] = notif_type
    if category and "category" not in payload:
        payload["category"] = category
    headers = {"Content-Type": "application/json"}
    secret = wc.get("secret") or ""
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        headers["X-PiHerder-Webhook-Secret"] = secret
    try:
        validate_webhook_url(wc["url"])
    except ValueError:
        _note_delivery_failure("webhook")
        return {"ok": False, "error": "webhook target is not allowed"}
    try:
        r = httpx.post(
            wc["url"],
            json=payload,
            headers=headers,
            timeout=10,
            follow_redirects=False,
        )
        if r.status_code >= 400:
            _note_delivery_failure("webhook")
            return {"ok": False, "error": f"HTTP {r.status_code}", "status_code": r.status_code}
        return {"ok": True, "status_code": r.status_code}
    except Exception as e:
        logger.debug("webhook failed: %s", e)
        _note_delivery_failure("webhook")
        return {"ok": False, "error": str(e)[:200]}


# ── SMTP (H-lite) ────────────────────────────────────────────────────────────


def smtp_config() -> dict[str, Any]:
    cfg = app_cfg.load_settings()
    enc = (cfg.get("smtp_password_encrypted") or "").strip()
    return {
        "enabled": bool(cfg.get("smtp_enabled")),
        "host": (cfg.get("smtp_host") or "").strip(),
        "port": int(cfg.get("smtp_port") or 587),
        "security": (cfg.get("smtp_security") or "starttls").lower(),
        "username": (cfg.get("smtp_username") or "").strip(),
        "has_password": bool(enc),
        "from_email": (cfg.get("smtp_from_email") or "").strip(),
        "from_name": (cfg.get("smtp_from_name") or "PiHerder").strip() or "PiHerder",
        "alert_to": (cfg.get("smtp_alert_to") or "").strip(),
        "alert_enabled": bool(cfg.get("smtp_alert_enabled")),
        "alert_min_severity": (cfg.get("smtp_alert_min_severity") or "warning").lower(),
        "notify_categories": cfg.get("smtp_notify_categories"),
        "password_reset_enabled": bool(cfg.get("smtp_password_reset_enabled", True)),
    }


def smtp_ready() -> bool:
    sc = smtp_config()
    return bool(sc["enabled"] and sc["host"] and sc["from_email"])


def password_reset_available() -> bool:
    sc = smtp_config()
    return smtp_ready() and bool(sc.get("password_reset_enabled", True))


def set_smtp_password(plain: str) -> None:
    plain = plain or ""
    if not plain.strip():
        return
    app_cfg.save_settings({"smtp_password_encrypted": encrypt_str(plain)})


def clear_smtp_password() -> None:
    app_cfg.save_settings({"smtp_password_encrypted": ""})


def _smtp_password() -> str:
    enc = (app_cfg.load_settings().get("smtp_password_encrypted") or "").strip()
    if not enc:
        return ""
    try:
        return decrypt_str(enc)
    except Exception as e:
        logger.warning("SMTP password decrypt failed: %s", e)
        return ""


def send_email(
    *,
    to: str | list[str],
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> dict[str, Any]:
    sc = smtp_config()
    if not sc["enabled"]:
        return {"ok": False, "error": "SMTP disabled"}
    if not sc["host"] or not sc["from_email"]:
        return {"ok": False, "error": "SMTP host/from incomplete"}
    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients:
        return {"ok": False, "error": "no recipients"}

    msg = EmailMessage()
    msg["Subject"] = subject
    from_name = sc["from_name"]
    from_email = sc["from_email"]
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = ", ".join(recipients)
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    host = sc["host"]
    port = int(sc["port"] or 587)
    security = sc["security"]
    user = sc["username"]
    password = _smtp_password()

    try:
        if security == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                if security == "starttls":
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        return {"ok": True, "to": recipients}
    except Exception as e:
        logger.warning("SMTP send failed: %s", e)
        return {"ok": False, "error": str(e)[:300]}


def send_test_email(to: str) -> dict[str, Any]:
    brand = "PiHerder"
    return send_email(
        to=to,
        subject=f"{brand} test email",
        body_text=(
            f"This is a test message from {brand}.\n\n"
            "If you received this, SMTP is configured correctly.\n"
        ),
    )


def maybe_email_notification(
    *,
    severity: str,
    title: str,
    body: Optional[str] = None,
    link_url: Optional[str] = None,
    notif_type: Optional[str] = None,
    category: Optional[str] = None,
) -> None:
    from . import alert_policy as apol

    sc = smtp_config()
    if not sc["alert_enabled"] or not smtp_ready():
        return
    if _rank(severity) < _rank(sc["alert_min_severity"]):
        return
    allow = apol.parse_allowlist(sc.get("notify_categories"))
    if not apol.category_allowed(category, allow):
        return
    to = sc["alert_to"] or sc["from_email"]
    if not to:
        return
    lines = [f"[{severity.upper()}] {title}"]
    if body:
        lines.append(body)
    if link_url:
        lines.append(f"\nOpen: {link_url}")
    result = send_email(
        to=[x.strip() for x in to.split(",") if x.strip()],
        subject=f"PiHerder: {title}"[:200],
        body_text="\n".join(lines),
    )
    if not result.get("ok"):
        _note_delivery_failure("email")
    return result


def _parse_webhook_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address((raw or "").split("%", 1)[0].strip())


def _canonical_address(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def _address_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    addr = _canonical_address(addr)
    if addr.is_multicast or addr.is_unspecified:
        return True
    return any(addr.version == net.version and addr in net for net in _WEBHOOK_BLOCKED_NETS)


def _address_allowlisted(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    nets: list,
) -> bool:
    addr = _canonical_address(addr)
    for net in nets:
        if addr.version != net.version:
            continue
        if addr in net:
            return True
    return False


def _resolve_webhook_host(host: str) -> list[str]:
    """Addresses for a webhook host. Tests replace this. No URL is logged."""
    found: list[str] = []
    for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        sockaddr = item[4]
        if sockaddr:
            found.append(str(sockaddr[0]))
    return found


def _webhook_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [_parse_webhook_ip(host)]
    except ValueError:
        pass
    try:
        raws = _resolve_webhook_host(host)
    except Exception:
        return []
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw in raws:
        try:
            out.append(_parse_webhook_ip(raw))
        except ValueError:
            return []
    return out


def validate_webhook_url(url: str) -> str:
    """Accept an http(s) URL whose addresses are public, or on the allowlist.

    Private, loopback, and link-local targets are refused. An explicit
    ``PIHERDER_WEBHOOK_ALLOW_CIDRS`` list opts those addresses back in.
    The error text has no host and no address.
    """
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Webhook URL must be http(s)://host/…")
    addresses = _webhook_addresses(parsed.hostname)
    if not addresses:
        raise ValueError("Webhook target is not allowed.")
    allow = parse_trusted_proxy_cidrs(os.environ.get("PIHERDER_WEBHOOK_ALLOW_CIDRS"))
    for addr in addresses:
        if _address_blocked(addr) and not _address_allowlisted(addr, allow):
            raise ValueError("Webhook target is not allowed.")
    return u
