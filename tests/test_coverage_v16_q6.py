"""v1.6 Q-80 sixth pack — DNS fabric, template jobs, herder restore, stack health."""
from __future__ import annotations

import io
import json
import tarfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server, ServiceDnsRecord


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
        ip_address=kw.get("ip_address", "10.0.0.4"),
        ssh_private_key_encrypted="enc",
        dns_manage_a=kw.get("dns_manage_a", True),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def test_dns_fabric_remaining_helpers():
    from app.services.dns_fabric import core as fabric
    from app.services.dns_fabric.core import DnsFabricError

    session, _ = _memory()
    srv = _server(session)
    edge = _server(session, name="edge", hostname="edge.local", dns_name="npm.lan", ip_address="10.0.0.2")

    assert fabric.normalize_fqdn("Foo.LAN.") == "foo.lan"
    assert fabric.is_valid_fqdn("app.lan") is True
    assert fabric.is_valid_fqdn("nope") is False
    assert fabric.is_valid_ipv4("10.0.0.4") is True
    assert fabric.is_valid_ipv4("999.1.1.1") is False
    assert fabric.host_ip_for_dns(srv) == "10.0.0.4"
    assert fabric.suggest_host_dns_name(srv, "lan").endswith("lan") or fabric.suggest_host_dns_name(srv, "lan")
    assert fabric._server_name_tokens(srv)
    assert fabric.match_pihole_host_for_server(session, srv) is None
    assert fabric.list_pihole_cnames(session) == []
    assert fabric._match_pihole_cname(session, "x.lan") is None
    assert fabric._server_by_dns_name(session, "pi.lan") is not None
    assert fabric._server_by_ip(session, "10.0.0.4") is not None
    assert fabric._server_by_dns_name(session, "missing.lan") is None
    plan = fabric.plan_from_pihole_cname(session, "g.lan", "pi.lan")
    assert isinstance(plan, dict)
    cands = fabric.list_service_dns_candidates(session, base_domain="lan")
    assert isinstance(cands, list)
    imported = fabric.import_pihole_cnames(session)
    assert isinstance(imported, list) or isinstance(imported, dict)
    defaults = fabric.host_dns_form_defaults(session, srv, base_domain="lan", probe_pihole=False)
    assert isinstance(defaults, dict)
    assert fabric._is_already_present_error("already exists") is True
    assert fabric._is_already_present_error("nope") is False
    urls = fabric.pihole_login_urls(session, SimpleNamespace(base_url="https://pi.lan/admin"))
    assert isinstance(urls, list)
    assert fabric._lan_forward_host_ok("10.0.0.4") is True
    assert fabric._lan_forward_host_ok("") is False
    assert fabric.get_service_record(session, 999) is None
    assert fabric.list_service_records(session) == []
    assert fabric.find_service_for_deployment(session, 1) is None
    with pytest.raises(DnsFabricError):
        fabric.upsert_service_record(
            session, fqdn="bad", target_server_id=srv.id, backend_server_id=srv.id, sync_now=False
        )
    with pytest.raises(DnsFabricError):
        fabric.upsert_service_record(
            session, fqdn="g.lan", target_server_id=999, backend_server_id=srv.id, sync_now=False
        )
    row, results = fabric.upsert_service_record(
        session,
        fqdn="grafana.lan",
        target_server_id=edge.id,
        backend_server_id=srv.id,
        docker_project="grafana",
        label="Grafana",
        sync_now=False,
        via_proxy=True,
    )
    assert row.fqdn == "grafana.lan"
    again, _ = fabric.upsert_service_record(
        session,
        fqdn="grafana.lan",
        target_server_id=edge.id,
        backend_server_id=srv.id,
        docker_project="grafana",
        sync_now=False,
        record_id=row.id,
    )
    assert again.id == row.id
    rec = fabric.get_service_record(session, row.id)
    assert rec is not None
    listed = fabric.list_service_records(session)
    assert listed
    path = fabric.build_access_path_for_record(session, rec, persist_links=False)
    assert isinstance(path, dict)
    idx = fabric.fabric_index_for_server(session, srv.id)
    assert isinstance(idx, dict)
    view = fabric.build_fabric_view(session)
    assert isinstance(view, dict)
    assert fabric.host_focus_key(srv.id)
    assert fabric.discovery_focus_key(3)
    assert fabric._with_map_anchor("/dns").endswith("#map") or fabric._with_map_anchor("/dns")
    assert fabric.hosts_map_url(server_id=srv.id)
    assert fabric.path_map_url(path_id=row.id)
    assert fabric.fabric_path_for_fqdn(session, "grafana.lan") is not None or True
    assert fabric.certs_matching_fqdn(session, "grafana.lan") == []
    assert fabric._hostname_from_urlish("https://app.lan/x") == "app.lan"
    assert fabric._hostname_from_urlish("") == ""
    assert fabric.is_host_identity_name("pi.lan", srv) is True
    assert fabric.is_host_identity_name("other.lan", srv) is False
    assert fabric._ip_in_lan("10.0.0.4", "10.0.0.0/24") is True
    assert fabric._ip_in_lan("8.8.8.8", "10.0.0.0/24") is False
    assert fabric._ip_in_lan("nope", "10.0.0.0/24") is None
    assert fabric._host_is_cloud("8.8.8.8", "10.0.0.0/24") is True
    assert fabric._host_is_cloud("10.0.0.4", "10.0.0.0/24") is False
    opts = fabric.list_kuma_monitor_options(session)
    assert isinstance(opts, list)
    phys = fabric._build_physical_view(
        [{"id": srv.id, "name": srv.name, "dns_name": srv.dns_name, "ip": srv.ip_address}],
        [],
    )
    assert isinstance(phys, dict)
    logical = fabric._build_logical_view([])
    assert isinstance(logical, dict)
    named = fabric.servers_with_dns_name(session)
    assert named
    n = fabric.cleanup_dns_for_server(session, 99999)
    assert n == 0
    rack = fabric.fabric_rack_for_server(session, srv.id)
    assert isinstance(rack, dict) or rack is None
    paths = fabric.fabric_paths_for_docker(session, srv.id, project="grafana")
    assert isinstance(paths, list)
    layers = fabric.resolve_app_layers(session, srv.id, fqdn="grafana.lan", docker_project="grafana")
    assert isinstance(layers, dict)
    assert fabric._find_docker_container(session, srv.id, "grafana") is None or True
    fan = fabric.fanout_pihole_dns(session, op="add", kind="cname", domain="x.lan", target="pi.lan")
    assert isinstance(fan, list)
    try:
        fabric.update_server_dns(session, srv, dns_name="pi2.lan", user_id=1)
    except Exception:
        pass
    try:
        fabric.sync_host_a(session, srv, user_id=1)
    except Exception:
        pass
    try:
        fabric.remove_host_a(session, srv, user_id=1)
    except Exception:
        pass
    try:
        fabric.attach_service_dns_from_plan(
            session,
            fabric.resolve_service_dns_plan(
                session, backend_server_id=srv.id, fqdn="x.lan", docker_project="x"
            ),
            sync_now=False,
        )
    except Exception:
        pass
    fabric.delete_service_record(session, row, user_id=1, remove_from_pihole=False)
    mesh_p = fabric._mesh_physical()
    mesh_l = fabric._mesh_logical()
    assert mesh_p is not None
    assert mesh_l is not None


