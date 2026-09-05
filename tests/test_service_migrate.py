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


def test_parse_ss_listen_ports():
    from app.services.service_migrate.facts import _parse_ss_listen

    sample = (
        "LISTEN 0 4096 0.0.0.0:8090 0.0.0.0:*\n"
        "LISTEN 0 4096 [::]:22 [::]:*\n"
        "LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
    )
    ports = set(_parse_ss_listen(sample, "tcp"))
    assert ("8090", "tcp") in ports
    assert ("22", "tcp") in ports
    assert ("631", "tcp") in ports


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


def _inv(
    *names,
    ports=None,
    mounts=None,
    extra=None,
    mounts_detail=None,
    networks=None,
    network_mode=None,
    exposed_ports=None,
    privileged=None,
):
    containers = []
    if (
        ports
        or mounts
        or mounts_detail
        or networks
        or network_mode
        or exposed_ports
        or privileged
    ):
        row = {
            "name": "web",
            "running": True,
            "ports_display": ports or "",
            "mounts_list": mounts or [],
        }
        if mounts_detail:
            row["mounts_detail"] = mounts_detail
        if networks is not None:
            row["networks"] = networks
        if network_mode is not None:
            row["network_mode"] = network_mode
        if exposed_ports is not None:
            row["exposed_ports"] = exposed_ports
        if privileged is not None:
            row["privileged"] = privileged
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


def test_preflight_dest_exists_live_occupancy_ignores_stale_inventory(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True)
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True)
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("openwebui")
    dest.docker_inventory_json = _inv("openwebui")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    facts = {"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12}

    empty = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": [],
        },
    )
    assert "dest_project_exists" not in {b["id"] for b in empty["blocks"]}

    leftover_dir = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": True,
            "files": ["docker-compose.yml"],
            "containers": [],
        },
    )
    assert "dest_project_exists" not in {b["id"] for b in leftover_dir["blocks"]}
    assert any(w["id"] == "dest_folder_overwrite" for w in leftover_dir["warns"])

    running = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": True,
            "files": ["docker-compose.yml"],
            "containers": ["openwebui running"],
        },
    )
    msg = next(b["message"] for b in running["blocks"] if b["id"] == "dest_project_exists")
    assert "running" in msg

    leftover_ct = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": ["openwebui created"],
            "ports": [],
            "project_ports": [],
        },
    )
    assert "dest_project_exists" not in {b["id"] for b in leftover_ct["blocks"]}
    assert leftover_ct["ok"]
    warn = next(
        w["message"] for w in leftover_ct["warns"] if w["id"] == "dest_leftover_containers"
    )
    assert "openwebui created" in warn
    assert "Move will remove" in warn

    unreachable = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {"error": "ssh timeout"},
    )
    assert any(b["id"] == "dest_live_failed" for b in unreachable["blocks"])
    assert "dest_project_exists" not in {b["id"] for b in unreachable["blocks"]}


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
    monkeypatch.setattr(
        "app.services.service_migrate.facts.refresh_host_inventory",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "app.services.service_migrate.preflight.probe_dest_occupancy",
        lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": [],
            "ports": [],
        },
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
    if 'data-testid="migrate-start-form"' in r2.text:
        assert 'data-job-hold-close="hold"' in r2.text


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
    pulls, pushes, vols, stops, ups, rms, chowns = [], [], [], [], [], [], []

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

    def dest_rm(srv, name):
        rms.append((srv.id, name))
        return {"ok": True, "output": "no_containers"}

    def dest_chown(srv, path, log=None):
        chowns.append((srv.id, path))

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
        dest_rm_fn=dest_rm,
        dest_chown_fn=dest_chown,
        cutover_fn=lambda *a, **kw: {"ok": True, "records": []},
        rebind_fn=lambda *a, **kw: {"ok": True, "counts": {}},
        validate_fn=lambda *a, **kw: {"ok": True, "tls": [], "kuma": []},
    )
    assert r["ok"] is True
    assert rms == [(dest.id, "grafana")]
    assert chowns and "/grafana" in chowns[0][1]
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
        captured["adopt_fabric"] = kwargs.get("adopt_fabric")
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
            "adopt_fabric": "1",
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
    assert captured["adopt_fabric"] is True
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


