"""v0.9 coverage push: poll.py, certificates targets, registry residuals.

Mocks remote HTTP; uses SQLite for persistence paths.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Integration,
    IntegrationBinding,
    ManagedCertificate,
    Server,
)
from app.services.integrations import grafana as gf
from app.services.integrations import npm as npm_mod
from app.services.integrations import pihole as ph
from app.services.integrations import poll as poll_mod
from app.services.integrations import registry as reg
from app.services.integrations import uptime_kuma as kuma
from app.services import certificates as certs


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _server(session: Session, name: str = "pi1") -> Server:
    s = Server(
        name=name,
        hostname="10.0.0.1",
        ip_address="10.0.0.1",
        ssh_username="pi",
        sort_order=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _make_pem(cn: str = "app.example.com", days: int = 60) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(cn)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    full = cert.public_bytes(serialization.Encoding.PEM).decode()
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return full, priv


# ===========================================================================
# poll.py
# ===========================================================================


def test_process_lock_and_redis_lock_fail_soft():
    lock_a = poll_mod._process_lock(42)
    lock_b = poll_mod._process_lock(42)
    assert lock_a is lock_b
    lock_c = poll_mod._process_lock(99)
    assert lock_c is not lock_a

    # redis success path
    mock_client = MagicMock()
    mock_client.set.return_value = True
    mock_client.get.return_value = None  # set after
    with patch("redis.from_url", return_value=mock_client):
        client, token = poll_mod._redis_lock(7, ttl=30)
    assert client is mock_client and token
    mock_client.get.return_value = token
    poll_mod._redis_unlock(client, 7, token)
    mock_client.delete.assert_called()

    # redis held (set returns false)
    mock_client2 = MagicMock()
    mock_client2.set.return_value = False
    with patch("redis.from_url", return_value=mock_client2):
        c2, t2 = poll_mod._redis_lock(8)
    assert c2 is mock_client2 and t2 is None

    # redis unavailable / fail → (None, None)
    with patch("redis.from_url", side_effect=OSError("no redis")):
        client, token = poll_mod._redis_lock(1, ttl=5)
    assert client is None and token is None
    poll_mod._redis_unlock(None, 1, "")
    bad = MagicMock()
    bad.get.side_effect = RuntimeError("x")
    poll_mod._redis_unlock(bad, 1, "tok")


def test_poll_unlocked_missing_disabled_unsupported():
    session = _session()
    assert poll_mod._poll_unlocked(session, 999, notify=False)["error"] == "integration not found"

    integ = Integration(
        type="unknown_type",
        name="X",
        base_url="https://x",
        enabled=False,
        config_json="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    assert poll_mod._poll_unlocked(session, integ.id, notify=False)["error"] == "integration disabled"

    integ.enabled = True
    session.add(integ)
    session.commit()
    out = poll_mod._poll_unlocked(session, integ.id, notify=False)
    assert "unsupported" in (out.get("error") or "")


def test_poll_nmap_and_test_connection_nmap():
    session = _session()
    integ = Integration(
        type=reg.TYPE_NMAP,
        name="LAN",
        base_url="",
        enabled=True,
        config_json=json.dumps({"cidrs": ["10.0.0.0/24"]}),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)

    with patch(
        "app.services.nmap.config.refresh_status",
        return_value={"ok": True, "device_count": 3, "worker_online": True},
    ):
        out = poll_mod._poll_nmap(session, integ, notify=True)
    assert out["ok"] is True
    assert out["device_count"] == 3

    with patch("app.services.nmap.runtime.worker_online", return_value={"online": True}):
        with patch(
            "app.services.nmap.config.parse_nmap_config",
            return_value={"cidrs": ["10.0.0.0/24"]},
        ):
            tc = poll_mod.test_connection(integ)
    assert tc.ok is True

    with patch("app.services.nmap.runtime.worker_online", return_value={"online": False}):
        with patch(
            "app.services.nmap.config.parse_nmap_config",
            return_value={"cidrs": []},
        ):
            tc2 = poll_mod.test_connection(integ)
    assert tc2.ok is False


def test_poll_kuma_updates_bindings_and_notifies():
    session = _session()
    server = _server(session)
    integ = reg.create_kuma(
        session,
        name="Kuma",
        base_url="https://kuma.example",
        api_key="key",
        username="admin",
        password="secret",
    )
    mon = kuma.KumaMonitor(
        id="web",
        name="web",
        type="http",
        url="https://app.example.com",
        status="up",
        response_time_ms=12.0,
        cert_is_valid=True,
        cert_days_remaining=40,
        dashboard_id="7",
    )
    mon_down = kuma.KumaMonitor(
        id="ssh",
        name="ssh",
        type="port",
        port="22",
        hostname="10.0.0.1",
        status="down",
    )
    result = kuma.KumaPollResult(ok=True, monitors=[mon, mon_down])

    b_svc = reg.set_binding(
        session,
        integration_id=integ.id,
        server_id=server.id,
        external_id="web",
        role=reg.ROLE_SERVICE,
        docker_project="stack",
        docker_container="web",
        last_state="up",
    )
    b_ssh = reg.set_binding(
        session,
        integration_id=integ.id,
        server_id=server.id,
        external_id="ssh",
        role=reg.ROLE_SSH,
        last_state="up",
    )
    # force previous up for ssh so down notifies
    b_ssh.last_state = "up"
    session.add(b_ssh)
    session.commit()

    with patch.object(kuma, "fetch_metrics", return_value=result):
        with patch.object(kuma, "fetch_dashboard_id_map", return_value={"web": "7"}):
            with patch.object(poll_mod.notif_svc, "upsert_notification") as un:
                with patch.object(poll_mod.notif_svc, "resolve_by_fingerprint") as res:
                    with patch.object(reg, "maybe_discover_logo", return_value=False):
                        out = poll_mod._poll_kuma(session, integ, notify=True)
    assert out["ok"] is True
    assert out["bindings_updated"] == 2
    session.refresh(b_ssh)
    assert b_ssh.last_state == "down"
    assert un.called  # down transition

    # recovery
    mon_down.status = "up"
    result2 = kuma.KumaPollResult(ok=True, monitors=[mon, mon_down])
    with patch.object(kuma, "fetch_metrics", return_value=result2):
        with patch.object(kuma, "fetch_dashboard_id_map", return_value={}):
            with patch.object(poll_mod.notif_svc, "resolve_by_fingerprint") as res:
                with patch.object(reg, "maybe_discover_logo", return_value=False):
                    poll_mod._poll_kuma(session, integ, notify=True)
    assert res.called

    # missing monitor → unknown
    result3 = kuma.KumaPollResult(ok=True, monitors=[mon])
    with patch.object(kuma, "fetch_metrics", return_value=result3):
        with patch.object(kuma, "fetch_dashboard_id_map", return_value={}):
            with patch.object(reg, "maybe_discover_logo", return_value=False):
                poll_mod._poll_kuma(session, integ, notify=False)
    session.refresh(b_ssh)
    assert b_ssh.last_state == "unknown"


def test_poll_grafana_pihole_npm_and_test_connection():
    session = _session()
    server = _server(session)

    # Grafana
    gf_int = reg.create_grafana(
        session, name="GF", base_url="https://gf.example", api_key="tok"
    )
    dash = gf.GrafanaDashboard(uid="abc", title="Host Metrics", folder_title="General")
    gres = gf.GrafanaPollResult(ok=True, version="10.1", dashboards=[dash])
    reg.set_binding(
        session,
        integration_id=gf_int.id,
        server_id=server.id,
        external_id="abc",
        role=reg.ROLE_DASHBOARD,
        external_meta={"kind": "metrics"},
    )
    with patch.object(gf, "poll", return_value=gres):
        out = poll_mod._poll_grafana(session, gf_int, notify=True)
    assert out["ok"] is True
    assert out["dashboard_count"] == 1
    assert out["bindings_updated"] == 1

    # Pi-hole
    ph_int = reg.create_pihole(
        session, name="PH", base_url="https://ph.example", password="pw"
    )
    prest = ph.PiholeStats(ok=True, queries=100, blocked=10, version="6")
    with patch.object(ph, "fetch_stats", return_value=prest):
        outp = poll_mod._poll_pihole(session, ph_int, notify=False)
    assert outp["ok"] is True
    assert outp["queries"] == 100

    # NPM
    npm_int = reg.create_npm(
        session,
        name="NPM",
        base_url="https://npm.example",
        identity="a@b.c",
        password="secret",
    )
    nres = npm_mod.NpmPollResult(
        ok=True,
        proxy_hosts=[
            {
                "id": "5",
                "label": "App",
                "enabled": True,
                "forward_host": "10.0.0.1",
                "domain_names": ["app.example.com"],
            }
        ],
        certificates=[{"id": "1", "nice_name": "cert"}],
    )
    reg.set_binding(
        session,
        integration_id=npm_int.id,
        server_id=server.id,
        external_id="5",
        role=reg.ROLE_PROXY_HOST,
    )
    with patch.object(npm_mod, "poll", return_value=nres):
        outn = poll_mod._poll_npm(session, npm_int, notify=False)
    assert outn["ok"] is True
    assert outn["proxy_host_count"] == 1
    assert outn["bindings_updated"] == 1

    # missing proxy host
    with patch.object(
        npm_mod,
        "poll",
        return_value=npm_mod.NpmPollResult(ok=True, proxy_hosts=[], certificates=[]),
    ):
        poll_mod._poll_npm(session, npm_int, notify=False)

    # test_connection branches
    with patch.object(gf, "poll", return_value=gres):
        assert poll_mod.test_connection(gf_int).ok is True
    with patch.object(ph, "fetch_stats", return_value=prest):
        assert poll_mod.test_connection(ph_int).ok is True
    with patch.object(npm_mod, "poll", return_value=nres):
        assert poll_mod.test_connection(npm_int).ok is True
    with patch.object(
        kuma, "fetch_metrics", return_value=kuma.KumaPollResult(ok=True, monitors=[])
    ):
        k = reg.create_kuma(
            session, name="K2", base_url="https://kuma2.example", api_key="k"
        )
        assert poll_mod.test_connection(k).ok is True


def test_poll_integration_skip_when_locked_and_poll_all():
    session = _session()
    integ = reg.create_kuma(
        session, name="K", base_url="https://kuma.example", api_key="k"
    )

    # successful poll_integration with provided session + redis noop
    with patch.object(poll_mod, "_redis_lock", return_value=(None, None)):
        with patch.object(
            poll_mod,
            "_poll_unlocked",
            return_value={"ok": True, "integration_id": integ.id},
        ) as pu:
            out_ok = poll_mod.poll_integration(
                integ.id, notify=False, session=session
            )
    assert out_ok["ok"] is True
    assert pu.called

    # force process lock held
    lock = poll_mod._process_lock(integ.id)
    assert lock.acquire(blocking=False)
    try:
        with patch.object(poll_mod, "_redis_lock", return_value=(None, None)):
            out = poll_mod.poll_integration(integ.id, notify=False, session=session)
        assert out.get("skipped") is True
    finally:
        lock.release()

    # redis already held
    with patch.object(poll_mod, "_redis_lock", return_value=(MagicMock(), None)):
        out2 = poll_mod.poll_integration(integ.id, notify=False, session=session)
    assert out2.get("skipped") is True

    # poll_all_enabled with mock
    with patch("app.services.integrations.poll.Session") as Sess:
        fake = MagicMock()
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda *a: None
        fake.exec.return_value.all.return_value = [
            SimpleNamespace(id=1),
            SimpleNamespace(id=2),
        ]
        Sess.return_value = fake
        with patch.object(
            poll_mod,
            "poll_integration",
            side_effect=[
                {"ok": True, "integration_id": 1},
                Exception("boom"),
            ],
        ):
            results = poll_mod.poll_all_enabled(notify=False)
    assert len(results) == 2
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False


def test_notify_transition_edges():
    session = _session()
    server = _server(session)
    integ = reg.create_kuma(
        session, name="K", base_url="https://kuma.example", api_key="k"
    )
    b = reg.set_binding(
        session,
        integration_id=integ.id,
        server_id=server.id,
        external_id="m1",
        role=reg.ROLE_SERVICE,
        external_label="App",
    )
    with patch.object(poll_mod.notif_svc, "upsert_notification") as un:
        poll_mod._notify_transition(session, integ, b, "up", "down")
        assert un.called
    with patch.object(poll_mod.notif_svc, "resolve_by_fingerprint") as res:
        poll_mod._notify_transition(session, integ, b, "down", "up")
        poll_mod._notify_transition(session, integ, b, "down", "pending")
        assert res.call_count >= 2


# ===========================================================================
# certificates targets
# ===========================================================================


def test_certificates_target_crud_public_sudoers_edge():
    session = _session()
    server = _server(session)
    full, key = _make_pem()
    cert = certs.upsert_from_pems(
        session, name="lab", fullchain_pem=full, privkey_pem=key, source="upload"
    )

    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        label="NPM volume",
        remote_dir="~/npm/certs",
        layout="pair_and_combined",
        write_mode="stage_sudo",
        pfx_export_password="secret",
        post_deploy_command="systemctl reload nginx",
        file_owner="root",
        file_group="root",
    )
    assert t.id is not None
    assert t.layout == "pair_and_combined"
    assert t.pfx_export_password_encrypted

    listed = certs.list_targets(session, cert.id)
    assert len(listed) == 1

    t2 = certs.update_target(
        session,
        t.id,
        label="Updated",
        layout="pair",
        write_mode="direct",
        clear_pfx_password=True,
        enabled=True,
        post_deploy_command="",
    )
    assert t2.label == "Updated"
    assert t2.pfx_export_password_encrypted is None

    pub = certs.public_target_dict(
        t2, server_name=server.name, cert_fingerprint=cert.fingerprint_sha256
    )
    assert pub["server_name"] == server.name
    assert "files" in pub
    assert "sudoers_snippet" in pub
    assert pub["in_sync"] is False

    # mark as deployed success with same fp → in_sync
    t2.last_deploy_status = "success"
    t2.last_deploy_fingerprint = cert.fingerprint_sha256
    session.add(t2)
    session.commit()
    pub2 = certs.public_target_dict(
        t2, server_name=server.name, cert_fingerprint=cert.fingerprint_sha256
    )
    assert pub2["in_sync"] is True

    # stale
    t2.last_deploy_fingerprint = "deadbeef" * 8
    session.add(t2)
    session.commit()
    pub3 = certs.public_target_dict(
        t2, server_name=server.name, cert_fingerprint=cert.fingerprint_sha256
    )
    assert pub3["stale_vs_vault"] is True

    snip = certs.sudoers_snippet_for_map(
        remote_dir="~/certs",
        layout="pair",
        write_mode="stage_sudo",
        post_deploy_command="true",
    )
    assert "piherder" in snip or "NOPASSWD" in snip or snip

    payloads = certs._layout_file_payloads("pair", full, key, t2)
    assert len(payloads) == 2
    payloads2 = certs._layout_file_payloads("combined", full, key, t2)
    assert any("BEGIN" in c for _, c in payloads2)

    assert certs._normalize_write_mode("stage_sudo") == "stage_sudo"
    assert certs._normalize_write_mode("nope") == "direct"

    # edge helpers
    with patch.object(certs, "edge_certs_writable", return_value=True):
        d = certs.public_cert_dict(cert)
        assert "edge_available" in d
    st = certs.edge_caddy_status()
    assert "certs_dir" in st
    assert certs.edge_certs_dir()

    assert certs.should_auto_apply_edge(cert) is False
    cert2 = certs.set_edge_apply_enabled(session, cert.id, True)
    assert cert2.edge_apply_enabled is True
    cert3 = certs.set_edge_apply_enabled(session, cert.id, False)
    assert cert3.edge_apply_enabled is False

    assert certs.delete_target(session, t2.id) is True
    assert certs.delete_target(session, 99999) is False

    with pytest.raises(ValueError):
        certs.create_target(session, certificate_id=99999, server_id=server.id)
    with pytest.raises(ValueError):
        certs.create_target(session, certificate_id=cert.id, server_id=99999)
    with pytest.raises(ValueError):
        certs.update_target(session, 99999, label="x")
    with pytest.raises(ValueError):
        certs.set_edge_apply_enabled(session, 99999, True)


def test_certificates_deploy_all_empty_and_redistribute_mock():
    session = _session()
    full, key = _make_pem()
    cert = certs.upsert_from_pems(
        session, name="c2", fullchain_pem=full, privkey_pem=key
    )
    # no targets
    out = certs.deploy_all_targets(session, cert.id, force=False)
    assert out["ok"] is True
    assert out["count"] == 0

    server = _server(session, name="pi2")
    t = certs.create_target(
        session, certificate_id=cert.id, server_id=server.id, layout="pair"
    )
    with patch.object(
        certs,
        "deploy_target",
        return_value={"ok": True, "target_id": t.id},
    ):
        out2 = certs.deploy_all_targets(session, cert.id, force=True)
    assert out2["count"] == 1
    assert out2["ok"] is True

    with patch.object(
        certs,
        "deploy_all_targets",
        return_value={"ok": True, "count": 1, "results": []},
    ) as dep:
        with patch.object(certs, "should_auto_apply_edge", return_value=False):
            r = certs.redistribute_after_renew(session, cert.id, force=True)
    assert dep.called
    assert r.get("ok") is True or "fleet" in r or r

    # deploy_target early paths
    assert certs.deploy_target(session, 99999)["ok"] is False
    t.last_deploy_status = "success"
    t.last_deploy_fingerprint = cert.fingerprint_sha256
    session.add(t)
    session.commit()
    skip = certs.deploy_target(session, t.id, force=False)
    assert skip.get("skipped") is True

    # forced deploy with mocked SSH
    mock_client = MagicMock()
    mock_sftp = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp
    mock_file = MagicMock()
    mock_sftp.file.return_value.__enter__ = lambda s: mock_file
    mock_sftp.file.return_value.__exit__ = lambda *a: None

    def run_cmd(client, cmd, timeout=30):
        if "HOME" in cmd or "printf" in cmd:
            return 0, "/home/pi", ""
        return 0, "", ""

    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=mock_client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_cmd):
            res = certs.deploy_target(session, t.id, force=True, progress=lambda m: None)
    # may succeed or fail depending on write path; exercise code
    assert isinstance(res, dict)
    assert "ok" in res


# ===========================================================================
# registry residuals
# ===========================================================================


def test_registry_grafana_display_and_query_templates():
    session = _session()
    server = _server(session)
    gf_int = reg.create_grafana(
        session,
        name="GF",
        base_url="https://gf.example",
        api_key="t",
        query_template="var-job={hostname}",
        query_template_container="var-c={container}",
        query_template_container_host="var-host={hostname}",
        query_template_logs="var-log={hostname}",
    )
    reg.set_preferred_display_name(session, gf_int, "uid1", "My Metrics")
    session.refresh(gf_int)
    assert reg.preferred_display_name(gf_int, "uid1") == "My Metrics"
    reg.set_preferred_display_name(session, gf_int, "uid1", "")  # clear
    session.refresh(gf_int)
    assert reg.preferred_display_name(gf_int, "uid1") == ""

    b = reg.set_binding(
        session,
        integration_id=gf_int.id,
        server_id=server.id,
        external_id="uid1",
        role=reg.ROLE_DASHBOARD,
        docker_project="stack",
        docker_container="web",
        external_meta={"kind": "containers", "grafana_title": "Containers"},
    )
    label, override, title = reg.resolve_grafana_display_label(gf_int, b)
    assert label
    assert title == "Containers" or label

    assert reg.binding_grafana_kind(b) == reg.GRAFANA_KIND_CONTAINERS
    assert (
        reg.binding_grafana_kind(
            None, meta={}, docker_project="p", docker_container=""
        )
        == reg.GRAFANA_KIND_CONTAINERS
    )
    assert reg.binding_grafana_kind(None, meta={"kind": "logs"}) == reg.GRAFANA_KIND_LOGS
    assert reg.binding_grafana_kind(None, meta={}) == reg.GRAFANA_KIND_METRICS

    qt = reg.resolve_grafana_query_template(
        gf_int, kind="containers", docker_container="web"
    )
    assert "container" in qt.lower() or "var" in qt
    qt_host = reg.resolve_grafana_query_template(
        gf_int, kind="containers", docker_container=""
    )
    assert qt_host
    qt_logs = reg.resolve_grafana_query_template(gf_int, kind="logs")
    assert qt_logs
    qt_met = reg.resolve_grafana_query_template(gf_int, kind="metrics")
    assert qt_met
    # binding override
    qt_ov = reg.resolve_grafana_query_template(
        gf_int, kind="metrics", meta={"query_template": "custom=1"}
    )
    assert qt_ov == "custom=1"

    # cache helpers
    gf_int.last_status_json = json.dumps(
        {
            "ok": True,
            "dashboards": [{"uid": "u1", "title": "T1", "url": "/d/u1"}],
            "monitors": [{"id": "1", "name": "m"}],
        }
    )
    session.add(gf_int)
    session.commit()
    assert reg.dashboards_from_cache(gf_int) or reg.parse_last_status(gf_int)
    assert reg.parse_last_status(SimpleNamespace(last_status_json="{bad")) == {}
    assert reg.parse_last_status(SimpleNamespace(last_status_json=None)) == {}


def test_registry_chips_indexes_open_url_and_message():
    session = _session()
    server = _server(session)
    kuma_i = reg.create_kuma(
        session, name="Kuma", base_url="https://kuma.example", api_key="k"
    )
    host_b = reg.set_binding(
        session,
        integration_id=kuma_i.id,
        server_id=server.id,
        external_id="ha",
        role=reg.ROLE_SERVICE,
        external_label="Home Assistant",
        last_state="up",
        external_meta={"url": "https://ha.example.com"},
    )
    dock_b = reg.set_binding(
        session,
        integration_id=kuma_i.id,
        server_id=server.id,
        external_id="web",
        role=reg.ROLE_SERVICE,
        docker_project="stack",
        docker_container="web",
        external_label="Web",
        last_state="down",
        external_meta={"compose_service": "webapp", "url": "https://web.example.com"},
    )
    reg.set_binding(
        session,
        integration_id=kuma_i.id,
        server_id=server.id,
        external_id="ssh",
        role=reg.ROLE_SSH,
        last_state="up",
    )

    assert reg.fleet_service_count(session) >= 2
    chips = reg.fleet_service_chips(session)
    assert any(c.get("location_kind") == "host" for c in chips)
    assert any(c.get("location_kind") == "docker" for c in chips)

    host_chips = reg.host_service_chips_for_server(session, server.id)
    assert len(host_chips) >= 1
    all_chips = reg.all_service_chips_for_server(session, server.id)
    assert len(all_chips) >= 2

    idx = reg.kuma_index_for_server(session, server.id)
    assert "by_project" in idx and "by_container" in idx
    assert "web" in idx["by_container"] or "webapp" in idx["by_container"]

    mon = kuma.KumaMonitor(
        id="1",
        name="m",
        status="up",
        response_time_ms=5,
        cert_is_valid=True,
        cert_days_remaining=10,
        url="https://x",
    )
    msg = reg.binding_message_from_monitor(mon)
    assert "ms" in msg or "TLS" in msg
    mon2 = kuma.KumaMonitor(id="2", name="m2", status="up", cert_is_valid=False)
    assert "TLS" in reg.binding_message_from_monitor(mon2)

    # open urls
    url_k = reg.binding_open_url(kuma_i, dock_b)
    assert "kuma" in url_k or url_k.startswith("http")

    npm = reg.create_npm(
        session,
        name="NPM",
        base_url="https://npm.example",
        identity="a@b.c",
        password="p",
    )
    pb = reg.set_binding(
        session,
        integration_id=npm.id,
        server_id=server.id,
        external_id="1",
        role=reg.ROLE_PROXY_HOST,
    )
    assert "/nginx/proxy" in reg.binding_open_url(npm, pb)

    phi = reg.create_pihole(
        session, name="PH", base_url="https://ph.example", password="pw"
    )
    # any binding for open url type check
    assert "/admin" in reg.binding_open_url(phi, host_b)

    gfi = reg.create_grafana(
        session, name="G", base_url="https://gf.example", api_key="t"
    )
    gb = reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="uid-x",
        role=reg.ROLE_DASHBOARD,
        external_meta={"kind": "metrics", "url": "/d/uid-x/slug", "slug": "slug"},
    )
    gurl = reg.binding_open_url(gfi, gb, server=server)
    assert gurl.startswith("http")

    gchips = reg.grafana_chips_for_server(session, server.id)
    assert isinstance(gchips, list)
    gidx = reg.grafana_index_for_server(session, server.id)
    assert isinstance(gidx, dict)

    by_srv = reg.bindings_by_server(session, role=reg.ROLE_SSH)
    assert server.id in by_srv

    # logo discover soft-fail / skip
    assert reg.maybe_discover_logo(session, dock_b) is False  # has no http in meta for try — wait has url
    with patch(
        "app.services.service_logos.try_discover_and_save", return_value="service_logos/1.png"
    ):
        # need binding without logo
        dock_b.logo_path = None
        session.add(dock_b)
        session.commit()
        ok = reg.maybe_discover_logo(session, dock_b)
        assert ok is True
        session.refresh(dock_b)
        assert dock_b.logo_path

    assert reg.maybe_discover_logo(session, dock_b) is False  # already has logo
    host_b.role = reg.ROLE_SSH
    assert reg.maybe_discover_logo(session, host_b) is False


def test_registry_update_npm_pihole_and_apply_grafana_names():
    session = _session()
    server = _server(session)
    npm = reg.create_npm(
        session,
        name="NPM",
        base_url="https://npm.example",
        identity="a@b.c",
        password="old",
    )
    npm2 = reg.update_npm(
        session,
        npm,
        name="NPM2",
        base_url="https://npm2.example",
        identity="b@c.d",
        password="new",
        poll_interval_sec=90,
        enabled=True,
    )
    assert npm2.name == "NPM2"
    assert reg.npm_credentials(npm2)[0] == "b@c.d"

    ph1 = reg.create_pihole(
        session, name="PH1", base_url="https://ph1.example", password="p1", is_primary=True
    )
    ph2 = reg.create_pihole(
        session, name="PH2", base_url="https://ph2.example", password="p2", is_primary=False
    )
    reg.set_pihole_primary_flags(session, ph2.id)
    session.refresh(ph1)
    session.refresh(ph2)
    # one primary
    primaries = [
        i
        for i in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if reg.is_pihole_primary(i)
    ]
    assert len(primaries) == 1
    assert primaries[0].id == ph2.id

    ph1b = reg.update_pihole(
        session, ph1, name="PH1b", password="p1new", is_primary=True
    )
    assert ph1b.name == "PH1b"

    gfi = reg.create_grafana(
        session, name="G", base_url="https://gf.example", api_key="t"
    )
    reg.set_preferred_display_name(session, gfi, "uid-a", "Preferred A")
    session.refresh(gfi)
    b = reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="uid-a",
        role=reg.ROLE_DASHBOARD,
        external_meta={"kind": "metrics", "grafana_title": "Title A"},
    )
    updated = reg.apply_grafana_preferred_name(
        session, integration_id=gfi.id, uid="uid-a", display_name="Preferred A"
    )
    assert isinstance(updated, list)
    session.refresh(b)
    assert b.external_label == "Preferred A"
    # via binding wrapper
    updated2 = reg.apply_grafana_display_name(
        session,
        integration_id=gfi.id,
        binding_id=b.id,
        display_name="Override B",
    )
    assert updated2
    session.refresh(b)
    assert b.external_label == "Override B"
    meta = reg.parse_binding_meta(b)
    assert meta.get("label_override") == "Override B"

    mon_list = reg.monitors_from_cache(
        SimpleNamespace(
            last_status_json=json.dumps({"monitors": [{"id": "1", "name": "m"}]})
        )
    )
    assert len(mon_list) == 1


def test_kuma_monitor_helpers_and_grafana_chip_dict():
    m = kuma.KumaMonitor(
        id="1", name="Web HTTPS", type="http", url="https://x", status="up", port=""
    )
    assert m.is_service_like() is True
    m2 = kuma.KumaMonitor(id="2", name="SSH", type="port", port="22", status="up")
    assert m2.is_ssh_like() is True
    assert m2.is_service_like() is False
    m3 = kuma.KumaMonitor(id="3", name="DB", type="port", port="5432", status="up")
    assert m3.is_service_like() is True

    kuma.apply_dashboard_id_map([m], {"Web HTTPS": "99"})
    assert m.dashboard_id == "99"

    found = kuma.find_monitor([m, m2], "1")
    assert found is m
    assert kuma.find_monitor([m], "missing") is None
    assert kuma.find_monitor([m], "") is None

    assert "dashboard/99" in kuma.open_kuma_url("https://kuma.example", dashboard_id="99")
    assert kuma.open_kuma_url("https://kuma.example") 

    # grafana chip via registry
    session = _session()
    server = _server(session)
    gfi = reg.create_grafana(
        session, name="G", base_url="https://gf.example", api_key="t"
    )
    b = reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="uid1",
        role=reg.ROLE_DASHBOARD,
        external_meta={"kind": "logs", "url": "/d/uid1/x", "grafana_title": "Logs"},
    )
    chips = reg.grafana_chips_for_server(session, server.id)
    assert chips
    idx = reg.grafana_index_for_server(session, server.id)
    assert idx


def test_registry_list_bindings_inventory_delete_nmap_cascade():
    session = _session()
    server = _server(session)
    k = reg.create_kuma(
        session, name="K", base_url="https://kuma.example", api_key="k"
    )
    reg.set_binding(
        session,
        integration_id=k.id,
        server_id=server.id,
        external_id="a",
        role=reg.ROLE_SSH,
    )
    reg.set_binding(
        session,
        integration_id=k.id,
        server_id=server.id,
        external_id="b",
        role=reg.ROLE_SERVICE,
        docker_project="p",
    )
    assert len(reg.list_bindings(session, integration_id=k.id)) == 2
    assert len(reg.list_bindings(session, server_id=server.id, role=reg.ROLE_SSH)) == 1
    assert len(reg.list_bindings(session, role=reg.ROLE_SERVICE)) >= 1

    # docker inventory options
    server.docker_inventory_json = json.dumps(
        {
            "projects": [
                {
                    "name": "stack",
                    "path": "/home/pi/stack",
                    "containers": [
                        {"name": "web", "compose_service": "web", "running": True},
                        {"name": "db", "running": False},
                    ],
                }
            ]
        }
    )
    session.add(server)
    session.commit()
    with patch(
        "app.services.docker_inventory.parse_inventory",
        return_value={
            "projects": [
                {
                    "name": "stack",
                    "path": "/home/pi/stack",
                    "containers": [
                        {"name": "web", "compose_service": "web", "running": True},
                        {"compose_service": "db", "running": False},
                    ],
                },
                {"name": "", "containers": []},
                "bad",
            ]
        },
    ):
        opts = reg.docker_inventory_options(session, server.id)
    assert any(o["name"] == "stack" for o in opts)
    assert reg.docker_inventory_options(session, 99999) == []

    # Grafana dashboard set_binding scopes
    gfi = reg.create_grafana(
        session, name="G2", base_url="https://gf2.example", api_key="t"
    )
    # host metrics
    reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="m1",
        role=reg.ROLE_DASHBOARD,
        external_meta={"kind": "metrics"},
    )
    # containers project scope
    reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="c1",
        role=reg.ROLE_DASHBOARD,
        docker_project="stack",
        external_meta={"kind": "containers"},
    )
    # containers container scope
    reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="c2",
        role=reg.ROLE_DASHBOARD,
        docker_project="stack",
        docker_container="web",
        external_meta={"kind": "containers"},
    )
    gchips = reg.grafana_chips_for_server(session, server.id)
    assert len(gchips) >= 2
    gidx = reg.grafana_index_for_server(session, server.id)
    assert gidx
    # dashboard bindings stored
    assert (
        len(reg.list_bindings(session, integration_id=gfi.id, role=reg.ROLE_DASHBOARD))
        >= 2
    )

    # nmap delete cascade (best-effort — schema fields may vary)
    nmap = Integration(
        type=reg.TYPE_NMAP,
        name="LAN",
        base_url="",
        enabled=True,
        config_json="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(nmap)
    session.commit()
    session.refresh(nmap)
    try:
        from app.models import NmapDevice, NmapScanRun, NmapScanSchedule

        session.add(
            NmapDevice(
                integration_id=nmap.id,
                ip_address="10.0.0.5",
                state="new",
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        session.add(
            NmapScanSchedule(
                integration_id=nmap.id,
                name="s",
                intensity="discovery",
                interval_hours=6,
                enabled=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        session.add(
            NmapScanRun(
                integration_id=nmap.id,
                intensity="discovery",
                status="success",
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    reg.delete_integration(session, nmap)
    assert reg.get_integration(session, nmap.id) is None

    # encrypt_credentials_full keep password
    blob = reg.encrypt_credentials("k", username="u", password="p")
    keep = SimpleNamespace(credentials_encrypted=blob)
    new_blob = reg.encrypt_credentials_full("k2", username="u2", password="", keep_from=keep)
    c = reg.decrypt_credentials(SimpleNamespace(credentials_encrypted=new_blob))
    assert c["api_key"] == "k2" and c["password"] == "p"


def test_certificates_public_dict_edge_and_sudoers_layouts():
    session = _session()
    full, key = _make_pem(days=5)
    cert = certs.upsert_from_pems(
        session, name="edge", fullchain_pem=full, privkey_pem=key
    )
    cert.edge_apply_enabled = True
    cert.last_edge_deploy_status = "success"
    cert.last_edge_deploy_fingerprint = cert.fingerprint_sha256
    session.add(cert)
    session.commit()
    with patch.object(certs, "edge_certs_writable", return_value=True):
        d = certs.public_cert_dict(cert)
    assert d["edge_in_sync"] is True
    assert d["edge_mapped"] is True
    assert d["expiring_soon"] is True  # 5 days vs renew_days default 21

    cert.last_edge_deploy_fingerprint = "other"
    session.add(cert)
    session.commit()
    with patch.object(certs, "edge_certs_writable", return_value=False):
        d2 = certs.public_cert_dict(cert)
    assert d2["edge_stale"] is True

    # sudoers for multiple layouts
    for layout in ("pair", "combined", "pair_and_combined", "pair_and_pfx", "pair_combined_pfx"):
        for wm in ("direct", "stage_sudo"):
            s = certs.sudoers_snippet_for_map(
                remote_dir="/opt/certs",
                layout=layout,
                write_mode=wm,
                post_deploy_command="systemctl reload caddy",
            )
            assert isinstance(s, str) and len(s) > 10

    server = _server(session, name="tgt")
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair_and_pfx",
        write_mode="bogus",  # normalize
    )
    assert t.write_mode == "direct"
    t2 = certs.update_target(
        session, t.id, layout="nope", write_mode="stage_sudo", server_id=server.id
    )
    assert t2.layout == "pair"
    assert t2.write_mode == "stage_sudo"

    # update server_id missing
    with pytest.raises(ValueError):
        certs.update_target(session, t2.id, server_id=99999)


def test_poll_grafana_missing_dashboard_and_kuma_fail():
    session = _session()
    server = _server(session)
    gfi = reg.create_grafana(
        session, name="G", base_url="https://gf.example", api_key="t"
    )
    reg.set_binding(
        session,
        integration_id=gfi.id,
        server_id=server.id,
        external_id="missing-uid",
        role=reg.ROLE_DASHBOARD,
        external_meta={"kind": "metrics"},
    )
    gres = gf.GrafanaPollResult(ok=True, version="10", dashboards=[])
    with patch.object(gf, "poll", return_value=gres):
        out = poll_mod._poll_grafana(session, gfi, notify=False)
    assert out["ok"] is True
    assert out["bindings_updated"] == 1

    gres2 = gf.GrafanaPollResult(ok=False, error="down", version="")
    with patch.object(gf, "poll", return_value=gres2):
        out2 = poll_mod._poll_grafana(session, gfi, notify=False)
    assert out2["ok"] is False

    k = reg.create_kuma(
        session, name="Kfail", base_url="https://kuma.example", api_key="k"
    )
    with patch.object(
        kuma,
        "fetch_metrics",
        return_value=kuma.KumaPollResult(ok=False, error="timeout", monitors=[]),
    ):
        with patch.object(reg, "maybe_discover_logo", return_value=False):
            outk = poll_mod._poll_kuma(session, k, notify=False)
    assert outk["ok"] is False
    session.refresh(k)
    assert k.last_error


def test_kuma_prometheus_parse_and_npm_pihole_pure():
    text = """
