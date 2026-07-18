"""Uptime Kuma coverage audit (PLAN H3)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.dns_fabric import kuma_coverage as cov


def test_score_service_binding_project_match():
    b = SimpleNamespace(
        docker_project="grafana",
        docker_container="grafana",
        external_label="Grafana HTTPS",
        external_id="12",
    )
    tokens = cov._tokens("grafana.example.com", "grafana", "Grafana")
    assert cov._score_service_binding(b, tokens=tokens, docker_project="grafana") >= 40


def test_score_service_binding_wrong_project_is_zero():
    b = SimpleNamespace(
        docker_project="npm",
        docker_container="",
        external_label="NPM",
        external_id="1",
    )
    tokens = cov._tokens("grafana.example.com", "grafana", None)
    assert cov._score_service_binding(b, tokens=tokens, docker_project="grafana") == 0


def test_score_host_scoped_service_partial():
    b = SimpleNamespace(
        docker_project="",
        docker_container="",
        external_label="Host HTTP",
        external_id="99",
    )
    tokens = cov._tokens("3dprint.example.com", None, "3dprint")
    sc = cov._score_service_binding(b, tokens=tokens, docker_project=None)
    assert sc >= 10


def test_build_audit_no_kuma():
    session = MagicMock()
    with patch.object(cov, "kuma_integrations_enabled", return_value=[]):
        out = cov.build_kuma_coverage_audit(session)
    assert out["has_kuma"] is False
    assert out["summary"]["total_services"] == 0


def test_build_audit_covered_and_gap():
    kuma = SimpleNamespace(
        id=1, name="Kuma", enabled=True, last_status_json=None, type="uptime_kuma"
    )
    bind_ok = SimpleNamespace(
        id=10,
        integration_id=1,
        server_id=5,
        role="service",
        docker_project="grafana",
        docker_container="grafana",
        external_id="g1",
        external_label="Grafana",
        last_state="up",
    )
    bind_ssh = SimpleNamespace(
        id=11,
        integration_id=1,
        server_id=6,
        role="ssh_reachability",
        docker_project=None,
        docker_container=None,
        external_id="ssh6",
        external_label="SSH pi",
        last_state="up",
    )
    rec_ok = SimpleNamespace(
        id=100,
        fqdn="grafana.lab",
        label="Grafana",
        docker_project="grafana",
        backend_server_id=5,
        target_server_id=5,
    )
    rec_gap = SimpleNamespace(
        id=101,
        fqdn="lonely.lab",
        label="Lonely",
        docker_project="orphan",
        backend_server_id=7,
        target_server_id=7,
    )
    rec_partial = SimpleNamespace(
        id=102,
        fqdn="only-ssh.lab",
        label="SSH only app",
        docker_project="app",
        backend_server_id=6,
        target_server_id=6,
    )
    servers = [
        SimpleNamespace(id=5, name="host-a"),
        SimpleNamespace(id=6, name="host-b"),
        SimpleNamespace(id=7, name="host-c"),
    ]

    session = MagicMock()

    def _exec(q):
        # Very rough: return binds or servers or records based on call order / type
        return MagicMock()

    # Patch list paths inside build_kuma_coverage_audit
    with (
        patch.object(cov, "kuma_integrations_enabled", return_value=[kuma]),
        patch("app.services.dns_fabric.kuma_coverage.select") as _sel,
        patch("app.services.dns_fabric.kuma_coverage.IntegrationBinding"),
        patch("app.services.dns_fabric.kuma_coverage.Server"),
        patch("app.services.dns_fabric.kuma_coverage.ServiceDnsRecord"),
    ):
        # session.exec returns different lists
        calls = {"n": 0}

        def exec_side_effect(*_a, **_k):
            calls["n"] += 1
            m = MagicMock()
            if calls["n"] == 1:
                m.all.return_value = [bind_ok, bind_ssh]
            elif calls["n"] == 2:
                m.all.return_value = servers
            else:
                m.all.return_value = [rec_ok, rec_gap, rec_partial]
            return m

        session.exec.side_effect = exec_side_effect
        out = cov.build_kuma_coverage_audit(session)

    assert out["has_kuma"] is True
    assert out["summary"]["covered"] == 1
    assert out["summary"]["none"] == 1
    assert out["summary"]["partial"] == 1
    by_fqdn = {s["fqdn"]: s for s in out["services"]}
    assert by_fqdn["grafana.lab"]["coverage"] == "covered"
    assert by_fqdn["lonely.lab"]["coverage"] == "none"
    assert by_fqdn["only-ssh.lab"]["coverage"] == "partial"
    assert len(out["gaps"]) == 2


def test_attach_coverage_to_fabric_services():
    services = [{"id": 1, "fqdn": "a"}, {"id": 2, "fqdn": "b"}]
    audit = {
        "has_kuma": True,
        "by_service_id": {
            1: {
                "coverage": "covered",
                "reason": "ok",
                "bindings": [{"binding_id": 1}],
                "kuma_href": "/integrations/1",
            }
        },
    }
    cov.attach_coverage_to_fabric_services(services, audit)
    assert services[0]["kuma_coverage"] == "covered"
    assert services[1]["kuma_coverage"] == "none"


def test_score_monitor_for_service_url_match():
    mon = {
        "name": "Grafana",
        "url": "https://grafana.lab.example/login",
        "type": "http",
    }
    tokens = cov._tokens("grafana.lab.example", "grafana", None)
    sc = cov._score_monitor_for_service(
        mon, tokens=tokens, fqdn="grafana.lab.example"
    )
    assert sc >= 50


def test_is_infra_role_postgres():
    patterns = list(cov.DEFAULT_INFRA_MUTE_PATTERNS)
    assert cov._is_infra_role(
        name="piherder-db-1",
        image="postgres:16-alpine",
        compose_service="db",
        patterns=patterns,
    )
    assert not cov._is_infra_role(
        name="grafana",
        image="grafana/grafana:latest",
        compose_service="grafana",
        patterns=patterns,
    )


def test_filter_path_gaps_public_drops_host_identity_partial():
    gaps = [
        {
            "coverage": "partial",
            "is_host_identity": True,
            "fqdn": "host.lab",
        },
        {
            "coverage": "none",
            "is_host_identity": False,
            "docker_project": "web",
            "fqdn": "app.lab",
        },
        {
            "coverage": "partial",
            "is_host_identity": False,
            "docker_project": "api",
            "fqdn": "api.lab",
        },
    ]
    out = cov.filter_path_gaps(gaps, mode="public")
    fqdns = {g["fqdn"] for g in out}
    assert "host.lab" not in fqdns
    assert "app.lab" in fqdns
    assert "api.lab" in fqdns


def test_parse_host_ports():
    ports = cov._parse_host_ports("0.0.0.0:5432->5432/tcp, :::5432->5432/tcp")
    assert "5432" in ports