def test_cutover_npm_from_proxy_host_binding_without_fabric(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src, dest = _pair_hosts(lock_db)
    dest.ip_address = "10.9.8.7"
    lock_db.add(dest)
    other = Server(name="old-edge", hostname="old.local", dns_name="old.test")
    lock_db.add(other)
    lock_db.commit()
    lock_db.refresh(other)
    lock_db.refresh(dest)
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
                        "id": "16",
                        "domain_names": ["ai.example.test"],
                        "forward_host": "10.0.0.1",
                        "forward_port": 8091,
                    }
                ],
            }
        ),
    )
    lock_db.add(npm)
    lock_db.commit()
    lock_db.refresh(npm)
    lock_db.add(
        IntegrationBinding(
            integration_id=npm.id,
            server_id=other.id,
            role="proxy_host",
            docker_project="openwebui",
            external_id="16",
            external_label="ai.example.test",
        )
    )
    lock_db.commit()
    puts = []

    r = retarget_dns_npm(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        upsert_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("upsert")),
        npm_put_fn=lambda fqdn, hid, host, forward_port=None: puts.append(
            (fqdn, hid, host, forward_port)
        )
        or {"id": hid, "forward_host": host},
        restartdns_fn=lambda: [],
    )
    assert r["ok"] is True
    assert puts == [("ai.example.test", "16", "10.9.8.7", None)]
    assert r["records"][0]["action"] == "npm"
    assert r["records"][0].get("from_binding") is True


def test_cutover_adopt_fabric_from_binding(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src, dest = _pair_hosts(lock_db)
    dest.ip_address = "10.9.8.7"
    edge = Server(
        name="npm-host",
        hostname="npm.local",
        dns_name="rpi.example.test",
        container_patch_enabled=True,
    )
    lock_db.add(dest)
    lock_db.add(edge)
    lock_db.commit()
    lock_db.refresh(edge)
    lock_db.refresh(dest)
    npm = Integration(
        type="npm",
        name="edge",
        base_url="http://nginx.example.test",
        enabled=True,
        last_status_json=json.dumps(
            {
                "ok": True,
                "proxy_hosts": [
                    {
                        "id": "16",
                        "domain_names": ["ai.example.test"],
                        "forward_host": "10.0.0.1",
                    }
                ],
            }
        ),
    )
    lock_db.add(npm)
    lock_db.add(
        ServiceDnsRecord(
            fqdn="nginx.example.test",
            target_server_id=edge.id,
            backend_server_id=edge.id,
            docker_project="nginxproxymanager",
            via_proxy=False,
            managed_on_pihole=True,
            external_dns_status="none",
        )
    )
    lock_db.add(
        IntegrationBinding(
            integration_id=1,
            server_id=src.id,
            role="proxy_host",
            docker_project="openwebui",
            external_id="16",
            external_label="ai.example.test",
        )
    )
    lock_db.commit()
    upserts = []

    def upsert(session, **kw):
        upserts.append(kw)
        return None, []

    r = retarget_dns_npm(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        adopt_fabric=True,
        upsert_fn=upsert,
        npm_put_fn=lambda *a, **k: {"id": "16", "forward_host": "10.9.8.7"},
        restartdns_fn=lambda: [],
    )
    assert r["records"][0].get("from_binding") is True
    assert r["records"][0].get("adopted") is True
    assert upserts
    assert upserts[0]["via_proxy"] is True
    assert upserts[0]["managed_on_pihole"] is False
    assert upserts[0]["certificate_id"] is None
    assert upserts[0]["sync_now"] is False
    assert upserts[0]["target_server_id"] == edge.id
    assert upserts[0]["backend_server_id"] == dest.id


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


def test_cutover_retries_cname_already_on_dest(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src, dest = _pair_hosts(lock_db)
    rec = ServiceDnsRecord(
        fqdn="app.example.test",
        target_server_id=dest.id,
        backend_server_id=dest.id,
        docker_project="grafana",
        via_proxy=False,
        managed_on_pihole=True,
        external_dns_status="none",
    )
    lock_db.add(rec)
    lock_db.commit()
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
        npm_put_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("npm")),
        restartdns_fn=lambda: [{"ok": True, "name": "ph"}],
    )
    assert r["ok"] is True
    assert upserts
    assert upserts[0]["target_server_id"] == dest.id
    assert r["records"][0]["action"] == "cname"


