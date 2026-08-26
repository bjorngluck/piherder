"""v1.4 Stream M — host lock, preflight, copy job. No live SSH."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import (
    AuditLog,
    CertificateTarget,
    ComposeProjectMeta,
    Integration,
    IntegrationBinding,
    Job,
    ManagedCertificate,
    RuntimeEdge,
    Server,
    ServiceDnsRecord,
    StackDeployment,
    User,
    VisualServiceStack,
)
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services.service_migrate import host_lock as hl
from app.services.service_migrate import preflight as pf


@pytest.fixture()
def lock_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lock.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_compose_project_name_rejects_paths():
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("../etc")
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("/home/pi/docker/foo")
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("foo/bar")
    with pytest.raises(hl.HostLockError):
        hl.compose_project_name("")
    assert hl.compose_project_name(" grafana ") == "grafana"


def test_haos_is_implicit_lock(lock_db):
    srv = Server(name="ha", hostname="ha.local", os_type="haos")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    st = hl.lock_state(lock_db, srv, "anything")
    assert st["locked"] is True
    assert st["implicit"] is True
    assert st["reason"] == "haos"
    with pytest.raises(hl.HostLockError) as e:
        hl.assert_unlocked(lock_db, srv, "anything")
    assert e.value.status_code == 403


def test_haos_cannot_lock_or_unlock(lock_db):
    srv = Server(name="ha", hostname="ha.local", os_type="haos")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    with pytest.raises(hl.HostLockError):
        hl.set_host_lock(lock_db, srv, "frigate", reason="hardware")
    with pytest.raises(hl.HostLockError):
        hl.unlock_host(lock_db, srv, "frigate")


def test_lock_unlock_persist(lock_db):
    srv = Server(name="pi", hostname="pi.local", os_type="debian")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    hl.set_host_lock(
        lock_db,
        srv,
        "frigate",
        reason="hardware",
        note="Coral TPU",
        user_id=1,
    )
    lock_db.commit()
    st = hl.lock_state(lock_db, srv, "frigate")
    assert st["locked"] is True
    assert st["reason"] == "hardware"
    assert st["note"] == "Coral TPU"
    with pytest.raises(hl.HostLockError):
        hl.assert_unlocked(lock_db, srv, "frigate")
    # second lock updates same unique row
    hl.set_host_lock(lock_db, srv, "frigate", reason="operator", note="wait")
    lock_db.commit()
    rows = lock_db.exec(
        select(ComposeProjectMeta).where(ComposeProjectMeta.server_id == srv.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].lock_reason == "operator"
    hl.unlock_host(lock_db, srv, "frigate")
    lock_db.commit()
    st2 = hl.lock_state(lock_db, srv, "frigate")
    assert st2["locked"] is False
    hl.assert_unlocked(lock_db, srv, "frigate")


def test_lock_reason_enum(lock_db):
    srv = Server(name="pi", hostname="pi.local")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    with pytest.raises(hl.HostLockError):
        hl.set_host_lock(lock_db, srv, "grafana", reason="haos")
    with pytest.raises(hl.HostLockError):
        hl.set_host_lock(lock_db, srv, "grafana", reason="nope")


def test_annotate_projects(lock_db):
    srv = Server(name="pi", hostname="pi.local")
    lock_db.add(srv)
    lock_db.commit()
    lock_db.refresh(srv)
    hl.set_host_lock(lock_db, srv, "frigate", reason="hardware")
    lock_db.commit()
    projects = [{"name": "frigate"}, {"name": "grafana"}]
    hl.annotate_projects(lock_db, srv, projects)
    assert projects[0]["host_lock"]["locked"] is True
    assert projects[1]["host_lock"]["locked"] is False


def test_migrate_enabled_default_off():
    assert hl.migrate_enabled() is False


@pytest.fixture()
def lock_client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies",
        lambda *a, **k: False,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lockhttp.db'}",
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
            email="admin@lock.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        viewer = User(
            email="viewer@lock.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.add(viewer)
        s.commit()
        s.refresh(admin)
        s.refresh(viewer)
        pi = Server(
            name="Lab Pi",
            hostname="lab.local",
            ssh_username="pi",
            ssh_password_encrypted=encrypt_str("x"),
            container_patch_enabled=True,
            os_type="debian",
        )
        ha = Server(
            name="HAOS",
            hostname="ha.local",
            ssh_username="root",
            ssh_password_encrypted=encrypt_str("x"),
            os_type="haos",
        )
        s.add(pi)
        s.add(ha)
        s.commit()
        s.refresh(pi)
        s.refresh(ha)
        ids = {
            "admin": admin.id,
            "viewer": viewer.id,
            "pi": pi.id,
            "ha": ha.id,
        }
    try:
        yield client, ids, engine
    finally:
        app.dependency_overrides.clear()


def _cookie(uid: int) -> dict[str, str]:
    return {"access_token": create_access_token({"sub": str(uid)})}


def test_http_lock_unlock_admin(lock_client):
    client, ids, engine = lock_client
    r = client.post(
        f"/servers/{ids['pi']}/docker/host-lock",
        data={"project": "frigate", "reason": "hardware", "note": "Coral TPU"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "host_locked" in (r.headers.get("location") or "")
    with Session(engine) as s:
        row = s.exec(
            select(ComposeProjectMeta).where(
                ComposeProjectMeta.server_id == ids["pi"],
                ComposeProjectMeta.compose_project == "frigate",
            )
        ).first()
        assert row is not None
        assert row.host_locked is True
        assert row.lock_reason == "hardware"
        audit = s.exec(
            select(AuditLog).where(AuditLog.action == "service_host_lock")
        ).first()
        assert audit is not None
        assert "frigate" in (audit.details or "")
        assert "Coral" in (audit.details or "")
    r2 = client.post(
        f"/servers/{ids['pi']}/docker/host-unlock",
        data={"project": "frigate"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "host_unlocked" in (r2.headers.get("location") or "")
    with Session(engine) as s:
        row = s.exec(
            select(ComposeProjectMeta).where(
                ComposeProjectMeta.server_id == ids["pi"],
                ComposeProjectMeta.compose_project == "frigate",
            )
        ).first()
        assert row.host_locked is False
        unlock = s.exec(
            select(AuditLog).where(AuditLog.action == "service_host_unlock")
        ).first()
        assert unlock is not None


def test_http_lock_viewer_403(lock_client):
    client, ids, _engine = lock_client
    r = client.post(
        f"/servers/{ids['pi']}/docker/host-lock",
        data={"project": "grafana", "reason": "operator"},
        cookies=_cookie(ids["viewer"]),
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_http_lock_haos_refused(lock_client):
    client, ids, engine = lock_client
    r = client.post(
        f"/servers/{ids['ha']}/docker/host-lock",
        data={"project": "addon", "reason": "operator"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers.get("location") or ""
    assert "error=host_lock" in loc
    with Session(engine) as s:
        n = len(
            s.exec(
                select(ComposeProjectMeta).where(
                    ComposeProjectMeta.server_id == ids["ha"]
                )
            ).all()
        )
        assert n == 0


def test_http_lock_rejects_path_project(lock_client):
    client, ids, _engine = lock_client
    r = client.post(
        f"/servers/{ids['pi']}/docker/host-lock",
        data={"project": "../etc", "reason": "operator"},
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=host_lock" in (r.headers.get("location") or "")


def _inv(*names, ports=None, mounts=None, extra=None, mounts_detail=None):
    containers = []
    if ports or mounts or mounts_detail:
        row = {
            "name": "web",
            "running": True,
            "ports_display": ports or "",
            "mounts_list": mounts or [],
        }
        if mounts_detail:
            row["mounts_detail"] = mounts_detail
        containers.append(row)
    projects = [{"name": n, "containers": list(containers)} for n in names]
    payload = {"v": 2, "projects": projects}
    if extra:
        payload["projects"][0].update(extra)
    return json.dumps(payload)


def test_preflight_blocks_haos_and_same_host(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True, os_type="debian")
    ha = Server(name="ha", hostname="ha.local", container_patch_enabled=True, os_type="haos")
    lock_db.add(src)
    lock_db.add(ha)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(ha)
    src.docker_inventory_json = _inv("grafana")
    lock_db.add(src)
    lock_db.commit()
    r = pf.run_preflight(lock_db, source=src, dest=ha, project="grafana")
    ids = {b["id"] for b in r["blocks"]}
    assert "haos_dest" in ids
    r2 = pf.run_preflight(lock_db, source=src, dest=src, project="grafana")
    assert any(b["id"] == "same_host" for b in r2["blocks"])


def test_preflight_dest_docker_off_and_project_exists(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True)
    dest = Server(name="b", hostname="b.local", container_patch_enabled=False)
    dest2 = Server(name="c", hostname="c.local", container_patch_enabled=True)
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.add(dest2)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    lock_db.refresh(dest2)
    src.docker_inventory_json = _inv("grafana")
    dest2.docker_inventory_json = _inv("grafana")
    lock_db.add(src)
    lock_db.add(dest2)
    lock_db.commit()
    r = pf.run_preflight(lock_db, source=src, dest=dest, project="grafana")
    assert any(b["id"] == "dest_docker_off" for b in r["blocks"])
    r2 = pf.run_preflight(lock_db, source=src, dest=dest2, project="grafana")
    assert any(b["id"] == "dest_project_exists" for b in r2["blocks"])


def test_preflight_host_lock_and_arch(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True)
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True)
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("frigate")
    lock_db.add(src)
    lock_db.commit()
    hl.set_host_lock(lock_db, src, "frigate", reason="hardware", note="TPU")
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="frigate",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "x86_64", "docker_base_writable": True, "disk_free_bytes": 10**12},
    )
    ids = {b["id"] for b in r["blocks"]}
    assert "host_lock" in ids
    assert "arch_mismatch" in ids


def test_preflight_ports_dns_npm_busy(lock_db):
    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        dns_name="a.example.test",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        dns_name="",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("app", ports="8080->80/tcp")
    dest.docker_inventory_json = _inv("other", ports="8080->80/tcp")
    lock_db.add(src)
    lock_db.add(dest)
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="app",
        via_proxy=False,
        external_dns_status="checklist",
    )
    rec2 = ServiceDnsRecord(
        fqdn="via.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="app",
        via_proxy=True,
        external_dns_status="done",
    )
    lock_db.add(rec)
    lock_db.add(rec2)
    job = Job(
        server_id=dest.id,
        job_type="backup",
        status="running",
        details="{}",
    )
    lock_db.add(job)
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="app",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
    )
    ids = {b["id"] for b in r["blocks"]}
    assert "port_clash" in ids
    assert "dest_dns_name" in ids
    assert "npm_missing" in ids
    assert "busy_dest" in ids
    assert any(w["id"] == "external_dns" for w in r["warns"])
    dests = pf.eligible_destinations(lock_db, src)
    assert [d.id for d in dests] == [dest.id]


def test_preflight_npm_unmatched_and_devices(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True, dns_name="b.test")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv(
        "cam",
        mounts=["/dev/apex_0:/dev/apex_0", "./data:/data"],
    )
    lock_db.add(src)
    npm = Integration(
        type="npm",
        name="edge",
        base_url="http://npm.test",
        enabled=True,
        last_status_json=json.dumps(
            {
                "ok": True,
                "proxy_hosts": [
                    {"id": "1", "domain_names": ["other.test"], "forward_host": "10.0.0.1"}
                ],
            }
        ),
    )
    lock_db.add(npm)
    rec = ServiceDnsRecord(
        fqdn="cam.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="cam",
        via_proxy=True,
        external_dns_status="done",
    )
    lock_db.add(rec)
    lock_db.commit()
    lock_db.refresh(npm)
    bind = IntegrationBinding(
        integration_id=npm.id,
        server_id=src.id,
        role="service",
        docker_project="cam",
        external_id="9",
        external_label="http://10.1.2.3:5000",
    )
    lock_db.add(bind)
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="cam",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
    )
    ids = {b["id"] for b in r["blocks"]}
    assert "npm_unmatched" in ids
    assert any(w["id"] == "devices" for w in r["warns"])
    assert any(w["id"] == "kuma_ip" for w in r["warns"])


def test_http_migrate_flag_off_404(lock_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.server_docker.host_lock_svc.migrate_surface_allowed",
        lambda: False,
    )
    client, ids, _engine = lock_client
    r = client.get(
        f"/servers/{ids['pi']}/docker/migrate?project=grafana",
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_http_migrate_wizard_and_preflight(lock_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.server_docker.host_lock_svc.migrate_surface_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.probe_host_facts",
        lambda server: {
            "arch": "aarch64",
            "disk_free_bytes": 10**12,
            "docker_base_writable": True,
        },
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.herder_free_bytes",
        lambda: 10**12,
    )
    monkeypatch.setattr(
        "app.services.service_migrate.facts.inspect_project_mounts",
        lambda server, row: row,
    )
    client, ids, engine = lock_client
    with Session(engine) as s:
        other = Server(
            name="Pi 2",
            hostname="lab2.local",
            ssh_username="pi",
            ssh_password_encrypted=encrypt_str("x"),
            container_patch_enabled=True,
            os_type="debian",
            dns_name="pi2.example.test",
        )
        s.add(other)
        pi = s.get(Server, ids["pi"])
        pi.container_patch_enabled = True
        pi.docker_inventory_json = _inv("grafana")
        s.add(pi)
        s.commit()
        s.refresh(other)
        dest_id = other.id
    r = client.get(
        f"/servers/{ids['pi']}/docker/migrate?project=grafana",
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Move grafana" in r.text
    assert 'data-testid="migrate-dest"' in r.text
    assert 'data-testid="migrate-preflight-wait"' in r.text
    r2 = client.get(
        f"/servers/{ids['pi']}/docker/migrate/preflight?project=grafana&dest={dest_id}",
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r2.status_code == 200, r2.text[:3000]
    assert 'data-testid="migrate-preflight-result"' in r2.text
    assert 'data-testid="migrate-dest-project"' in r2.text
    assert 'data-testid="migrate-dest-project-path"' in r2.text
    assert 'data-testid="migrate-recheck"' in r2.text


def test_http_migrate_viewer_403(lock_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.server_docker.host_lock_svc.migrate_surface_allowed",
        lambda: True,
    )
    client, ids, _engine = lock_client
    r = client.get(
        f"/servers/{ids['pi']}/docker/migrate?project=grafana",
        cookies=_cookie(ids["viewer"]),
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_preflight_named_volume_not_outside_jail(lock_db):
    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        dns_name="b.test",
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv(
        "grafana",
        mounts_detail=[
            {
                "source": "/var/lib/docker/volumes/grafana_data/_data",
                "destination": "/var/lib/grafana",
                "type": "volume",
                "name": "grafana_data",
                "size_bytes": 4096,
            }
        ],
    )
    lock_db.add(src)
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
    )
    assert "bind_outside_jail" not in {b["id"] for b in r["blocks"]}
    named = [i for i in r["dataset"]["items"] if i["kind"] == "named"]
    assert named and named[0]["volume"] == "grafana_data"
    assert r["leftover_remove"]["project_path"] == "/home/pi/docker/grafana"
    assert r["leftover_remove"]["named_volumes"] == ["grafana_data"]


def test_preflight_busy_dest_as_migrate_target(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True, dns_name="b.test")
    other = Server(name="c", hostname="c.local", container_patch_enabled=True)
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.add(other)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    lock_db.refresh(other)
    src.docker_inventory_json = _inv("grafana")
    lock_db.add(src)
    lock_db.add(
        Job(
            server_id=other.id,
            job_type="service_migrate",
            status="running",
            details=json.dumps({"dest_server_id": dest.id}),
        )
    )
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
    )
    assert any(b["id"] == "busy_dest" for b in r["blocks"])


def test_preflight_ignores_own_running_migrate_job(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True, dns_name="b.test")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("openwebui")
    job = Job(
        server_id=src.id,
        job_type="service_migrate",
        status="running",
        details=json.dumps({"dest_server_id": dest.id, "project": "openwebui"}),
    )
    lock_db.add(src)
    lock_db.add(job)
    lock_db.commit()
    lock_db.refresh(job)
    blocked = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
    )
    ids = {b["id"] for b in blocked["blocks"]}
    assert "busy_source" in ids
    assert "busy_dest" in ids
    ok = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        ignore_job_id=job.id,
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
    )
    ids2 = {b["id"] for b in ok["blocks"]}
    assert "busy_source" not in ids2
    assert "busy_dest" not in ids2


def test_preflight_blocks_truncated_docker_ps_mount(lock_db):
    from app.services.service_migrate.copy import CopyError, rsync_host_to_herder
    from app.services.service_migrate.overrides import is_truncated_host_path

    assert is_truncated_host_path("/home/piherder…") is True
    assert is_truncated_host_path("/home/bjorn/docker/openwebui") is False
    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True, dns_name="b.test")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("openwebui", mounts=["/home/piherder…:/data"])
    lock_db.add(src)
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
    )
    assert any(b["id"] == "bind_truncated" for b in r["blocks"])
    assert r["dataset"]["truncated"]
    assert not any(i.get("source") == "/home/piherder…" for i in r["dataset"]["items"])
    with pytest.raises(CopyError, match="truncated"):
        rsync_host_to_herder(src, "/home/piherder…", "/tmp")


def test_staging_tree_summary_lists_dotfiles(tmp_path):
    from app.services.service_migrate.copy import staging_tree_summary

    root = tmp_path / "proj"
    root.mkdir()
    (root / "docker-compose.yml").write_text("x\n", encoding="utf-8")
    (root / ".env").write_text("A=1\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "db").write_text("z\n", encoding="utf-8")
    text = staging_tree_summary(root)
    assert "3 file" in text
    assert ".env" in text
    assert "docker-compose.yml" in text
    assert "data/" in text


def test_copy_rejects_bad_volume_name(tmp_path):
    from app.services.service_migrate.copy import CopyError, copy_named_volume

    src = Server(name="a", hostname="a.local")
    dest = Server(name="b", hostname="b.local")
    with pytest.raises(CopyError):
        copy_named_volume(src, dest, "../etc", tmp_path)
    with pytest.raises(CopyError):
        copy_named_volume(src, dest, "foo/bar", tmp_path)


def test_pipeline_copy_and_start_mocked(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import run_copy_and_start

    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        dns_name="b.test",
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv(
        "grafana",
        mounts_detail=[
            {
                "source": "/var/lib/docker/volumes/grafana_data/_data",
                "destination": "/var/lib/grafana",
                "type": "volume",
                "name": "grafana_data",
            },
            {
                "source": "/home/pi/docker/extra-bind",
                "destination": "/extra",
                "type": "bind",
            },
        ],
    )
    lock_db.add(src)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )
    pulls, pushes, vols, stops, ups = [], [], [], [], []

    def pull(server, remote, local, log=None):
        pulls.append((server.id, remote, str(local)))
        Path(local).mkdir(parents=True, exist_ok=True)

    def push(server, local, remote, log=None):
        pushes.append((server.id, str(local), remote))

    def vol(source, dest, volume, staging, log=None):
        vols.append(volume)

    def stop(srv, path):
        stops.append((srv.id, path))
        return {"success": True}

    def up(srv, path):
        ups.append((srv.id, path))
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        job_id=9,
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
        pull_fn=pull,
        push_fn=push,
        vol_fn=vol,
        stop_fn=stop,
        up_fn=up,
        cutover_fn=lambda *a, **kw: {"ok": True, "records": []},
        rebind_fn=lambda *a, **kw: {"ok": True, "counts": {}},
        validate_fn=lambda *a, **kw: {"ok": True, "tls": [], "kuma": []},
    )
    assert r["ok"] is True
    assert stops and stops[0][0] == src.id
    assert ups and ups[0][0] == dest.id
    assert "grafana_data" in vols
    assert any("grafana" in p[1] for p in pulls)
    assert any("grafana" in p[2] for p in pushes)
    assert any("extra-bind" in p[1] for p in pulls)


def test_http_migrate_start_queues_job(lock_client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.server_docker.host_lock_svc.migrate_surface_allowed",
        lambda: True,
    )

    class FakeJob:
        id = 77
        status = "pending"

    captured = {}

    def fake_enqueue(source_id, dest_id, project, **kwargs):
        captured["source_id"] = source_id
        captured["dest_id"] = dest_id
        captured["project"] = project
        captured["leftover"] = kwargs.get("leftover")
        captured["devices_ack"] = kwargs.get("devices_ack")
        captured["dest_project"] = kwargs.get("dest_project")
        captured["port_map"] = kwargs.get("port_map")
        return FakeJob()

    monkeypatch.setattr("app.services.jobs.enqueue_service_migrate", fake_enqueue)
    client, ids, engine = lock_client
    with Session(engine) as s:
        other = Server(
            name="Pi 2",
            hostname="lab2.local",
            ssh_username="pi",
            ssh_password_encrypted=encrypt_str("x"),
            container_patch_enabled=True,
            os_type="debian",
            dns_name="pi2.example.test",
        )
        s.add(other)
        s.commit()
        s.refresh(other)
        dest_id = other.id
    r = client.post(
        f"/servers/{ids['pi']}/docker/migrate",
        data={
            "project": "grafana",
            "dest": str(dest_id),
            "leftover": "down",
            "devices_ack": "1",
            "dest_project": "grafana-b",
            "dest_port_8080_tcp": "8081",
        },
        cookies=_cookie(ids["admin"]),
        headers={"X-PiHerder-Async": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:2000]
    body = r.json()
    assert body["job_id"] == 77
    assert body["job_type"] == "service_migrate"
    assert captured["source_id"] == ids["pi"]
    assert captured["dest_id"] == dest_id
    assert captured["project"] == "grafana"
    assert captured["leftover"] == "down"
    assert captured["devices_ack"] is True
    assert captured["dest_project"] == "grafana-b"
    assert captured["port_map"]["8080/tcp"] == "8081"
    r_rm_no = client.post(
        f"/servers/{ids['pi']}/docker/migrate",
        data={
            "project": "grafana",
            "dest": str(dest_id),
            "leftover": "remove",
        },
        cookies=_cookie(ids["admin"]),
        headers={"X-PiHerder-Async": "1"},
        follow_redirects=False,
    )
    assert r_rm_no.status_code == 400
    r_rm = client.post(
        f"/servers/{ids['pi']}/docker/migrate",
        data={
            "project": "grafana",
            "dest": str(dest_id),
            "leftover": "remove",
            "leftover_remove_ack": "1",
        },
        cookies=_cookie(ids["admin"]),
        headers={"X-PiHerder-Async": "1"},
        follow_redirects=False,
    )
    assert r_rm.status_code == 200, r_rm.text[:2000]
    assert captured["leftover"] == "remove"
    r_v = client.post(
        f"/servers/{ids['pi']}/docker/migrate",
        data={"project": "grafana", "dest": str(dest_id)},
        cookies=_cookie(ids["viewer"]),
        headers={"X-PiHerder-Async": "1"},
        follow_redirects=False,
    )
    assert r_v.status_code == 403


def test_cutover_direct_cname_and_restartdns(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        dns_name="a.example.test",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        dns_name="b.example.test",
        ip_address="10.0.0.9",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="grafana",
        via_proxy=False,
        managed_on_pihole=True,
        external_dns_status="none",
    )
    lock_db.add(rec)
    lock_db.commit()
    lock_db.refresh(rec)
    upserts = []
    restarts = []

    def upsert(session, **kw):
        upserts.append(kw)
        return rec, []

    r = retarget_dns_npm(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        upsert_fn=upsert,
        npm_put_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("npm")),
        restartdns_fn=lambda: restarts.append({"ok": True, "name": "ph"}) or [
            {"ok": True, "name": "ph"}
        ],
    )
    assert r["ok"] is True
    assert upserts[0]["target_server_id"] == dest.id
    assert upserts[0]["backend_server_id"] == dest.id
    assert upserts[0]["via_proxy"] is False
    assert restarts
    assert r["records"][0]["action"] == "cname"


def test_cutover_npm_keeps_edge_cname(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        dns_name="b.test",
        ip_address="10.1.2.3",
    )
    npm_edge = Server(name="npm", hostname="npm.local", dns_name="npm.test")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.add(npm_edge)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    lock_db.refresh(npm_edge)
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=npm_edge.id,
        backend_server_id=src.id,
        docker_project="grafana",
        via_proxy=True,
        managed_on_pihole=True,
        external_dns_status="done",
    )
    lock_db.add(rec)
    npm = Integration(
        type="npm",
        name="edge",
        base_url="http://npm.test",
        enabled=True,
        last_status_json=json.dumps(
            {
                "ok": True,
                "proxy_hosts": [
                    {
                        "id": "12",
                        "domain_names": ["app.example.test"],
                        "forward_host": "10.0.0.1",
                    }
                ],
            }
        ),
    )
    lock_db.add(npm)
    lock_db.commit()
    lock_db.refresh(rec)
    puts = []
    upserts = []

    def upsert(session, **kw):
        upserts.append(kw)
        return rec, []

    r = retarget_dns_npm(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        upsert_fn=upsert,
        npm_put_fn=lambda fqdn, hid, host: puts.append((fqdn, hid, host))
        or {"id": hid, "forward_host": host},
        restartdns_fn=lambda: (_ for _ in ()).throw(AssertionError("ftl")),
    )
    assert r["ok"] is True
    assert puts[0][0] == "app.example.test"
    assert puts[0][2] == "10.1.2.3"
    assert upserts[0]["target_server_id"] == npm_edge.id
    assert upserts[0]["backend_server_id"] == dest.id
    assert upserts[0]["via_proxy"] is True


def test_cutover_skips_host_identity(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        dns_name="host.example.test",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        dns_name="b.example.test",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    rec = ServiceDnsRecord(
        fqdn="host.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="grafana",
        via_proxy=False,
    )
    lock_db.add(rec)
    lock_db.commit()
    r = retarget_dns_npm(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        upsert_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("upsert")),
        restartdns_fn=lambda: [],
    )
    assert r["records"][0]["action"] == "skip_host_identity"


def test_npm_retarget_put_full_object(monkeypatch):
    from app.services.integrations import npm as npm_mod

    captured = {}

    def fake_get(base_url, token, path, **kw):
        if path.endswith("/12"):
            return {
                "id": 12,
                "domain_names": ["app.example.test"],
                "forward_host": "10.0.0.1",
                "forward_port": 81,
                "forward_scheme": "http",
                "ssl_forced": True,
                "certificate_id": 3,
                "enabled": True,
                "created_on": "ignore-me",
            }
        return []

    def fake_put(base_url, token, path, body, **kw):
        captured["path"] = path
        captured["body"] = body
        return {"id": 12}

    monkeypatch.setattr(npm_mod, "_get_json", fake_get)
    monkeypatch.setattr(npm_mod, "_put_json", fake_put)
    r = npm_mod.retarget_proxy_host_backend(
        "http://npm.test", "tok", "12", "10.1.2.3"
    )
    assert r["old_forward_host"] == "10.0.0.1"
    assert r["forward_host"] == "10.1.2.3"
    assert captured["path"] == "/api/nginx/proxy-hosts/12"
    assert captured["body"]["forward_host"] == "10.1.2.3"
    assert captured["body"]["forward_port"] == 81
    assert captured["body"]["ssl_forced"] is True
    assert captured["body"]["certificate_id"] == 3
    assert "created_on" not in captured["body"]


def _pair_hosts(lock_db):
    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
        dns_name="a.test",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
        dns_name="b.test",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    return src, dest


def test_pipeline_requires_devices_ack(lock_db):
    from app.services.service_migrate.pipeline import MigrateError, run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv("cam", mounts=["/dev/apex_0:/dev/apex_0"])
    lock_db.add(src)
    lock_db.commit()
    with pytest.raises(MigrateError, match="Hardware"):
        run_copy_and_start(
            lock_db,
            source=src,
            dest=dest,
            project="cam",
            job_id=3,
            source_facts={"arch": "aarch64"},
            dest_facts={
                "arch": "aarch64",
                "docker_base_writable": True,
                "disk_free_bytes": 10**12,
            },
            herder_free=10**12,
            devices_ack=False,
        )


def test_pipeline_leftover_down(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv("grafana")
    lock_db.add(src)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )
    downs = []

    def dummy(*a, **k):
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        job_id=4,
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
        pull_fn=lambda *a, **k: None,
        push_fn=lambda *a, **k: None,
        vol_fn=lambda *a, **k: None,
        stop_fn=dummy,
        up_fn=dummy,
        cutover_fn=lambda *a, **k: {"ok": True},
        rebind_fn=lambda *a, **k: {"ok": True},
        validate_fn=lambda *a, **k: {"ok": True},
        leftover="down",
        down_fn=lambda srv, path: downs.append((srv.id, path)) or {"success": True},
    )
    assert r["leftover"] == "down"
    assert downs and downs[0][0] == src.id


def test_pipeline_dest_up_includes_compose_output(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import MigrateError, run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv("openwebui")
    lock_db.add(src)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )

    def dummy(*a, **k):
        return {"success": True}

    with pytest.raises(MigrateError, match="bind source path does not exist") as ei:
        run_copy_and_start(
            lock_db,
            source=src,
            dest=dest,
            project="openwebui",
            job_id=12,
            source_facts={"arch": "aarch64"},
            dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
            herder_free=10**12,
            pull_fn=dummy,
            push_fn=dummy,
            vol_fn=dummy,
            stop_fn=dummy,
            up_fn=lambda *a, **k: {
                "success": False,
                "up_ok": False,
                "pull_ok": True,
                "error": "up -d failed",
                "output": "=== docker compose up -d (rc=1) ===\nError: bind source path does not exist: /home/piherder/open-webui-data",
            },
            cutover_fn=lambda *a, **k: {"ok": True},
            rebind_fn=lambda *a, **k: {"ok": True},
            validate_fn=lambda *a, **k: {"ok": True},
        )
    assert "up -d failed" in str(ei.value)


def test_pipeline_dest_up_ok_despite_pull_error(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv("openwebui")
    lock_db.add(src)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )

    def dummy(*a, **k):
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        job_id=13,
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
        pull_fn=dummy,
        push_fn=dummy,
        vol_fn=dummy,
        stop_fn=dummy,
        up_fn=lambda *a, **k: {
            "success": False,
            "up_ok": True,
            "pull": True,
            "pull_ok": False,
            "error": "pull failed: unauthorized",
            "output": "unauthorized",
        },
        cutover_fn=lambda *a, **k: {"ok": True},
        rebind_fn=lambda *a, **k: {"ok": True},
        validate_fn=lambda *a, **k: {"ok": True},
    )
    assert r["ok"] is True


def test_normalize_leftover_and_path_jail():
    from app.services.service_migrate.leftover import (
        LeftoverError,
        jailed_source_project_path,
        named_volumes_from_dataset,
        normalize_leftover,
    )

    assert normalize_leftover(None) == "stopped"
    assert normalize_leftover("DOWN") == "down"
    assert normalize_leftover("remove") == "remove"
    assert normalize_leftover("wipe") == "stopped"
    src = Server(
        name="a",
        hostname="a.local",
        docker_base_dir="/home/pi/docker",
        ssh_username="pi",
    )
    assert jailed_source_project_path(src, "grafana") == "/home/pi/docker/grafana"
    with pytest.raises(LeftoverError):
        jailed_source_project_path(
            Server(name="x", hostname="x", docker_base_dir="/", ssh_username="root"),
            "grafana",
        )
    vols = named_volumes_from_dataset(
        {
            "items": [
                {"kind": "named", "volume": "grafana_data", "source": "/var/lib/docker/volumes/grafana_data/_data"},
                {"kind": "named", "volume": "../etc", "source": "x"},
                {"kind": "bind_relative", "source": "./data"},
            ]
        }
    )
    assert vols == ["grafana_data"]


def test_pipeline_leftover_remove_source_only(lock_db, tmp_path, monkeypatch):
    from app.models import ComposeProjectMeta
    from app.services.service_migrate.pipeline import run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv(
        "grafana",
        mounts_detail=[
            {
                "source": "/var/lib/docker/volumes/grafana_data/_data",
                "destination": "/var/lib/grafana",
                "type": "volume",
                "name": "grafana_data",
            }
        ],
    )
    lock_db.add(src)
    lock_db.add(
        ComposeProjectMeta(
            server_id=src.id, compose_project="grafana", host_locked=False
        )
    )
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )
    downs, vols, trees = [], [], []

    def dummy(*a, **k):
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        job_id=5,
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
        pull_fn=lambda *a, **k: None,
        push_fn=lambda *a, **k: None,
        vol_fn=lambda *a, **k: None,
        stop_fn=dummy,
        up_fn=dummy,
        cutover_fn=lambda *a, **k: {"ok": True},
        rebind_fn=lambda *a, **k: {"ok": True},
        validate_fn=lambda *a, **k: {"ok": True},
        leftover="remove",
        down_fn=lambda srv, path: downs.append((srv.id, path)) or {"success": True},
        rm_vol_fn=lambda srv, vol: vols.append((srv.id, vol)) or {"success": True},
        rm_tree_fn=lambda srv, path: trees.append((srv.id, path)) or {"success": True},
    )
    assert r["leftover"] == "remove"
    assert r["leftover_detail"]["project_removed"] is True
    assert r["leftover_detail"]["volumes_removed"] == ["grafana_data"]
    assert downs and downs[0][0] == src.id
    assert vols == [(src.id, "grafana_data")]
    assert trees and trees[0][0] == src.id
    assert trees[0][1] == "/home/pi/docker/grafana"
    assert dest.id not in {x[0] for x in downs + vols + trees}
    remaining = lock_db.exec(
        select(ComposeProjectMeta).where(ComposeProjectMeta.server_id == src.id)
    ).all()
    assert remaining == []


def test_leftover_remove_refuses_wrong_path(lock_db):
    from app.services.service_migrate.leftover import LeftoverError, apply_leftover

    src, dest = _pair_hosts(lock_db)
    with pytest.raises(LeftoverError, match="project path"):
        apply_leftover(
            lock_db,
            source=src,
            dest=dest,
            project="grafana",
            leftover="remove",
            src_proj="/etc/grafana",
        )


def test_leftover_remove_disables_source_cert(lock_db):
    from app.services.service_migrate.leftover import apply_leftover

    src, dest = _pair_hosts(lock_db)
    cert = ManagedCertificate(name="app-tls", fingerprint_sha256="ab" * 32)
    lock_db.add(cert)
    lock_db.commit()
    lock_db.refresh(cert)
    tgt = CertificateTarget(
        certificate_id=cert.id, server_id=src.id, enabled=True, verify_url="https://app.test"
    )
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=dest.id,
        backend_server_id=dest.id,
        docker_project="grafana",
        certificate_id=cert.id,
    )
    lock_db.add(tgt)
    lock_db.add(rec)
    lock_db.commit()
    out = apply_leftover(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        leftover="remove",
        dataset={"items": []},
        down_fn=lambda *a, **k: {"success": True},
        rm_vol_fn=lambda *a, **k: {"success": True},
        rm_tree_fn=lambda *a, **k: {"success": True},
    )
    assert out["project_removed"] is True
    assert out["certs_disabled"] == 1
    lock_db.refresh(tgt)
    assert tgt.enabled is False


def test_rebind_moves_rows(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    dep = StackDeployment(server_id=src.id, project_name="grafana")
    bind = IntegrationBinding(
        integration_id=1,
        server_id=src.id,
        role="service",
        docker_project="grafana",
        external_id="9",
        last_state="up",
    )
    vs = VisualServiceStack(
        server_id=src.id, compose_project="grafana", name="Main", slug="main"
    )
    edge = RuntimeEdge(
        from_server_id=src.id,
        from_project="grafana",
        to_server_id=src.id,
        to_project="grafana",
        kind="depends_on",
        source="manual",
    )
    cert = ManagedCertificate(name="app-tls", fingerprint_sha256="ab" * 32)
    lock_db.add(dep)
    lock_db.add(bind)
    lock_db.add(vs)
    lock_db.add(edge)
    lock_db.add(cert)
    lock_db.commit()
    lock_db.refresh(cert)
    tgt = CertificateTarget(certificate_id=cert.id, server_id=src.id, verify_url="https://app.test")
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="grafana",
        certificate_id=cert.id,
    )
    lock_db.add(tgt)
    lock_db.add(rec)
    lock_db.commit()
    out = rebind_control_plane(lock_db, source=src, dest=dest, project="grafana")
    assert out["counts"]["stack_deployments"] == 1
    assert out["counts"]["kuma_bindings"] == 1
    assert out["counts"]["visual_stacks"] == 1
    assert out["counts"]["edges"] == 1
    assert out["counts"]["cert_targets"] == 1
    lock_db.refresh(dep)
    lock_db.refresh(bind)
    lock_db.refresh(vs)
    lock_db.refresh(edge)
    assert dep.server_id == dest.id
    assert bind.server_id == dest.id
    assert vs.server_id == dest.id
    assert edge.from_server_id == dest.id
    clones = lock_db.exec(
        select(CertificateTarget).where(CertificateTarget.server_id == dest.id)
    ).all()
    assert len(clones) == 1
    assert clones[0].certificate_id == cert.id


def test_validate_tls_fail_and_kuma_down(lock_db):
    from app.services.service_migrate.validate import ValidateError, validate_migrate

    src, dest = _pair_hosts(lock_db)
    cert = ManagedCertificate(name="app-tls", fingerprint_sha256="cd" * 32)
    lock_db.add(cert)
    lock_db.commit()
    lock_db.refresh(cert)
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=dest.id,
        backend_server_id=dest.id,
        docker_project="grafana",
        certificate_id=cert.id,
    )
    lock_db.add(rec)
    lock_db.commit()

    def bad_tls(**kw):
        assert "sni=app.example.test" in kw["verify_url"]
        return {"ok": False, "status": "failed", "message": "fingerprint mismatch"}

    with pytest.raises(ValidateError, match="TLS"):
        validate_migrate(
            lock_db,
            source=src,
            dest=dest,
            project="grafana",
            tls_fn=bad_tls,
            kuma_poll_fn=lambda iid: None,
        )

    rec.certificate_id = None
    lock_db.add(rec)
    bind = IntegrationBinding(
        integration_id=2,
        server_id=dest.id,
        role="service",
        docker_project="grafana",
        external_id="mon",
        external_label="app",
        last_state="down",
    )
    lock_db.add(bind)
    lock_db.commit()
    with pytest.raises(ValidateError, match="Kuma"):
        validate_migrate(
            lock_db,
            source=src,
            dest=dest,
            project="grafana",
            tls_fn=lambda **kw: {"ok": True, "status": "ok"},
            kuma_poll_fn=lambda iid: None,
        )


def test_port_map_and_dest_project_override(lock_db):
    from app.services.service_migrate.overrides import (
        parse_port_map_from_mapping,
        remap_named_volume,
        validate_port_map,
    )

    parsed = parse_port_map_from_mapping(
        {"dest_port_8080_tcp": "8081", "dest_port_53_udp": "5353", "other": "x"}
    )
    assert parsed == {"8080/tcp": "8081", "53/udp": "5353"}
    clean, errs = validate_port_map(parsed)
    assert not errs
    assert clean["8080/tcp"] == "8081"
    _, bad = validate_port_map({"8080/tcp": "0"})
    assert bad
    _, dup = validate_port_map({"8080/tcp": "9000", "8081/tcp": "9000"})
    assert dup
    assert remap_named_volume("grafana_data", "grafana", "grafana-b") == "grafana-b_data"
    assert remap_named_volume("shared", "grafana", "grafana-b") == "shared"

    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True, dns_name="b.test")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("app", ports="8080->80/tcp")
    dest.docker_inventory_json = json.dumps(
        {
            "v": 2,
            "projects": [
                {
                    "name": "app",
                    "containers": [
                        {
                            "name": "other",
                            "running": True,
                            "ports_display": "8080->80/tcp",
                        }
                    ],
                }
            ],
        }
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    blocked = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="app",
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
    )
    ids = {b["id"] for b in blocked["blocks"]}
    assert "port_clash" in ids
    assert "dest_project_exists" in ids
    ok = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="app",
        dest_project="app-b",
        port_map={"8080/tcp": "8081"},
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
    )
    ids2 = {b["id"] for b in ok["blocks"]}
    assert "port_clash" not in ids2
    assert "dest_project_exists" not in ids2
    assert ok["dest_project"] == "app-b"
    assert ok["ports"][0]["dest_host"] == "8081"
    still = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="app",
        dest_project="app-b",
        port_map={"8080/tcp": "8080"},
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
    )
    assert any(b["id"] == "port_clash" for b in still["blocks"])


def test_compose_staging_overrides(tmp_path):
    from app.services.service_migrate.overrides import apply_staging_overrides

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "name: app\nservices:\n  web:\n    image: nginx\n    ports:\n      - \"8080:80/tcp\"\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("COMPOSE_PROJECT_NAME=app\nOTHER=1\n", encoding="utf-8")
    out = apply_staging_overrides(
        tmp_path,
        dest_project="app-b",
        source_project="app",
        port_map={"8080/tcp": "8081"},
        volume_renames={},
    )
    assert "docker-compose.yml" in out["files"]
    text = compose.read_text(encoding="utf-8")
    assert "8081:80" in text
    assert "name: app-b" in text
    assert "COMPOSE_PROJECT_NAME=app-b" in env.read_text(encoding="utf-8")


def test_pipeline_dest_project_and_port_map(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv(
        "grafana",
        ports="8080->80/tcp",
        mounts_detail=[
            {
                "source": "/var/lib/docker/volumes/grafana_data/_data",
                "destination": "/var/lib/grafana",
                "type": "volume",
                "name": "grafana_data",
            }
        ],
    )
    dest.docker_inventory_json = _inv("other", ports="8080->80/tcp")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )
    pulls, pushes, vols, ups = [], [], [], []

    def pull(server, remote, local, log=None):
        pulls.append(remote)
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "docker-compose.yml").write_text(
            "name: grafana\nservices:\n  web:\n    ports:\n      - '8080:80'\n",
            encoding="utf-8",
        )

    def push(server, local, remote, log=None):
        pushes.append(remote)

    def vol(source, dest, volume, staging, log=None, dest_volume=None):
        vols.append(dest_volume or volume)

    def stop(srv, path):
        return {"success": True}

    def up(srv, path):
        ups.append(path)
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        dest_project="grafana-b",
        port_map={"8080/tcp": "8081"},
        job_id=11,
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
        pull_fn=pull,
        push_fn=push,
        vol_fn=vol,
        stop_fn=stop,
        up_fn=up,
        cutover_fn=lambda *a, **kw: {"ok": True, "records": []},
        rebind_fn=lambda *a, **kw: {"ok": True, "counts": {}},
        validate_fn=lambda *a, **kw: {"ok": True, "tls": [], "kuma": []},
    )
    assert r["ok"] is True
    assert r["dest_project"] == "grafana-b"
    assert any(p.endswith("/grafana-b") for p in pushes)
    assert ups and ups[0].endswith("/grafana-b")
    assert vols == ["grafana-b_data"]
    staged = tmp_path / "11" / "project" / "docker-compose.yml"
    text = staged.read_text(encoding="utf-8")
    assert "8081" in text
    assert "grafana-b" in text


def test_rebind_renames_dest_project(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    dep = StackDeployment(server_id=src.id, project_name="grafana")
    lock_db.add(dep)
    lock_db.commit()
    rebind_control_plane(
        lock_db, source=src, dest=dest, project="grafana", dest_project="grafana-b"
    )
    lock_db.refresh(dep)
    assert dep.server_id == dest.id
    assert dep.project_name == "grafana-b"


def test_bind_outside_default_dest_clears_block(lock_db):
    from app.services.service_migrate.overrides import parse_bind_overrides_from_mapping

    src = Server(
        name="a",
        hostname="a.local",
        container_patch_enabled=True,
        docker_base_dir="/home/bjorn/docker",
        ssh_username="bjorn",
        dns_name="a.test",
    )
    dest = Server(
        name="b",
        hostname="b.local",
        container_patch_enabled=True,
        docker_base_dir="/home/bjorn/docker",
        ssh_username="bjorn",
        dns_name="b.test",
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv(
        "signal-api",
        mounts_detail=[
            {
                "source": "/home/bjorn/other/signal-data",
                "destination": "/data",
                "type": "bind",
            }
        ],
    )
    lock_db.add(src)
    lock_db.commit()
    facts = {"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12}
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="signal-api",
        source_facts={**facts, "docker_base": "/home/bjorn/docker"},
        dest_facts={**facts, "docker_base": "/home/bjorn/docker"},
        herder_free=10**12,
    )
    assert "bind_outside_jail" not in {b["id"] for b in r["blocks"]}
    assert r["binds"]
    assert r["binds"][0]["source"] == "/home/bjorn/other/signal-data"
    assert r["binds"][0]["dest"] == "/home/bjorn/other/signal-data"
    assert r["bind_map"]["/home/bjorn/other/signal-data"] == "/home/bjorn/other/signal-data"
    skipped = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="signal-api",
        bind_overrides=[
            {
                "source": "/home/bjorn/other/signal-data",
                "dest": "",
                "skip": True,
            }
        ],
        source_facts={**facts, "docker_base": "/home/bjorn/docker"},
        dest_facts={**facts, "docker_base": "/home/bjorn/docker"},
        herder_free=10**12,
    )
    assert "bind_outside_jail" not in {b["id"] for b in skipped["blocks"]}
    assert any(w["id"] == "bind_skipped" for w in skipped["warns"])
    rows = parse_bind_overrides_from_mapping(
        {
            "bind_src_0": "/home/bjorn/other/signal-data",
            "dest_bind_0": "/home/bjorn/docker/signal-api/signal-data",
        }
    )
    assert rows[0]["source"].endswith("signal-data")


def test_compose_rewrite_bind_source(tmp_path):
    from app.services.service_migrate.overrides import apply_staging_overrides

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  web:\n    volumes:\n      - /home/bjorn/other/data:/data\n",
        encoding="utf-8",
    )
    apply_staging_overrides(
        tmp_path,
        bind_map={"/home/bjorn/other/data": "/home/bjorn/docker/app/data"},
    )
    text = compose.read_text(encoding="utf-8")
    assert "/home/bjorn/docker/app/data:/data" in text
    assert "/home/bjorn/other/data:" not in text


def test_compose_rewrites_tilde_bind_to_absolute_dest(tmp_path):
    from app.services.service_migrate.overrides import apply_staging_overrides

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  openwebui:\n    image: ghcr.io/open-webui/open-webui:main\n"
        "    volumes:\n      - ~/open-webui-data:/app/backend/data\n"
        "    ports:\n      - \"8090:8080\"\n",
        encoding="utf-8",
    )
    apply_staging_overrides(
        tmp_path,
        bind_map={
            "/home/piherder/open-webui-data": "/home/piherder/open-webui-data"
        },
    )
    text = compose.read_text(encoding="utf-8")
    assert "- /home/piherder/open-webui-data:/app/backend/data" in text
    assert "~/open-webui-data" not in text
    assert "8090:8080" in text
    assert "ghcr.io/open-webui/open-webui:main" in text

