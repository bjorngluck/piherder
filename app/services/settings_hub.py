"""Compact one-line summaries for Settings hub cards (v1.3 Settings IA)."""
from __future__ import annotations

from typing import Any, Mapping

from . import account_stepup as step
from . import password_policy as pwpol
from . import alert_policy as apol


_SCOPE_LABEL = {
    "off": "2FA optional",
    "admins": "2FA: admins",
    "operators": "2FA: operators+",
    "all": "2FA: everyone",
}


def security_line(cfg: Mapping[str, Any] | None = None) -> str:
    src = dict(cfg or {})
    pw = pwpol.policy_summary(src)
    scope = step.force_2fa_scope(src)
    grace = int(src.get("force_2fa_grace_days") or 0)
    bits = [pw, _SCOPE_LABEL.get(scope, f"2FA: {scope}")]
    if scope != "off" and grace:
        bits.append(f"grace {grace}d")
    return " · ".join(bits)


def console_line(console_pol: Mapping[str, Any] | None = None) -> str:
    p = dict(console_pol or {})
    if not p.get("enabled"):
        return "Web console off (compose PIHERDER_SSH_CONSOLE)"
    idle = int(p.get("console_idle_sec") or 900)
    mx = int(p.get("console_max_sec") or 3600)
    audit = str(p.get("console_audit_mode") or "off").strip().lower() or "off"
    req = bool(p.get("console_audit_required"))
    audit_l = {"off": "audit off", "commands": "commands", "commands_output": "commands+output"}.get(
        audit, audit
    )
    if req and audit == "off":
        audit_l = "audit required"
    bits = [f"idle {idle // 60}m", f"max {mx // 60}m", audit_l]
    role = str(p.get("console_privileged_role") or "admin").strip().lower()
    if role == "operator":
        bits.append("privileged: operator+")
    return " · ".join(bits)


def sso_line(cfg: Mapping[str, Any] | None = None) -> str:
    src = dict(cfg or {})
    if not src.get("oidc_enabled"):
        return "SSO off"
    name = (src.get("oidc_display_name") or "SSO").strip() or "SSO"
    issuer = (src.get("oidc_issuer") or "").strip()
    if issuer:
        host = issuer.split("://", 1)[-1].rstrip("/").split("/", 1)[0]
        return f"On · {name} · {host}"
    return f"On · {name}"


def cleanup_line(data_cleanup: Mapping[str, Any] | None = None) -> str:
    dc = dict(data_cleanup or {})
    if not dc.get("enabled"):
        return "Schedule off"
    bits = []
    if dc.get("jobs_enabled", True):
        bits.append(f"jobs {int(dc.get('jobs_days') or 30)}d")
    if dc.get("audit_enabled", True):
        bits.append(f"audit {int(dc.get('audit_days') or 30)}d")
    if dc.get("nmap_enabled"):
        bits.append(f"nmap {int(dc.get('nmap_days') or 30)}d")
    return "On · " + (" · ".join(bits) if bits else "no categories")


def alert_policy_line(alert_policy_ui: Mapping[str, Any] | None = None) -> str:
    rows = list((alert_policy_ui or {}).get("categories") or [])
    if not rows:
        rows = [{"id": cid, "enabled": True} for cid, _ in apol.CATEGORIES]
    on = [r for r in rows if r.get("enabled", True)]
    muted = [r.get("label") or r.get("id") for r in rows if not r.get("enabled", True)]
    if not muted:
        return f"{len(on)} categories on · defaults"
    if len(muted) <= 2:
        return f"{len(on)} on · muted: {', '.join(str(m) for m in muted)}"
    return f"{len(on)} on · {len(muted)} muted"


def files_line(*, enabled: bool = False, max_h: str = "", env_locked: bool = False) -> str:
    if not enabled:
        return "Off (compose PIHERDER_HOST_FILES)"
    bits = [f"transfer {max_h or '512 MiB'}"]
    if env_locked:
        bits.append("env lock")
    return " · ".join(bits)


def hub_context(
    *,
    cfg: Mapping[str, Any] | None = None,
    console_pol: Mapping[str, Any] | None = None,
    data_cleanup: Mapping[str, Any] | None = None,
    alert_policy_ui: Mapping[str, Any] | None = None,
    files_enabled: bool = False,
    files_max_h: str = "",
    files_max_locked: bool = False,
) -> dict[str, str]:
    return {
        "security": security_line(cfg),
        "console": console_line(console_pol),
        "sso": sso_line(cfg),
        "cleanup": cleanup_line(data_cleanup),
        "alert_policy": alert_policy_line(alert_policy_ui),
        "files": files_line(enabled=files_enabled, max_h=files_max_h, env_locked=files_max_locked),
    }