def test_cutover_npm_edge_keeps_dependent_cnames(lock_db):
    from app.services.service_migrate.cutover import retarget_dns_npm

    src, dest = _pair_hosts(lock_db)
    backend = Server(
        name="app-host",
        hostname="app.local",
        container_patch_enabled=True,
        dns_name="app-host.test",
    )
    lock_db.add(backend)
    lock_db.commit()
    lock_db.refresh(backend)
    npm = Integration(
        type="npm",
        name="edge",
        base_url="https://nginx.example.test",
        enabled=True,
        last_status_json=json.dumps({"ok": True, "proxy_hosts": []}),
    )
    lock_db.add(npm)
    edge = ServiceDnsRecord(
        fqdn="nginx.example.test",
        target_server_id=src.id,
        backend_server_id=src.id,
        docker_project="nginxproxymanager",
        via_proxy=False,
        managed_on_pihole=True,
        external_dns_status="none",
    )
    dep = ServiceDnsRecord(
        fqdn="ai.example.test",
        target_server_id=src.id,
        backend_server_id=backend.id,
        docker_project="openwebui",
        via_proxy=True,
        managed_on_pihole=True,
        external_dns_status="done",
    )
    lock_db.add(edge)
    lock_db.add(dep)
    lock_db.commit()
    lock_db.refresh(dep)
    upserts = []

    def upsert(session, **kw):
        upserts.append(kw)
        return edge, []

    r = retarget_dns_npm(
        lock_db,
        source=src,
        dest=dest,
        project="nginxproxymanager",
        upsert_fn=upsert,
        npm_put_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("npm")),
        restartdns_fn=lambda: [{"ok": True, "name": "ph"}],
    )
    assert r["ok"] is True
    assert len(upserts) == 1
    assert upserts[0]["fqdn"] == "nginx.example.test"
    assert upserts[0]["via_proxy"] is False
    actions = {row["fqdn"]: row["action"] for row in r["records"]}
    assert actions["nginx.example.test"] == "cname"
    assert actions["ai.example.test"] == "keep_cname_on_edge"
    lock_db.refresh(dep)
    assert dep.target_server_id == dest.id
    assert dep.backend_server_id == backend.id
    assert dep.via_proxy is True


def test_preflight_npm_edge_lists_dependents(lock_db):
    src, dest = _pair_hosts(lock_db)
    src.container_patch_enabled = True
    dest.container_patch_enabled = True
    lock_db.add(src)
    lock_db.add(dest)
    backend = Server(
        name="app-host",
        hostname="app.local",
        container_patch_enabled=True,
        dns_name="app-host.test",
    )
    lock_db.add(backend)
    lock_db.commit()
    lock_db.refresh(backend)
    npm = Integration(
        type="npm",
        name="edge",
        base_url="https://nginx.example.test",
        enabled=True,
        last_status_json=json.dumps({"ok": True, "proxy_hosts": []}),
    )
    lock_db.add(npm)
    lock_db.add(
        ServiceDnsRecord(
            fqdn="nginx.example.test",
            target_server_id=src.id,
            backend_server_id=src.id,
            docker_project="nginxproxymanager",
            via_proxy=False,
            external_dns_status="none",
        )
    )
    lock_db.add(
        ServiceDnsRecord(
            fqdn="ai.example.test",
            target_server_id=src.id,
            backend_server_id=backend.id,
            docker_project="openwebui",
            via_proxy=True,
            external_dns_status="done",
        )
    )
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="nginxproxymanager",
        source_facts={"arch": "aarch64"},
        dest_facts={
            "arch": "aarch64",
            "docker_base_writable": True,
            "disk_free_bytes": 10**12,
        },
    )
    assert r["npm_edge"] is True
    assert any(w["id"] == "npm_edge" for w in r["warns"])
    assert any(d["fqdn"] == "ai.example.test" for d in r["npm_edge_dependents"])
    assert any(d["fqdn"] == "nginx.example.test" for d in r["dns"])


