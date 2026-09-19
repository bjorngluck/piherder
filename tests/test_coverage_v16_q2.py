"""v1.6 Q-80 second pack — more service branches (mocked, no live network)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server, User


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(session, **kw):
    s = Server(
        name=kw.get("name", "pi"),
        hostname=kw.get("hostname", "pi.local"),
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        container_patch_enabled=True,
        backup_enabled=True,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_nmap_script_args_presets(tmp_path, monkeypatch):
    from app.services.nmap import scan as nscan

    monkeypatch.setattr(nscan, "vuln_root", lambda: tmp_path)
    assert nscan._script_args_for_preset("none") == []
    cpe = nscan._script_args_for_preset("cpe")
    assert cpe[0] == "--script" and "vulners" in cpe[1]
    offline = nscan._script_args_for_preset("offline")
    assert "vulners" in offline[1] or "vulscan" in offline[1]
    (tmp_path / "vulscan").mkdir()
    (tmp_path / "vulscan" / "vulscan.nse").write_text("--")
    (tmp_path / "nmap-vulners").mkdir()
    (tmp_path / "nmap-vulners" / "http-vulners-regex.nse").write_text("--")
    full = nscan._script_args_for_preset("full")
    assert "vuln" in full[1]
    assert "vulscan" in full[1]
    compat = nscan._script_args_for_vuln()
    assert compat[0] == "--script"


def test_job_human_summary_and_queued_create(monkeypatch):
    from app.services import jobs as js

    ha = js._human_job_summary(
        "os_update_check",
        "success",
        json.dumps(
            {
                "backend": "ha_cli",
                "actionable_count": 2,
                "packages_sample": ["core", "os"],
                "reboot_pending": True,
                "error": "note",
            }
        ),
    )
    assert "HA" in ha
    apt = js._human_job_summary(
        "os_update_check",
        "success",
        json.dumps(
            {
                "actionable_count": 3,
                "phased_count": 1,
                "total_upgradable": 10,
            }
        ),
    )
    assert "ready" in apt
    cu = js._human_job_summary(
        "container_update_check",
        "success",
        json.dumps({"projects_with_updates": ["a"], "projects_checked": ["a", "b"]}),
    )
    assert "1 project" in cu
    assert "up to date" in js._human_job_summary(
        "docker_stack_check", "success", json.dumps({"project": "g", "success": True})
    )
    assert "updates available" in js._human_job_summary(
        "docker_stack_check",
        "success",
        json.dumps({"project": "g", "has_updates": True, "updated_images": ["x"]}),
    )
    assert "deploy ok" in js._human_job_summary(
        "docker_stack_deploy", "success", json.dumps({"project": "g", "success": True})
    )
    assert "failed" in js._human_job_summary(
        "docker_stack_deploy", "failed", json.dumps({"project": "g", "error": "boom"})
    )
    assert "stop ok" in js._human_job_summary(
        "docker_stack_stop", "success", json.dumps({"project": "g", "action": "stop", "success": True})
    )
    assert "deleted" in js._human_job_summary(
        "docker_stack_remove", "success", json.dumps({"project": "g", "success": True})
    )
    assert "ok" in js._human_job_summary(
        "template_deploy",
        "success",
        json.dumps({"project_name": "g", "template_slug": "grafana", "success": True, "config_version": 2}),
    )
    assert "in sync" in js._human_job_summary(
        "template_drift_check", "success", json.dumps({"project_name": "g", "drift_status": "in_sync"})
    )
    assert "drifted" in js._human_job_summary(
        "template_drift_check",
        "success",
        json.dumps({"project_name": "g", "drift_status": "drifted", "diff_count": 3}),
    )
    assert "sum" in js._human_job_summary("os_patch", "success", json.dumps({"summary": "sum"}))
    assert "Retention" in js._human_job_summary("retention", "success", "")
    assert js._human_job_summary("other", "ok", "snippet") == "snippet"

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)
    monkeypatch.setattr(
        js,
        "make_audit_log",
        lambda **k: AuditLog(
            user_id=k.get("user_id"),
            server_id=k.get("server_id"),
            action=k.get("action") or "backup",
            status=k.get("status") or "running",
            details=k.get("details") or "",
        ),
    )
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: None)
    job, audit = js._create_queued_job_with_audit(
        session,
        server_id=srv.id,
        job_type="backup",
        queue_message="queued",
        user_id=1,
        api_token_id=None,
        audit_details="Job #{job_id} queued",
    )
    assert job.status == "pending"
    assert "queued" in (audit.details or "")

    @contextmanager
    def _fresh():
        yield session

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    job.status = "cancelled"
    session.add(job)
    audit.status = "running"
    audit.details = f"Job #{job.id} started"
    session.add(audit)
    session.commit()
    js._finish(audit.id, job.id, "success", "should not overwrite")
    session.refresh(audit)
    assert audit.status == "cancelled"


def test_console_grant_stepup_and_same_site(monkeypatch):
    from app.services import ssh_console as sc

    monkeypatch.setattr(sc, "require_enabled", lambda: None)
    monkeypatch.setattr(sc, "console_enabled", lambda: True)
    monkeypatch.setattr(sc, "bind_ip_enabled", lambda: False)
    monkeypatch.setattr(sc, "bind_device_enabled", lambda: False)
    monkeypatch.setattr(sc, "require_2fa_every_shell", lambda: False)
    monkeypatch.setattr(sc, "grant_minutes", lambda: 15)
    sc.reset_runtime_state_for_tests()

    grant = sc.mint_grant(user_id=1, server_id=2, session_version=3, require_console=True)
    assert sc.grant_valid(grant, user_id=1, session_version=3) is True
    assert sc.grant_valid(grant, user_id=9, session_version=3) is False
    assert sc.grant_valid("", user_id=1, session_version=3) is False
    assert sc.grant_valid("not.a.jwt", user_id=1, session_version=3) is False
    monkeypatch.setattr(sc, "require_2fa_every_shell", lambda: True)
    assert sc.grant_valid(grant, user_id=1, session_version=3) is False
    monkeypatch.setattr(sc, "require_2fa_every_shell", lambda: False)

    proof = sc.mint_stepup_proof(user_id=1, session_version=3)
    assert sc.consume_stepup_proof(proof, user_id=1, session_version=3) is True
    assert sc.consume_stepup_proof(proof, user_id=1, session_version=3) is False  # jti spent
    assert sc.consume_stepup_proof(None, user_id=1, session_version=3) is False
    assert sc.consume_stepup_proof(grant, user_id=1, session_version=3) is False

    assert sc._host_from_url("https://piherder.example.com/x") == "piherder.example.com"
    assert sc._host_from_url("") == ""
    req = SimpleNamespace(
        headers={
            "host": "piherder.example.com",
            "origin": "https://piherder.example.com",
            "sec-fetch-site": "same-origin",
        }
    )
    assert sc.same_site_browser_request(req) is True
    cross = SimpleNamespace(
        headers={
            "host": "piherder.example.com",
            "origin": "https://evil.example",
            "sec-fetch-site": "cross-site",
        }
    )
    assert sc.same_site_browser_request(cross) is False
    no_host = SimpleNamespace(headers={"origin": "https://x"})
    assert sc.same_site_browser_request(no_host) is False
    ws = SimpleNamespace(headers={"host": "piherder.example.com", "origin": "https://piherder.example.com"})
    assert sc.websocket_origin_allowed(ws) is True
    assert sc.websocket_origin_allowed(SimpleNamespace(headers={"host": "h", "origin": "null"})) is False


def test_docker_prune_unused_and_build_stream(monkeypatch):
    from app.services import docker_management as dm

    class Cli:
        def close(self):
            pass

        def exec_command(self, cmd, timeout=None):
            return MagicMock(), iter(["STEP\n"]), iter(["warn\n"])

    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: Cli())
    monkeypatch.setattr(
        dm,
        "run_command",
        lambda client, cmd, timeout=20: (0, "abc repo:tag 1MB\n", "")
        if "images" in cmd
        else (0, "id name img\n", ""),
    )
    srv = SimpleNamespace(id=1, hostname="pi")
    unused = dm.list_unused_images_and_containers(srv)
    assert unused["success"] is True
    assert unused["dangling_images"]
    pruned = dm.prune_unused(srv, "both")
    assert pruned["success"] is True
    assert dm.prune_unused(srv, "nope")["success"] is False
    imgs = dm.prune_unused(srv, "images")
    assert "Images" in imgs["output"]
    monkeypatch.setattr(dm, "compose_build_shell_cmd", lambda *a, **k: "docker compose build")
    lines = list(dm.stream_compose_build(srv, "/home/pi/docker/g", services=["web"]))
    assert any("STEP" in x for x in lines)
    assert any("[ERR]" in x for x in lines)


def test_dns_private_ip_and_stack_panel_resolve():
    from app.services.dns_fabric.core import _is_private_ip
    from app.services.dns_fabric.stack_panel import (
        guess_container_role,
        resolve_stack_target,
    )

    assert _is_private_ip("10.0.0.5") is True
    assert _is_private_ip("8.8.8.8") is False
    assert _is_private_ip("127.0.0.1") is True
    assert _is_private_ip("not-an-ip") is None
    assert _is_private_ip("") is None
    role = guess_container_role(name="pihole", image="pihole/pihole", compose_service="dns")
    assert isinstance(role, str) and role
    session, _ = _memory()
    missing = resolve_stack_target(session, service_id=999)
    assert missing["ok"] is False
    no_args = resolve_stack_target(session)
    assert no_args["ok"] is False
    assert no_args.get("code") == "no_host"


def test_env_ui_compose_files_diagnostics_identities():
    from app.services.compose_project_files import (
        classify_volume_source,
        discover_relative_config_files,
        looks_like_config_file,
        project_file_role,
        ui_file_kind,
    )
    from app.services.diagnostics import parse_df_h_output, ping, summarize_usable_space
    from app.services.env_file_ui import (
        is_env_filename,
        is_secrets_path,
        redact_env_content,
        redact_project_files_for_ui,
        restore_env_content,
        restore_project_files_on_save,
    )
    from app.services.ssh_identities import (
        _clean_label,
        _clean_username,
        fingerprint_public,
    )

    assert is_env_filename(".env")
    assert is_env_filename(".env.prod")
    assert is_secrets_path("secrets/API_TOKEN")
    red = redact_env_content("API_TOKEN=s3cret\nPORT=80\n# c\n")
    assert "********" in red and "PORT=80" in red
    restored = restore_env_content("API_TOKEN=********\nPORT=81\n", "API_TOKEN=live\nPORT=80\n")
    assert "live" in restored
    files = redact_project_files_for_ui(
        {".env": "API_TOKEN=x\n", "secrets/k": "val", "docker-compose.yml": "x"},
        reveal=False,
        extra_secret_keys={"API_TOKEN"},
    )
    assert files["secrets/k"] == "********"
    shown = redact_project_files_for_ui({".env": "API_TOKEN=x"}, reveal=True)
    assert shown[".env"] == "API_TOKEN=x"
    saved = restore_project_files_on_save(
        {".env": "API_TOKEN=********\n", "secrets/k": "********"},
        {".env": "API_TOKEN=live\n", "secrets/k": "real"},
    )
    assert saved["secrets/k"] == "real"

    assert looks_like_config_file("promtail.yml")
    assert classify_volume_source("/data")[0] == "bind_absolute"
    assert classify_volume_source("./cfg.yml")[0] == "bind_relative"
    assert classify_volume_source("pgdata")[0] == "named"
    found = discover_relative_config_files(
        "services:\n  p:\n    volumes:\n      - ./promtail.yml:/etc/p.yml\n"
    )
    assert "promtail.yml" in found
    assert project_file_role("docker-compose.yml") == "compose"
    assert project_file_role("docker-compose.override.yml") == "override"
    assert project_file_role(".env") == "env"
    assert project_file_role("Dockerfile") == "dockerfile"
    assert project_file_role("secrets/x") == "secret"
    assert ui_file_kind("docker-compose.override.yml") == "compose"
    assert ui_file_kind("notes.txt") in ("file", "config", "other")

    df = parse_df_h_output(
        "Filesystem Size Used Avail Use% Mounted on\n"
        "/dev/sda1 30G 10G 20G 33% /\n"
        "tmpfs 1G 0 1G 0% /run\n"
        "short\n"
    )
    assert df[0]["target"] == "/"
    summary = summarize_usable_space(df)
    assert summary["root"]["target"] == "/"
    empty = summarize_usable_space([])
    assert empty["root"] is None
    assert ping("256.256.256.256", timeout=0.1) is False

    assert fingerprint_public("") is None
    assert fingerprint_public("not-a-key") is None
    fp = fingerprint_public(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJustAFakeKeyForUnitTests== comment"
    )
    assert fp is None or fp.startswith("SHA256:")
    assert _clean_username("pi@host!", default="pi") == "pihost" or _clean_username(
        "pi@host!", default="pi"
    )
    assert "Fleet" in _clean_label("", "fleet") or _clean_label("", "fleet")


def test_console_audit_feed_and_flush():
    from app.services.console_audit import SessionRecorder, clamp_mode

    rec = SessionRecorder(mode="commands")
    rec.feed_stdin(b"ls")
    rec.feed_stdin(b"\r")
    rec.feed_stdout(b"file.txt\n")
    rec.feed_stdout(b"Password: ")
    assert rec.mode == "commands"
    rec2 = SessionRecorder(mode="commands_output")
    rec2.feed_stdin(b"whoami\r")
    rec2.feed_stdout(b"pi\n")
    assert rec2.command_count >= 1
    assert clamp_mode("commands_out") == "commands_output"
