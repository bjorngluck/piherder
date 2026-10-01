"""Hosted MCP at POST /mcp — auth, handshake, and one tool call."""
from __future__ import annotations

import json
import logging


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from app.database import get_session
    from app.main import app
    from app.models import User
    from app.security.auth import get_password_hash
    from app.services import api_tokens as tok

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.demo.reject_if_demo", lambda *_a, **_k: None)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        user = User(
            email="mcp-host@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        _read, read_plain = tok.create_api_token(
            s, name="mcp-read", created_by=user, scopes=["read"]
        )
        _jobs, jobs_plain = tok.create_api_token(
            s, name="mcp-jobs", created_by=user, scopes=["read", "jobs", "edit", "files"]
        )
        _noread, noread_plain = tok.create_api_token(
            s, name="mcp-noread", created_by=user, scopes=["jobs"]
        )
        _locked, locked_plain = tok.create_api_token(
            s,
            name="mcp-locked",
            created_by=user,
            scopes=["read"],
            allowed_cidrs=["10.0.0.0/8"],
        )
    return client, app, engine, {
        "read": read_plain,
        "jobs": jobs_plain,
        "noread": noread_plain,
        "locked": locked_plain,
    }


def _rpc(client, token: str | None, method: str, params: dict | None = None, **extra):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra.pop("headers", {}))
    return client.post("/mcp", json=body, headers=headers, **extra)


def test_unauthenticated_and_bad_token_fail_clean(tmp_path, monkeypatch, caplog):
    client, app, _engine, _secrets = _client(tmp_path, monkeypatch)
    caplog.set_level(logging.DEBUG)
    try:
        missing = _rpc(client, None, "initialize", {"protocolVersion": "2025-03-26"})
        assert missing.status_code == 401
        assert "Bearer" in (missing.headers.get("www-authenticate") or "")
        assert "resource_metadata" not in (missing.headers.get("www-authenticate") or "").lower()
        assert missing.json()["detail"]
        assert "ph_notareal" not in missing.text

        bogus = "ph_notarealtokenvalue000000000000000"
        bad = _rpc(client, bogus, "initialize", {"protocolVersion": "2025-03-26"})
        assert bad.status_code == 401
        assert bogus not in bad.text
        assert bogus not in caplog.text

        leaked = "ph_querysecretvalue000000000000000"
        q = client.post(
            f"/mcp?token={leaked}",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Bearer ph_ignored", "Content-Type": "application/json"},
        )
        assert q.status_code == 400
        assert leaked not in q.text
        assert "query string" in q.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_ip_allowlist_and_origin_and_methods(tmp_path, monkeypatch):
    client, app, _engine, secrets = _client(tmp_path, monkeypatch)
    try:
        locked = _rpc(client, secrets["locked"], "initialize", {"protocolVersion": "2025-03-26"})
        assert locked.status_code == 403
        assert "not allowed" in locked.json()["detail"].lower()
        assert secrets["locked"] not in locked.text

        origin = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Origin": "https://evil.example",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # A Bearer token does not allow a foreign http(s) Origin.
        assert origin.status_code == 403

        same_host = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Origin": "http://testserver",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        assert same_host.status_code == 200

        null_origin = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Origin": "null",
                "Content-Type": "application/json",
            },
        )
        assert null_origin.status_code == 403

        bare = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Origin": "https://evil.example", "Content-Type": "application/json"},
        )
        assert bare.status_code == 403

        get_r = client.get("/mcp")
        assert get_r.status_code == 405
        assert "POST" in (get_r.headers.get("allow") or "")
        delete_r = client.delete("/mcp")
        assert delete_r.status_code == 405
    finally:
        app.dependency_overrides.clear()