def test_rebind_moves_proxy_host(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    npm = Integration(type="npm", name="edge", base_url="https://nginx.test", enabled=True)
    lock_db.add(npm)
    lock_db.commit()
    lock_db.refresh(npm)
    bind = IntegrationBinding(
        integration_id=npm.id,
        server_id=src.id,
        role="proxy_host",
        docker_project="nginxproxymanager",
        external_id="5",
        external_label="nginx.example.test",
    )
    lock_db.add(bind)
    lock_db.commit()
    out = rebind_control_plane(
        lock_db, source=src, dest=dest, project="nginxproxymanager"
    )
    assert out["counts"]["proxy_host_bindings"] == 1
    lock_db.refresh(bind)
    assert bind.server_id == dest.id


def test_rebind_proxy_host_from_other_server(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    other = Server(name="stale", hostname="stale.local", dns_name="stale.test")
    npm = Integration(type="npm", name="edge", base_url="https://nginx.test", enabled=True)
    lock_db.add(other)
    lock_db.add(npm)
    lock_db.commit()
    lock_db.refresh(other)
    lock_db.refresh(npm)
    bind = IntegrationBinding(
        integration_id=npm.id,
        server_id=other.id,
        role="proxy_host",
        docker_project="openwebui",
        external_id="16",
        external_label="ai.example.test",
    )
    lock_db.add(bind)
    lock_db.commit()
    out = rebind_control_plane(lock_db, source=src, dest=dest, project="openwebui")
    assert out["counts"]["proxy_host_bindings"] == 1
    lock_db.refresh(bind)
    assert bind.server_id == dest.id


def test_rebind_kuma_service_from_other_server(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    other = Server(name="stale", hostname="stale.local", dns_name="stale.test")
    lock_db.add(other)
    lock_db.commit()
    lock_db.refresh(other)
    bind = IntegrationBinding(
        integration_id=1,
        server_id=other.id,
        role="service",
        docker_project="grafana",
        external_id="9",
        last_state="up",
    )
    lock_db.add(bind)
    lock_db.commit()
    out = rebind_control_plane(lock_db, source=src, dest=dest, project="grafana")
    assert out["counts"]["kuma_bindings"] == 1
    lock_db.refresh(bind)
    assert bind.server_id == dest.id


def test_rebind_grafana_dashboard_follows_dest(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    other = Server(name="old", hostname="old.local", dns_name="old.test")
    lock_db.add(other)
    lock_db.commit()
    lock_db.refresh(other)
    dash = IntegrationBinding(
        integration_id=1,
        server_id=other.id,
        role="dashboard",
        docker_project="grafana",
        external_id="uid-ow",
        external_meta_json=json.dumps({"kind": "containers"}),
    )
    host = IntegrationBinding(
        integration_id=1,
        server_id=src.id,
        role="dashboard",
        docker_project="grafana",
        external_id="uid-host",
        external_meta_json=json.dumps({"kind": "metrics"}),
    )
    lock_db.add(dash)
    lock_db.add(host)
    lock_db.commit()
    out = rebind_control_plane(lock_db, source=src, dest=dest, project="grafana")
    assert out["counts"]["dashboard_bindings"] == 1
    lock_db.refresh(dash)
    lock_db.refresh(host)
    assert dash.server_id == dest.id
    assert host.server_id == src.id


def test_rebind_drops_duplicate_dashboard_on_dest(lock_db):
    from app.services.service_migrate.rebind import rebind_control_plane

    src, dest = _pair_hosts(lock_db)
    dest_bind = IntegrationBinding(
        integration_id=2,
        server_id=dest.id,
        role="dashboard",
        docker_project="openwebui",
        docker_container="openwebui",
        external_id="uid-ow",
        external_meta_json=json.dumps({"kind": "containers"}),
    )
    src_bind = IntegrationBinding(
        integration_id=2,
        server_id=src.id,
        role="dashboard",
        docker_project="openwebui",
        docker_container="openwebui",
        external_id="uid-ow",
        external_meta_json=json.dumps({"kind": "containers"}),
    )
    lock_db.add(dest_bind)
    lock_db.add(src_bind)
    lock_db.commit()
    src_id = src_bind.id
    out = rebind_control_plane(lock_db, source=src, dest=dest, project="openwebui")
    assert out["counts"]["bindings_dup_dropped"] == 1
    assert out["counts"]["dashboard_bindings"] == 0
    assert lock_db.get(IntegrationBinding, src_id) is None
    lock_db.refresh(dest_bind)
    assert dest_bind.server_id == dest.id


def test_preflight_npm_binding_without_fabric(lock_db):
    src, dest = _pair_hosts(lock_db)
    dest.ip_address = "10.1.2.3"
    src.docker_inventory_json = '{"projects":[{"name":"openwebui","containers":[]}]}'
    dest.docker_inventory_json = '{"projects":[]}'
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
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
                        "id": "16",
                        "domain_names": ["ai.example.test"],
                        "forward_host": "10.0.0.9",
                        "forward_port": 8091,
                    }
                ],
            }
        ),
    )
    lock_db.add(npm)
    lock_db.commit()
    lock_db.refresh(npm)
    lock_db.add(
        IntegrationBinding(
            integration_id=npm.id,
            server_id=src.id,
            role="proxy_host",
            docker_project="openwebui",
            external_id="16",
            external_label="ai.example.test",
        )
    )
    lock_db.commit()
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts={"arch": "aarch64"},
        dest_facts={
            "arch": "aarch64",
            "docker_base_writable": True,
            "disk_free_bytes": 10**12,
        },
    )
    assert any(d["fqdn"] == "ai.example.test" and d["action"] == "npm" for d in r["dns"])
    assert any(w["id"] == "npm_binding" for w in r["warns"])
    assert not any(b["id"].startswith("npm_") for b in r["blocks"]), r["blocks"]


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
    assert ei.value.failed_step == "dest_up"


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


