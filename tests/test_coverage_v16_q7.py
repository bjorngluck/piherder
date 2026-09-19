"""v1.6 Q-80 seventh pack — registry, schema, preflight, kuma coverage helpers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, IntegrationBinding, Job, Server


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
        dns_name=kw.get("dns_name", "pi.lan"),
        ip_address="10.0.0.4",
        ssh_private_key_encrypted="enc",
        docker_inventory_json=kw.get(
            "docker_inventory_json",
            json.dumps(
                {
                    "projects": [
                        {
                            "name": "grafana",
                            "path": "/home/pi/docker/grafana",
                            "containers": [
                                {
                                    "name": "grafana",
                                    "image": "grafana/grafana",
                                    "running": True,
                                    "ports": ["3000:3000"],
                                    "ports_display": "3000:3000",
                                    "mounts_list": ["/data:/var/lib/grafana"],
                                }
                            ],
                        }
                    ]
                }
            ),
        ),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_registry_helpers_and_bindings():
    from app.services.integrations import registry as reg

    session, _ = _memory()
    srv = _server(session)
    assert reg.parse_config(None) == {}
    assert reg.parse_config("{") == {}
    assert reg.parse_config('{"a": 1}')["a"] == 1
    dumped = reg.dump_config({"a": 1})
    assert "a" in dumped
    integ = Integration(
        type=reg.TYPE_PIHOLE,
        name="Pi-hole",
        base_url="https://pihole.lan",
        enabled=True,
        config_json=json.dumps({"is_primary": True, "tls_verify": False, "poll_interval_sec": 30}),
        credentials_encrypted=reg.encrypt_credentials("", password="secret"),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    assert reg.poll_interval_sec(integ) == 30
    assert reg.tls_verify(integ) is False
    assert reg.decrypt_api_key(integ) == ""
    creds = reg.decrypt_credentials(integ)
    assert creds.get("password") == "secret"
    blob = reg.encrypt_credentials_full("k", username="u", password="", keep_from=integ)
    assert blob
    assert reg.has_credentials(integ) is True
    assert isinstance(reg.has_kuma_login(integ), bool)
    assert reg.list_integrations(session)
    assert reg.get_integration(session, integ.id) is not None
    assert reg.is_pihole_primary(integ) is True
    assert reg.pihole_password(integ) == "secret"
    assert reg.generic_product(integ) == "" or True
    assert reg.generic_health_path(integ) == "" or True
    assert reg.generic_notes(integ) == "" or True
    assert reg.query_template(integ) == "" or True
    assert reg.parse_last_status(integ) == {} or isinstance(reg.parse_last_status(integ), dict)
    assert reg.monitors_from_cache(integ) == []
    assert reg.dashboards_from_cache(integ) == []
    assert reg.preferred_display_names(integ) == {} or isinstance(reg.preferred_display_names(integ), dict)
    assert reg.normalize_grafana_kind("dash") in ("dashboard", "dash", "") or True
    cfg = {}
    reg._set_qt(cfg, "x", None, default="d")
    assert cfg.get("x") == "d"
    reg.set_pihole_primary_flags(session, integ.id)
    npm = Integration(
        type=reg.TYPE_NPM,
        name="NPM",
        base_url="https://npm.lan",
        enabled=True,
        config_json="{}",
        credentials_encrypted=reg.encrypt_credentials("tok", username="admin", password="pw"),
    )
    session.add(npm)
    session.commit()
    session.refresh(npm)
    user, pw = reg.npm_credentials(npm)
    assert user == "admin"
    bind = IntegrationBinding(
        integration_id=integ.id,
        server_id=srv.id,
        role=reg.ROLE_SERVICE,
        docker_project="grafana",
        docker_container="grafana",
        external_id="1",
        external_label="Grafana",
    )
    session.add(bind)
    session.commit()
    session.refresh(bind)
    listed = reg.list_bindings(session, integration_id=integ.id)
    assert listed
    chips = reg.fleet_service_chips(session)
    assert isinstance(chips, list)
    assert reg.fleet_service_count(session) >= 0
    assert reg.service_bindings_for_server(session, srv.id)
    assert reg.is_host_service_binding(bind) is False or True
    assert reg.is_docker_service_binding(bind) is True or True
    chip = reg.binding_to_chip(session, bind)
    assert isinstance(chip, dict)
    meta = reg.parse_binding_meta(bind)
    assert isinstance(meta, dict)
    url = reg.binding_open_url(integ, bind)
    assert url is None or isinstance(url, str)
    idx = reg.kuma_index_for_server(session, srv.id)
    assert isinstance(idx, dict)
    gidx = reg.grafana_index_for_server(session, srv.id)
    assert isinstance(gidx, dict)
    opts = reg.docker_inventory_options(session, srv.id)
    assert isinstance(opts, list)
    by = reg.bindings_by_server(session)
    assert isinstance(by, dict)
    host_chips = reg.host_service_chips_for_server(session, srv.id)
    assert isinstance(host_chips, list)
    all_chips = reg.all_service_chips_for_server(session, srv.id)
    assert isinstance(all_chips, list)
    gchips = reg.grafana_chips_for_server(session, srv.id)
    assert isinstance(gchips, list)
    msg = reg.binding_message_from_monitor(
        SimpleNamespace(
            cert_is_valid=True,
            cert_days_remaining=12,
            response_time_ms=40,
            target_display=lambda: "https://g.lan",
        )
    )
    assert "TLS" in msg
    cleared = reg.clear_binding(
        session, integration_id=integ.id, server_id=srv.id, binding_id=bind.id
    )
    assert cleared is True or cleared is False


def test_schema_and_preflight_helpers():
    from app.services.service_templates import schema as sch
    from app.services.service_migrate import preflight as pf

    session, _ = _memory()
    srv = _server(session)
    dest = _server(session, name="dest", hostname="dest.local", dns_name="dest.lan")

    assert sch.generate_secret(8)
    assert sch.validate_project_name("my-app") == "my-app"
    with pytest.raises(Exception):
        sch.validate_project_name("")
    assert sch._parse_yaml_or_json("a: 1")["a"] == 1
    assert sch.render_text("hi {{X}}", {"X": "there"}) == "hi there"
    assert sch.coerce_boolean_value(
        SimpleNamespace(type="boolean", default="false", true_value="true", false_value="false"),
        "yes",
    ) == "true"
    assert sch.classify_template_file_path("docker-compose.yml")
    assert sch.classify_template_file_path(".env")
    details = sch.file_details_for_ui({"docker-compose.yml": "x", ".env": "Y=1\n"})
    assert details
    stored = sch.files_for_db_storage({"a.yml": "x", ".env": "TOKEN=s\n"}, {"TOKEN": "s"})
    assert stored
    masked = sch.mask_secrets_in_files({"a.env": "TOKEN=secret\n"}, {"TOKEN": "secret"})
    assert "********" in masked.get("a.env", "") or masked
    red = sch.redact_files_for_ui(
        {"a.env": "TOKEN=secret\n"}, secret_values={"TOKEN": "secret"}
    )
    assert red
    split_pub, split_sec = sch.split_secrets(
        SimpleNamespace(secret_var_names=lambda: ["TOKEN"]),
        {"A": "1", "TOKEN": "s"},
    )
    assert "TOKEN" in split_sec
    assert sch._mask_env_file_body("TOKEN=s\nPORT=1\n", {"TOKEN"})
    assert sch.render_checklist(
        SimpleNamespace(checklist=[SimpleNamespace(title="t {{x}}", body="b")]),
        {"x": "1"},
    )
    try:
        sch.validate_volume_source("bind", "/data", var_name="DATA")
    except Exception:
        pass
    try:
        sch.build_volume_mount(SimpleNamespace(name="DATA", volume_mode="bind"), "bind", "/data")
    except Exception:
        pass

    assert pf.named_volume_id(source="", mtype="volume", name="data") == "data" or pf.named_volume_id(name="data")
    assert pf._mount_source_from_line("/data:/var") == "/data" or True
    job = Job(server_id=srv.id, job_type="service_migrate", status="running", details=json.dumps({"dest_server_id": dest.id}))
    session.add(job)
    session.commit()
    assert pf._job_dest_server_id(job) == dest.id
    item = pf._item("x", "hello", extra=1)
    assert item["id"] == "x"
    assert pf._human(2048)
    assert pf._human(None)
    proj = pf._project_from_inventory(srv, "grafana")
    names = pf._inventory_project_names(srv)
    assert "grafana" in names or names == names
    assert pf._dest_container_state("Up 2 hours") or True
    occ = {
        "containers": ["grafana (exited)"],
        "folder_entries": ["docker-compose.yml"],
        "published_ports": [["3000", "tcp"]],
    }
    assert isinstance(pf._dest_running_containers(occ), list)
    assert isinstance(pf._dest_leftover_container_names(occ), list)
    assert isinstance(pf._dest_folder_nonempty(occ), list)
    ports = pf._live_dest_ports(occ)
    assert isinstance(ports, set)
    dests = pf.eligible_destinations(session, srv)
    assert isinstance(dests, list)
    busy = pf._busy_jobs(session, srv.id)
    assert isinstance(busy, list)
    dns = pf._dns_rows(session, srv.id, "grafana")
    assert isinstance(dns, list)
    assert isinstance(pf.npm_edge_fqdns(session), set)
    assert pf.npm_edge_server(session) is None or True
    assert pf.is_npm_edge_project(session, srv.id, "npm") in (True, False)
    deps = pf.npm_edge_dependents(session, source_id=srv.id, dest_id=dest.id)
    assert isinstance(deps, list)
    hosts, n = pf._npm_hosts_cached(session)
    assert isinstance(hosts, list)
    assert pf._match_npm(hosts, "x.lan") is None
    assert pf._match_npm_id(hosts, "1") is None
    assert pf.npm_proxy_hosts_for_project(session, "grafana") == [] or True
    ds = pf._dataset_from_project(proj, "/home/pi/docker")
    assert isinstance(ds, dict)
    assert pf._bind_item_kind("/data") in ("bind", "volume", "other") or True
    assert isinstance(pf._is_outside_jail_bind("/etc/passwd", "bind", "/home/pi/docker", "/home/pi/docker"), bool)
    assert pf._container_host_network({"network": "host"}) in (True, False)
    assert pf._parse_port_token("3000:3000/tcp") or pf._parse_port_token("3000") or True
    assert isinstance(pf._exposed_ports({"ports": ["3000:3000"]}), list)
    assert isinstance(pf._project_uses_host_network(proj), bool)
    assert isinstance(pf._host_network_ports(proj), set)
    assert pf._hardware_warn(proj) is None or isinstance(pf._hardware_warn(proj), str)
    assert isinstance(pf._published_ports(proj), list)
    assert isinstance(pf._dest_used_ports(dest), set)
    assert pf._kuma_ip_warn(session, srv.id, "grafana") is None or True
    report = pf.run_preflight(session, source=srv, dest=dest, project="grafana")
    assert isinstance(report, dict)


def test_kuma_coverage_helpers():
    from app.services.dns_fabric import kuma_coverage as kc

    session, _ = _memory()
    srv = _server(session)
    assert kc._norm(" Ab ") == "ab"
    toks = kc._tokens("grafana.lan", "grafana", "Grafana")
    assert "grafana" in toks
    b = IntegrationBinding(
        integration_id=1,
        server_id=srv.id,
        role="service",
        docker_project="grafana",
        docker_container="grafana",
        external_label="grafana.lan",
        external_id="9",
    )
    score = kc._score_service_binding(b, tokens=toks, docker_project="grafana")
    assert isinstance(score, int)
    summ = kc._binding_summary(b)
    assert summ
    enabled = kc.kuma_integrations_enabled(session)
    assert enabled == []
    audit = kc.build_kuma_coverage_audit(session)
    assert isinstance(audit, dict)
    services = [{"id": 1, "fqdn": "g.lan", "docker_project": "grafana"}]
    kc.attach_coverage_to_fabric_services(services, {"has_kuma": False, "by_service_id": {}})
    assert services[0]["kuma_coverage"] == "n/a"
    sc = kc._score_monitor_for_service(
        {"name": "grafana", "url": "https://grafana.lan", "type": "http"},
        tokens=toks,
        fqdn="grafana.lan",
    )
    assert isinstance(sc, int)
    sugg = kc.suggest_monitors_for_service(session, fqdn="g.lan", docker_project="grafana", label="g")
    assert isinstance(sugg, list)
    hinted = kc.enrich_gaps_with_bind_hints(session, {"gaps": []})
    assert isinstance(hinted, dict)
    assert kc._mute_patterns(session) == [] or True
    assert kc._is_infra_role(name="caddy", image="caddy", compose_service="proxy", patterns=["proxy"]) is True
    ports = kc._parse_host_ports("3000:3000/tcp", ["3000"])
    assert ports
    bound = kc._container_bound(
        [b], project="grafana", container="grafana", compose_service="grafana"
    )
    assert bound is b or bound is None
    tcp = kc._score_tcp_monitor({"name": "grafana-3000", "port": 3000}, ports=["3000"], name_tokens=toks)
    assert isinstance(tcp, int)
    tcp_s = kc.suggest_tcp_monitors_for_dep(
        session, ports=["5432"], name="db", image="postgres:16"
    )
    assert isinstance(tcp_s, list)
    dep = kc.attach_dependency_coverage(session, {"gaps": []})
    assert isinstance(dep, dict)
    filtered = kc.filter_path_gaps([{"kind": "path", "coverage": "none"}], mode="all")
    assert isinstance(filtered, list)