def test_initialize_tools_and_health_match_api(tmp_path, monkeypatch):
    client, app, _engine, secrets = _client(tmp_path, monkeypatch)
    try:
        note = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert note.status_code == 202
        assert note.content == b""

        init = _rpc(
            client,
            secrets["read"],
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        assert init.status_code == 200, init.text
        body = init.json()
        assert body["result"]["protocolVersion"] == "2025-03-26"
        assert body["result"]["serverInfo"]["name"] == "piherder"
        assert body["result"]["capabilities"]["tools"]["listChanged"] is False
        assert secrets["read"] not in init.text

        listed = _rpc(client, secrets["read"], "tools/list")
        names = [tool["name"] for tool in listed.json()["result"]["tools"]]
        assert names[:8] == [
            "health",
            "summary",
            "list_servers",
            "get_server",
            "inventory",
            "services",
            "list_jobs",
            "get_job",
        ]
        assert "trigger_job" not in names
        assert "set_features" not in names
        assert "write_file" not in names
        health_tool = next(t for t in listed.json()["result"]["tools"] if t["name"] == "health")
        assert health_tool["annotations"]["readOnlyHint"] is True

        called = _rpc(
            client,
            secrets["read"],
            "tools/call",
            {"name": "health", "arguments": {}},
        )
        assert called.status_code == 200, called.text
        result = called.json()["result"]
        assert result["isError"] is False
        parsed = json.loads(result["content"][0]["text"])
        direct = client.get(
            "/api/v1/health",
            headers={"Authorization": f"Bearer {secrets['read']}"},
        )
        assert direct.status_code == 200
        assert parsed["ok"] is True
        assert parsed["scopes"] == direct.json()["scopes"]
        assert result["structuredContent"]["ok"] is True

        hidden = _rpc(
            client,
            secrets["read"],
            "tools/call",
            {"name": "trigger_job", "arguments": {"server_id": 1, "job_type": "backup"}},
        )
        hidden_body = hidden.json()["result"]
        assert hidden_body["isError"] is True
        assert "Unknown tool" in hidden_body["content"][0]["text"]
    finally:
        app.dependency_overrides.clear()


def test_scope_filter_jobs_and_sse_and_noread(tmp_path, monkeypatch):
    client, app, _engine, secrets = _client(tmp_path, monkeypatch)
    try:
        listed = _rpc(client, secrets["jobs"], "tools/list")
        tools = listed.json()["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        assert "trigger_job" in by_name
        assert "set_features" in by_name
        assert "delete_file" in by_name
        assert by_name["trigger_job"]["annotations"]["destructiveHint"] is True
        assert by_name["read_file"]["annotations"]["readOnlyHint"] is True
        assert by_name["write_file"]["annotations"]["destructiveHint"] is True
        assert "p" in by_name["write_file"]["inputSchema"]["required"]
        assert "p" in by_name["mkdir"]["inputSchema"]["required"]
        enum = by_name["trigger_job"]["inputSchema"]["properties"]["job_type"]["enum"]
        assert "backup" in enum
        assert "host_reboot" in enum
        assert "docker_stack_restart" in enum
        assert "template_redeploy" in enum
        assert "service_migrate" not in enum
        assert "docker_stack_down" not in enum
        assert "template_drift_check" not in enum

        rejected = _rpc(
            client,
            secrets["jobs"],
            "tools/call",
            {"name": "trigger_job", "arguments": {"server_id": 1, "job_type": "service_migrate"}},
        )
        err = rejected.json()["result"]
        assert err["isError"] is True
        assert "must be one of" in err["content"][0]["text"]
        assert "service_migrate" not in err["content"][0]["text"] or "must be one of" in err["content"][0]["text"]

        noread = _rpc(client, secrets["noread"], "initialize", {"protocolVersion": "2025-06-18"})
        assert noread.status_code == 200
        assert noread.json()["error"]["message"].startswith("API token missing scope: read")

        sse = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 7, "method": "ping"},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        assert sse.status_code == 200
        assert "text/event-stream" in (sse.headers.get("content-type") or "")
        assert sse.text.startswith("event: message\n")
        data_line = next(line for line in sse.text.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["id"] == 7
        assert payload["result"] == {}

        resources = _rpc(client, secrets["read"], "resources/list")
        assert resources.json()["result"] == {"resources": []}
    finally:
        app.dependency_overrides.clear()


def test_demo_blocks_hosted_mcp(tmp_path, monkeypatch):
    client, app, _engine, secrets = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    try:
        r = _rpc(client, secrets["read"], "initialize", {})
        assert r.status_code == 403
        assert secrets["read"] not in r.text
    finally:
        app.dependency_overrides.clear()


def test_read_tools_and_feature_toggle(tmp_path, monkeypatch):
    from sqlmodel import Session

    from app.models import Server

    client, app, engine, secrets = _client(tmp_path, monkeypatch)
    try:
        with Session(engine) as session:
            srv = Server(name="Lab", hostname="lab.local", ssh_username="pi", backup_enabled=True)
            session.add(srv)
            session.commit()
            session.refresh(srv)
            sid = srv.id

        for method, params in (
            ("summary", {}),
            ("list_servers", {"q": "", "limit": 10, "offset": 0}),
            ("inventory", {}),
            ("inventory", {"server_id": sid}),
            ("services", {}),
            ("list_jobs", {"active_only": False, "limit": 5}),
            ("get_server", {"server_id": sid}),
        ):
            res = _rpc(client, secrets["read"], "tools/call", {"name": method, "arguments": params})
            assert res.status_code == 200, res.text
            assert res.json()["result"]["isError"] is False, method

        missing_job = _rpc(
            client, secrets["read"], "tools/call", {"name": "get_job", "arguments": {"job_id": 99999}}
        )
        assert missing_job.json()["result"]["isError"] is True
        assert "404" in missing_job.json()["result"]["content"][0]["text"]

        empty = _rpc(
            client,
            secrets["jobs"],
            "tools/call",
            {"name": "set_features", "arguments": {"server_id": sid}},
        )
        assert empty.json()["result"]["isError"] is True
        assert "No feature fields" in empty.json()["result"]["content"][0]["text"]

        toggled = _rpc(
            client,
            secrets["jobs"],
            "tools/call",
            {"name": "set_features", "arguments": {"server_id": sid, "backup": False}},
        )
        assert toggled.json()["result"]["isError"] is False, toggled.text
        assert toggled.json()["result"]["structuredContent"]["changed"]["backup"] is False

        bad_json = client.post(
            "/mcp",
            content=b"not-json",
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        assert bad_json.status_code == 400
        assert bad_json.json()["error"]["code"] == -32700

        batch = client.post(
            "/mcp",
            json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        assert batch.status_code == 400

        missing_method = _rpc(client, secrets["read"], "widgets/spin")
        assert missing_method.json()["error"]["code"] == -32601

        prompts = _rpc(client, secrets["read"], "prompts/list")
        assert prompts.json()["result"] == {"prompts": []}

        bad_type = client.post(
            "/mcp",
            content=b"{}",
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "text/plain",
                "Accept": "application/json",
            },
        )
        assert bad_type.status_code == 415

        bad_accept = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "application/json",
                "Accept": "text/html",
            },
        )
        assert bad_accept.status_code == 406

        bad_proto = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Authorization": f"Bearer {secrets['read']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": "1999-01-01",
            },
        )
        assert bad_proto.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_trigger_job_returns_api_202_and_409(monkeypatch):
    import asyncio

    from fastapi.responses import JSONResponse

    from app.services import mcp_hosted

    calls = {"n": 0}

    async def fake_create(server_id, body, background, session, auth):
        calls["n"] += 1
        if body.job_type == "docker_stack_check":
            assert body.source_filter == "/home/pi/docker/grafana"
            return JSONResponse(
                status_code=202,
                content={"job_id": 6, "status": "pending", "job_type": body.job_type},
            )
        if calls["n"] == 1:
            return JSONResponse(
                status_code=202,
                content={"job_id": 5, "status": "pending", "job_type": "backup"},
            )
        return JSONResponse(
            status_code=409,
            content={"detail": "Backup already running", "job": {"id": 5}},
        )

    monkeypatch.setattr("app.routers.api_v1.create_server_job", fake_create)
    auth = type("Auth", (), {"scopes": {"read", "jobs"}})()

    async def _run():
        first = await mcp_hosted.call_tool(
            "trigger_job",
            {"server_id": 1, "job_type": "backup"},
            session=None,
            auth=auth,
        )
        second = await mcp_hosted.call_tool(
            "trigger_job",
            {"server_id": 1, "job_type": "backup", "source_filter": "root"},
            session=None,
            auth=auth,
        )
        stack = await mcp_hosted.call_tool(
            "trigger_job",
            {
                "server_id": 1,
                "job_type": "docker_stack_check",
                "source_filter": "/home/pi/docker/grafana",
            },
            session=None,
            auth=auth,
        )
        refused = await mcp_hosted.call_tool(
            "trigger_job",
            {"server_id": 1, "job_type": "service_migrate"},
            session=None,
            auth=auth,
        )
        return first, second, stack, refused

    first, second, stack, refused = asyncio.run(_run())
    assert first["isError"] is False
    assert first["structuredContent"]["http_status"] == 202
    assert second["isError"] is False
    assert second["structuredContent"]["http_status"] == 409
    assert second["structuredContent"]["already_active"] is True
    assert "already_active" not in first["structuredContent"]
    assert stack["isError"] is False
    assert stack["structuredContent"]["http_status"] == 202
    assert refused["isError"] is True
    assert "must be one of" in refused["content"][0]["text"]