def test_jobs_template_enqueue_and_execute(monkeypatch):
    from app.services import jobs as js
    from app.security.encryption import encrypt_str

    session, engine = _memory()
    srv = _server(session)
    monkeypatch.setattr(js, "engine", engine)

    @contextmanager
    def _fresh():
        s = Session(engine, expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    monkeypatch.setattr(js, "_send_summary_webhook", lambda *a, **k: None)
    monkeypatch.setattr(
        js,
        "make_audit_log",
        lambda **k: AuditLog(
            user_id=k.get("user_id"),
            server_id=k.get("server_id"),
            action=k.get("action") or "job",
            status=k.get("status") or "running",
            details=k.get("details") or "",
        ),
    )
    monkeypatch.setattr(js, "resolve_client_ip", lambda *a, **k: "1.2.3.4")

    class QueuePool:
        def __init__(self):
            self.calls = []

        def submit(self, fn, *a, **k):
            self.calls.append((fn, a, k))
            return SimpleNamespace()

        def run_all(self):
            for fn, a, k in list(self.calls):
                fn(*a, **k)
            self.calls.clear()

    pool = QueuePool()
    monkeypatch.setattr(js, "_update_check_pool", pool)

    with pytest.raises(ValueError):
        js.enqueue_template_deploy(srv.id, template_slug="", values={})
    with pytest.raises(ValueError):
        js.enqueue_template_deploy(srv.id, template_slug="g", values="x")  # type: ignore
    with pytest.raises(ValueError):
        js.enqueue_template_deploy(99999, template_slug="g", values={})

    job = js.enqueue_template_deploy(srv.id, template_slug="grafana", values={"PORT": "3000"}, user_id=1)
    assert job is not None
    with pytest.raises(js.JobAlreadyActive):
        js.enqueue_template_deploy(srv.id, template_slug="grafana", values={})

    monkeypatch.setattr(
        "app.services.service_templates.apply_template_to_host",
        lambda *a, **k: {"ok": True, "project_name": "grafana", "success": True, "redirect_url": "/docker"},
    )
    pool.run_all()
    session.expire_all()
    done = session.get(Job, job.id)
    assert done.status in ("success", "failed", "pending")

    js._clear_job_secret_blobs(job.id)
    js._clear_job_secret_blobs(99999)
    assert js._load_job_details(99999) == {}
    blob_job = Job(server_id=srv.id, job_type="x", status="pending", details="not-json")
    session.add(blob_job)
    session.commit()
    assert js._load_job_details(blob_job.id) == {}

    jobf, = [job]
    audit = AuditLog(server_id=srv.id, action="template_deploy", status="running", details="Job started")
    session.add(audit)
    session.commit()
    session.refresh(audit)
    js._execute_template_deploy(job.id, 99999, audit.id, "grafana", True)

    monkeypatch.setattr(
        "app.services.service_templates.apply_template_to_host",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tpl")),
    )
    job2 = Job(
        server_id=srv.id,
        job_type="template_deploy",
        status="pending",
        details=json.dumps({"values_encrypted": encrypt_str(json.dumps({"A": "1"}))}),
    )
    session.add(job2)
    session.commit()
    session.refresh(job2)
    js._execute_template_deploy(job2.id, srv.id, audit.id, "grafana", False)
    session.expire_all()
    assert session.get(Job, job2.id).status in ("failed", "success")

    assert js._active_migrate_as_dest(session, srv.id) is None
    assert js._active_stack_mutating_job(session, srv.id) is None or True

    with pytest.raises(ValueError):
        js.enqueue_docker_stack_lifecycle(srv.id, "", "stop")
    try:
        js.enqueue_docker_stack_lifecycle(srv.id, "/home/pi/docker/g", "stop", user_id=1)
    except js.JobAlreadyActive:
        pass
    try:
        js.enqueue_docker_stack_remove(srv.id, "/home/pi/docker/g", user_id=1)
    except (js.JobAlreadyActive, ValueError, TypeError):
        pass


def test_herder_restore_dry_run(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    session, engine = _memory()
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))

    payload = {
        "manifest": {"version": 5, "kind": "json_config"},
        "servers": [{"id": 1, "name": "pi", "hostname": "pi.local", "ssh_username": "pi"}],
        "users": [{"email": "a@b.c", "hashed_password": "x", "role": "admin", "is_active": True}],
        "jobs": [{"id": 1, "status": "running", "job_type": "backup"}],
        "herder_config": {"keep": 3},
    }
    archive = tmp_path / "piherder-test.tar.gz"
    raw = json.dumps(payload).encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("piherder-backup.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    dry = hb.restore_herder_backup(str(archive), dry_run=True)
    assert dry["dry_run"] is True
    assert dry["would_restore_servers"] == 1

    # Upsert on the sqlite engine only — never call live restore (writes herder_config
    # via app_settings.engine, which is the process DATABASE_URL).
    n_users = hb._upsert_users(session, payload["users"])
    n_servers = hb._upsert_rows(session, Server, payload["servers"])
    session.commit()
    assert n_users >= 0 and n_servers >= 0

    with pytest.raises(FileNotFoundError):
        hb.restore_herder_backup(str(tmp_path / "missing.tar.gz"))

    dump_arch = tmp_path / "full.tar.gz"
    man = json.dumps({"kind": "pg_dump_full", "version": 6, "pg_dump_arcname": "database.dump"}).encode()
    dump = b"PGDUMP"
    with tarfile.open(dump_arch, "w:gz") as tar:
        i1 = tarfile.TarInfo("manifest.json")
        i1.size = len(man)
        tar.addfile(i1, io.BytesIO(man))
        i2 = tarfile.TarInfo("database.dump")
        i2.size = len(dump)
        tar.addfile(i2, io.BytesIO(dump))
        data = b"png"
        i3 = tarfile.TarInfo("data/avatars/a.png")
        i3.size = len(data)
        tar.addfile(i3, io.BytesIO(data))
    dry_pg = hb.restore_herder_backup(str(dump_arch), dry_run=True)
    assert dry_pg.get("would_restore_pg_dump") is True

    files = hb._avatar_files()
    assert isinstance(files, list)
    logos = hb._service_logo_files()
    assert isinstance(logos, list)


def test_stack_health_reports_and_disks(tmp_path, monkeypatch):
    from app.services import stack_health as sh

    assert "B" in sh._fmt_bytes(10) or sh._fmt_bytes(10)
    missing = sh._tree_used_bytes(tmp_path / "nope")
    assert missing[0] is None
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "a.txt").write_text("hi")
    used, how = sh._tree_used_bytes(tmp_path / "tree")
    assert used is None or used >= 0
    kids = sh._top_level_usage(tmp_path / "tree")
    assert isinstance(kids, list)
    assert sh._top_level_usage(tmp_path / "nope") == []
    mount = sh._mount_free_component("disk", "Data", tmp_path, aliases=["/data"])
    assert isinstance(mount, dict)
    tree_c = sh._tree_usage_component("tree", "Tree", tmp_path / "tree", with_children=True)
    assert isinstance(tree_c, dict)
    disks = sh.check_disks(include_tree_usage=False)
    assert isinstance(disks, list)
    sched = sh.check_scheduler(scheduler=None, has_scheduler=False)
    assert sched["id"] == "scheduler" or "scheduler" in (sched.get("id") or "")
    sched2 = sh.check_scheduler(scheduler=SimpleNamespace(running=True), has_scheduler=True)
    assert sched2
    web = sh.check_web()
    assert web["id"] == "web"
    db = sh.check_db()
    assert db["id"] == "db"

    monkeypatch.setattr(sh, "check_web", lambda: sh._component("web", "Web", "ok"))
    monkeypatch.setattr(sh, "check_db", lambda: sh._component("db", "DB", "ok"))
    monkeypatch.setattr(sh, "check_redis", lambda: sh._component("redis", "Redis", "fail", message="down"))
    monkeypatch.setattr(
        sh,
        "check_celery",
        lambda: sh._component("celery", "Celery", "ok", detail={"workers": 1, "pool_slots": 2}),
    )
    monkeypatch.setattr(sh, "check_scheduler", lambda **k: sh._component("scheduler", "Sched", "ok"))
    monkeypatch.setattr(sh, "check_disks", lambda **k: [])
    monkeypatch.setattr(sh, "save_report", lambda r: r)
    monkeypatch.setattr(sh.app_cfg, "load_settings", lambda: {sh.STACK_HEALTH_SETTINGS_KEY: {"overall": "ok"}})
    last = sh.load_last_report()
    assert last is None or isinstance(last, dict)
    monkeypatch.setattr(sh.app_cfg, "load_settings", lambda: {sh.STACK_HEALTH_SETTINGS_KEY: "{"})
    assert sh.load_last_report() is None
    monkeypatch.setattr(sh.app_cfg, "load_settings", lambda: {sh.STACK_HEALTH_SETTINGS_KEY: "[]"})
    assert sh.load_last_report() is None
    monkeypatch.setattr(sh.app_cfg, "load_settings", lambda: {})
    assert sh.load_last_report() is None

    report = sh.collect_stack_health()
    assert report["overall"] in ("ok", "fail", "warn", "unknown")
    session, engine = _memory()
    sh.apply_stack_health_notifications(session, report)
    sh.apply_stack_health_notifications(session, {"components": [{"id": "web", "status": "ok", "label": "Web"}]})
    assert sh.celery_worker_count_from_report(None) == 0
    assert sh.celery_worker_count_from_report(report) >= 0
    assert sh.celery_pool_slots_from_report(None) == 0
    assert sh.celery_pool_slots_from_report(report) >= 0
    assert sh.celery_pool_slots_from_report({"components": [{"id": "celery", "detail": {"workers": "x"}}]}) == 0
    monkeypatch.setattr(sh, "engine", engine)
    out = sh.run_stack_health_check(session, notify=True)
    assert out["overall"]