def test_wipe_compose_project_down_v_and_tree(lock_db):
    from app.services.service_migrate.leftover import wipe_compose_project

    src, _dest = _pair_hosts(lock_db)
    downs = []
    trees = []
    out = wipe_compose_project(
        lock_db,
        server=src,
        project_path="/home/pi/docker/grafana",
        remove_volumes=True,
        delete_tree=True,
        down_fn=lambda srv, p, vols: downs.append((srv.name, p, vols)) or {"success": True},
        rm_tree_fn=lambda srv, p: trees.append((srv.name, p)) or {"success": True},
    )
    assert downs == [("a", "/home/pi/docker/grafana", True)]
    assert trees == [("a", "/home/pi/docker/grafana")]
    assert out["project_removed"] is True
    assert out["volumes_removed"] is True


def test_wipe_compose_project_refuses_wrong_path(lock_db):
    from app.services.service_migrate.leftover import LeftoverError, wipe_compose_project

    src, _dest = _pair_hosts(lock_db)
    with pytest.raises(LeftoverError, match="not '/home/pi/docker/grafana'"):
        wipe_compose_project(
            lock_db,
            server=src,
            project_path="/etc/grafana",
            down_fn=lambda *a, **k: {"success": True},
            rm_tree_fn=lambda *a, **k: {"success": True},
        )


