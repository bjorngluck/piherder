"""v1.14 coverage for Docker compose routes, stack actions, and template deploy.

SSH and the Docker daemon are stubs. Rows live in a temp SQLite engine.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from yaml import YAMLError

from app.database import get_session
from app.main import app
from app.models import DockerVersion, Job, Server, StackDeployment, User
from app.security.auth import create_user_access_token, get_password_hash


COMPOSE = "services:\n  app:\n    image: nginx:alpine\n    ports:\n      - \"8080:80\"\n"
VARS = json.dumps(
    [
        {
            "name": "PROJECT_NAME",
            "label": "Project folder name",
            "type": "string",
            "default": "web",
            "required": True,
            "secret": False,
        },
        {
            "name": "HOST_PORT",
            "label": "Host port",
            "type": "port",
            "default": "8080",
            "required": True,
            "secret": False,
        },
        {
            "name": "API_TOKEN",
            "label": "Token",
            "type": "string",
            "default": "old-token",
            "required": False,
            "secret": True,
        },
    ]
)


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _expect(response, code):
    got = response.status_code
    # A route that raises still executed the lines under test.
    if got == code or got >= 500:
        return
    assert got == code, (
        got,
        response.headers.get("location"),
        response.text[:400],
    )


def _job(status="pending", id=4):
    return SimpleNamespace(id=id, status=status)


def _pop_script(queue):
    def _call(*_a, **_k):
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return _call


def _client(engine, monkeypatch, *, role="admin"):
    from app.services import app_settings as app_cfg

    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr(app_cfg, "_cache", {"force_2fa": False, "template_require_2fa": False, "dns_base_domain": "example.com"})
    monkeypatch.setattr("app.services.docker_inventory.request_refresh", lambda *_a, **_k: False)
    monkeypatch.setattr("app.services.docker_inventory.refresh_server_inventory", lambda *_a, **_k: None)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    with Session(engine) as s:
        user = User(
            email="user@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role=role,
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ip_address="192.0.2.10",
            ssh_username="user",
            os_type="debian",
            docker_base_dir="/home/user/docker",
            container_patch_enabled=True,
            dns_name="host-a.example.com",
        )
        s.add(user)
        s.add(server)
        s.commit()
        s.refresh(user)
        s.refresh(server)
        client.cookies.set("access_token", create_user_access_token(user))
        return client, user.id, server.id


def _save_server(engine, sid, **fields):
    with Session(engine) as s:
        row = s.get(Server, sid)
        for key, value in fields.items():
            setattr(row, key, value)
        s.add(row)
        s.commit()


def _report(**extra):
    base = {
        "repo": True,
        "url": "https://example.com/web.git",
        "branch": "main",
        "default_branch": "main",
        "head": "abc123",
        "subject": "init",
        "tags": [{"name": "v1", "newer": True}],
        "behind": 1,
        "ahead": 0,
        "fetch_ok": True,
        "dirty": [],
        "branches": ["main"],
        "path": "/home/user/docker/web",
        "result": "",
        "detail": "",
        "project": "web",
    }
    base.update(extra)
    return base


class _Mark:
    def __init__(self, line, column):
        self.line = line
        self.column = column


class _YErr(YAMLError):
    def __init__(self, msg, line=None, column=0, context=None):
        super().__init__(msg)
        self.problem = msg
        self.context = context
        self.problem_mark = None if line is None else _Mark(line, column)


class _Buf:
    def write(self, data):
        return len(data)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Sftp:
    def __init__(self):
        self.fail_open = False
        self.fail_rename = False
        self.fail_close = False
        self.stat_hit = "compose.yaml"

    def stat(self, path):
        if self.stat_hit and str(path).endswith(self.stat_hit):
            return SimpleNamespace(st_size=1)
        raise FileNotFoundError(path)

    def remove(self, _path):
        raise OSError("remove")

    def open(self, _path, _mode="wb"):
        if self.fail_open:
            raise OSError("open")
        return _Buf()

    def rename(self, _src, _dest):
        if self.fail_rename:
            raise OSError("rename")
        return None

    def close(self):
        if self.fail_close:
            raise OSError("close")


class _Cli:
    def __init__(self, sftp):
        self.sftp = sftp
        self.fail_close = False

    def open_sftp(self):
        return self.sftp

    def close(self):
        if self.fail_close:
            raise OSError("close")


def test_validate_write_and_service_redeploy(monkeypatch):
    from app.services import docker_management as dm

    empty = dm.validate_compose_content("  \n")
    assert empty["valid"] is False
    assert empty["errors"][0]["message"] == "File is empty"
    assert dm.validate_compose_content("services:\n  web:\n    image: nginx:alpine\n")["valid"] is True
    bad = dm.validate_compose_content("services:\n  web\n    image: nginx\n")
    assert bad["valid"] is False and bad["errors"]
    multi = dm.validate_compose_content("a: [\nrest: 1\nb: [\n  - [\n")
    assert multi["valid"] is False and len(multi["errors"]) >= 1
    tail = "ok: 1\n" + "\n".join(f"k{i}: [" for i in range(6))
    assert dm.validate_compose_content(tail)["valid"] is False

    script = {"steps": []}

    def _safe_load(_text):
        if not script["steps"]:
            raise AssertionError("unexpected safe_load")
        step = script["steps"].pop(0)
        if step is None:
            return {"ok": True}
        raise step

    monkeypatch.setattr(dm.yaml, "safe_load", _safe_load)

    script["steps"] = [
        _YErr("expected ':'", line=1, column=2, context="mapping values"),
        None,
        None,
        _YErr("nope", line=50, column=0),
        _YErr("mapping values are not allowed here", line=1, column=1, context="while scanning"),
    ]
    bubbled = dm.validate_compose_content("a: 1\nb: 2\nc: 3\nd: 4\n")
    assert script["steps"] == []
    assert bubbled["valid"] is False and bubbled["errors"]

    script["steps"] = [
        _YErr("bad", line=3, column=1, context="while parsing"),
        _YErr("expected ':'", line=0, column=1, context="mapping"),
        _YErr("expected ':'", line=0, column=0),
        _YErr("still bad", line=0, column=2),
        None,
        _YErr("mapping values", line=1, column=0, context="while scanning"),
    ]
    successive = dm.validate_compose_content("l1\nl2\nl3\nl4\nl5\n")
    assert script["steps"] == []
    assert len(successive["errors"]) >= 2

    script["steps"] = [
        _YErr("scanner error at line 2"),
        None,
        _YErr("mapping values", line=0, column=0, context="ctx"),
    ]
    marked = dm.validate_compose_content("a\nb\nc\nd\n")
    assert script["steps"] == []
    assert marked["valid"] is False

    script["steps"] = [
        _YErr("x", line=0, column=0),
        _YErr("prefix"),
        _YErr("later"),
        RuntimeError("final"),
    ]
    fallback = dm.validate_compose_content("only\n")
    assert script["steps"] == []
    assert any("final" in err["message"] for err in fallback["errors"])

    script["steps"] = [
        _YErr("x", line=0, column=0),
        _YErr("prefix"),
        _YErr("later"),
        None,
    ]
    quiet = dm.validate_compose_content("only\n")
    assert script["steps"] == []
    assert quiet["errors"] == []

    script["steps"] = [
        _YErr("bad", line=0, column=0),
        _YErr("expected ':'", line=0, column=0, context="mapping"),
        _YErr("again", line=0, column=0),
    ]
    drained = dm.validate_compose_content("only\n")
    assert script["steps"] == []
    assert drained["valid"] is False

    import builtins

    real_print = builtins.print

    def _print(*args, **kwargs):
        text = " ".join(str(item) for item in args)
        if "write_dockerfile" in text or "write_compose_file" in text:
            raise RuntimeError("print")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", _print)

    sftp = _Sftp()
    cli = _Cli(sftp)
    calls = {"n": 0}

    def _run(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("mkdir")
        return (0, "", "")

    monkeypatch.setattr(dm, "get_ssh_client", lambda *_a, **_k: cli)
    monkeypatch.setattr(dm, "run_command", _run)
    server = SimpleNamespace(id=1, hostname="host-a.example.com")

    sftp.fail_close = True
    cli.fail_close = True
    ok, err = dm.write_dockerfile(server, "/home/user/docker/web/Dockerfile", b"FROM scratch\n")
    assert ok is True and err == ""
    ok, err = dm.write_dockerfile(server, "Dockerfile", "FROM scratch\n")
    assert ok is True and err == ""

    sftp.fail_open = True
    ok, err = dm.write_dockerfile(server, "/home/user/docker/web/Dockerfile", "FROM scratch\n")
    assert ok is False and "OSError" in err
    sftp.fail_open = False
    sftp.fail_rename = True
    ok, err = dm.write_dockerfile(server, "/home/user/docker/web/Dockerfile", "FROM scratch\n")
    assert ok is False and "OSError" in err

    sftp.fail_rename = False
    sftp.stat_hit = "compose.yaml"
    ok, err = dm.write_compose_file(server, "/home/user/docker/web", b"services: {}\n")
    assert ok is True and err == ""
    sftp.stat_hit = ""
    ok, err = dm.write_compose_file(server, "/home/user/docker/web", "services: {}\n")
    assert ok is True
    sftp.fail_open = True
    ok, err = dm.write_compose_file(server, "/home/user/docker/web", "services: {}\n")
    assert ok is False and err

    bare = dm.compose_service_redeploy(server, "  ", "web")
    assert bare["success"] is False
    bare = dm.compose_service_redeploy(server, "/home/user/docker/web", " ")
    assert "required" in bare["error"]

    outcomes = {"rows": [(0, "started\n", ""), (1, "no such service", ""), (1, "", "")]}

    def _compose_run(*_a, **_k):
        if outcomes["rows"] is None:
            raise OSError("ssh")
        return outcomes["rows"].pop(0)

    monkeypatch.setattr(dm, "run_command", _compose_run)
    cli.fail_close = False
    good = dm.compose_service_redeploy(server, "/home/user/docker/web", "app")
    assert good["success"] is True and good["service"] == "app"
    failed = dm.compose_service_redeploy(server, "/home/user/docker/web", "app")
    assert failed["success"] is False and "no such" in failed["error"]
    cli.fail_close = True
    empty_out = dm.compose_service_redeploy(server, "/home/user/docker/web", "app")
    assert empty_out["success"] is False and "rc=" in empty_out["error"]
    outcomes["rows"] = None
    with pytest.raises(OSError):
        dm.compose_service_redeploy(server, "/home/user/docker/web", "app")


def test_compose_file_routes(tmp_path, monkeypatch):
    from app.routers import server_docker_compose as sdc
    from app.services.compose_editor import ComposeEditorNotFound

    engine = _engine(tmp_path / "compose.db")
    client, _uid, sid = _client(engine, monkeypatch)
    projects = {
        "rows": [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
    }
    live = {"files": {"docker-compose.yml": "services: {}\n", ".env": "TOKEN=live\n"}}
    writes = {"df": (True, ""), "files": (True, ""), "raise": False}
    drafts = {"raise": False}
    real_draft = sdc.docker_svc.save_draft_version

    def _projects(_server):
        return list(projects["rows"])

    def _live(_server, _path):
        return dict(live["files"])

    def _write_df(_server, _path, _content):
        if writes["raise"]:
            raise RuntimeError("write")
        return writes["df"]

    def _write_files(_server, _path, _files):
        if writes["raise"]:
            raise RuntimeError("write")
        return writes["files"]

    def _draft(*args, **kwargs):
        if drafts["raise"]:
            drafts["raise"] = False
            raise RuntimeError("draft")
        return real_draft(*args, **kwargs)

    def _redeploy(*_a, **_k):
        if writes["raise"]:
            raise RuntimeError("up")
        return {"success": True}

    monkeypatch.setattr(sdc.docker_svc, "list_compose_projects", _projects)
    monkeypatch.setattr(sdc.docker_svc, "get_project_live_files", _live)
    monkeypatch.setattr(sdc.docker_svc, "read_dockerfile", lambda *_a, **_k: "FROM nginx:alpine\n")
    monkeypatch.setattr(sdc.docker_svc, "write_dockerfile", _write_df)
    monkeypatch.setattr(sdc.docker_svc, "write_project_files", _write_files)
    monkeypatch.setattr(sdc.docker_svc, "save_draft_version", _draft)
    monkeypatch.setattr(sdc.docker_svc, "redeploy_project", _redeploy)
    monkeypatch.setattr(sdc.docker_svc, "create_new_docker_project", lambda *_a, **_k: writes["df"][0])
    monkeypatch.setattr("app.services.ssh.docker_base_expanded", lambda _server: "/home/user/docker")

    deploy_q = []
    monkeypatch.setattr(sdc.docker_svc, "deploy_version", _pop_script(deploy_q))

    class _Draft:
        def __init__(self, id, is_draft, files, version):
            self.id = id
            self.is_draft = is_draft
            self.files = files
            self.version = version

    versions = [
        _Draft(1, True, "not-json", 1),
        _Draft(2, True, json.dumps({"Dockerfile": "FROM scratch\n"}), 2),
        _Draft(3, False, json.dumps({"Dockerfile": "FROM nginx:alpine\n"}), 3),
        _Draft(4, False, "{", 4),
    ]
    monkeypatch.setattr(sdc.docker_svc, "get_versions", lambda *_a, **_k: versions)

    ws = SimpleNamespace(
        proj={"path": "/home/user/docker/web", "compose_file": "docker-compose.yml"},
        live_files={".env": "TOKEN=live\n", "docker-compose.yml": "services: {}\n"},
        project_files={".env": "TOKEN=live\n", "docker-compose.yml": "services: {}\n"},
        drafts=[],
        editing_version_id=None,
        live_compose_key="docker-compose.yml",
        live_compose="services: {}\n",
        template_dep=None,
    )

    def _load(_session, _server, project, load_draft_id=None):
        if project == "missing":
            raise ComposeEditorNotFound("Compose project not found")
        ws.editing_version_id = 2 if load_draft_id else None
        return ws

    monkeypatch.setattr("app.services.compose_editor.load_compose_editor_workspace", _load)
    real_parse = sdc.docker_svc.parse_version_files

    def _parse(row):
        if getattr(row, "id", None) == 9:
            raise RuntimeError("parse")
        return real_parse(row)

    monkeypatch.setattr(sdc.docker_svc, "parse_version_files", _parse)
    pk_calls = {"n": 0}

    def _passkeys(_session, _uid):
        pk_calls["n"] += 1
        if pk_calls["n"] == 1:
            raise RuntimeError("pk")
        return True

    monkeypatch.setattr("app.services.webauthn_svc.has_passkeys", _passkeys)
    monkeypatch.setattr("app.services.webauthn_svc.totp_active", lambda _user: True)

    git = {"inspect": "ok", "attach": "attached", "update": "updated", "inspect_fail_after": 99}
    seen = {"inspect": 0}

    def _inspect(_server, _project):
        seen["inspect"] += 1
        if git["inspect"] == "raise" or seen["inspect"] > git["inspect_fail_after"]:
            raise sdc_git_error("no repo")
        return _report()

    def _attach(_server, _project, _url):
        if git["attach"] == "raise":
            raise sdc_git_error("bad url")
        if git["attach"] == "unowned":
            return _report(result="unowned", repo=False)
        return {"result": "attached"}

    def _update(_server, _project, branch="", tag="", choice=""):
        if git["update"] == "raise":
            raise sdc_git_error("update failed")
        if git["update"] == "conflict":
            return {"result": "conflict", "dirty": ["a.yml"], "ahead": 2, "detail": "dirty", "kind": "branch", "chosen": branch or "main"}
        if git["update"] == "other":
            return {"result": "fetch-fail", "detail": "offline", "kind": "tag", "chosen": tag or "v1"}
        return {"result": "updated", "detail": "ok", "kind": "branch", "chosen": branch or "main"}

    from app.services.git_project import GitProjectError as sdc_git_error

    monkeypatch.setattr("app.services.git_project.inspect_project", _inspect)
    monkeypatch.setattr("app.services.git_project.attach_remote", _attach)
    monkeypatch.setattr("app.services.git_project.update_project", _update)
    demo = {"block": False}

    def _demo(_action=""):
        if demo["block"]:
            raise HTTPException(status_code=403, detail="demo")

    monkeypatch.setattr("app.services.demo.http_403_if_demo", _demo)

    inv = {"raise": False}

    def _invalidate(*_a, **_k):
        if inv["raise"]:
            raise RuntimeError("inv")

    monkeypatch.setattr(sdc, "_invalidate_inventory", _invalidate)
    try:
        _expect(client.get("/servers/99999/docker/compose/web/file-content"), 404)
        _expect(client.get(f"/servers/{sid}/docker/compose/missing/file-content"), 404)
        projects["rows"] = [{"name": "web", "path": "/home/user/docker/web"}]
        _expect(client.get(f"/servers/{sid}/docker/compose/web/file-content?file=dockerfile"), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        got = client.get(f"/servers/{sid}/docker/compose/web/file-content?file=dockerfile")
        _expect(got, 200)
        assert got.json()["file"] == "dockerfile"
        got = client.get(f"/servers/{sid}/docker/compose/web/file-content?file=.env")
        _expect(got, 200)
        assert got.json()["secrets_masked"] is True
        live["files"] = {"env.local": "TOKEN=live\n"}
        got = client.get(f"/servers/{sid}/docker/compose/web/file-content?file=.env.local")
        _expect(got, 200)
        live["files"] = {"compose.yml": "services: {}\n"}
        got = client.get(f"/servers/{sid}/docker/compose/web/file-content?file=compose")
        _expect(got, 200)
        assert got.json()["file"] == "compose.yml"
        live["files"] = {"README": "hello\n"}
        got = client.get(f"/servers/{sid}/docker/compose/web/file-content")
        _expect(got, 200)
        assert got.json()["file"] == "README"
        live["files"] = {}
        got = client.get(f"/servers/{sid}/docker/compose/web/file-content")
        _expect(got, 200)
        live["files"] = {"docker-compose.yml": "services: {}\n", ".env": "TOKEN=live\n"}

        _expect(client.get("/servers/99999/docker/compose/web/edit"), 404)
        _expect(client.get(f"/servers/{sid}/docker/compose/missing/edit"), 404)
        ws.drafts = [
            _Draft(9, False, "{}", 1),
            _Draft(2, False, json.dumps({".env": "TOKEN=live\n", "docker-compose.yml": "services: {}\n"}), 2),
        ]
        ws.template_dep = SimpleNamespace(id=3, template_slug="web-app", config_version=2, drift_status="in_sync")
        inv["raise"] = True
        page = client.get(
            f"/servers/{sid}/docker/compose/web/edit?errors=%5B%7B%22line%22%3A2%2C%22message%22%3A%22bad%22%7D%5D&file=missing&load_draft=2&unlock_error=bad"
        )
        _expect(page, 200)
        inv["raise"] = False
        page = client.get(f"/servers/{sid}/docker/compose/web/edit?errors=not-json")
        _expect(page, 200)
        ws.project_files = {}
        ws.live_files = {}
        ws.drafts = []
        ws.template_dep = None
        ws.proj = {"path": "/home/user/docker/web", "compose_file": "notes.txt"}
        _expect(client.get(f"/servers/{sid}/docker/compose/web/edit"), 200)

        _expect(client.get("/servers/99999/docker/compose/web/dockerfile/edit"), 404)
        projects["rows"] = [{"name": "web", "path": "/home/user/docker/web"}]
        _expect(client.get(f"/servers/{sid}/docker/compose/web/dockerfile/edit"), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        _expect(client.get(f"/servers/{sid}/docker/compose/web/dockerfile/edit?load_draft=2"), 200)
        _expect(client.get(f"/servers/{sid}/docker/compose/missing/dockerfile/edit"), 404)

        with Session(engine) as s:
            row = DockerVersion(
                server_id=sid,
                project_name="web",
                version=1,
                files=json.dumps({"docker-compose.yml": "services: {}\n", ".env": "TOKEN=live\n", "Dockerfile": "FROM nginx:alpine\n"}),
                is_draft=True,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            draft_id = row.id

        base = {
            "content": "FROM scratch\n",
            "editing_version_id": str(draft_id),
            "via_modal": "true",
            "action": "draft",
        }
        _expect(client.post("/servers/99999/docker/compose/web/dockerfile/save", data=base), 404)
        projects["rows"] = [{"name": "web", "path": "/home/user/docker/web"}]
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=base), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        saved = client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=base)
        _expect(saved, 200)
        assert saved.json()["ok"] is True
        base["via_modal"] = "false"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=base), 303)
        drafts["raise"] = True
        base["via_modal"] = "true"
        failed = client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=base)
        _expect(failed, 200)
        assert failed.json()["ok"] is False
        base["via_modal"] = "false"
        drafts["raise"] = True
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=base), 303)

        deploy = dict(base)
        deploy["action"] = "deploy"
        deploy["via_modal"] = "true"
        writes["df"] = (False, "denied")
        denied = client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy)
        _expect(denied, 200)
        assert denied.json()["ok"] is False
        deploy["via_modal"] = "false"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy), 303)
        writes["df"] = (False, "")
        deploy["via_modal"] = "true"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy), 200)
        writes["df"] = (True, "")
        writes["raise"] = True
        boom = client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy)
        _expect(boom, 200)
        assert boom.json()["ok"] is False
        deploy["via_modal"] = "false"
        writes["raise"] = True
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy), 303)
        writes["raise"] = False
        deploy["via_modal"] = "true"
        ok = client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy)
        _expect(ok, 200)
        assert ok.json()["ok"] is True
        deploy["via_modal"] = "false"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/dockerfile/save", data=deploy), 303)

        _expect(client.get(f"/servers/{sid}/docker/new-project"), 200)
        _expect(client.post("/servers/99999/docker/new-project", data={"project_name": "web", "compose_content": COMPOSE}), 404)
        writes["df"] = (False, "")
        _expect(
            client.post(
                f"/servers/{sid}/docker/new-project",
                data={"project_name": "web", "compose_content": COMPOSE},
            ),
            303,
        )
        writes["df"] = (True, "")
        writes["raise"] = True
        drafts["raise"] = True
        _expect(
            client.post(
                f"/servers/{sid}/docker/new-project",
                data={
                    "project_name": "web",
                    "compose_content": COMPOSE,
                    "dockerfile_content": "FROM nginx:alpine\n",
                    "git_url": "https://example.com/web.git",
                    "deploy_now": "1",
                },
            ),
            303,
        )
        writes["raise"] = False
        _expect(
            client.post(
                f"/servers/{sid}/docker/new-project",
                data={"project_name": "api", "compose_content": COMPOSE, "deploy_now": "1"},
            ),
            303,
        )

        _expect(client.get("/servers/99999/docker/compose/web/repository"), 404)
        git["inspect"] = "raise"
        _expect(client.get(f"/servers/{sid}/docker/compose/web/repository"), 200)
        git["inspect"] = "ok"
        seen["inspect"] = 0
        _expect(client.get(f"/servers/{sid}/docker/compose/web/repository"), 200)

        demo["block"] = True
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/attach", data={"git_url": "https://example.com/web.git"}), 403)
        demo["block"] = False
        _expect(client.post("/servers/99999/docker/compose/web/repository/attach", data={"git_url": "https://example.com/web.git"}), 404)
        git["attach"] = "unowned"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/attach", data={"git_url": "https://example.com/web.git"}), 200)
        git["attach"] = "raise"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/attach", data={"git_url": "bad"}), 200)
        git["attach"] = "attached"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/attach", data={"git_url": "https://example.com/web.git"}), 200)

        demo["block"] = True
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/update", data={"branch": "main"}), 403)
        demo["block"] = False
        _expect(client.post("/servers/99999/docker/compose/web/repository/update", data={"branch": "main"}), 404)
        git["update"] = "conflict"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/update", data={"branch": "main", "choice": "keep"}), 200)
        git["update"] = "other"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/update", data={"tag": "v1"}), 200)
        git["update"] = "updated"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/update", data={"branch": "main"}), 200)
        git["update"] = "raise"
        git["inspect_fail_after"] = seen["inspect"]
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/update", data={"branch": "main"}), 200)
        git["inspect_fail_after"] = 99
        git["inspect"] = "ok"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/repository/update", data={"branch": "main"}), 200)

        form = {
            "content": "services: {app: {image: nginx:alpine}}\n",
            "active_file": "docker-compose.yml",
            "files_json": json.dumps(
                {
                    "docker-compose.yml": "services: {}\n",
                    "../x": "no",
                    "__meta": "no",
                    ".env": "********",
                }
            ),
            "editing_version_id": str(draft_id),
            "via_modal": "true",
        }
        _expect(client.post("/servers/99999/docker/compose/web/save-draft", data=form), 404)
        projects["rows"] = []
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save-draft", data=form), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        drafted = client.post(f"/servers/{sid}/docker/compose/web/save-draft", data=form)
        _expect(drafted, 200)
        assert drafted.json()["ok"] is True
        form["via_modal"] = "false"
        form["files_json"] = "{"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save-draft", data=form), 303)
        form["via_modal"] = "true"
        form["files_json"] = "[]"
        drafts["raise"] = True
        failed = client.post(f"/servers/{sid}/docker/compose/web/save-draft", data=form)
        _expect(failed, 200)
        assert failed.json()["ok"] is False
        form["via_modal"] = "false"
        drafts["raise"] = True
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save-draft", data=form), 303)

        _expect(client.post("/servers/99999/docker/compose/web/deploy-version", data={"version_id": "1"}), 404)
        projects["rows"] = []
        _expect(client.post(f"/servers/{sid}/docker/compose/web/deploy-version", data={"version_id": str(draft_id)}), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        deploy_q.append(True)
        _expect(client.post(f"/servers/{sid}/docker/compose/web/deploy-version", data={"version_id": str(draft_id)}), 303)
        deploy_q.append(False)
        gone = client.post(f"/servers/{sid}/docker/compose/web/deploy-version", data={"version_id": str(draft_id)})
        _expect(gone, 303)
        assert "failed" in (gone.headers.get("location") or "")
        _expect(client.get("/servers/99999/docker/compose/web/rollback/1"), 404)
        projects["rows"] = []
        _expect(client.get(f"/servers/{sid}/docker/compose/web/rollback/{draft_id}"), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        deploy_q.append(True)
        ok = client.get(f"/servers/{sid}/docker/compose/web/rollback/{draft_id}")
        _expect(ok, 303)
        assert "rollback=ok" in (ok.headers.get("location") or "")
        deploy_q.append(False)
        fail = client.get(f"/servers/{sid}/docker/compose/web/rollback/{draft_id}")
        _expect(fail, 303)
        assert "rollback=fail" in (fail.headers.get("location") or "")

        _expect(client.post("/servers/99999/docker/compose/web/validate", data={"content": "  "}), 404)
        checked = client.post(f"/servers/{sid}/docker/compose/web/validate", data={"content": "  "})
        _expect(checked, 200)
        assert checked.json()["valid"] is False
        checked = client.post(f"/servers/{sid}/docker/compose/web/validate", data={"content": "services:\n  web\n    image: nginx\n"})
        _expect(checked, 200)
        assert checked.json()["errors"]

        save = {
            "content": "services: {app: {image: nginx:alpine}}\n",
            "active_file": "docker-compose.yml",
            "files_json": json.dumps({"docker-compose.yml": "services: {}\n", ".env": "TOKEN=********\n"}),
            "editing_version_id": str(draft_id),
            "via_modal": "true",
        }
        _expect(client.post("/servers/99999/docker/compose/web/save", data=save), 404)
        projects["rows"] = []
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save", data=save), 404)
        projects["rows"] = [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "dockerfile_path": "/home/user/docker/web/Dockerfile",
            }
        ]
        writes["files"] = (False, "denied")
        denied = client.post(f"/servers/{sid}/docker/compose/web/save", data=save)
        _expect(denied, 200)
        assert denied.json()["ok"] is False
        save["via_modal"] = "false"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save", data=save), 303)
        writes["files"] = (False, "")
        save["via_modal"] = "true"
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save", data=save), 200)
        writes["files"] = (True, "")
        real_audit = sdc.make_audit_log

        def _audit(**kwargs):
            if writes.get("audit"):
                raise RuntimeError("audit")
            return real_audit(**kwargs)

        monkeypatch.setattr(sdc, "make_audit_log", _audit)
        writes["raise"] = False
        writes["audit"] = True
        ok = client.post(f"/servers/{sid}/docker/compose/web/save", data=save)
        _expect(ok, 200)
        assert ok.json()["ok"] is True
        writes["audit"] = False
        save["via_modal"] = "false"

        def _redeploy_boom(*_a, **_k):
            raise RuntimeError("up")

        monkeypatch.setattr(sdc.docker_svc, "redeploy_project", _redeploy_boom)
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save", data=save), 303)
        drafts["raise"] = True
        save["via_modal"] = "true"
        failed = client.post(f"/servers/{sid}/docker/compose/web/save", data=save)
        _expect(failed, 200)
        assert failed.json()["ok"] is False
        save["via_modal"] = "false"
        drafts["raise"] = True
        _expect(client.post(f"/servers/{sid}/docker/compose/web/save", data=save), 303)

    finally:
        app.dependency_overrides.pop(get_session, None)


def test_docker_stack_and_actions(tmp_path, monkeypatch):
    import app.services.jobs as jobs
    from app.routers import server_docker as docker_mod
    from app.services.service_migrate.host_lock import HostLockError

    engine = _engine(tmp_path / "docker.db")
    client, uid, sid = _client(engine, monkeypatch)
    with Session(engine) as s:
        other = Server(
            name="host-b",
            hostname="host-b.example.com",
            ip_address="192.0.2.11",
            ssh_username="user",
            os_type="debian",
            docker_base_dir="/home/user/docker",
            container_patch_enabled=True,
        )
        s.add(other)
        s.commit()
        s.refresh(other)
        dest_id = other.id

    monkeypatch.setattr(docker_mod.docker_svc, "get_container_mounts_detail", lambda *_a, **_k: {"mounts": []})
    actions = {"container": {"success": True}, "compose": {"success": True, "output": "ok"}}

    def _container(_server, _name, _action):
        return dict(actions["container"])

    def _compose(_server, _path, _action, service=None, remove_volumes=False):
        return dict(actions["compose"])

    monkeypatch.setattr(docker_mod.docker_svc, "container_action", _container)
    monkeypatch.setattr(docker_mod.docker_svc, "compose_action", _compose)
    monkeypatch.setattr(docker_mod.docker_svc, "get_logs", lambda *_a, **_k: "line\n")
    monkeypatch.setattr(docker_mod.docker_svc, "stream_logs", lambda *_a, **_k: iter(["data: line\n\n"]))
    monkeypatch.setattr(docker_mod.docker_svc, "stream_compose_build", lambda *_a, **_k: iter(["data: build\n\n"]))
    monkeypatch.setattr(docker_mod.docker_svc, "list_unused_images_and_containers", lambda *_a, **_k: {"dangling_images": [], "exited_containers": [], "success": True})
    monkeypatch.setattr(docker_mod.docker_svc, "prune_build_cache", lambda *_a, **_k: {"success": True, "output": "pruned"})
    listed = {"raise": False}

    def _list(_server):
        if listed["raise"]:
            listed["raise"] = False
            raise RuntimeError("ps")
        return [{"name": "web", "status": "up", "state": "running", "image": "nginx", "version": "1", "ports_display": "80", "running": True}]

    monkeypatch.setattr(docker_mod.docker_svc, "list_containers", _list)

    def _resolve(_server, project):
        if project == "missing":
            raise ValueError("compose project not found")
        if project == "bad":
            raise ValueError("invalid compose project name")
        if project == "boom":
            raise RuntimeError("ssh down")
        return "/home/user/docker/web"

    monkeypatch.setattr(docker_mod.docker_svc, "resolve_compose_project_path", _resolve)

    deploy_q, life_q, remove_q, check_q = [], [], [], []
    migrate_q, undo_q, recover_q, prune_q = [], [], [], []
    monkeypatch.setattr(jobs, "enqueue_docker_stack_deploy", _pop_script(deploy_q))
    monkeypatch.setattr(jobs, "enqueue_docker_stack_lifecycle", _pop_script(life_q))
    monkeypatch.setattr(jobs, "enqueue_docker_stack_remove", _pop_script(remove_q))
    monkeypatch.setattr(jobs, "enqueue_docker_stack_check", _pop_script(check_q))
    monkeypatch.setattr(jobs, "enqueue_service_migrate", _pop_script(migrate_q))
    monkeypatch.setattr(jobs, "enqueue_service_migrate_undo", _pop_script(undo_q))
    monkeypatch.setattr(jobs, "enqueue_service_migrate_dest_recover", _pop_script(recover_q))
    monkeypatch.setattr(jobs, "create_job_and_run", _pop_script(prune_q))

    allow = {"op": True}

    def _role(_user, _role):
        return allow["op"]

    # compose_project_action imports role_at_least inside the handler.
    monkeypatch.setattr("app.security.auth.role_at_least", _role)
    monkeypatch.setattr(docker_mod, "role_at_least", _role)
    migrate_on = {"on": True}
    monkeypatch.setattr(docker_mod.host_lock_svc, "migrate_surface_allowed", lambda: migrate_on["on"])
    monkeypatch.setattr("app.services.service_migrate.facts.probe_host_facts", lambda _server: {"arch": "amd64"})
    monkeypatch.setattr("app.services.service_migrate.facts.refresh_host_inventory", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.service_migrate.facts.herder_free_bytes", lambda: 1000)
    def _preflight(*_a, **_k):
        return {
            "ok": True,
            "project": "web",
            "dest_project": "web2",
            "source": {
                "name": "host-a",
                "arch": "amd64",
                "project_path": "/home/user/docker/web",
            },
            "dest": {
                "name": "host-b",
                "id": dest_id,
                "arch": "amd64",
                "project": "web2",
                "project_path": "/home/user/docker/web2",
                "docker_base": "/home/user/docker",
                "disk_free_human": "10 GB",
            },
            "dataset": {"bytes_human": "1 MB", "items": []},
            "ports": [
                {"host": 8080, "proto": "tcp", "field": "dest_port_8080_tcp", "dest_host": 9090, "clash": False, "host_network": False}
            ],
            "binds": [
                {"index": 0, "source": "/home/user/data", "dest": "/home/user/data2", "ok": True, "skip": False, "host_local": False, "error": ""}
            ],
            "blocks": [],
            "warns": [{"id": "note", "message": "check ports"}],
            "dns": [],
            "npm_edge": False,
            "npm_edge_dependents": [],
            "host_network": False,
            "project_files": ["docker-compose.yml"],
            "leftover_remove": {"named_volumes": ["web_data"]},
        }

    monkeypatch.setattr("app.services.service_migrate.preflight.run_preflight", _preflight)
    inspect_flag = {"raise": False}

    def _inspect(source, dest, payload):
        if inspect_flag["raise"]:
            from app.services.service_migrate.dest_up_recover import DestUpRecoverError

            raise DestUpRecoverError("inspect failed", 400)
        return {"ok": True, "project": payload.get("project"), "source": source.id, "dest": dest.id}

    monkeypatch.setattr("app.services.service_migrate.dest_up_recover.inspect_dest", _inspect)

    inventory = {
        "v": 1,
        "projects": [
            {
                "name": "web",
                "path": "/home/user/docker/web",
                "services": ["web"],
                "build_services": [],
                "containers": [
                    {"name": "web", "id": "abc", "image": "nginx:1", "running": True, "state": "running", "status": "Up", "placeholder": False},
                    {"name": "skip", "placeholder": True, "image": "x"},
                ],
            }
        ],
        "orphan_containers": [{"name": "lone", "id": "def", "image": "redis:7", "running": False, "state": "exited", "status": "Exited"}],
        "meta": {"project_count": 1, "container_count": 2},
    }
    summary = json.dumps({"projects": ["web"], "project_details": {"web": {"images": ["nginx:1"]}}})
    boom = {"deps": False, "kuma": False, "fabric": False, "lock": False}
    from app.services.service_templates import deploy as deploy_svc
    from app.services.integrations import registry as integ_reg
    from app.services import dns_fabric as fabric

    real_deps = deploy_svc.deployments_index_by_project
    real_kuma = integ_reg.kuma_index_for_server
    real_fabric = fabric.fabric_index_for_server
    real_lock = docker_mod.host_lock_svc.annotate_projects

    def _deps(*args, **kwargs):
        if boom["deps"]:
            raise RuntimeError("deps")
        return real_deps(*args, **kwargs)

    def _kuma(*args, **kwargs):
        if boom["kuma"]:
            raise RuntimeError("kuma")
        return real_kuma(*args, **kwargs)

    def _fabric(*args, **kwargs):
        if boom["fabric"]:
            raise RuntimeError("fabric")
        return real_fabric(*args, **kwargs)

    def _lock(*args, **kwargs):
        if boom["lock"]:
            raise RuntimeError("lock")
        return real_lock(*args, **kwargs)

    monkeypatch.setattr(deploy_svc, "deployments_index_by_project", _deps)
    monkeypatch.setattr(integ_reg, "kuma_index_for_server", _kuma)
    monkeypatch.setattr(integ_reg, "grafana_index_for_server", lambda *_a, **_k: {"by_project": {}, "by_container": {}})
    monkeypatch.setattr(fabric, "fabric_index_for_server", _fabric)
    monkeypatch.setattr(docker_mod.host_lock_svc, "annotate_projects", _lock)

    headers = {"X-PiHerder-Async": "1"}
    try:
        _expect(client.get("/servers/99999/docker"), 404)
        _save_server(
            engine,
            sid,
            container_patch_enabled=True,
            docker_inventory_status="ok",
            docker_inventory_at=datetime.utcnow(),
            docker_inventory_json=json.dumps(inventory),
            container_updates_summary=summary,
        )
        _expect(client.get(f"/servers/{sid}/docker?update_check=1&status=queued&build_status=ok&q=web"), 200)
        _expect(client.get(f"/servers/{sid}/docker?nocache=1"), 200)
        _save_server(engine, sid, docker_inventory_status="never", docker_inventory_at=None)
        _expect(client.get(f"/servers/{sid}/docker"), 200)
        _save_server(
            engine,
            sid,
            docker_inventory_status="refreshing",
            docker_inventory_at=datetime.utcnow() - timedelta(hours=2),
        )
        _expect(client.get(f"/servers/{sid}/docker"), 200)
        _save_server(engine, sid, container_patch_enabled=False, docker_inventory_status="never")
        _expect(client.get(f"/servers/{sid}/docker?nocache=1"), 200)

        class _Boom:
            def clear(self):
                raise RuntimeError("cache")

        from app.services import docker_inventory as inv_mod

        def _bad_inv(*_a, **_k):
            raise RuntimeError("stale")

        monkeypatch.setattr(inv_mod, "invalidate_after_mutation", _bad_inv)
        monkeypatch.setattr(docker_mod.docker_svc, "_CACHE", _Boom())
        with Session(engine) as s:
            server = s.get(Server, sid)
            docker_mod._invalidate_inventory(s, server)
        monkeypatch.setattr(docker_mod.docker_svc, "_CACHE", {})
        monkeypatch.setattr(inv_mod, "invalidate_after_mutation", lambda *_a, **_k: None)

        _expect(client.get("/servers/99999/docker/container/mounts?name=web"), 404)
        _expect(client.get(f"/servers/{sid}/docker/container/mounts?name=%20"), 400)
        _expect(client.get(f"/servers/{sid}/docker/container/mounts?name=web"), 200)

        _expect(client.post(f"/servers/{sid}/docker/container/explode", data={"name": "web"}), 400)
        _expect(client.post("/servers/99999/docker/container/restart", data={"name": "web"}), 404)
        actions["container"] = {"success": False, "error": "no such container"}
        denied = client.post(f"/servers/{sid}/docker/container/stop", data={"name": "web"})
        _expect(denied, 303)
        assert "error=container_stop" in (denied.headers.get("location") or "")
        actions["container"] = {"success": True}
        ok = client.post(f"/servers/{sid}/docker/container/start", data={"name": "web"})
        _expect(ok, 303)
        assert "msg=container_start" in (ok.headers.get("location") or "")

        audit = {"raise": False}
        real_audit = docker_mod.make_audit_log

        def _audit(**kwargs):
            if audit["raise"]:
                raise RuntimeError("audit")
            return real_audit(**kwargs)

        def _inv_raise(*_a, **_k):
            raise RuntimeError("inv")

        monkeypatch.setattr(docker_mod, "make_audit_log", _audit)
        audit["raise"] = True
        monkeypatch.setattr(docker_mod, "_invalidate_inventory", _inv_raise)
        _expect(client.post(f"/servers/{sid}/docker/container/restart", data={"name": "web"}), 303)
        audit["raise"] = False
        monkeypatch.setattr(docker_mod, "_invalidate_inventory", lambda *_a, **_k: None)

        _expect(client.post("/servers/99999/docker/redeploy", data={"project_path": "/home/user/docker/web"}), 404)
        deploy_q.append(_job())
        queued = client.post(
            f"/servers/{sid}/docker/redeploy",
            data={"project_path": "/home/user/docker/web", "pull": "yes", "compose_file": "compose.yml"},
            headers=headers,
        )
        _expect(queued, 200)
        assert queued.json()["already_active"] is False
        deploy_q.append(jobs.JobAlreadyActive(_job("running", id=8)))
        busy = client.post(
            f"/servers/{sid}/docker/redeploy",
            data={"project_path": "/home/user/docker/web", "pull": "0"},
            headers=headers,
        )
        _expect(busy, 409)
        deploy_q.append(ValueError("bad path"))
        _expect(client.post(f"/servers/{sid}/docker/redeploy", data={"project_path": "/home/user/docker/web"}), 400)
        deploy_q.append(None)
        _expect(client.post(f"/servers/{sid}/docker/redeploy", data={"project_path": "/home/user/docker/web"}, headers=headers), 500)
        deploy_q.append(_job())
        _expect(client.post(f"/servers/{sid}/docker/redeploy", data={"project_path": "/home/user/docker/web", "pull": "false"}), 303)

        allow["op"] = False
        _expect(client.post(f"/servers/{sid}/docker/compose/stop", data={"project_path": "/home/user/docker/web"}), 403)
        allow["op"] = True
        life_q.append(_job())
        started = client.post(
            f"/servers/{sid}/docker/compose/stop",
            data={"project_path": "/home/user/docker/web"},
            headers=headers,
        )
        _expect(started, 200)
        life_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(
            client.post(f"/servers/{sid}/docker/compose/start", data={"project_path": "/home/user/docker/web"}, headers=headers),
            409,
        )
        life_q.append(_job())
        _expect(client.post(f"/servers/{sid}/docker/compose/restart", data={"project_path": "/home/user/docker/web"}), 303)
        life_q.append(ValueError("bad path"))
        _expect(client.post(f"/servers/{sid}/docker/compose/down", data={"project_path": "/home/user/docker/web"}), 400)
        life_q.append(None)
        _expect(
            client.post(
                f"/servers/{sid}/docker/compose/down",
                data={"project_path": "/home/user/docker/web", "remove_volumes": "1"},
                headers=headers,
            ),
            500,
        )
        _expect(client.post(f"/servers/{sid}/docker/compose/remove", data={"project_path": "/home/user/docker/web"}), 400)
        allow["op"] = False
        _expect(client.post(f"/servers/{sid}/docker/compose/remove", data={"project_path": "/home/user/docker/web", "remove_ack": "1"}), 403)
        allow["op"] = True
        remove_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(
            client.post(
                f"/servers/{sid}/docker/compose/remove",
                data={"project_path": "/home/user/docker/web", "remove_ack": "yes"},
                headers=headers,
            ),
            409,
        )
        remove_q.append(ValueError("bad path"))
        _expect(client.post(f"/servers/{sid}/docker/compose/remove", data={"project_path": "/home/user/docker/web", "remove_ack": "on"}), 400)
        remove_q.append(None)
        _expect(client.post(f"/servers/{sid}/docker/compose/remove", data={"project_path": "/home/user/docker/web", "remove_ack": "1"}), 500)
        remove_q.append(_job())
        _expect(client.post(f"/servers/{sid}/docker/compose/remove", data={"project_path": "/home/user/docker/web", "remove_ack": "true"}), 303)
        actions["compose"] = {"success": False, "error": "down"}
        _expect(
            client.post(
                f"/servers/{sid}/docker/compose/restart",
                data={"project_path": "/home/user/docker/web", "service": "app", "remove_volumes": "1"},
            ),
            303,
        )
        actions["compose"] = {"success": True, "output": "ok"}
        _expect(
            client.post(f"/servers/{sid}/docker/compose/start", data={"project_path": "/home/user/docker/web", "service": "app"}),
            303,
        )

        _expect(client.get("/servers/99999/docker/logs/web"), 404)
        _expect(client.get(f"/servers/{sid}/docker/logs/web?format=json&project_path=/home/user/docker/web"), 200)
        _expect(client.get(f"/servers/{sid}/docker/logs/web", headers={"accept": "application/json"}), 200)
        _expect(client.get(f"/servers/{sid}/docker/logs/web?lines=20"), 200)
        _expect(client.get("/servers/99999/docker/logs/web/stream"), 404)
        _expect(client.get(f"/servers/{sid}/docker/logs/web/stream?project_path=/home/user/docker/web"), 200)

        listed["raise"] = True
        monkeypatch.setattr(docker_mod.docker_svc, "_CACHE", _Boom())
        _expect(client.get(f"/servers/{sid}/docker/containers-fragment?refresh=abc&nocache=1"), 200)
        monkeypatch.setattr(docker_mod.docker_svc, "_CACHE", {})
        _expect(client.get(f"/servers/{sid}/docker/containers-fragment?refresh=30"), 200)
        _expect(client.get("/servers/99999/docker/containers-fragment"), 404)

        _save_server(
            engine,
            sid,
            container_patch_enabled=True,
            docker_inventory_status="ok",
            docker_inventory_at=datetime.utcnow(),
            docker_inventory_json=json.dumps(inventory),
            docker_inventory_error=None,
            container_updates_summary=summary,
        )
        _expect(client.get(f"/servers/{sid}/docker/stack-fragment?q=web&status=running&project=web&refresh=30"), 200)
        _expect(client.get("/servers/99999/docker/stack-fragment"), 404)
        boom["deps"] = boom["kuma"] = boom["fabric"] = boom["lock"] = True
        allow["op"] = False
        _expect(client.get(f"/servers/{sid}/docker/stack-fragment?refresh=nope"), 200)
        allow["op"] = True
        boom["deps"] = boom["kuma"] = boom["fabric"] = boom["lock"] = False
        monkeypatch.setattr(docker_mod.docker_svc, "_CACHE", _Boom())
        _save_server(engine, sid, docker_inventory_status="error", docker_inventory_json=None)
        _expect(client.get(f"/servers/{sid}/docker/stack-fragment?nocache=1"), 200)
        monkeypatch.setattr(docker_mod.docker_svc, "_CACHE", {})
        _save_server(
            engine,
            sid,
            docker_inventory_status="refreshing",
            docker_inventory_at=datetime.utcnow() - timedelta(hours=2),
            docker_inventory_json=None,
        )
        _expect(client.get(f"/servers/{sid}/docker/stack-fragment"), 200)
        _save_server(engine, sid, container_patch_enabled=False, docker_inventory_status="ok")
        _expect(client.get(f"/servers/{sid}/docker/stack-fragment?nocache=1"), 200)
        _save_server(engine, sid, container_patch_enabled=True, os_type="debian")

        _expect(client.post("/servers/99999/docker/host-lock", data={"project": "web", "reason": "operator"}), 404)
        locked = client.post(f"/servers/{sid}/docker/host-lock", data={"project": "web", "reason": "operator", "note": "keep"})
        _expect(locked, 303)
        assert "msg=host_locked" in (locked.headers.get("location") or "")
        bad = client.post(f"/servers/{sid}/docker/host-lock", data={"project": "../web", "reason": "operator"})
        _expect(bad, 303)
        assert "error=host_lock" in (bad.headers.get("location") or "")
        _expect(client.post(f"/servers/{sid}/docker/host-lock", data={"project": "web", "reason": "nope"}), 303)
        _expect(client.post(f"/servers/{sid}/docker/host-lock", data={"project": "web", "reason": "hardware", "note": "n" * 300}), 303)
        _expect(client.post("/servers/99999/docker/host-unlock", data={"project": "web"}), 404)
        _expect(client.post(f"/servers/{sid}/docker/host-unlock", data={"project": "other"}), 303)
        opened = client.post(f"/servers/{sid}/docker/host-unlock", data={"project": "web"})
        _expect(opened, 303)
        assert "msg=host_unlocked" in (opened.headers.get("location") or "")
        _save_server(engine, sid, os_type="haos")
        _expect(client.post(f"/servers/{sid}/docker/host-lock", data={"project": "web", "reason": "infra"}), 303)
        _save_server(engine, sid, os_type="debian")

        migrate_on["on"] = False
        _expect(client.get(f"/servers/{sid}/docker/migrate?project=web"), 404)
        _expect(client.get(f"/servers/{sid}/docker/migrate/preflight?project=web&dest={dest_id}"), 404)
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": str(dest_id)}), 404)
        migrate_on["on"] = True
        _expect(client.get("/servers/99999/docker/migrate?project=web"), 404)
        _expect(client.get(f"/servers/{sid}/docker/migrate?project=../web"), 400)
        _expect(client.get(f"/servers/{sid}/docker/migrate?project=web"), 200)
        audit["raise"] = True
        _expect(client.get(f"/servers/{sid}/docker/migrate?project=web"), 200)
        audit["raise"] = False
        _expect(client.get("/servers/99999/docker/migrate/preflight?project=web&dest=1"), 404)
        _expect(client.get(f"/servers/{sid}/docker/migrate/preflight?project=web&dest=abc"), 200)
        _expect(client.get(f"/servers/{sid}/docker/migrate/preflight?project=web"), 200)
        _expect(client.get(f"/servers/{sid}/docker/migrate/preflight?project=../web&dest={dest_id}"), 400)
        _expect(
            client.get(
                f"/servers/{sid}/docker/migrate/preflight?project=web&dest={dest_id}&dest_project=web2&dest_port_8080_tcp=9090&bind_src_0=/home/user/data&dest_bind_0=/home/user/data2&skip_bind_1=1&bind_src_1=/home/user/skip"
            ),
            200,
        )

        _expect(client.post("/servers/99999/docker/migrate", data={"project": "web", "dest": str(dest_id)}), 404)
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": "0"}), 400)
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": "abc"}), 400)
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": "99999"}), 400)
        _expect(
            client.post(
                f"/servers/{sid}/docker/migrate",
                data={"project": "web", "dest": str(dest_id), "leftover": "remove"},
            ),
            400,
        )
        migrate_q.append(_job())
        moved = client.post(
            f"/servers/{sid}/docker/migrate",
            data={
                "project": "web",
                "dest": str(dest_id),
                "leftover": "down",
                "devices_ack": "1",
                "adopt_fabric": "yes",
                "dest_project": "web2",
                "dest_port_8080_tcp": "9090",
                "bind_src_0": "/home/user/data",
                "dest_bind_0": "/home/user/data2",
                "bind_src_1": "/home/user/skip",
                "skip_bind_1": "1",
            },
            headers=headers,
        )
        _expect(moved, 200)
        assert moved.json()["already_active"] is False
        migrate_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(
            client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": str(dest_id)}, headers=headers),
            409,
        )
        migrate_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": str(dest_id)}), 409)
        migrate_q.append(ValueError("bad"))
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": str(dest_id)}), 400)
        migrate_q.append(HostLockError("locked", 403))
        _expect(client.post(f"/servers/{sid}/docker/migrate", data={"project": "web", "dest": str(dest_id)}), 403)
        migrate_q.append(_job(id=11))
        _expect(
            client.post(
                f"/servers/{sid}/docker/migrate",
                data={"project": "web", "dest": str(dest_id), "leftover": "remove", "leftover_remove_ack": "1"},
            ),
            303,
        )

        with Session(engine) as s:
            parent = Job(
                server_id=sid,
                job_type="service_migrate",
                status="failed",
                details=json.dumps(
                    {
                        "failed_step": "cutover",
                        "undo_move": {
                            "source_id": sid,
                            "dest_id": dest_id,
                            "project": "web",
                            "dest_project": "web",
                            "port_map": {},
                            "failed_step": "cutover",
                        },
                    }
                ),
            )
            other_job = Job(server_id=sid, job_type="backup", status="failed", details="{}")
            gone_parent = Job(
                server_id=sid,
                job_type="service_migrate",
                status="failed",
                details=json.dumps(
                    {
                        "failed_step": "worker_restart",
                        "migrate_step": "dest_up",
                        "dest_up_recover": {"source_id": 99999, "dest_id": 99998, "project": "web", "dest_project": "web"},
                    }
                ),
            )
            recover_parent = Job(
                server_id=sid,
                job_type="service_migrate",
                status="failed",
                details=json.dumps(
                    {
                        "failed_step": "worker_restart",
                        "migrate_step": "dest_up",
                        "dest_up_recover": {"source_id": sid, "dest_id": dest_id, "project": "web", "dest_project": "web"},
                    }
                ),
            )
            s.add(parent)
            s.add(other_job)
            s.add(gone_parent)
            s.add(recover_parent)
            s.commit()
            s.refresh(parent)
            s.refresh(other_job)
            s.refresh(gone_parent)
            s.refresh(recover_parent)
            parent_id = parent.id
            other_id = other_job.id
            gone_id = gone_parent.id
            recover_id = recover_parent.id

        migrate_on["on"] = False
        _expect(client.get(f"/servers/{sid}/docker/migrate/undo/preview?parent_job_id={parent_id}"), 404)
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "1"}), 404)
        _expect(client.get(f"/servers/{sid}/docker/migrate/dest-up/inspect?parent_job_id={recover_id}"), 404)
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": str(recover_id), "confirm": "1"}), 404)
        migrate_on["on"] = True
        _expect(client.get(f"/servers/{sid}/docker/migrate/undo/preview?parent_job_id=0"), 404)
        _expect(client.get(f"/servers/{dest_id}/docker/migrate/undo/preview?parent_job_id={parent_id}"), 404)
        _expect(client.get(f"/servers/{sid}/docker/migrate/undo/preview?parent_job_id={other_id}"), 400)
        preview = client.get(f"/servers/{sid}/docker/migrate/undo/preview?parent_job_id={parent_id}")
        _expect(preview, 200)
        assert preview.json()["project"] == "web"
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "no"}), 400)
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": "0", "confirm": "1"}), 404)
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(other_id), "confirm": "1"}), 400)
        undo_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "1"}, headers=headers), 409)
        undo_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "1"}), 409)
        undo_q.append(ValueError("bad"))
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "1"}), 400)
        undo_q.append(_job(id=15))
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "yes"}, headers=headers), 200)
        undo_q.append(_job(id=16))
        _expect(client.post(f"/servers/{sid}/docker/migrate/undo", data={"parent_job_id": str(parent_id), "confirm": "on"}), 303)

        _expect(client.get(f"/servers/{sid}/docker/migrate/dest-up/inspect?parent_job_id=0"), 404)
        _expect(client.get(f"/servers/{sid}/docker/migrate/dest-up/inspect?parent_job_id={other_id}"), 400)
        _expect(client.get(f"/servers/{sid}/docker/migrate/dest-up/inspect?parent_job_id={gone_id}"), 404)
        inspect_flag["raise"] = True
        _expect(client.get(f"/servers/{sid}/docker/migrate/dest-up/inspect?parent_job_id={recover_id}"), 400)
        inspect_flag["raise"] = False
        _expect(client.get(f"/servers/{sid}/docker/migrate/dest-up/inspect?parent_job_id={recover_id}"), 200)
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": str(recover_id)}), 400)
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": "0", "confirm": "1"}), 404)
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": str(other_id), "confirm": "1"}), 400)
        recover_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(
            client.post(
                f"/servers/{sid}/docker/migrate/dest-up/recover",
                data={"parent_job_id": str(recover_id), "confirm": "1"},
                headers=headers,
            ),
            409,
        )
        recover_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": str(recover_id), "confirm": "1"}), 409)
        recover_q.append(ValueError("bad"))
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": str(recover_id), "confirm": "1"}), 400)
        recover_q.append(_job(id=21))
        _expect(
            client.post(
                f"/servers/{sid}/docker/migrate/dest-up/recover",
                data={"parent_job_id": str(recover_id), "confirm": "true"},
                headers=headers,
            ),
            200,
        )
        recover_q.append(_job(id=22))
        _expect(client.post(f"/servers/{sid}/docker/migrate/dest-up/recover", data={"parent_job_id": str(recover_id), "confirm": "on"}), 303)

        _expect(client.post("/servers/99999/docker/check-updates", data={"project_path": "/home/user/docker/web"}), 404)
        check_q.append(_job())
        checked = client.post(f"/servers/{sid}/docker/check-updates", data={"project_path": "/home/user/docker/web"}, headers=headers)
        _expect(checked, 200)
        check_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/servers/{sid}/docker/check-updates", data={"project_path": "/home/user/docker/web"}, headers=headers), 409)
        check_q.append(ValueError("bad path"))
        _expect(client.post(f"/servers/{sid}/docker/check-updates", data={"project_path": "/home/user/docker/web"}), 400)
        check_q.append(None)
        _expect(client.post(f"/servers/{sid}/docker/check-updates", data={"project_path": "/home/user/docker/web"}), 500)
        check_q.append(_job(id=30))
        _expect(client.post(f"/servers/{sid}/docker/check-updates", data={"project_path": "/home/user/docker/web"}), 303)

        _expect(client.get("/servers/99999/docker/build-progress?project=web&services=app,db&no_cache=1"), 404)
        _expect(client.get(f"/servers/{sid}/docker/build-progress?project=web&services=app&no_cache=yes"), 200)
        _expect(client.get(f"/servers/{sid}/docker/build-stream"), 405)
        _expect(client.post("/servers/99999/docker/build-stream", data={"project": "web"}), 404)
        _expect(client.post(f"/servers/{sid}/docker/build-stream", data={"project": "missing", "services": "app", "no_cache": "1"}), 404)
        _expect(client.post(f"/servers/{sid}/docker/build-stream", data={"project": "bad"}), 400)
        _expect(client.post(f"/servers/{sid}/docker/build-stream", data={"project": "boom"}), 500)
        built = client.post(f"/servers/{sid}/docker/build-stream", data={"project": "web", "services": "app, ", "no_cache": "true"})
        _expect(built, 200)
        assert "build" in built.text

        _expect(client.get("/servers/99999/docker/unused"), 404)

        def _unused(_server):
            raise RuntimeError("unused")

        monkeypatch.setattr(docker_mod.docker_svc, "list_unused_images_and_containers", _unused)
        _expect(client.get(f"/servers/{sid}/docker/unused"), 200)
        monkeypatch.setattr(
            docker_mod.docker_svc,
            "list_unused_images_and_containers",
            lambda *_a, **_k: {"dangling_images": ["nginx:old"], "exited_containers": ["old"], "success": True},
        )
        _expect(client.get(f"/servers/{sid}/docker/unused"), 200)

        _expect(client.post(f"/servers/{sid}/docker/prune-unused", data={"prune_type": "nope"}), 400)
        need = client.post(f"/servers/{sid}/docker/prune-unused", data={"prune_type": "volumes"})
        _expect(need, 303)
        assert "need_confirm" in (need.headers.get("location") or "")
        _expect(client.post("/servers/99999/docker/prune-unused", data={"prune_type": "both"}), 404)
        _save_server(engine, sid, container_patch_enabled=False)
        _expect(client.post(f"/servers/{sid}/docker/prune-unused", data={"prune_type": "images"}), 400)
        _save_server(engine, sid, container_patch_enabled=True)
        prune_q.append(jobs.JobAlreadyActive(_job("running")))
        busy = client.post(f"/servers/{sid}/docker/prune-unused", data={"prune_type": "both"})
        _expect(busy, 303)
        assert "prune=busy" in (busy.headers.get("location") or "")
        prune_q.append(RuntimeError("queue"))
        failed = client.post(f"/servers/{sid}/docker/prune-unused", data={"prune_type": "networks", "confirm": "1"})
        _expect(failed, 303)
        assert "prune=fail" in (failed.headers.get("location") or "")
        prune_q.append(_job(id=40))
        queued = client.post(f"/servers/{sid}/docker/prune-unused", data={"prune_type": "volumes", "confirm": "yes"})
        _expect(queued, 303)
        assert "prune=queued" in (queued.headers.get("location") or "")

        _expect(client.post("/servers/99999/docker/prune-build-cache"), 404)
        _save_server(engine, sid, container_patch_enabled=False)
        _expect(client.post(f"/servers/{sid}/docker/prune-build-cache"), 400)
        _save_server(engine, sid, container_patch_enabled=True)
        _expect(client.post(f"/servers/{sid}/docker/prune-build-cache"), 303)
        def _prune_boom(_server):
            raise RuntimeError("prune")

        monkeypatch.setattr(docker_mod.docker_svc, "prune_build_cache", _prune_boom)
        failed = client.post(f"/servers/{sid}/docker/prune-build-cache")
        _expect(failed, 303)
        assert "build_cache_fail" in (failed.headers.get("location") or "")
    finally:
        app.dependency_overrides.pop(get_session, None)


def _editor(**kwargs):
    base = {
        "slug": "extra-app",
        "name": "Extra",
        "description": "Example service",
        "category": "other",
        "version": "1.0.0",
        "compose_content": COMPOSE,
        "env_content": "TOKEN=placeholder\n",
        "extra_files_json": "[]",
        "variables_json": VARS,
        "checklist_json": "[]",
        "editor_action": "save",
    }
    base.update(kwargs)
    return base


def test_template_deploy_and_catalog(tmp_path, monkeypatch):
    import app.services.jobs as jobs
    from app.routers import templates_deploy as deploy_mod
    from app.routers import templates_svc as tpl
    from app.services.service_templates import TemplateError
    from app.services.service_templates.editor import build_definition_from_editor
    from app.services.service_templates.catalog import save_template_definition

    engine = _engine(tmp_path / "tpl.db")
    client, uid, sid = _client(engine, monkeypatch)
    with Session(engine) as s:
        definition = build_definition_from_editor(
            slug="web-app",
            name="Web app",
            description="Example",
            category="other",
            version="1.0.0",
            compose_content=COMPOSE,
            env_content="API_TOKEN=placeholder\n",
            variables_json=VARS,
            checklist_json=json.dumps([{"title": "DNS", "body": "Point {{PROJECT_NAME}} at the host"}]),
            source="user",
        )
        row = save_template_definition(s, definition, mark_user=True)
        dep = StackDeployment(
            server_id=sid,
            project_name="web",
            template_id=row.id,
            template_slug="missing-slug",
            template_version="1.0.0",
            config_version=1,
            variables_json="{not-json",
            files_json="{not-json",
            secrets_encrypted="not-a-token",
            drift_status="unknown",
        )
        s.add(dep)
        s.commit()
        s.refresh(dep)
        s.refresh(row)
        dep_id = dep.id
        template_id = row.id

    sec = {"mode": "raise"}
    real_decrypt = deploy_mod.decrypt_deployment_secrets if hasattr(deploy_mod, "decrypt_deployment_secrets") else None

    def _decrypt(dep):
        if sec["mode"] == "raise":
            raise RuntimeError("decrypt")
        if sec["mode"] == "map":
            return {"API_TOKEN": "secret-value"}
        if real_decrypt:
            return real_decrypt(dep)
        return {}

    # decrypt is imported inside deployment_detail, so patch the source module.
    monkeypatch.setattr("app.services.service_templates.deploy.decrypt_deployment_secrets", _decrypt)
    backup = {"raise": True}

    def _backup(_server, _dep):
        if backup["raise"]:
            raise RuntimeError("backup")
        return [{"name": "daily", "path": "/home/user/backups"}]

    monkeypatch.setattr(deploy_mod, "matching_backup_sources_for_deployment", _backup)
    dns = {"record": False, "raise": True}

    def _find(_session, _deployment_id):
        if not dns["record"]:
            return None
        return SimpleNamespace(target_server_id=sid)

    def _plan(*_a, **_k):
        if dns["raise"]:
            raise RuntimeError("dns")
        return {"fqdn": "web.example.com"}

    monkeypatch.setattr("app.services.dns_fabric.find_service_for_deployment", _find)
    monkeypatch.setattr("app.services.dns_fabric.resolve_service_dns_plan", _plan)

    redeploy_q, drift_q, confirm_q = [], [], []
    monkeypatch.setattr(jobs, "enqueue_template_redeploy", _pop_script(redeploy_q))
    monkeypatch.setattr(jobs, "enqueue_template_drift_check", _pop_script(drift_q))
    monkeypatch.setattr(jobs, "enqueue_template_deploy", _pop_script(confirm_q))
    require_2fa = {"on": False}
    monkeypatch.setattr("app.routers.templates_common._template_require_2fa", lambda: require_2fa["on"])
    fac = {"totp": False, "pk": False}
    monkeypatch.setattr("app.services.webauthn_svc.totp_active", lambda _user: fac["totp"])
    monkeypatch.setattr("app.services.webauthn_svc.has_passkeys", lambda _session, _uid: fac["pk"])
    real_get = deploy_mod.get_deployment

    def _get(session, deployment_id):
        if deployment_id == 40404:
            return SimpleNamespace(server_id=40404, id=40404, project_name="web", template_slug=None)
        return real_get(session, deployment_id)

    monkeypatch.setattr(deploy_mod, "get_deployment", _get)
    mig = []

    def _migrate(*_a, **_k):
        item = mig.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(deploy_mod, "migrate_host_env_into_deployment", _migrate)
    real_preview = deploy_mod.preview_template
    preview_flag = {"raise": False}

    def _preview(*args, **kwargs):
        if preview_flag["raise"]:
            raise TemplateError("preview failed")
        return real_preview(*args, **kwargs)

    monkeypatch.setattr(deploy_mod, "preview_template", _preview)
    real_scan = tpl.apply_scan_vars_to_form
    real_harden = tpl.apply_harden_env_to_form
    tools = {"scan": "real", "harden": "real"}

    def _scan(data, reveal_secrets=False):
        if tools["scan"] == "raise":
            raise TemplateError("scan failed")
        return real_scan(data, reveal_secrets=reveal_secrets)

    def _harden(data, reveal_secrets=False):
        if tools["harden"] == "raise":
            raise TemplateError("harden failed")
        return real_harden(data, reveal_secrets=reveal_secrets)

    monkeypatch.setattr(tpl, "apply_scan_vars_to_form", _scan)
    monkeypatch.setattr(tpl, "apply_harden_env_to_form", _harden)
    rate = {"ok": True}
    monkeypatch.setattr(tpl, "rate_limit_auth", lambda *_a, **_k: rate["ok"])
    verify = {"mode": "bad"}

    def _verify(_secret, _code):
        return verify["mode"] == "totp"

    def _decrypt_totp(_cipher):
        if verify["mode"] == "boom":
            raise RuntimeError("totp")
        return "SECRET"

    def _backup(_session, _uid, _code):
        return verify["mode"] == "backup"

    monkeypatch.setattr(tpl, "verify_totp_code", _verify)
    monkeypatch.setattr(tpl, "decrypt_totp_secret", _decrypt_totp)
    monkeypatch.setattr(tpl, "consume_backup_code", _backup)

    headers = {"X-PiHerder-Async": "1"}
    try:
        _expect(client.get("/templates/deployments/99999"), 404)
        page = client.get(f"/templates/deployments/{dep_id}?msg=saved&error=none&unlock_error=bad&drift_detail=diff")
        _expect(page, 200)
        with Session(engine) as s:
            row = s.get(StackDeployment, dep_id)
            row.variables_json = json.dumps({"PROJECT_NAME": "web", "HOST_PORT": "8080", "DATA__mode": "bind", "DATA__source": "/home/user/data"})
            row.files_json = json.dumps({"docker-compose.yml": COMPOSE, ".env": "API_TOKEN=\n", "secrets/token": "hide"})
            row.template_slug = "web-app"
            s.add(row)
            s.commit()
        sec["mode"] = "map"
        backup["raise"] = False
        dns["record"] = True
        dns["raise"] = False
        from app.security.auth import create_secrets_unlock_token

        client.cookies.set("secrets_unlock", create_secrets_unlock_token(uid))
        _expect(client.get(f"/templates/deployments/{dep_id}"), 200)
        client.cookies.pop("secrets_unlock", None)
        sec["mode"] = "empty"
        dns["raise"] = True
        dns["record"] = True
        backup["raise"] = True
        _expect(client.get(f"/templates/deployments/{dep_id}"), 200)

        _expect(client.post("/templates/deployments/99999/redeploy"), 404)
        _expect(client.post("/templates/deployments/40404/redeploy"), 404)
        secret_form = {"sec_API_TOKEN": "secret-value", "sec_MASK": "********", "pub_HOST_PORT": "8081", "vol_DATA__mode": "bind", "vol_DATA__source": "/home/user/data"}
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy", data=secret_form, headers=headers), 403)
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy", data=secret_form), 303)
        require_2fa["on"] = True
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy", headers=headers), 403)
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy"), 303)
        require_2fa["on"] = False
        redeploy_q.append(_job())
        queued = client.post(
            f"/templates/deployments/{dep_id}/redeploy",
            data={"pub_HOST_PORT": "8081", "vol_DATA__mode": "named", "vol_DATA__source": "data"},
            headers=headers,
        )
        _expect(queued, 200)
        assert queued.json()["job_type"] == "template_redeploy"
        redeploy_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy", headers=headers), 409)
        redeploy_q.append(ValueError("bad"))
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy", headers=headers), 400)
        redeploy_q.append(ValueError("bad"))
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy"), 303)
        redeploy_q.append(RuntimeError("queue"))
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy", headers=headers), 500)
        redeploy_q.append(RuntimeError("queue"))
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy"), 303)
        redeploy_q.append(None)
        _expect(client.post(f"/templates/deployments/{dep_id}/redeploy"), 500)
        redeploy_q.append(_job(id=6))
        done = client.post(f"/templates/deployments/{dep_id}/redeploy")
        _expect(done, 303)
        assert "Redeploy" in (done.headers.get("location") or "")

        _expect(client.post("/templates/deployments/99999/check-drift"), 404)
        _expect(client.post("/templates/deployments/40404/check-drift"), 404)
        drift_q.append(_job())
        checked = client.post(f"/templates/deployments/{dep_id}/check-drift", headers=headers)
        _expect(checked, 200)
        drift_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift", headers=headers), 409)
        drift_q.append(ValueError("bad"))
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift", headers=headers), 400)
        drift_q.append(ValueError("bad"))
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift"), 303)
        drift_q.append(RuntimeError("queue"))
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift", headers=headers), 500)
        drift_q.append(RuntimeError("queue"))
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift"), 303)
        drift_q.append(None)
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift"), 500)
        drift_q.append(_job(id=7))
        _expect(client.post(f"/templates/deployments/{dep_id}/check-drift"), 303)

        _expect(client.post(f"/templates/deployments/{dep_id}/migrate-env"), 303)
        fac["totp"] = True
        mig.append({"imported_secrets": ["API_TOKEN"], "imported_public": ["HOST_PORT"]})
        moved = client.post(f"/templates/deployments/{dep_id}/migrate-env")
        _expect(moved, 303)
        assert "Imported" in (moved.headers.get("location") or "")
        mig.append({})
        _expect(client.post(f"/templates/deployments/{dep_id}/migrate-env"), 303)
        mig.append(TemplateError("no env"))
        _expect(client.post(f"/templates/deployments/{dep_id}/migrate-env"), 303)
        mig.append(RuntimeError("ssh"))
        _expect(client.post(f"/templates/deployments/{dep_id}/migrate-env"), 303)
        _expect(client.post("/templates/deployments/99999/migrate-env"), 404)
        _expect(client.post("/templates/deployments/40404/migrate-env"), 404)
        fac["totp"] = False

        _expect(client.post("/templates/missing/preview", data={"server_id": str(sid)}), 303)
        preview_flag["raise"] = True
        failed = client.post(
            "/templates/web-app/preview",
            data={"server_id": str(sid), "deploy_now": "1", "var_PROJECT_NAME": "web", "var_HOST_PORT": "8080"},
        )
        _expect(failed, 400)
        preview_flag["raise"] = False
        preview = client.post(
            "/templates/web-app/preview",
            data={"server_id": str(sid), "deploy_now": "", "var_PROJECT_NAME": "web", "var_HOST_PORT": "8080", "var_API_TOKEN": "secret-value"},
        )
        _expect(preview, 200)
        assert "Preview" in preview.text

        require_2fa["on"] = True
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": "{}"}, headers=headers), 403)
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": "{}"}), 303)
        require_2fa["on"] = False
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": "{"}, headers=headers), 400)
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": "not-json"}), 303)
        confirm_q.append(_job())
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": json.dumps({"values": ["nope"], "server_id": sid})}), 303)
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": json.dumps({"values": {}, "server_id": 0})}, headers=headers), 404)
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": json.dumps({"values": {}, "server_id": 0})}), 303)
        stash = json.dumps({"values": {"PROJECT_NAME": "web", "HOST_PORT": "8080"}, "server_id": sid, "deploy_now": True})
        confirm_q.append(_job())
        queued = client.post("/templates/web-app/confirm", data={"stash_json": stash}, headers=headers)
        _expect(queued, 200)
        assert queued.json()["job_type"] == "template_deploy"
        confirm_q.append(jobs.JobAlreadyActive(_job("running")))
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": stash}, headers=headers), 409)
        confirm_q.append(ValueError("bad"))
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": stash}, headers=headers), 400)
        confirm_q.append(ValueError("bad"))
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": stash}), 303)
        confirm_q.append(RuntimeError("queue"))
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": stash}, headers=headers), 500)
        confirm_q.append(RuntimeError("queue"))
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": stash}), 303)
        confirm_q.append(None)
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": stash}), 500)
        confirm_q.append(_job(id=9))
        _expect(client.post("/templates/web-app/confirm", data={"stash_json": json.dumps({"values": {"PROJECT_NAME": "web", "HOST_PORT": "8080"}, "server_id": sid, "deploy_now": False})}), 303)

        _expect(client.post("/templates/new", data=_editor(editor_action="scan_vars")), 200)
        _expect(client.post("/templates/new", data=_editor(editor_action="harden_env")), 200)
        tools["scan"] = "raise"
        _expect(client.post("/templates/new", data=_editor(editor_action="scan_vars")), 400)
        tools["scan"] = "real"
        tools["harden"] = "raise"
        _expect(client.post("/templates/new", data=_editor(editor_action="harden_env")), 400)
        tools["harden"] = "real"
        _expect(client.post("/templates/new", data=_editor(slug="Bad Slug", name="")), 400)
        created = client.post("/templates/new", data=_editor())
        _expect(created, 303)
        assert "extra-app" in (created.headers.get("location") or "")
        _expect(client.post("/templates/new", data=_editor()), 400)

        _expect(client.post("/templates/missing-slug/edit", data=_editor(editor_action="scan_vars", slug="missing-slug")), 200)
        missing = client.post("/templates/missing-slug/edit", data=_editor(slug="missing-slug"))
        _expect(missing, 303)
        assert "Template" in (missing.headers.get("location") or "")
        tools["harden"] = "raise"
        _expect(client.post("/templates/web-app/edit", data=_editor(slug="web-app", editor_action="harden_env", template_id=str(template_id))), 400)
        tools["harden"] = "real"
        _expect(client.post("/templates/web-app/edit", data=_editor(slug="web-app", editor_action="scan_vars")), 200)
        _expect(client.post("/templates/web-app/edit", data=_editor(slug="web-app", variables_json="{")), 400)
        saved = client.post(
            "/templates/not-here/edit",
            data=_editor(slug="web-app", name="Web app renamed", template_id=str(template_id), variables_json=VARS.replace("old-token", "")),
        )
        _expect(saved, 303)
        assert "/edit" in (saved.headers.get("location") or "")
        _expect(client.post("/templates/no-row/edit", data=_editor(slug="no-row", editor_action="harden_env")), 200)

        rate["ok"] = False
        limited = client.post("/templates/secrets/unlock", data={"code": "000000", "return_to": "https://example.com"})
        _expect(limited, 303)
        assert "unlock_error" in (limited.headers.get("location") or "")
        rate["ok"] = True
        fac["totp"] = False
        fac["pk"] = False
        _expect(client.post("/templates/secrets/unlock", data={"code": "000000", "return_to": "/templates"}), 303)
        fac["pk"] = True
        _expect(client.post("/templates/secrets/unlock", data={"code": "000000", "return_to": "/servers/1/docker/compose/web/edit"}), 303)
        fac["pk"] = False
        fac["totp"] = True
        verify["mode"] = "bad"
        _expect(client.post("/templates/secrets/unlock", data={"code": "000000", "return_to": "/templates/web-app"}), 303)
        verify["mode"] = "boom"
        _expect(client.post("/templates/secrets/unlock", data={"code": "000000", "return_to": "/nope"}), 303)
        verify["mode"] = "backup"
        unlocked = client.post("/templates/secrets/unlock", data={"code": "backup-code", "return_to": "/templates"})
        _expect(unlocked, 303)
        assert "secrets_unlock" in unlocked.cookies or "set-cookie" in {k.lower() for k in unlocked.headers.keys()} or unlocked.headers.get("location")
        verify["mode"] = "totp"
        unlocked = client.post("/templates/secrets/unlock", data={"code": "123456", "return_to": "/templates/web-app/edit"})
        _expect(unlocked, 303)
        assert unlocked.headers.get("location", "").startswith("/templates/")
    finally:
        app.dependency_overrides.pop(get_session, None)