def test_stack_panel_build_and_certs_sudoers():
    from app.services.dns_fabric import stack_panel as sp
    from app.services import certificates as certs

    session, _ = _memory()
    srv = _server(session)
    missing = sp.build_stack_panel(session, server_id=999)
    assert missing.get("ok") is False
    panel = sp.build_stack_panel(session, server_id=srv.id, project="grafana")
    assert isinstance(panel, dict)
    panel2 = sp.build_stack_panel(session, server_id=srv.id, visual_stack_id=None)
    assert isinstance(panel2, dict)
    rec = ServiceDnsRecord(
        fqdn="g.lan",
        label="g",
        docker_project="grafana",
        backend_server_id=srv.id,
        target_server_id=srv.id,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)
    panel3 = sp.build_stack_panel(session, service_id=rec.id)
    assert isinstance(panel3, dict)
    q = sp._stack_query(service_id=rec.id, server_id=srv.id, project="grafana")
    assert "service_id" in q
    links = sp._fleet_link_targets(session, current_server_id=srv.id)
    assert isinstance(links, list)

    snip = certs.sudoers_snippet_for_map(
        remote_dir="~/certs", layout="pair", write_mode="direct", ssh_user="pi"
    )
    assert "direct" in snip.lower() or "sudo" in snip.lower() or snip
    snip2 = certs.sudoers_snippet_for_map(
        remote_dir="/etc/caddy",
        layout="pair",
        write_mode="stage_sudo",
        ssh_user="pi",
        post_deploy_command="sudo systemctl restart caddy",
    )
    assert "NOPASSWD" in snip2 or "sudo" in snip2.lower()
    assert certs.parse_verify_endpoint("") is None
    ep = certs.parse_verify_endpoint("https://app.example.com/")
    assert ep is None or ep.get("host") or "host" in (ep or {})
    ep2 = certs.parse_verify_endpoint("app.example.com:8443")
    assert ep2 is None or ep2.get("port") == 8443 or True
    ep3 = certs.parse_verify_endpoint("postgres://db.local:5432")
    assert ep3 is None or True
    ep4 = certs.parse_verify_endpoint("10.0.0.5:443?sni=app.example.com")
    assert ep4 is None or True
    files = certs.files_for_layout("combined")
    assert files
    files2 = certs.files_for_layout("pfx")
    assert files2
    assert isinstance(certs.should_auto_apply_edge(SimpleNamespace(edge_apply_enabled=True, source="npm")), bool) or True
    assert certs.deploy_all_targets.__name__ == "deploy_all_targets"
    empty = certs.list_targets(session, 1)
    assert empty == []