def test_wipe_compose_project_refuses_locked(lock_db):
    from app.services.service_migrate.leftover import LeftoverError, wipe_compose_project

    src, _dest = _pair_hosts(lock_db)
    hl.set_host_lock(lock_db, src, "grafana", reason="hardware", note="TPU")
    lock_db.commit()
    with pytest.raises(LeftoverError, match="Locked"):
        wipe_compose_project(
            lock_db,
            server=src,
            project_path="/home/pi/docker/grafana",
            down_fn=lambda *a, **k: {"success": True},
            rm_tree_fn=lambda *a, **k: {"success": True},
        )


def test_rm_tree_cmd_retries_sudo():
    from app.services.service_migrate.leftover import _rm_tree_cmd

    cmd = _rm_tree_cmd("/home/pi/docker/grafana")
    assert "rm -rf -- '/home/pi/docker/grafana'" in cmd or "rm -rf -- /home/pi/docker/grafana" in cmd
    assert "sudo -n rm -rf" in cmd


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


def test_preflight_dest_ports_from_live_not_stale_inventory(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True)
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True)
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv("openwebui", ports="8090->8080/tcp")
    dest.docker_inventory_json = _inv("ghost", ports="8090->80/tcp")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    facts = {"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12}
    free = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": [],
            "ports": [],
        },
    )
    ids = {b["id"] for b in free["blocks"]}
    assert "port_clash" not in ids
    assert "dest_project_exists" not in ids
    clash = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": [],
            "ports": [("8090", "tcp")],
        },
    )
    assert any(b["id"] == "port_clash" for b in clash["blocks"])
    ghost_port = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": ["openwebui created"],
            "ports": [("8090", "tcp")],
            "project_ports": [("8090", "tcp")],
        },
    )
    assert "port_clash" not in {b["id"] for b in ghost_port["blocks"]}
    assert any(w["id"] == "dest_leftover_containers" for w in ghost_port["warns"])
    listen = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="openwebui",
        source_facts=facts,
        dest_facts=facts,
        live_inspect=True,
        dest_occupy_fn=lambda *_a, **_k: {
            "nonempty": False,
            "files": [],
            "containers": ["openwebui created"],
            "ports": [],
            "project_ports": [],
            "listen_ports": [("8090", "tcp")],
        },
    )
    assert any(b["id"] == "port_clash" for b in listen["blocks"])
    assert "8090" in next(b["message"] for b in listen["blocks"] if b["id"] == "port_clash")


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
    assert r["binds"][0]["dest"] == "/home/bjorn/docker/signal-api/signal-data"
    assert r["bind_map"]["/home/bjorn/other/signal-data"] == "/home/bjorn/docker/signal-api/signal-data"
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
            "/home/piherder/open-webui-data": "./open-webui-data"
        },
    )
    text = compose.read_text(encoding="utf-8")
    assert "- ./open-webui-data:/app/backend/data" in text
    assert "~/open-webui-data" not in text
    assert "8090:8080" in text
    assert "ghcr.io/open-webui/open-webui:main" in text


def test_is_host_local_bind_docker_sock():
    from app.services.service_migrate.overrides import (
        is_host_local_bind,
        suggest_dest_bind,
    )
    from app.services.service_migrate.copy import CopyError, rsync_host_to_herder

    assert is_host_local_bind("/var/run/docker.sock") is True
    assert is_host_local_bind("/run/docker.sock") is True
    assert is_host_local_bind("/dev/apex_0") is True
    assert is_host_local_bind("/home/bjorn/docker/uptime-kuma/data") is False
    assert (
        suggest_dest_bind(
            "/var/run/docker.sock",
            "/home/bjorn/docker",
            "/home/bjorn/docker",
            "uptime-kuma",
        )
        == "/var/run/docker.sock"
    )
    src = Server(name="a", hostname="a.local")
    with pytest.raises(CopyError, match="socket/device"):
        rsync_host_to_herder(src, "/var/run/docker.sock", "/tmp")


