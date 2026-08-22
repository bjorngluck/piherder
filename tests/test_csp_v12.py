"""v1.2 Content-Security-Policy helpers."""
from __future__ import annotations

from app.security import headers as hdr


def test_build_csp_core_directives(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_PUBLIC_URL", "https://ph.example.com:8443")
    csp = hdr.build_csp()
    assert "default-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "frame-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" in csp  # legacy template scripts (1.3: nonces)
    assert "'unsafe-eval'" not in csp  # Tailwind is compiled CSS, not Play
    assert "connect-src" in csp
    assert "wss://ph.example.com:8443" in csp
    # Wildcard WebSocket schemes would let XSS open a socket to anywhere
    connect = [p for p in csp.split("; ") if p.startswith("connect-src ")][0]
    tokens = connect.split()[1:]
    assert "ws:" not in tokens
    assert "wss:" not in tokens
    assert "upgrade-insecure-requests" in csp
    # No third-party CDNs
    assert "jsdelivr" not in csp
    assert "cdn." not in csp


def test_openapi_ui_csp_allows_jsdelivr(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_PUBLIC_URL", "https://ph.example.com:8443")
    assert hdr.is_openapi_ui_path("/docs")
    assert hdr.is_openapi_ui_path("/redoc")
    assert not hdr.is_openapi_ui_path("/openapi.json")
    assert not hdr.is_openapi_ui_path("/auth/login")
    docs = hdr.build_csp(for_openapi_ui=True)
    assert "https://cdn.jsdelivr.net" in docs
    assert "https://fonts.googleapis.com" in docs
    app = hdr.build_csp(for_openapi_ui=False)
    assert "jsdelivr" not in app


def test_build_csp_http_lab_no_upgrade(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_PUBLIC_URL", "http://localhost:8000")
    csp = hdr.build_csp()
    assert "upgrade-insecure-requests" not in csp


def test_security_headers_dict_enforcement(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP", True)
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_REPORT_ONLY", False)
    h = hdr.security_headers_dict()
    assert "Content-Security-Policy" in h
    assert "Content-Security-Policy-Report-Only" not in h
    assert h["X-Frame-Options"] == "SAMEORIGIN"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "Referrer-Policy" in h
    assert "Permissions-Policy" in h
    assert "publickey-credentials-get=(self)" in h["Permissions-Policy"]


def test_security_headers_report_only(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP", True)
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_REPORT_ONLY", True)
    h = hdr.security_headers_dict()
    assert "Content-Security-Policy-Report-Only" in h
    assert "Content-Security-Policy" not in h


def test_compiled_tailwind_css_present():
    from pathlib import Path

    css = Path("app/static/css/tailwind.css")
    assert css.is_file(), "run bash scripts/build-tailwind.sh and commit the CSS"
    text = css.read_text(encoding="utf-8", errors="replace")
    assert len(text) > 5000
    assert ".flex{" in text or ".flex {" in text
    assert ".hidden{" in text or ".hidden {" in text
    compact = text.replace(" ", "")
    # Intentional box-sizing layer (Play layout model) — not full Preflight
    assert "*,:after,:before{box-sizing:border-box}" in compact
    # Full Tailwind Preflight would reset body/buttons and fight themes.css
    assert "button,input" not in compact
    assert "img,video{max-width:100%" not in compact


def test_csp_can_disable(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP", False)
    h = hdr.security_headers_dict()
    assert "Content-Security-Policy" not in h
    assert "Content-Security-Policy-Report-Only" not in h
    # Other headers remain
    assert h["X-Frame-Options"] == "SAMEORIGIN"