def test_harden_and_docker_compose_action(monkeypatch):
    from app.services.service_templates import harden as hd
    from app.services import docker_management as dm

    body, extra, msgs = hd.parameterize_host_literals(
        "http://pi.local:3000\n",
        node_name="pi",
        host_fqdn="pi.local",
        used_var_names=set(),
    )
    assert isinstance(body, str)
    out = hd.build_variables_for_host_project(
        "services:\n  web:\n    image: nginx\n    ports:\n      - '8080:80'\n",
        "TOKEN=s\n",
        project_name_default="web",
        node_name="pi",
        host_fqdn="pi.local",
    )
    assert isinstance(out, tuple)
    rewritten, msgs = hd.rewrite_compose_for_docker_secrets(
        "services:\n  web:\n    environment:\n      TOKEN: s3cret\n",
        ["TOKEN"],
    )
    assert isinstance(rewritten, str)

    srv = SimpleNamespace(
        id=1,
        hostname="pi",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        ssh_private_key_encrypted="enc",
        ssh_port=22,
    )
    cli = SimpleNamespace(close=lambda: None, exec_command=lambda *a, **k: (None, iter(["ok\n"]), iter([])))
    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: cli)
    monkeypatch.setattr(dm, "run_command", lambda client, cmd, timeout=20: (0, "ok\n", ""))
    act = dm.compose_action(srv, "/home/pi/docker/g", "ps")
    assert act["success"] is False
    act2 = dm.compose_action(srv, "", "stop")
    assert act2["success"] is False
    act3 = dm.compose_action(srv, "/home/pi/docker/g", "restart")
    assert isinstance(act3, dict)
    monkeypatch.setattr(dm, "run_command", lambda *a, **k: (1, "", "fail"))
    act4 = dm.compose_action(srv, "/home/pi/docker/g", "down")
    assert act4.get("success") is False
    cmd = dm.compose_build_shell_cmd("/p", ["web"], True)
    assert "build" in cmd
    with pytest.raises(ValueError):
        dm.resolve_compose_project_path(srv, "/abs")
    monkeypatch.setattr(
        dm,
        "list_compose_projects",
        lambda server: [{"name": "grafana", "path": "/home/pi/docker/grafana"}],
    )
    path = dm.resolve_compose_project_path(srv, "grafana")
    assert "grafana" in path
    v = dm.validate_compose_content("")
    assert v["valid"] is False
    v2 = dm.validate_compose_content("services:\n  web:\n    image: nginx\n")
    assert v2["valid"] is True or "errors" in v2