def test_file_tools_and_argument_errors(monkeypatch):
    import asyncio

    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    from app.services import mcp_hosted

    auth = type("Auth", (), {"scopes": {"read", "files", "jobs"}})()

    def list_files(server_id, p, session, auth):
        return {"entries": [{"name": "a.txt"}], "rel": p}

    def download(server_id, p, session, auth):
        return StreamingResponse(iter([b"hello ", b"world"]), media_type="application/octet-stream")

    async def upload(server_id, file, p, session, auth):
        return {"ok": True, "bytes": 5, "rel": "notes/a.txt"}

    def mkdir(server_id, body, session, auth):
        return {"ok": True, "rel": body.name}

    def rename(server_id, body, session, auth):
        return {"ok": True, "from": body.src, "to": body.dest}

    def delete(server_id, p, session, auth):
        raise HTTPException(404, detail="not found")

    monkeypatch.setattr("app.routers.api_v1.api_files_list", list_files)
    monkeypatch.setattr("app.routers.api_v1.api_files_download", download)
    monkeypatch.setattr("app.routers.api_v1.api_files_upload", upload)
    monkeypatch.setattr("app.routers.api_v1.api_files_mkdir", mkdir)
    monkeypatch.setattr("app.routers.api_v1.api_files_rename", rename)
    monkeypatch.setattr("app.routers.api_v1.api_files_delete", delete)

    async def _run():
        listed = await mcp_hosted.call_tool(
            "list_files", {"server_id": 1, "p": "notes"}, None, auth
        )
        read = await mcp_hosted.call_tool(
            "read_file", {"server_id": 1, "p": "notes/a.txt"}, None, auth
        )
        written = await mcp_hosted.call_tool(
            "write_file",
            {"server_id": 1, "p": "notes", "name": "a.txt", "text": "hello"},
            None,
            auth,
        )
        made = await mcp_hosted.call_tool(
            "mkdir", {"server_id": 1, "p": "", "name": "notes"}, None, auth
        )
        renamed = await mcp_hosted.call_tool(
            "rename_file",
            {"server_id": 1, "p": "notes", "src": "a.txt", "dest": "b.txt"},
            None,
            auth,
        )
        deleted = await mcp_hosted.call_tool(
            "delete_file", {"server_id": 1, "p": "notes/b.txt"}, None, auth
        )
        missing = await mcp_hosted.call_tool("get_server", {}, None, auth)
        huge = await mcp_hosted.call_tool(
            "write_file",
            {
                "server_id": 1,
                "p": "",
                "name": "big.txt",
                "text": "x" * (mcp_hosted.MAX_FILE_BYTES + 1),
            },
            None,
            auth,
        )
        missing_p = await mcp_hosted.call_tool(
            "mkdir", {"server_id": 1, "name": "notes"}, None, auth
        )
        bad_steps = await mcp_hosted.call_tool(
            "trigger_job",
            {"server_id": 1, "job_type": "os_patch", "os_steps": "upgrade"},
            None,
            auth,
        )
        return listed, read, written, made, renamed, deleted, missing, huge, missing_p, bad_steps

    listed, read, written, made, renamed, deleted, missing, huge, missing_p, bad_steps = asyncio.run(
        _run()
    )
    assert listed["structuredContent"]["entries"][0]["name"] == "a.txt"
    assert read["structuredContent"]["text"] == "hello world"
    assert read["structuredContent"]["truncated"] is False
    assert written["structuredContent"]["ok"] is True
    assert made["structuredContent"]["rel"] == "notes"
    assert renamed["structuredContent"]["to"] == "b.txt"
    assert deleted["isError"] is True
    assert "404" in deleted["content"][0]["text"]
    assert missing["isError"] is True
    assert "Missing server_id" in missing["content"][0]["text"]
    assert huge["isError"] is True
    assert "cap is" in huge["content"][0]["text"]
    assert missing_p["isError"] is True
    assert "Missing p" in missing_p["content"][0]["text"]
    assert bad_steps["isError"] is True
    assert "os_steps" in bad_steps["content"][0]["text"]


