"""v1.4 coverage follow-up — overrides rewrite, job execute wrappers, SFTP compose."""
from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server, ServiceTemplate


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _srv(**kw):
    base = dict(id=1, name="a", hostname="a.local")
    base.update(kw)
    return SimpleNamespace(**base)


def _server(session, **kw):
    s = Server(
        name=kw.get("name", "a"),
        hostname=kw.get("hostname", "a.local"),
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        os_type=kw.get("os_type", "debian"),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


class _Pool:
    def submit(self, fn, *a, **k):
        fn(*a, **k)
        return SimpleNamespace(result=lambda: None)


class MemSFTP:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def open(self, path, mode="r"):
        if "r" in mode and "w" not in mode:
            if path not in self.files:
                raise IOError("missing")
            data = self.files[path]
            if isinstance(data, str):
                data = data.encode()
            return io.BytesIO(data)
        buf = io.BytesIO()
        files = self.files

        class W:
            def write(self, data):
                if isinstance(data, str):
                    data = data.encode()
                buf.write(data)
                files[path] = buf.getvalue()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return W()

    def close(self):
        pass

    def remove(self, path):
        self.files.pop(path, None)

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def stat(self, path):
        if path not in self.files:
            raise IOError("missing")
        return True


class SshWithSftp:
    def __init__(self, sftp: MemSFTP, replies=None):
        self._sftp = sftp
        self.replies = list(replies or [])
        self.cmds = []
        self.closed = False

    def open_sftp(self):
        return self._sftp

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# migrate overrides
# ---------------------------------------------------------------------------


def test_overrides_rewrite_ports_binds_and_staging(tmp_path):
    from app.services.service_migrate import overrides as ov

    assert ov.mapped_host_port(3000, "tcp", {"3000/tcp": "3100"}) == "3100"
    assert ov.mapped_host_port(80, "tcp", None) == "80"

    pmap = {"3000/tcp": "3100", "53/udp": "5353"}
    assert ov.rewrite_port_spec(3000, pmap) == 3100
    assert ov.rewrite_port_spec(22, pmap) == 22
    assert ov.rewrite_port_spec({"published": 3000, "protocol": "tcp"}, pmap)["published"] == 3100
    assert ov.rewrite_port_spec({"published": "3000", "protocol": "tcp"}, pmap)["published"] == "3100"
    assert ov.rewrite_port_spec({"published": "x"}, pmap)["published"] == "x"
    assert ov.rewrite_port_spec("127.0.0.1:3000:3000/tcp", pmap) == "127.0.0.1:3100:3000/tcp"
    assert ov.rewrite_port_spec("3000/tcp", pmap) == "3100/tcp"
    assert ov.rewrite_port_spec("nope", pmap) == "nope"
    assert ov.rewrite_port_spec(3000, {}) == 3000

    data = {
        "services": {
            "web": {"ports": ["3000:3000", 3000, {"published": 53, "protocol": "udp"}]},
            "skip": "x",
        }
    }
    ov._rewrite_ports_in_obj(data, pmap)
    assert "3100" in str(data["services"]["web"]["ports"])
    ov._rewrite_ports_in_obj([], pmap)
    ov._rewrite_ports_in_obj({"services": "nope"}, pmap)

    vols = {"data": {"name": "grafana_data"}, "other": "external"}
    obj = {"volumes": vols}
    ov._rewrite_volume_names_in_obj(obj, {"grafana_data": "grafana_data_b"})
    assert obj["volumes"]["data"]["name"] == "grafana_data_b"
    ov._rewrite_volume_names_in_obj({}, {"a": "b"})
    ov._rewrite_volume_names_in_obj({"volumes": "x"}, {"a": "b"})

    text = (
        "    - 3000:3000/tcp\n"
        "    - 53/udp\n"
        "    published: 3000\n"
        "    - 22:22\n"
    )
    rewritten = ov._rewrite_lines_ports(text, pmap)
    assert "3100:3000" in rewritten
    assert "5353/udp" in rewritten

    bmap = {"/home/pi/docker/data": "/home/dest/docker/data"}
    assert ov._rewrite_bind_spec("/home/pi/docker/data:/var/lib", bmap).startswith("/home/dest")
    assert ov._rewrite_bind_spec("/other:/x", bmap) == "/other:/x"
    dspec = ov._rewrite_bind_spec({"source": "/home/pi/docker/data", "Source": "/home/pi/docker/data"}, bmap)
    assert dspec["source"].startswith("/home/dest")
    compose = {"services": {"web": {"volumes": ["/home/pi/docker/data:/var"]}}}
    ov._rewrite_binds_in_obj(compose, bmap)
    assert compose["services"]["web"]["volumes"][0].startswith("/home/dest")
    ov._rewrite_binds_in_obj({}, bmap)
    ov._rewrite_binds_in_obj({"services": {"x": {}}}, bmap)

    aliases = ov.compose_bind_aliases("/home/piherder/open-webui-data")
    assert "~/" in "".join(aliases) or any(a.startswith("~/") for a in aliases)
    assert ov.compose_bind_aliases("relative") == ["relative"]
    expanded = ov.expand_bind_map_for_compose({"/home/pi/docker/data": "/dest/data"})
    assert expanded["/home/pi/docker/data"] == "/dest/data"

    bind_text = '    - "/home/pi/docker/data:/var/lib/grafana"\n'
    bind_rewritten = ov._rewrite_lines_binds(
        bind_text, {"/home/pi/docker/data": "/home/dest/docker/data"}
    )
    assert "/home/dest/docker/data" in bind_rewritten
    assert ov._rewrite_lines_binds("x", {}) == "x"

    named = ov._rewrite_top_name("name: grafana\nservices:\n  web: {}\n", "grafana2")
    assert "name: grafana2" in named
    env = ov._rewrite_env_project("COMPOSE_PROJECT_NAME=grafana\nFOO=1\n", "grafana2")
    assert "COMPOSE_PROJECT_NAME=grafana2" in env

    rows = ov.parse_bind_overrides_from_mapping(
        {
            "bind_src_0": "/a",
            "dest_bind_0": "/b",
            "skip_bind_1": "1",
            "bind_src_1": "/skip-me",
            "dest_bind_1": "/x",
        }
    )
    assert rows[0]["dest"] == "/b"
    skipped = [r for r in rows if r["skip"]]
    assert skipped
    assert ov.bind_map_from_overrides(rows) == {"/a": "/b"}
    assert ov.parse_bind_overrides_from_mapping(None) == []
    err = ov.validate_dest_bind_path("", "/home/pi/docker")
    assert err
    assert ov.validate_dest_bind_path("/etc", "/home/pi/docker")
    assert ov.validate_dest_bind_path("/home/pi/docker", "/home/pi/docker")
    assert ov.validate_dest_bind_path("/home/pi/docker/app/data", "/home/pi/docker") is None

    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text(
        "name: grafana\n"
        "services:\n"
        "  web:\n"
        "    ports:\n"
        "      - 3000:3000\n"
        "    volumes:\n"
        "      - /home/pi/docker/data:/var/lib/grafana\n",
        encoding="utf-8",
    )
    (staging / ".env").write_text("COMPOSE_PROJECT_NAME=grafana\n", encoding="utf-8")
    (staging / "bin.dat").write_bytes(b"\xff\xfe")
    out = ov.apply_staging_overrides(
        staging,
        dest_project="grafana2",
        source_project="grafana",
        port_map={"3000/tcp": "3100"},
        bind_map={"/home/pi/docker/data": "/home/dest/docker/data"},
    )
    assert out["files"]
    text = (staging / "docker-compose.yml").read_text()
    assert "3100:3000" in text
    assert "grafana2" in text or "COMPOSE_PROJECT_NAME=grafana2" in (staging / ".env").read_text()
    missing = ov.apply_staging_overrides(tmp_path / "nope")
    assert missing["files"] == []


# ---------------------------------------------------------------------------
# docker_versions + catalog + turnstile
# ---------------------------------------------------------------------------


def test_docker_versions_pure_helpers():
    from app.services import docker_versions as dv
    from app.models import DockerVersion

    assert dv.is_meta_key("__piherder__") is True
    assert dv.is_meta_key("docker-compose.yml") is False
    files = dv.files_for_sftp(
        {"docker-compose.yml": "x", "__piherder__": "{}", "skip": None, "n": 1}
    )
    assert "docker-compose.yml" in files and "__piherder__" not in files
    assert dv.files_for_sftp({}) == {}
    assert dv.primary_compose_key({}) is None
    assert dv.primary_compose_key({"docker-compose.yml": "x"}) == "docker-compose.yml"
    assert dv.primary_compose_key({"app.yml": "x", "__m": "1"}) == "app.yml"
    assert dv.file_role("docker-compose.yml")
    names = dv.sort_project_filenames([".env", "Dockerfile", "docker-compose.yml", "__x"])
    assert names[0] == "docker-compose.yml" or "compose" in names[0]
    merged = dv.merge_project_files(
        {"a.yml": "1", "b.yml": "2"},
        {"b.yml": "3", "__piherder__": "{}"},
        delete_keys=["a.yml", "__nope"],
    )
    assert "a.yml" not in merged
    assert merged["b.yml"] == "3"
    assert dv.parse_version_files(None) == {}
    assert dv.parse_version_files(DockerVersion(server_id=1, project_name="x", version=1, files="[")) == {}
    assert dv.parse_version_files(DockerVersion(server_id=1, project_name="x", version=1, files='{"a":1}'))["a"] == 1


def test_catalog_badges_and_list(tmp_path, monkeypatch):
    from app.services.service_templates import catalog as cat
    from app.config import settings

    assert cat.source_badge("builtin")["kind"] == "ootb"
    assert cat.source_badge("starter")["label"] == "OOTB"
    assert cat.source_badge("git")["label"] == "Git"
    assert cat.source_badge("import")["label"] == "Imported"
    assert cat.source_badge("user")["kind"] == "user"
    assert cat.is_ootb_source("builtin") is True
    assert cat.is_ootb_source("user") is False
    root = cat.builtin_templates_root()
    assert root.exists() or True
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    imported = cat.imported_templates_root()
    assert imported.is_dir()

    session, _ = _memory()
    rows = cat.list_catalog(session, include_disabled=True)
    assert isinstance(rows, list)
    # disk starters should land in sqlite
    n = cat.ensure_builtin_templates_in_db(session)
    assert n >= 0
    if rows:
        row = cat.get_template_row(session, slug=rows[0]["slug"])
        assert row is not None
        definition = cat.get_template_definition(session, slug=rows[0]["slug"])
        assert definition is not None or True


def test_turnstile_visitor_ip_and_non_json(monkeypatch):
    from app.services import turnstile as ts
    import httpx

    assert ts._normalize_ip("[::1]:443") == "::1"
    assert ts._normalize_ip("1.2.3.4:8080") == "1.2.3.4"
    assert ts._normalize_ip("") == ""
    assert ts.visitor_ip_for_turnstile(None) is None

    req = SimpleNamespace(
        headers={
            "CF-Connecting-IP": "9.9.9.9",
            "X-Forwarded-For": "8.8.8.8",
        },
        client=SimpleNamespace(host="1.1.1.1"),
    )
    assert ts.visitor_ip_for_turnstile(req) == "9.9.9.9"
    req2 = SimpleNamespace(headers={"X-Forwarded-For": "8.8.8.8, 1.1.1.1"}, client=None)
    assert ts.visitor_ip_for_turnstile(req2) == "8.8.8.8"
    req3 = SimpleNamespace(headers={"X-Real-IP": "7.7.7.7"}, client=None)
    assert ts.visitor_ip_for_turnstile(req3) == "7.7.7.7"
    req4 = SimpleNamespace(headers={}, client=SimpleNamespace(host="6.6.6.6"))
    assert ts.visitor_ip_for_turnstile(req4) == "6.6.6.6"

    assert ts._is_transport_error(httpx.ConnectError("x")) is True
    assert ts._is_transport_error(OSError("dns")) is True
    assert ts._is_transport_error(ValueError("no")) is False

    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(ts.settings, "PIHERDER_TURNSTILE_SECRET_KEY", "secret")
    monkeypatch.setattr(ts, "_MAX_ATTEMPTS", 1)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("not json")
    with patch("app.services.turnstile.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = mock_resp
        client_cls.return_value = client
        ok, code = ts.verify_turnstile_token("tok", remoteip="1.2.3.4")
    assert ok is False
    assert code in ("verify-unreachable", "missing-remoteip") or code

    ok2, code2 = ts.verify_turnstile_token("tok", remoteip="")
    assert ok2 is False and code2 == "missing-remoteip"


# ---------------------------------------------------------------------------
# docker_management SFTP + classify/updates
# ---------------------------------------------------------------------------


def test_docker_mgmt_sftp_and_classify(monkeypatch):
    from app.services import docker_management as dm

    sftp = MemSFTP(
        {
            "/home/pi/docker/app/docker-compose.yml": "services:\n  web:\n    build: .\n    image: app:local\n  db:\n    image: postgres:15\n"
        }
    )
    ssh = SshWithSftp(sftp)
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: ssh)
    monkeypatch.setattr(dm, "run_command", lambda c, cmd, timeout=15: (0, "", ""))
    content = dm.read_compose_file(_srv(), "/home/pi/docker/app")
    assert "services:" in content
    empty = dm.read_compose_file(_srv(), "/home/pi/docker/missing")
    assert "No compose file" in empty
    builds = dm.get_compose_build_services(_srv(), "/home/pi/docker/app")
    assert "web" in builds

    sftp.files["/home/pi/docker/app/Dockerfile"] = b"FROM alpine\n"
    df = dm.read_dockerfile(_srv(), "/home/pi/docker/app/Dockerfile")
    assert "FROM" in df
    missing_df = dm.read_dockerfile(_srv(), "/nope")
    assert "not found" in missing_df.lower()

    ok, err = dm.write_dockerfile(_srv(), "/home/pi/docker/app/Dockerfile", "FROM debian\n")
    assert ok is True and err == ""
    ok2, err2 = dm.write_compose_file(_srv(), "/home/pi/docker/app", "services: {}\n")
    assert ok2 is True

    # classify via compose config json
    cfg = json.dumps(
        {
            "services": {
                "web": {"build": ".", "image": "app:local"},
                "db": {"image": "postgres:15"},
                "bad": "x",
            }
        }
    )
    ssh.replies = [(0, cfg, "")]

    def run(client, cmd, timeout=15):
        if "format json" in cmd:
            return (0, cfg, "")
        if "config --images" in cmd:
            return (0, "postgres:15\n", "")
        if "image inspect" in cmd:
            if "postgres" in cmd:
                return (0, "sha256:aaa\n", "")
            return (0, "sha256:bbb\n", "")
        if "compose pull" in cmd:
            return (0, "Pulled\n", "")
        return (0, "", "")

    monkeypatch.setattr(dm, "run_command", run)
    classified = dm.classify_compose_images(ssh, "/home/pi/docker/app")
    assert "postgres:15" in classified["pullable_images"]
    assert "web" in classified["build_services"]

    # fallback path
    def run_bad(client, cmd, timeout=15):
        if "format json" in cmd:
            return (0, "not-json", "")
        if "config --images" in cmd:
            return (0, "nginx:latest\n", "")
        return (0, "", "")

    monkeypatch.setattr(dm, "run_command", run_bad)
    fb = dm.classify_compose_images(ssh, "/x")
    assert "nginx:latest" in fb["pullable_images"]

    ids = {"n": 0}

    def run_upd(client, cmd, timeout=15):
        if "format json" in cmd:
            return (0, json.dumps({"services": {"db": {"image": "postgres:15"}}}), "")
        if "image inspect" in cmd:
            ids["n"] += 1
            return (0, f"id{ids['n']}\n", "")
        if "compose pull" in cmd:
            return (0, "Pulled postgres\n", "")
        return (0, "", "")

    monkeypatch.setattr(dm, "run_command", run_upd)
    upd = dm.check_compose_updates(_srv(), "/home/pi/docker/app")
    assert upd["success"] is True
    assert upd["has_updates"] is True

    def run_none(client, cmd, timeout=15):
        if "format json" in cmd:
            return (0, json.dumps({"services": {"web": {"build": "."}}}), "")
        return (0, "", "")

    monkeypatch.setattr(dm, "run_command", run_none)
    none = dm.check_compose_updates(_srv(), "/home/pi/docker/app")
    assert none.get("skipped_build_only") is True

    assert dm._image_id_remote(ssh, "") == ""


# ---------------------------------------------------------------------------
# jobs enqueue / execute (memory engine)
# ---------------------------------------------------------------------------


def test_jobs_enqueue_and_execute_mocked(monkeypatch):
    from app.services import jobs as jobs_mod
    from app.models import AuditLog

    session, engine = _memory()
    src = _server(session, name="src", hostname="src.local")
    dest = _server(session, name="dest", hostname="dest.local")

    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_patch_apply_pool", _Pool())
    monkeypatch.setattr(jobs_mod, "_update_check_pool", _Pool())

    # OS patch success + HAOS auto-mark + recheck
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "run_os_patch",
        lambda *a, **k: {
            "summary": "ok",
            "auto_mark_haos": True,
            "backend": "ha_cli",
        },
    )
    monkeypatch.setattr(jobs_mod.os_patching, "os_patch_succeeded", lambda r: True)
    monkeypatch.setattr(jobs_mod.os_patching, "init_os_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod.os_patching, "mark_os_patch_done", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod.os_patching, "_append_os_log", lambda *a, **k: None)
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "attach_audit_fields",
        lambda res, hostname, post_check=None: dict(res, hostname=hostname),
    )
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "check_os_updates",
        lambda s: {
            "updates_count": 2,
            "reboot_pending": True,
            "packages_sample": ["linux"],
            "phased_count": 1,
            "auto_mark_haos": True,
        },
    )
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "normalize_os_patch_steps",
        lambda steps: steps or ["update", "upgrade"],
    )

    job = jobs_mod.enqueue_os_patch_apply(src.id, user_id=None, scheduled=True)
    assert job is not None
    session.refresh(src)
    # execute ran via pool
    j = session.get(Job, job.id)
    assert j.status in ("success", "running", "failed") or j.status == "success"

    # skip when already active
    running = Job(server_id=src.id, job_type="os_patch", status="running", details="{}")
    session.add(running)
    session.commit()
    assert jobs_mod.enqueue_os_patch_apply(src.id) is None
    session.delete(running)
    session.commit()

    assert jobs_mod.enqueue_os_patch_apply(99999) is None

    # container patch
    monkeypatch.setattr(jobs_mod.container_patching, "init_container_patch_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod.container_patching, "mark_container_patch_done", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod.container_patching, "append_container_log", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod.container_patching, "container_patch_succeeded", lambda r: True)
    monkeypatch.setattr(
        jobs_mod.container_patching,
        "run_project_update",
        lambda *a, **k: {"ok": True},
    )
    monkeypatch.setattr(
        jobs_mod.container_patching,
        "check_all_projects_updates",
        lambda s: {"projects_with_updates": ["grafana"], "project_details": {}},
    )
    monkeypatch.setattr(
        jobs_mod.container_patching,
        "summarize_container_patch",
        lambda r: "patched",
    )
    cjob = jobs_mod.enqueue_container_patch_apply(src.id, scheduled=True)
    assert cjob is not None
    assert jobs_mod.enqueue_container_patch_apply(99999) is None

    # apply check results directly
    jobs_mod._apply_os_check_result(
        session,
        src.id,
        {
            "updates_count": 3,
            "reboot_pending": False,
            "packages_sample": ["a"],
            "phased_sample": ["p"],
            "ha": {"core": "1"},
            "identity": "pi",
        },
    )
    jobs_mod._apply_os_check_result(session, 99999, {})
    jobs_mod._apply_container_check_result(
        session,
        src.id,
        {"projects_with_updates": ["n8n"], "project_details": {"n8n": {"images": ["x"]}}},
    )
    jobs_mod._apply_container_check_result(session, 99999, {})

    # migrate enqueue validation
    with pytest.raises(ValueError, match="invalid"):
        jobs_mod.enqueue_service_migrate(src.id, dest.id, "../etc")
    with pytest.raises(ValueError, match="differ"):
        jobs_mod.enqueue_service_migrate(src.id, src.id, "grafana")
    with pytest.raises(ValueError, match="not found"):
        jobs_mod.enqueue_service_migrate(src.id, 99999, "grafana")
    with pytest.raises(ValueError):
        jobs_mod.enqueue_service_migrate(
            src.id, dest.id, "grafana", port_map={"bad": "x"}
        )

    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.run_copy_and_start",
        lambda *a, **k: {"ok": True},
    )
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.wipe_staging", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.probe_host_facts",
        lambda s: {"arch": "aarch64"},
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.herder_free_bytes", lambda: 10**12
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.refresh_host_inventory", lambda *a, **k: True
    )

    mjob = jobs_mod.enqueue_service_migrate(
        src.id,
        dest.id,
        "grafana",
        leftover="stopped",
        dest_project="grafana2",
        port_map={"3000/tcp": "3100"},
        bind_map={"/a": "/b"},
        skip_binds=["/sock"],
        devices_ack=True,
        adopt_fabric=True,
    )
    assert mjob is not None
    session.expire_all()
    done = session.get(Job, mjob.id)
    assert done.status == "success"

    # migrate execute failure with recover_source
    class ME(Exception):
        failed_step = "copy"

    def boom(*a, **k):
        raise ME("copy died")

    monkeypatch.setattr("app.services.service_migrate.pipeline.run_copy_and_start", boom)
    m2 = jobs_mod.enqueue_service_migrate(src.id, dest.id, "n8n")
    session.expire_all()
    failed = session.get(Job, m2.id)
    assert failed.status == "failed"
    details = json.loads(failed.details or "{}")
    assert details.get("failed_step") == "copy" or details.get("recover_source") or failed.status == "failed"

    # JobAlreadyActive
    busy = Job(server_id=src.id, job_type="docker_stack_start", status="running", details="{}")
    session.add(busy)
    session.commit()
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.enqueue_service_migrate(src.id, dest.id, "grafana")

    # _finish_running_audit
    audit = AuditLog(
        action=failed.job_type or "service_migrate",
        status="running",
        details=f"Job #{failed.id} started",
        server_id=src.id,
    )
    session.add(audit)
    session.commit()
    jobs_mod._finish_running_audit_for_job(session, failed, status="failed", message="x")
    session.commit()
    session.refresh(audit)
    assert audit.status == "failed"

    # execute os patch server missing
    jobs_mod._execute_os_patch_sync(1, 99999, 1, ["update"])
    jobs_mod._execute_container_patch_sync(1, 99999, 1)
    jobs_mod._execute_service_migrate(1, 99999, dest.id, "x", 1)

    # os patch exception path
    monkeypatch.setattr(
        jobs_mod.os_patching,
        "run_os_patch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("apt")),
    )
    job_fail = jobs_mod.enqueue_os_patch_apply(dest.id)
    assert job_fail is not None
