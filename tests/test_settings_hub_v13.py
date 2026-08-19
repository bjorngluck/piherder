"""Settings hub one-line summaries (v1.3 IA)."""
from __future__ import annotations

from app.services import settings_hub as hub


def test_security_line_includes_password_and_2fa():
    line = hub.security_line(
        {
            "password_min_length": 12,
            "password_max_length": 72,
            "password_require_upper": True,
            "password_require_lower": True,
            "password_require_digit": True,
            "password_require_special": False,
            "force_2fa_scope": "operators",
            "force_2fa_grace_days": 7,
        }
    )
    assert "min=12" in line
    assert "2FA: operators+" in line
    assert "grace 7d" in line


def test_console_line_off_and_on():
    assert "off" in hub.console_line({"enabled": False}).lower()
    on = hub.console_line(
        {
            "enabled": True,
            "console_idle_sec": 900,
            "console_max_sec": 3600,
            "console_audit_mode": "commands",
            "console_privileged_role": "operator",
        }
    )
    assert "idle 15m" in on
    assert "commands" in on
    assert "operator+" in on


def test_sso_and_cleanup_and_alert_lines():
    assert hub.sso_line({"oidc_enabled": False}) == "SSO off"
    assert "idp.example" in hub.sso_line(
        {
            "oidc_enabled": True,
            "oidc_display_name": "Authentik",
            "oidc_issuer": "https://idp.example/application/o/piherder/",
        }
    )
    assert hub.cleanup_line({"enabled": False}) == "Schedule off"
    assert "jobs 30d" in hub.cleanup_line(
        {"enabled": True, "jobs_enabled": True, "jobs_days": 30, "audit_enabled": True, "audit_days": 30}
    )
    ctx = hub.hub_context(
        cfg={"oidc_enabled": False},
        console_pol={"enabled": False},
        data_cleanup={"enabled": False},
        alert_policy_ui={
            "categories": [
                {"id": "host", "label": "Hosts", "enabled": True},
                {"id": "discovery", "label": "LAN discovery", "enabled": False},
            ]
        },
    )
    assert "muted: LAN discovery" in ctx["alert_policy"]
    assert ctx["sso"] == "SSO off"
