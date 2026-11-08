"""v1.14 coverage for compose editor and repository routes."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlmodel import Session
from starlette.requests import Request

from app.database import get_engine
from app.models import Server, User
from app.routers import server_docker_compose as routes
from app.security.auth import get_password_hash
from app.services.git_project import GitProjectError


def _run(coro):
    return asyncio.run(coro)


def _request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/servers/1/docker",
        "raw_path": b"/servers/1/docker",
        "query_string": b"",
        "headers": [(b"host", b"example.com")],
        "client": ("192.0.2.10", 443),
        "server": ("example.com", 443),
        "root_path": "",
    }
    return Request(scope)


def test_compose_save_create_and_repository(monkeypatch):
    from app.services import docker_management as docker_svc

    class _Ver:
        version = 3
        is_draft = True
        deployed_at = None

    monkeypatch.setattr(routes, "_invalidate_inventory", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("inv")))
    monkeypatch.setattr(
        docker_svc,
        "list_compose_projects",
        lambda *_a, **_k: [{"name": "app", "path": "/home/user/app", "dockerfile_path": "/home/user/app/Dockerfile"}],
    )
    monkeypatch.setattr(routes, "_snapshot_for_save", lambda *_a, **_k: {"Dockerfile": "FROM example\n"})
    monkeypatch.setattr(docker_svc, "save_draft_version", lambda *_a, **_k: _Ver())
    writes = {"ok": False, "err": "denied"}

    def _write(*_a, **_k):
        return writes["ok"], writes["err"]

    monkeypatch.setattr(docker_svc, "write_dockerfile", _write)
    monkeypatch.setattr(docker_svc, "create_new_docker_project", lambda *_a, **_k: True)
    monkeypatch.setattr(docker_svc, "redeploy_project", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("deploy")))

    with Session(get_engine()) as session:
        user = User(
            email="compose@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        server = Server(name="Host A", hostname="host-a.example.com", ssh_username="user")
        session.add(user)
        session.add(server)
        session.commit()
        session.refresh(user)
        session.refresh(server)
        sid = server.id

        with pytest.raises(HTTPException):
            _run(
                routes.save_dockerfile(
                    999999, "app", "FROM example\n", "deploy", None, "false", _request(), session, user
                )
            )
        draft = _run(
            routes.save_dockerfile(sid, "app", "FROM example\n", "draft", None, "true", _request(), session, user)
        )
        assert draft.status_code == 200
        denied = _run(
            routes.save_dockerfile(sid, "app", "FROM example\n", "deploy", None, "true", _request(), session, user)
        )
        assert denied.status_code == 200
        writes["ok"] = True
        saved = _run(
            routes.save_dockerfile(sid, "app", "FROM example\n", "deploy", None, "false", _request(), session, user)
        )
        assert saved.status_code == 303
        monkeypatch.setattr(docker_svc, "save_draft_version", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db")))
        blown = _run(
            routes.save_dockerfile(sid, "app", "FROM example\n", "deploy", None, "true", _request(), session, user)
        )
        assert blown.status_code == 200

        created = _run(
            routes.create_docker_project(
                sid, "app", "services: {}\n", "FROM example\n", "https://example.com/app.git", "1", session, user
            )
        )
        assert created.status_code == 303
        with pytest.raises(HTTPException):
            _run(routes.create_docker_project(999999, "app", "x", "", "", None, session, user))

        def _inspect(*_a, **_k):
            return {"repo": True, "project": "app", "dirty": [], "tags": [], "branches": ["main"]}

        def _attach(*_a, **_k):
            return {"result": "attached"}

        def _update(*_a, **_k):
            return {"result": "conflict", "dirty": ["compose.yml"], "ahead": 1, "detail": "local"}

        monkeypatch.setattr("app.services.git_project.inspect_project", _inspect)
        monkeypatch.setattr("app.services.git_project.attach_remote", _attach)
        monkeypatch.setattr("app.services.git_project.update_project", _update)
        monkeypatch.setattr("app.services.demo.http_403_if_demo", lambda *_a, **_k: None)

        page = _run(routes.docker_repository(_request(), sid, "app", session, user))
        assert page.status_code == 200
        monkeypatch.setattr(
            "app.services.git_project.inspect_project",
            lambda *_a, **_k: (_ for _ in ()).throw(GitProjectError("unowned")),
        )
        err = _run(routes.docker_repository(_request(), sid, "app", session, user))
        assert err.status_code == 200
        monkeypatch.setattr("app.services.git_project.inspect_project", _inspect)
        attached = _run(routes.docker_repository_attach(_request(), sid, "app", "https://example.com/app.git", session, user))
        assert attached.status_code == 200
        monkeypatch.setattr(
            "app.services.git_project.attach_remote",
            lambda *_a, **_k: {"result": "unowned"},
        )
        unowned = _run(routes.docker_repository_attach(_request(), sid, "app", "https://example.com/app.git", session, user))
        assert unowned.status_code == 200
        updated = _run(
            routes.docker_repository_update(_request(), sid, "app", "main", "", "theirs", session, user)
        )
        assert updated.status_code == 200
        monkeypatch.setattr(
            "app.services.git_project.update_project",
            lambda *_a, **_k: (_ for _ in ()).throw(GitProjectError("dirty")),
        )
        conflict = _run(
            routes.docker_repository_update(_request(), sid, "app", "", "v1", "", session, user)
        )
        assert conflict.status_code == 200
