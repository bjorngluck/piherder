"""Outbound alert channels — webhook (Wh-lite) + SMTP (H-lite).

Settings live in AppSetting (DR with herder backup). SMTP password is Fernet-
encrypted in the JSON blob. Env WEBHOOK_* remains a fallback when UI URL empty.
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from ..config import settings as env_settings
from ..security.encryption import decrypt_str, encrypt_str
from . import app_settings as app_cfg

logger = logging.getLogger(__name__)

SEVERITIES = ("info", "warning", "critical")
_SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


def _rank(sev: str) -> int:
    return _SEV_RANK.get((sev or "warning").lower(), 1)


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
) -> dict[str, Any]:
    """POST JSON to configured webhook. event: notification|job|backup|test."""
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

    payload: dict[str, Any] = {
        "message": message,
        "event": event,
        "severity": severity,
        "number": wc["number"] or "",
        "recipients": _parse_recipients(wc["recipients_raw"]),
    }
    if extra:
        payload.update(extra)
    headers = {"Content-Type": "application/json"}
    secret = wc.get("secret") or ""
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        headers["X-PiHerder-Webhook-Secret"] = secret
    try:
        r = httpx.post(wc["url"], json=payload, headers=headers, timeout=10)
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}", "status_code": r.status_code}
        return {"ok": True, "status_code": r.status_code}
    except Exception as e:
        logger.debug("webhook failed: %s", e)
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
) -> None:
    sc = smtp_config()
    if not sc["alert_enabled"] or not smtp_ready():
        return
    if _rank(severity) < _rank(sc["alert_min_severity"]):
        return
    to = sc["alert_to"] or sc["from_email"]
    if not to:
        return
    lines = [f"[{severity.upper()}] {title}"]
    if body:
        lines.append(body)
    if link_url:
        lines.append(f"\nOpen: {link_url}")
    send_email(
        to=[x.strip() for x in to.split(",") if x.strip()],
        subject=f"PiHerder: {title}"[:200],
        body_text="\n".join(lines),
    )


def validate_webhook_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("Webhook URL must be http(s)://host/…")
    return u
