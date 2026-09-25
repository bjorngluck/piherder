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
    script = [p for p in csp.split("; ") if p.startswith("script-src ")][0]
    assert "'unsafe-inline'" not in script
    assert "script-src-attr 'unsafe-inline'" in csp
    style = [p for p in csp.split("; ") if p.startswith("style-src ")][0]
    assert "'unsafe-inline'" in style
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
    docs = hdr.build_csp(for_openapi_ui=True, nonce="abc")
    assert "https://cdn.jsdelivr.net" in docs
    assert "https://fonts.googleapis.com" in docs
    docs_script = [p for p in docs.split("; ") if p.startswith("script-src ")][0]
    assert "'unsafe-inline'" in docs_script
    assert "nonce-" not in docs_script
    app = hdr.build_csp(for_openapi_ui=False, nonce="abc")
    assert "jsdelivr" not in app
    assert "'nonce-abc'" in app
    assert "script-src-attr 'unsafe-inline'" in app


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


def test_stamp_inline_scripts_skips_src_json_and_handlers():
    html = (
        "<button onclick=\"go()\">x</button>"
        "<script src=\"/static/htmx.min.js\"></script>"
        "<script type=\"application/json\">{\"a\":1}</script>"
        "<script>\nwindow.PI = 1;\n</script>"
        "<script type=\"module\">export default 1;</script>"
    )
    out = hdr.stamp_inline_scripts(html, "n-1")
    assert 'onclick="go()"' in out
    assert '<script src="/static/htmx.min.js">' in out
    assert '<script type="application/json">' in out
    assert '<script nonce="n-1">\nwindow.PI = 1;\n</script>' in out
    assert '<script nonce="n-1" type="module">' in out
    assert out.count("nonce=") == 2


def test_demo_csp_is_report_only_until_enforce(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP", True)
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_REPORT_ONLY", False)
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_ENFORCE", False)
    monkeypatch.setattr(hdr, "demo_mode", lambda: True, raising=False)
    # csp_report_only imports demo_mode inside the function
    import app.services.demo as demo

    monkeypatch.setattr(demo, "demo_mode", lambda: True)
    h = hdr.security_headers_dict(nonce="n")
    assert "Content-Security-Policy-Report-Only" in h
    assert "Content-Security-Policy" not in h
    assert "'nonce-n'" in h["Content-Security-Policy-Report-Only"]
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_ENFORCE", True)
    enforced = hdr.security_headers_dict(nonce="n")
    assert "Content-Security-Policy" in enforced
    assert "Content-Security-Policy-Report-Only" not in enforced


def test_login_page_stamps_nonce_and_enforces(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP", True)
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_REPORT_ONLY", False)
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP_ENFORCE", True)
    import app.services.demo as demo

    monkeypatch.setattr(demo, "demo_mode", lambda: False)
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    from app.database import get_session
    from app.main import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        from sqlmodel import Session

        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        res = client.get("/auth/login")
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert res.status_code == 200
    csp = res.headers.get("content-security-policy") or ""
    assert "script-src-attr 'unsafe-inline'" in csp
    assert "'nonce-" in csp
    script = [p for p in csp.split("; ") if p.startswith("script-src ")][0]
    assert "'unsafe-inline'" not in script
    assert 'nonce="' in res.text
    assert "onclick" in res.text


def test_csp_can_disable(monkeypatch):
    monkeypatch.setattr(hdr.settings, "PIHERDER_CSP", False)
    h = hdr.security_headers_dict()
    assert "Content-Security-Policy" not in h
    assert "Content-Security-Policy-Report-Only" not in h
    # Other headers remain
    assert h["X-Frame-Options"] == "SAMEORIGIN"
