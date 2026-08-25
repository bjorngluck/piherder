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
    ComposeProjectMeta,
    Integration,
    IntegrationBinding,
    Job,
    Server,
    ServiceDnsRecord,
    User,
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
    r2 = client.get(
        f"/servers/{ids['pi']}/docker/migrate/preflight?project=grafana&dest={dest_id}",
        cookies=_cookie(ids["admin"]),
        follow_redirects=False,
    )
    assert r2.status_code == 200, r2.text[:3000]
    assert 'data-testid="migrate-preflight-result"' in r2.text


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
        data={"project": "grafana", "dest": str(dest_id)},
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

