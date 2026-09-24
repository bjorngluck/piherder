"""v1.6 Undo-1 — fail-path Move undo. No live SSH."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import (
    CertificateTarget,
    Job,
    ManagedCertificate,
    Server,
    ServiceDnsRecord,
    User,
)
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services.api_tokens import JOB_FEATURE_KEY
from app.services.service_migrate import host_lock as hl
from app.services.service_migrate.undo import (
    eligible_undo,
    invert_port_map,
    preview_undo,
    reenable_source_certs,
    UndoError,
    run_undo_pipeline,
    undo_move_details,
)


def _server(name: str, host: str) -> Server:
    return Server(
        name=name,
        hostname=host,
        ssh_username="pi",
        ssh_password_encrypted=encrypt_str("x"),
        container_patch_enabled=True,
        os_type="debian",
        dns_name=f"{name}.example.test",
        ip_address="10.0.0.10" if name == "src" else "10.0.0.20",
    )


def _job(server_id: int, *, status: str, step: str, dest_id: int) -> Job:
    payload = undo_move_details(
        source_id=server_id,
        dest_id=dest_id,
        project="grafana",
        dest_project="grafana",
        port_map={"8080/tcp": "8081"},
        failed_step=step,
    )
    details = {
        "project": "grafana",
        "dest_project": "grafana",
        "dest_server_id": dest_id,
        "source_id": server_id,
        "failed_step": step,
        "port_map": {"8080/tcp": "8081"},
    }
    if payload:
        details["undo_move"] = payload
    return Job(
        server_id=server_id,
        job_type="service_migrate",
        status=status,
        details=json.dumps(details),
    )


def test_undo_only_after_post_flip_failure():
    assert undo_move_details(
        source_id=1, dest_id=2, project="grafana", dest_project="grafana",
        port_map={}, failed_step="copy",
    ) is None
    assert undo_move_details(
        source_id=1, dest_id=2, project="grafana", dest_project="grafana",
        port_map={}, failed_step="cutover",
    )["failed_step"] == "cutover"
    green = Job(server_id=1, job_type="service_migrate", status="success", details="{}")
    assert eligible_undo(green) is None
    pre = _job(1, status="failed", step="dest_up", dest_id=2)
    assert eligible_undo(pre) is None
    post = _job(1, status="failed", step="validate", dest_id=2)
    assert eligible_undo(post)["project"] == "grafana"


def test_invert_port_map():
    assert invert_port_map({"8080/tcp": "8081"}) == {"8081/tcp": "8080"}


def test_pipeline_reverts_names_and_stops_without_down(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'undo.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = _server("src", "src.local")
        dst = _server("dst", "dst.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        cert = ManagedCertificate(name="app")
        s.add(cert)
        s.commit()
        s.refresh(cert)
        s.add(ServiceDnsRecord(
            fqdn="graf.example.test",
            target_server_id=dst.id,
            backend_server_id=dst.id,
            docker_project="grafana",
            via_proxy=True,
            certificate_id=cert.id,
        ))
        src_target = CertificateTarget(
            certificate_id=cert.id, server_id=src.id, enabled=False,
        )
        dst_target = CertificateTarget(
            certificate_id=cert.id, server_id=dst.id, enabled=True,
        )
        s.add(src_target)
        s.add(dst_target)
        s.commit()
        s.refresh(dst_target)
        dest_clone_id = dst_target.id
        calls = []

        def dns(*_a, **k):
            calls.append(("dns", k["source"].id, k["dest"].id, dict(k.get("port_map") or {})))
            return {"ok": True, "records": []}

        def rebind(*_a, **k):
            calls.append(("rebind", k["source"].id, k["dest"].id))
            return {"ok": True}

        def stop(server, path):
            calls.append(("stop", path))
            return {"success": True, "action": "stop", "output": "stopped"}

        def start(server, path):
            calls.append(("start", path))
            return {"success": True, "action": "start", "output": "started"}

        out = run_undo_pipeline(
            s,
            source=src,
            dest=dst,
            project="grafana",
            dest_project="grafana",
            port_map={"8080/tcp": "8081"},
            dns_fn=dns,
            rebind_fn=rebind,
            stop_fn=stop,
            start_fn=start,
        )
        assert out["dest_removed"] is False
        assert out["certs_reenabled"] == 1
        assert calls[0][0] == "stop" and calls[0][1].endswith("/grafana")
        assert "down" not in calls[0][1]
        assert calls[1][1] == dst.id and calls[1][2] == src.id
        assert calls[1][3] == {"8081/tcp": "8080"}
        assert calls[2] == ("rebind", dst.id, src.id)
        assert calls[3][0] == "start"
        s.refresh(src_target)
        assert src_target.enabled is True
        still = s.get(CertificateTarget, dest_clone_id)
        assert still is not None
        preview_job = _job(src.id, status="failed", step="cutover", dest_id=dst.id)
        s.add(preview_job)
        s.commit()
        s.refresh(preview_job)
        preview = preview_undo(s, preview_job)
        assert "graf.example.test" in preview["fqdns"]
        assert preview["npm"] == ["graf.example.test"]
        assert preview["keeps_dest_volumes"] is True


def test_stop_down_is_refused(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'undo2.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = _server("src", "src.local")
        dst = _server("dst", "dst.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        with pytest.raises(Exception) as ei:
            run_undo_pipeline(
                s,
                source=src,
                dest=dst,
                project="grafana",
                dest_project="grafana",
                dns_fn=lambda *a, **k: {"ok": True},
                rebind_fn=lambda *a, **k: {"ok": True},
                cert_fn=lambda *a, **k: 0,
                stop_fn=lambda server, path: {"success": True, "action": "down"},
                start_fn=lambda server, path: {"success": True, "action": "start"},
            )
        assert "down" in str(ei.value).lower()


def test_stop_failure_does_not_revert_names(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'undo-stop.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = _server("src", "src.local")
        dst = _server("dst", "dst.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        calls = []

        def dns(*_a, **_k):
            calls.append("dns")
            return {"ok": True}

        with pytest.raises(Exception) as ei:
            run_undo_pipeline(
                s,
                source=src,
                dest=dst,
                project="grafana",
                dest_project="grafana",
                dns_fn=dns,
                rebind_fn=lambda *a, **k: calls.append("rebind"),
                cert_fn=lambda *a, **k: 0,
                stop_fn=lambda server, path: {"success": False, "error": "ssh down"},
                start_fn=lambda server, path: calls.append("start") or {"success": True},
            )
        assert "ssh down" in str(ei.value)
        assert calls == []


def test_dns_failure_starts_dest_again(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'undo-dns.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = _server("src", "src.local")
        dst = _server("dst", "dst.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        starts = []

        def start(server, path):
            starts.append(server.id)
            return {"success": True, "action": "start"}

        with pytest.raises(UndoError) as ei:
            run_undo_pipeline(
                s,
                source=src,
                dest=dst,
                project="grafana",
                dest_project="grafana",
                dns_fn=lambda *a, **k: {"ok": False, "error": "npm down"},
                rebind_fn=lambda *a, **k: {"ok": True},
                cert_fn=lambda *a, **k: 0,
                stop_fn=lambda server, path: {"success": True, "action": "stop"},
                start_fn=start,
            )
        assert "npm down" in str(ei.value)
        assert starts == [dst.id]
        assert "dns" not in ei.value.steps
        assert "stop" not in ei.value.steps


def test_retry_skips_committed_steps(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'undo-retry.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = _server("src", "src.local")
        dst = _server("dst", "dst.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        calls = []
        run_undo_pipeline(
            s,
            source=src,
            dest=dst,
            project="grafana",
            dest_project="grafana",
            done=["stop", "dns", "rebind", "certs"],
            dns_fn=lambda *a, **k: calls.append("dns"),
            rebind_fn=lambda *a, **k: calls.append("rebind"),
            cert_fn=lambda *a, **k: calls.append("certs"),
            stop_fn=lambda server, path: calls.append("stop"),
            start_fn=lambda server, path: calls.append("start") or {"success": True},
        )
        assert calls == ["start"]


def test_pending_undo_blocks_another(tmp_path):
    from app.services.jobs_migrate import _parent_already_undone

    engine = create_engine(
        f"sqlite:///{tmp_path / 'undo-inflight.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = _server("src", "src.local")
        s.add(src)
        s.commit()
        s.refresh(src)
        parent = Job(server_id=src.id, job_type="service_migrate", status="failed", details="{}")
        s.add(parent)
        s.commit()
        s.refresh(parent)
        pending = Job(
            server_id=src.id,
            job_type="service_migrate_undo",
            status="pending",
            details=json.dumps({"parent_job_id": parent.id}),
        )
        s.add(pending)
        s.commit()
        assert _parent_already_undone(s, parent.id) is True
        failed = Job(
            server_id=src.id,
            job_type="service_migrate_undo",
            status="failed",
            details=json.dumps({"parent_job_id": 99999}),
        )
        s.add(failed)
        s.commit()
        assert _parent_already_undone(s, 99999) is False


def test_token_api_never_posts_undo():
    assert "service_migrate" not in JOB_FEATURE_KEY
    assert "service_migrate_undo" not in JOB_FEATURE_KEY
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", None) or set()
        if path.startswith("/api") and "undo" in path:
            assert "POST" not in methods


@pytest.fixture()
def undo_client(tmp_path, monkeypatch):
    monkeypatch.setattr(hl, "migrate_enabled", lambda: True)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies", lambda *a, **k: False,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'undohttp.db'}",
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
        admin = User(
            email="admin@undo.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        viewer = User(
            email="viewer@undo.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.add(viewer)
        src = _server("src", "src.local")
        dst = _server("dst", "dst.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(admin)
        s.refresh(viewer)
        s.refresh(src)
        s.refresh(dst)
        failed = _job(src.id, status="failed", step="rebind", dest_id=dst.id)
        green = _job(src.id, status="success", step="validate", dest_id=dst.id)
        green.details = json.dumps({"project": "grafana", "failed_step": None})
        pre = _job(src.id, status="failed", step="copy", dest_id=dst.id)
        s.add(failed)
        s.add(green)
        s.add(pre)
        s.commit()
        s.refresh(failed)
        s.refresh(green)
        s.refresh(pre)
        ids = {
            "admin": admin.id,
            "viewer": viewer.id,
            "src": src.id,
            "failed": failed.id,
            "green": green.id,
            "pre": pre.id,
        }
    try:
        yield client, ids
    finally:
        app.dependency_overrides.clear()


def _cookie(client: TestClient, uid: int) -> None:
    client.cookies.set("access_token", create_access_token({"sub": str(uid)}))


def test_http_undo_gates(undo_client, monkeypatch):
    client, ids = undo_client
    _cookie(client, ids["viewer"])
    r = client.post(
        f"/servers/{ids['src']}/docker/migrate/undo",
        data={"parent_job_id": str(ids["failed"]), "confirm": "1"},
    )
    assert r.status_code == 403

    _cookie(client, ids["admin"])
    monkeypatch.setattr(hl, "migrate_enabled", lambda: False)
    off = client.post(
        f"/servers/{ids['src']}/docker/migrate/undo",
        data={"parent_job_id": str(ids["failed"]), "confirm": "1"},
    )
    assert off.status_code == 404
    monkeypatch.setattr(hl, "migrate_enabled", lambda: True)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo = client.get(
        f"/servers/{ids['src']}/docker/migrate/undo/preview",
        params={"parent_job_id": ids["failed"]},
    )
    assert demo.status_code == 404
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    missing = client.post(
        f"/servers/{ids['src']}/docker/migrate/undo",
        data={"parent_job_id": str(ids["failed"])},
        headers={"X-PiHerder-Async": "1"},
    )
    assert missing.status_code == 400

    green = client.post(
        f"/servers/{ids['src']}/docker/migrate/undo",
        data={"parent_job_id": str(ids["green"]), "confirm": "1"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert green.status_code == 400

    pre = client.post(
        f"/servers/{ids['src']}/docker/migrate/undo",
        data={"parent_job_id": str(ids["pre"]), "confirm": "1"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert pre.status_code == 400

    captured = {}

    class FakeJob:
        id = 91
        status = "pending"

    def fake_enqueue(parent_job_id, **kwargs):
        captured["parent"] = parent_job_id
        captured["user"] = kwargs.get("user_id")
        return FakeJob()

    monkeypatch.setattr(
        "app.services.jobs.enqueue_service_migrate_undo", fake_enqueue,
    )
    ok = client.post(
        f"/servers/{ids['src']}/docker/migrate/undo",
        data={"parent_job_id": str(ids["failed"]), "confirm": "1"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert ok.status_code == 200, ok.text[:500]
    assert ok.json()["job_type"] == "service_migrate_undo"
    assert captured["parent"] == ids["failed"]
    preview = client.get(
        f"/servers/{ids['src']}/docker/migrate/undo/preview",
        params={"parent_job_id": ids["failed"]},
    )
    assert preview.status_code == 200, preview.text[:500]
    body = preview.json()
    assert body["project"] == "grafana"
    assert body["keeps_dest_dir"] is True