def test_present_file_and_origin_helpers():
    from app.services.mcp_hosted import origin_allowed, present_file

    text = present_file(b"hello", truncated=False)
    assert text["encoding"] == "utf-8"
    assert text["text"] == "hello"
    binary = present_file(b"\xff\xfe", truncated=True)
    assert binary["encoding"] == "base64"
    assert binary["truncated"] is True

    class Req:
        def __init__(self, headers):
            self.headers = headers

    assert origin_allowed(Req({})) is True
    assert origin_allowed(Req({"origin": "null"})) is False
    assert origin_allowed(Req({"origin": "https://pi.example", "host": "pi.example"})) is True
    assert origin_allowed(Req({"origin": "cursor://anysphere"})) is True
    assert origin_allowed(Req({"origin": "https://evil.example", "host": "pi.example"})) is False
    assert (
        origin_allowed(
            Req(
                {
                    "origin": "https://evil.example",
                    "host": "pi.example",
                    "authorization": "Bearer ph_x",
                }
            )
        )
        is False
    )


def test_origin_matches_public_url_without_bearer_bypass(monkeypatch):
    from app.services.mcp_hosted import origin_allowed

    monkeypatch.setattr(
        "app.services.password_reset.configured_public_origin",
        lambda: "https://pi.example:8443",
    )

    class Req:
        def __init__(self, headers):
            self.headers = headers

    assert (
        origin_allowed(
            Req({"origin": "https://pi.example:8443", "host": "web:8000"})
        )
        is True
    )
    assert (
        origin_allowed(
            Req(
                {
                    "origin": "https://evil.example",
                    "host": "web:8000",
                    "authorization": "Bearer ph_x",
                }
            )
        )
        is False
    )


def test_read_file_closes_stream_when_truncated():
    import asyncio

    from fastapi.responses import StreamingResponse

    from app.services import mcp_hosted

    closed = {"n": 0}

    def gen():
        try:
            yield b"a" * 100
            yield b"b" * 100
        finally:
            closed["n"] += 1

    async def _run():
        resp = StreamingResponse(gen(), media_type="application/octet-stream")
        data, truncated = await mcp_hosted._collect_capped(resp, 50)
        assert closed["n"] == 1
        return data, truncated

    data, truncated = asyncio.run(_run())
    assert truncated is True
    assert data == b"a" * 50
    assert closed["n"] == 1


def test_catalog_advertises_hosted_path():
    from app.services.api_tokens import api_meta_dict

    meta = api_meta_dict()
    assert meta["mcp"]["path"] == "/mcp"
    assert meta["mcp"]["transport"] == "streamable-http"


def test_tools_for_scopes_fail_closed_without_read():
    from app.services.mcp_hosted import tools_for_scopes

    assert tools_for_scopes({"jobs", "edit"}) == []
    names = [tool["name"] for tool in tools_for_scopes({"read"})]
    assert "health" in names
    assert "trigger_job" not in names
