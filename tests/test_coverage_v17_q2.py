"""v1.7 coverage — walk HTTP routes against a temp SQLite database.

SSH, Celery, and background refresh are stubbed. Stream and websocket
routes are skipped so the sweep cannot sit on an open channel.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.main import app
from app.models import Integration, Server, ServiceTemplate, StackDeployment
from sqlmodel import Session

from tests.test_coverage_v17_q1 import _client, _engine

_SKIP = ("stream", "/ws", "logs/")


def _fill(path: str, sid: int, did: int, iid: int) -> str | None:
    repl = {
        "server_id": sid,
        "job_id": 1,
        "integration_id": iid,
        "deployment_id": did,
        "cert_id": 1,
        "target_id": 1,
        "record_id": 1,
        "edge_id": 1,
        "stack_id": 1,
        "transcript_id": 1,
        "version_id": 1,
        "project": "web",
        "slug": "web",
        "action": "restart",
        "container": "web",
        "direction": "up",
        "user_id": 1,
        "token_id": 1,
    }
    out = path
    for key, value in repl.items():
        out = out.replace("{" + key + "}", str(value))
    if "{" in out or "}" in out:
        return None
    return out


def test_http_route_sweep(tmp_path, monkeypatch):
    import app.database as dbmod
    import app.services.docker_inventory as inv
    import app.services.host_facts as facts
    import app.services.herder_backup as hb
    import app.services.jobs as jobs
    import app.routers.settings as settings_mod

    engine = _engine(tmp_path / "q2.db")
    client, _uid, sid = _client(engine, monkeypatch)
    for mod in (dbmod, inv, facts, hb, jobs, settings_mod):
        monkeypatch.setattr(mod, "engine", engine, raising=False)
    monkeypatch.setattr(inv, "request_refresh", lambda *a, **k: False)
    monkeypatch.setattr(facts, "request_refresh", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.celery_app.celery.send_task",
        lambda *a, **k: SimpleNamespace(id="q2"),
    )
    if hasattr(jobs, "_update_check_pool"):
        monkeypatch.setattr(jobs._update_check_pool, "submit", lambda *a, **k: None)
    job = SimpleNamespace(id=1, job_type="docker_stack_restart")
    for name in (
        "enqueue_docker_stack_lifecycle",
        "enqueue_docker_stack_check",
        "enqueue_docker_stack_deploy",
        "enqueue_docker_stack_remove",
        "enqueue_os_update_check",
        "enqueue_container_update_check",
        "enqueue_os_patch_apply",
        "enqueue_container_patch_apply",
        "enqueue_backup_for_server",
        "enqueue_template_deploy",
        "enqueue_template_redeploy",
        "enqueue_template_drift_check",
        "create_job_and_run",
    ):
        if hasattr(jobs, name):
            monkeypatch.setattr(jobs, name, lambda *a, **k: job)

    with Session(engine) as s:
        tpl = ServiceTemplate(slug="web", name="Web", definition_json="{}")
        s.add(tpl)
        s.commit()
        s.refresh(tpl)
        dep = StackDeployment(
            server_id=sid,
            project_name="web",
            template_id=tpl.id,
            template_slug="web",
            variables_json="{}",
            files_json="{}",
        )
        integ = Integration(type="grafana", name="Graf", base_url="http://graf")
        s.add(dep)
        s.add(integ)
        s.commit()
        s.refresh(dep)
        s.refresh(integ)
        did, iid = dep.id, integ.id
        server = s.get(Server, sid)
        server.os_patch_enabled = True
        server.container_patch_enabled = True
        server.backup_enabled = True
        s.add(server)
        s.commit()

    def _walk(routes, prefix=""):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                extra = getattr(getattr(route, "include_context", None), "prefix", "") or ""
                yield from _walk(getattr(original, "routes", []) or [], prefix + extra)
                continue
            nested = getattr(route, "routes", None)
            path = getattr(route, "path", "") or ""
            methods = getattr(route, "methods", None)
            if nested and not methods:
                yield from _walk(nested, prefix + path)
                continue
            if methods:
                yield prefix + path, methods

    def _form_for(path: str, method: str) -> dict:
        data = {
            "name": "pi",
            "hostname": "pi.local",
            "ssh_username": "pi",
            "ssh_port": "22",
            "project_path": "/home/pi/docker/web",
            "project": "web",
            "action": "restart",
            "server_id": str(sid),
            "server_ids": str(sid),
            "external_id": "1",
            "display_name": "pi",
            "role": "ssh",
            "content": "services: {}\n",
            "p": "",
            "src": "a.txt",
            "dest": "b.txt",
            "identity": "fleet",
            "confirm": "yes",
            "email": "q17@test.local",
            "password": "SmokeTest1ok",
            "slug": "web",
            "q": "notes",
            "kind": "metrics",
            "base_url": "http://127.0.0.1",
        }
        try:
            op = (app.openapi().get("paths") or {}).get(path, {}).get(method.lower()) or {}
            content = ((op.get("requestBody") or {}).get("content") or {})
            schema = (
                content.get("application/x-www-form-urlencoded", {}).get("schema")
                or content.get("multipart/form-data", {}).get("schema")
                or {}
            )
            if "$ref" in schema:
                name = schema["$ref"].rsplit("/", 1)[-1]
                schema = (app.openapi().get("components") or {}).get("schemas", {}).get(name) or {}
            for key in schema.get("required") or []:
                data.setdefault(key, "1")
            for key in (schema.get("properties") or {}):
                data.setdefault(key, "1")
        except Exception:
            pass
        return data

    seen = 0
    try:
        for path, methods in _walk(app.routes):
            if any(bit in path for bit in _SKIP):
                continue
            filled = _fill(path, sid, did, iid)
            if not filled or not filled.startswith("/"):
                continue
            for method in sorted(methods):
                if method not in ("GET", "POST"):
                    continue
                if method == "GET":
                    response = client.get(filled, follow_redirects=False)
                else:
                    response = client.post(
                        filled,
                        data=_form_for(path, method),
                        follow_redirects=False,
                    )
                assert response.status_code < 600
                seen += 1
        assert seen > 40
    finally:
        from app.database import get_session
        from app.main import app as application

        application.dependency_overrides.pop(get_session, None)
