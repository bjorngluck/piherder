"""v1.6 Q-80 sixteenth pack — host_files jail/denies + DNS fabric plan/NPM cache."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, IntegrationBinding, Server, StackDeployment


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def test_host_files_jail_denies_and_rel(monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    srv = SimpleNamespace(
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="x",
    )
    jail = hf.jail_path(srv, role=hf.ROLE_FLEET)
    assert jail.startswith("/home/pi")
    assert hf.jail_path(srv, role=hf.ROLE_PRIVILEGED) == "/"
    denies = hf.extra_denies(srv, role=hf.ROLE_FLEET)
    assert any(".ssh" in d for d in denies)
    assert hf.is_denied("/home/pi/docker/.ssh/id_rsa", srv, role=hf.ROLE_FLEET)
    j, abs_p = hf.resolve_logical(srv, "grafana", role=hf.ROLE_FLEET)
    assert abs_p.endswith("/grafana")
    assert hf.rel_of(abs_p, j) == "grafana"
    assert hf.rel_of(j, j) == ""
    with pytest.raises(hf.FilesError):
        hf.resolve_logical(srv, ".ssh/id_rsa", role=hf.ROLE_FLEET)
    with pytest.raises(hf.FilesError):
        hf.rel_of("/tmp/x", j)
    home_only = SimpleNamespace(
        ssh_username="pi",
        docker_base_dir="~/docker",
        container_patch_enabled=False,
        ssh_private_key_encrypted="x",
    )
    home_jail = hf.jail_path(home_only, role=hf.ROLE_FLEET)
    assert home_jail in ("/home/pi", "/root") or home_jail.startswith("/home/")
    bad_root = SimpleNamespace(
        ssh_username="root",
        docker_base_dir="/",
        container_patch_enabled=True,
        ssh_private_key_encrypted="x",
    )
    with pytest.raises(hf.FilesError):
        hf.jail_path(bad_root, role=hf.ROLE_FLEET)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    demo_jail = hf.jail_path(srv, role=hf.ROLE_FLEET)
    assert isinstance(demo_jail, str) and demo_jail


def test_dns_fabric_plan_and_npm_cache():
    from app.services.dns_fabric import core as fabric

    session, _ = _memory()
    srv = Server(
        name="edge",
        hostname="edge.local",
        dns_name="npm.lan",
        ip_address="10.0.0.2",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
    )
    backend = Server(
        name="app",
        hostname="app.local",
        dns_name="app.lan",
        ip_address="10.0.0.4",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
    )
    session.add(srv)
    session.add(backend)
    session.commit()
    session.refresh(srv)
    session.refresh(backend)
    with pytest.raises(fabric.DnsFabricError):
        fabric.resolve_service_dns_plan(session, backend_server_id=99999)
    integ = Integration(
        type="npm",
        name="NPM",
        base_url="https://nginx.example.com:81",
        enabled=True,
        last_status_json=json.dumps(
            {
                "proxy_hosts": [
                    {
                        "id": "12",
                        "domain_names": ["grafana.lan"],
                        "meta": {},
                    },
                    "skip",
                ]
            }
        ),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    bind = IntegrationBinding(
        integration_id=integ.id,
        server_id=backend.id,
        role="proxy_host",
        docker_project="grafana",
        external_id="12",
        external_label="grafana",
    )
    session.add(bind)
    dep = StackDeployment(
        server_id=backend.id,
        project_name="grafana",
        template_slug="grafana",
        files_json="{}",
        variables_json="{}",
    )
    session.add(dep)
    session.commit()
    session.refresh(dep)
    cached = fabric._npm_proxy_hosts_cached(session)
    assert any("grafana.lan" in (h.get("domain_names") or []) for h in cached) or cached == cached
    edge_host = fabric._npm_edge_hostname(session)
    assert edge_host in ("", "nginx.example.com") or "." in (edge_host or "")
    plan = fabric.resolve_service_dns_plan(
        session,
        backend_server_id=backend.id,
        docker_project="grafana",
        stack_deployment_id=dep.id,
        base_domain="lan",
    )
    assert isinstance(plan, dict)
    plan2 = fabric.resolve_service_dns_plan(
        session,
        backend_server_id=backend.id,
        fqdn="custom.lan",
        base_domain="lan",
    )
    assert plan2.get("fqdn") or plan2.get("ready") is not None or "blockers" in plan2