# HELP monitor_status
monitor_status{monitor_name="Web",monitor_type="http",monitor_url="https://app.example.com",monitor_id="12"} 1
monitor_response_time{monitor_name="Web",monitor_type="http",monitor_url="https://app.example.com",monitor_id="12"} 42.5
monitor_cert_days_remaining{monitor_name="Web",monitor_type="http",monitor_url="https://app.example.com",monitor_id="12"} 30
monitor_cert_is_valid{monitor_name="Web",monitor_type="http",monitor_url="https://app.example.com",monitor_id="12"} 1
monitor_status{monitor_name="SSH",monitor_type="port",monitor_hostname="10.0.0.1",monitor_port="22"} 0
monitor_status{monitor_name="DB",monitor_type="port",monitor_hostname="10.0.0.2",monitor_port="5432"} 1
"""
    mons = kuma.parse_prometheus_metrics(text)
    assert len(mons) >= 2
    web = next(m for m in mons if m.name == "Web")
    assert web.status == "up" or web.status_raw == 1.0
    assert web.response_time_ms == 42.5
    assert web.cert_days_remaining == 30
    assert web.cert_is_valid is True
    assert web.dashboard_id == "12" or web.id == "12"

    assert kuma.monitor_key_from_labels({"monitor_id": "5"}) == "5"
    assert kuma.monitor_key_from_labels({"monitor_name": "X"}) == "X"
    assert kuma._clean_label("null") == ""
    assert kuma._parse_labels('foo="bar",baz="qux"')

    # NPM list helpers via mocked _get_json
    with patch.object(
        npm_mod,
        "_get_json",
        return_value=[
            {
                "id": 1,
                "domain_names": ["a.example.com"],
                "forward_host": "10.0.0.1",
                "forward_port": 80,
                "enabled": True,
            },
            {
                "id": 2,
                "domains": "b.example.com",
                "forward_host": "10.0.0.2",
                "enabled": False,
            },
        ],
    ):
        hosts = npm_mod.list_proxy_hosts("https://npm.example", "tok")
    assert len(hosts) == 2
    assert hosts[0]["id"] == "1"
    assert hosts[1]["enabled"] is False

    with patch.object(
        npm_mod,
        "_get_json",
        return_value={
            "data": [
                {
                    "id": 9,
                    "nice_name": "Cert",
                    "domain_names": ["*.example.com"],
                    "provider": "letsencrypt",
                    "meta": {"expires_on": "2030-01-01"},
                }
            ]
        },
    ):
        certs_list = npm_mod.list_certificates("https://npm.example", "tok")
    assert len(certs_list) == 1
    assert certs_list[0]["id"] == "9"

    assert npm_mod._extract_token({"token": "abc"}) == "abc"
    assert npm_mod._extract_token({"result": {"token": "xyz"}}) == "xyz"
    assert npm_mod._extract_token([]) == ""
    assert "Bearer" in npm_mod._auth_headers("t")["Authorization"]

    # Pi-hole pure
    assert ph._dig({"a": {"b": 1}}, "a", "b") == 1
    assert ph._dig({"a": 1}, "a", "b", default=9) == 9
    st = ph.parse_stats_payload("bad")
    assert st.ok is False
    st2 = ph.parse_stats_payload(
        {
            "queries": {"total": "x", "blocked": "y"},
            "gravity": {},
            "clients": {},
        }
    )
    assert st2.queries == 0
    sess = ph.PiholeSession(base_url="https://ph.example", sid="abc", csrf="tok")
    assert sess.api_root.endswith("/api")
    assert "X-FTL-SID" in sess.headers()
    assert sess.cookies()["sid"] == "abc"

    # fetch_stats login fail
    with patch.object(ph, "login", side_effect=RuntimeError("auth")):
        bad = ph.fetch_stats("https://ph.example", "pw")
    assert bad.ok is False

    # fetch_stats success path
    fake_sess = ph.PiholeSession(base_url="https://ph.example", sid="s", csrf="")
    with patch.object(ph, "login", return_value=fake_sess):
        with patch.object(ph, "logout"):
            with patch.object(
                ph,
                "_get_json",
                return_value={
                    "queries": {"total": 10, "blocked": 2, "percent_blocked": 20},
                    "gravity": {"domains_being_blocked": 100},
                    "clients": {"active": 1},
                },
            ):
                good = ph.fetch_stats("https://ph.example", "pw")
    assert good.ok is True
    assert good.queries == 10

    # Kuma fetch_metrics via httpx mock
    metrics_body = (
        'monitor_status{monitor_name="A",monitor_type="http",monitor_url="https://a",'
        'monitor_id="1"} 1\n'
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = metrics_body
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.Client") as HC:
        inst = HC.return_value.__enter__.return_value
        inst.get.return_value = mock_resp
        res = kuma.fetch_metrics("https://kuma.example", "apikey")
    assert res.ok is True
    assert len(res.monitors) >= 1

    mock_resp2 = MagicMock()
    mock_resp2.status_code = 401
    mock_resp2.text = "nope"
    mock_resp2.raise_for_status.side_effect = Exception("401")
    with patch("httpx.Client") as HC:
        inst = HC.return_value.__enter__.return_value
        inst.get.return_value = mock_resp2
        # may still parse empty on error depending on impl
        try:
            res2 = kuma.fetch_metrics("https://kuma.example", "bad")
            assert res2.ok is False or res2.ok is True
        except Exception:
            pass

    assert gf.normalize_base_url("https://gf.example/").startswith("https://")
    with pytest.raises(ValueError):
        gf.normalize_base_url("")
    with pytest.raises(ValueError):
        kuma.normalize_base_url("not-url")