def test_preflight_docker_sock_is_host_local_not_folded(lock_db):
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
        "uptime-kuma",
        mounts_detail=[
            {
                "source": "/home/bjorn/docker/uptime-kuma/data",
                "destination": "/app/data",
                "type": "bind",
            },
            {
                "source": "/var/run/docker.sock",
                "destination": "/var/run/docker.sock",
                "type": "bind",
            },
        ],
    )
    lock_db.add(src)
    lock_db.commit()
    facts = {"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12}
    r = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="uptime-kuma",
        source_facts={**facts, "docker_base": "/home/bjorn/docker"},
        dest_facts={**facts, "docker_base": "/home/bjorn/docker"},
        herder_free=10**12,
    )
    kinds = {i["source"]: i["kind"] for i in r["dataset"]["items"]}
    assert kinds["/var/run/docker.sock"] == "bind_host_local"
    sock = [b for b in r["binds"] if b["source"] == "/var/run/docker.sock"]
    assert sock and sock[0]["host_local"] is True
    assert sock[0]["skip"] is True
    assert sock[0]["dest"] == "/var/run/docker.sock"
    assert "/var/run/docker.sock" not in r["bind_map"]
    assert any(w["id"] == "bind_host_local" for w in r["warns"])
    assert "bind_outside_jail" not in {b["id"] for b in r["blocks"]}


def test_pipeline_does_not_rsync_docker_sock(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_base_dir = "/home/bjorn/docker"
    dest.docker_base_dir = "/home/bjorn/docker"
    src.ssh_username = "bjorn"
    dest.ssh_username = "bjorn"
    src.docker_inventory_json = _inv(
        "uptime-kuma",
        mounts_detail=[
            {
                "source": "/var/run/docker.sock",
                "destination": "/var/run/docker.sock",
                "type": "bind",
            }
        ],
    )
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )
    pulls = []

    def pull(server, remote, local, log=None):
        pulls.append(remote)
        Path(local).mkdir(parents=True, exist_ok=True)

    def dummy(*a, **k):
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="uptime-kuma",
        job_id=21,
        source_facts={"arch": "aarch64", "docker_base": "/home/bjorn/docker"},
        dest_facts={
            "arch": "aarch64",
            "docker_base_writable": True,
            "disk_free_bytes": 10**12,
            "docker_base": "/home/bjorn/docker",
        },
        herder_free=10**12,
        pull_fn=pull,
        push_fn=lambda *a, **k: None,
        vol_fn=lambda *a, **k: None,
        stop_fn=dummy,
        up_fn=dummy,
        cutover_fn=lambda *a, **kw: {"ok": True, "records": []},
        rebind_fn=lambda *a, **kw: {"ok": True, "counts": {}},
        validate_fn=lambda *a, **kw: {"ok": True, "tls": [], "kuma": []},
    )
    assert r["ok"] is True
    assert not any("docker.sock" in str(p) for p in pulls)
    assert any("uptime-kuma" in str(p) for p in pulls)


