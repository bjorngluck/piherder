"""Stored host, label, and notes text stays escaped in template JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from jinja2 import ChainableUndefined
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import AuditLog, Integration, Job, NmapDevice, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.templates import htmlsafe_json_dumps, templates

STORED = "</script><script>alert(1)</script>"
ESCAPED = "\\u003c/script\\u003e\\u003cscript\\u003ealert(1)\\u003c/script\\u003e"
HTML_ESCAPED = "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
_EXPR = re.compile(r"\{\{[^{}]*?(?:tojson|htmlsafe_json)[^{}]*?\}\}")


def test_htmlsafe_json_dumps_escapes_markup_chars():
    text = htmlsafe_json_dumps({"host": STORED, "label": "a&b", "note": "it's"})
    assert STORED not in text
    assert ESCAPED in text
    assert "\\u0026" in text
    assert "\\u0027" in text
    assert "<" not in text
    assert ">" not in text
    assert "&" not in text
    assert "'" not in text


def _bag(**extra):
    base = dict(
        id=1,
        name=STORED,
        hostname="host-a.example.com",
        ssh_username="user",
        label=STORED,
        notes=STORED,
        source=STORED,
        source_filter=STORED,
        active_job_id=1,
        display_name=STORED,
        compose_service=STORED,
        project=STORED,
        slug="web",
        description=STORED,
        category="app",
        version="1",
        compose_content=STORED,
        env_content=STORED,
        use_docker_secrets=False,
        variables_json=json.dumps([{"label": STORED, "notes": STORED}]),
        checklist_json=json.dumps([{"label": STORED}]),
        extra_files_json=json.dumps([{"path": "app.env", "content": STORED}]),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _context():
    dumped = json.dumps(
        [{"name": STORED, "label": STORED, "notes": STORED, "host": "host-a.example.com"}]
    )
    server = _bag()
    form = _bag()
    row = {
        "server_name": STORED,
        "label": STORED,
        "notes": STORED,
        "details": STORED,
        "name": STORED,
        "id": 1,
    }
    return {
        "jobs": [row],
        "logs": [row],
        "hosts_json": dumped,
        "docker_options_json": dumped,
        "host_projects_json": dumped,
        "hero_by_tab": {"backup": STORED},
        "form": form,
        "server": server,
        "rj": _bag(),
        "qd": _bag(),
        "qj": _bag(id=2),
        "running_backup_job": _bag(id=3),
        "full_backup_job": _bag(id=4),
        "p": _bag(fleet_link_targets=[{"label": STORED, "notes": STORED}]),
        "proj": _bag(services=[{"name": STORED}], build_services=[{"name": STORED}]),
        "h": _bag(projects=[{"name": STORED, "label": STORED, "notes": STORED}]),
        "c": _bag(),
        "container": STORED,
        "sid": 1,
        "project": STORED,
        "services": [STORED],
        "no_cache": False,
        "listing_boot": {"path": STORED, "notes": STORED, "label": STORED},
        "_pp": {"min_length": 8},
        "close_href": "/integrations/1",
        "layout_help": {"body": STORED},
        "layout_help_all": {"body": STORED},
        "write_mode_help": {"body": STORED},
        "map_presets": [{"id": "web", "label": STORED}],
        "servers_meta": [{"id": 1, "name": STORED, "ssh_username": "user"}],
        "targets": [{"id": 1, "label": STORED, "notes": STORED}],
        "initial_host_ids": [1],
        "initial_host_id": 1,
    }


def test_every_json_embedding_escapes_stored_text():
    env = templates.env
    previous = env.undefined
    env.undefined = ChainableUndefined
    ctx = _context()
    try:
        rendered = []
        root = Path("app/templates")
        for path in sorted(root.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            exprs = _EXPR.findall(text)
            if not exprs:
                continue
            for expr in exprs:
                html = env.from_string(expr).render(**ctx)
                assert STORED not in html, f"{path}: {expr}"
                assert "<" not in html, f"{path}: {expr}"
                rendered.append(path.as_posix())
        assert rendered
        assert any(name.endswith("jobs.html") for name in rendered)
        assert any(name.endswith("console_workspace.html") for name in rendered)
        assert any(name.endswith("certificates_detail.html") for name in rendered)
    finally:
        env.undefined = previous


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_pages_escape_stored_host_label_and_notes(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = User(
                email="user@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            host = Server(
                name=STORED,
                hostname="host-a.example.com",
                ssh_username="user",
                ip_address="192.0.2.10",
            )
            session.add(user)
            session.add(host)
            session.commit()
            session.refresh(user)
            session.refresh(host)
            session.add(
                Job(
                    server_id=host.id,
                    job_type="backup",
                    status="success",
                    details=json.dumps({"label": STORED, "notes": STORED}),
                )
            )
            session.add(
                AuditLog(
                    user_id=user.id,
                    server_id=host.id,
                    action="backup",
                    status="success",
                    details=json.dumps({"label": STORED, "notes": STORED}),
                )
            )
            integration = Integration(
                type="nmap",
                name="LAN",
                base_url="https://example.com",
                config_json=json.dumps({"notes": STORED, "cidrs": ["192.0.2.0/24"]}),
            )
            session.add(integration)
            session.commit()
            session.refresh(integration)
            session.add(
                NmapDevice(
                    integration_id=integration.id,
                    identity_key="ip:192.0.2.20",
                    ip_address="192.0.2.20",
                    hostname="host-b.example.com",
                    display_name=STORED,
                    notes=STORED,
                )
            )
            session.commit()
            sid = host.id
            iid = integration.id
            client.cookies.set("access_token", create_user_access_token(user))

        paths = [
            "/servers",
            f"/servers/{sid}",
            f"/servers/{sid}/backups",
            f"/servers/{sid}/docker",
            "/jobs",
            "/audit",
            "/console",
            f"/servers/{sid}/console?embed=1",
            f"/integrations/{iid}?tab=devices",
            f"/integrations/{iid}/edit",
        ]
        seen = []
        for path in paths:
            res = client.get(path, follow_redirects=True)
            assert res.status_code == 200, path
            body = res.text
            assert STORED not in body, path
            assert ESCAPED in body or HTML_ESCAPED in body, path
            seen.append(path)
        assert len(seen) == len(paths)
    finally:
        app.dependency_overrides.pop(get_session, None)