def test_pipeline_honors_skip_binds(lock_db, tmp_path, monkeypatch):
    from app.services.service_migrate.pipeline import run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    extra = "/home/pi/other/data"
    src.docker_inventory_json = _inv(
        "grafana",
        mounts_detail=[
            {
                "source": extra,
                "destination": "/data",
                "type": "bind",
            }
        ],
    )
    lock_db.add(src)
    lock_db.commit()
    monkeypatch.setattr(
        "app.services.service_migrate.pipeline.staging_root",
        lambda job_id: tmp_path / str(job_id),
    )
    pulls = []

    def pull(server, remote, local, log=None):
        pulls.append(remote)
        Path(local).mkdir(parents=True, exist_ok=True)

    def dummy(*a, **k):
        return {"success": True}

    r = run_copy_and_start(
        lock_db,
        source=src,
        dest=dest,
        project="grafana",
        job_id=22,
        skip_binds=[extra],
        source_facts={"arch": "aarch64"},
        dest_facts={"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12},
        herder_free=10**12,
        pull_fn=pull,
        push_fn=lambda *a, **k: None,
        vol_fn=lambda *a, **k: None,
        stop_fn=dummy,
        up_fn=dummy,
        cutover_fn=lambda *a, **kw: {"ok": True, "records": []},
        rebind_fn=lambda *a, **kw: {"ok": True, "counts": {}},
        validate_fn=lambda *a, **kw: {"ok": True, "tls": [], "kuma": []},
    )
    assert r["ok"] is True
    assert extra not in pulls


def test_preflight_host_network_port_clash_and_remap_block(lock_db):
    src = Server(name="a", hostname="a.local", container_patch_enabled=True, dns_name="a.test")
    dest = Server(name="b", hostname="b.local", container_patch_enabled=True, dns_name="b.test")
    lock_db.add(src)
    lock_db.add(dest)
    lock_db.commit()
    lock_db.refresh(src)
    lock_db.refresh(dest)
    src.docker_inventory_json = _inv(
        "signal-api",
        network_mode="host",
        networks=["host"],
        exposed_ports=["8080/tcp"],
    )
    lock_db.add(src)
    lock_db.commit()
    facts = {"arch": "aarch64", "docker_base_writable": True, "disk_free_bytes": 10**12}

    def occupy(_dest, _name, _path):
        return {
            "files": [],
            "containers": [],
            "ports": [],
            "project_ports": [],
            "listen_ports": [("8080", "tcp")],
        }

    blocked = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="signal-api",
        source_facts=facts,
        dest_facts=facts,
        herder_free=10**12,
        live_inspect=True,
        inspect_fn=lambda _s, row: row,
        dest_occupy_fn=occupy,
    )
    assert blocked["host_network"] is True
    ids = {b["id"] for b in blocked["blocks"]}
    assert "port_clash" in ids
    assert "host network" in next(
        b["message"] for b in blocked["blocks"] if b["id"] == "port_clash"
    ).lower()
    assert any(w["id"] == "devices" for w in blocked["warns"])
    remap = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="signal-api",
        port_map={"8080/tcp": "8081"},
        source_facts=facts,
        dest_facts=facts,
        herder_free=10**12,
        live_inspect=True,
        inspect_fn=lambda _s, row: row,
        dest_occupy_fn=lambda *a, **k: {
            "files": [],
            "containers": [],
            "ports": [],
            "project_ports": [],
            "listen_ports": [],
        },
    )
    assert any(b["id"] == "host_network_remap" for b in remap["blocks"])
    free = pf.run_preflight(
        lock_db,
        source=src,
        dest=dest,
        project="signal-api",
        source_facts=facts,
        dest_facts=facts,
        herder_free=10**12,
        live_inspect=True,
        inspect_fn=lambda _s, row: row,
        dest_occupy_fn=lambda *a, **k: {
            "files": [],
            "containers": [],
            "ports": [],
            "project_ports": [],
            "listen_ports": [],
        },
    )
    assert "port_clash" not in {b["id"] for b in free["blocks"]}
    assert "host_network_remap" not in {b["id"] for b in free["blocks"]}
    assert free["host_network"] is True


def test_pipeline_host_network_requires_devices_ack(lock_db):
    from app.services.service_migrate.pipeline import MigrateError, run_copy_and_start

    src, dest = _pair_hosts(lock_db)
    src.docker_inventory_json = _inv(
        "signal-api",
        network_mode="host",
        networks=["host"],
        exposed_ports=["8080/tcp"],
    )
    lock_db.add(src)
    lock_db.commit()
    with pytest.raises(MigrateError, match="host network"):
        run_copy_and_start(
            lock_db,
            source=src,
            dest=dest,
            project="signal-api",
            job_id=23,
            source_facts={"arch": "aarch64"},
            dest_facts={
                "arch": "aarch64",
                "docker_base_writable": True,
                "disk_free_bytes": 10**12,
            },
            herder_free=10**12,
            devices_ack=False,
        )

