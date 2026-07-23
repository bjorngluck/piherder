"""Deep unit coverage for certificates.py — SSH deploy, edge Caddy, NPM renew.

No live SSH / NPM / Caddy: MagicMock + tmp_path filesystem.
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

from app.models import Integration, ManagedCertificate, Server
from app.services import certificates as certs
from app.services.integrations import registry as reg


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _pem(cn: str = "app.example.com", days: int = 90) -> tuple[str, str]:
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


def _ssh_mocks(home: str = "/home/pi"):
    client = MagicMock()
    sftp = MagicMock()
    client.open_sftp.return_value = sftp
    fobj = MagicMock()
    sftp.file.return_value.__enter__ = lambda s: fobj
    sftp.file.return_value.__exit__ = lambda *a: None

    def run_command(client, cmd, timeout=30):
        c = str(cmd)
        if "printf" in c or "$HOME" in c:
            return 0, home, ""
        if "mkdir" in c and "fail-mkdir" in c:
            return 1, "", "permission denied"
        if "sudo install -d" in c and "fail-d" in home:
            return 1, "", "sudo fail"
        return 0, "ok", ""

    return client, sftp, run_command


# ---------------------------------------------------------------------------
# Edge Caddy
# ---------------------------------------------------------------------------


def test_reload_edge_caddy_paths(tmp_path, monkeypatch):
    from app.config import settings

    # missing caddyfile
    monkeypatch.setattr(settings, "CADDYFILE_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(settings, "CADDY_ADMIN_URL", "http://caddy:2019")
    bad = certs.reload_edge_caddy()
    assert bad["ok"] is False
    assert "Cannot read" in bad["error"]

    # empty
    cf = tmp_path / "Caddyfile"
    cf.write_text("   \n")
    monkeypatch.setattr(settings, "CADDYFILE_PATH", str(cf))
    empty = certs.reload_edge_caddy()
    assert empty["ok"] is False

    # success via urlopen mock
    cf.write_text("localhost:80 {\n  respond ok\n}\n")
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok = certs.reload_edge_caddy()
    assert ok["ok"] is True

    # HTTPError
    import urllib.error

    err = urllib.error.HTTPError(
        "http://caddy:2019/load", 400, "bad", hdrs=None, fp=None
    )
    err.read = lambda: b"invalid config"
    with patch("urllib.request.urlopen", side_effect=err):
        http_bad = certs.reload_edge_caddy()
    assert http_bad["ok"] is False

    # generic network error
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        net = certs.reload_edge_caddy()
    assert net["ok"] is False
    assert "Cannot reach" in net["error"]


def test_deploy_to_edge_caddy_write_and_skip(tmp_path, monkeypatch):
    session = _session()
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="edge", fullchain_pem=full, privkey_pem=key
    )
    monkeypatch.setattr(certs, "edge_certs_dir", lambda: str(tmp_path))
    # writable
    with patch.object(certs, "edge_certs_writable", return_value=True):
        with patch.object(certs, "reload_edge_caddy", return_value={"ok": True}):
            out = certs.deploy_to_edge_caddy(session, cert.id, force=True)
    assert out["ok"] is True
    assert (tmp_path / "fullchain.pem").is_file()
    assert (tmp_path / "privkey.pem").is_file()
    session.refresh(cert)
    assert cert.edge_apply_enabled is True
    assert cert.last_edge_deploy_status == "success"

    # skip when same fingerprint
    with patch.object(certs, "edge_certs_writable", return_value=True):
        skip = certs.deploy_to_edge_caddy(session, cert.id, force=False)
    assert skip.get("skipped") is True

    # not found
    assert certs.deploy_to_edge_caddy(session, 99999)["ok"] is False

    # not writable
    with patch.object(certs, "edge_certs_writable", return_value=False):
        nw = certs.deploy_to_edge_caddy(session, cert.id, force=True)
    assert nw["ok"] is False

    # reload fails after write
    with patch.object(certs, "edge_certs_writable", return_value=True):
        with patch.object(
            certs, "reload_edge_caddy", return_value={"ok": False, "error": "boom"}
        ):
            fail = certs.deploy_to_edge_caddy(session, cert.id, force=True)
    assert fail["ok"] is False
    assert fail.get("wrote") is True
    session.refresh(cert)
    assert cert.last_edge_deploy_status == "failed"

    # write OSError
    with patch.object(certs, "edge_certs_writable", return_value=True):
        with patch("builtins.open", side_effect=OSError("disk full")):
            wfail = certs.deploy_to_edge_caddy(session, cert.id, force=True)
    assert wfail["ok"] is False


# ---------------------------------------------------------------------------
# Fleet deploy SSH — direct + stage_sudo
# ---------------------------------------------------------------------------


def test_deploy_target_direct_success():
    session = _session()
    server = _server(session)
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="lab", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair",
        write_mode="direct",
        remote_dir="~/certs",
        post_deploy_command="true",
        file_owner="pi",
        file_group="pi",
        file_mode="600",
    )
    client, sftp, run_command = _ssh_mocks()
    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True, progress=lambda m: None)
    assert res["ok"] is True
    assert res["write_mode"] == "direct"
    session.refresh(t)
    assert t.last_deploy_status == "success"
    assert t.last_deploy_fingerprint == cert.fingerprint_sha256
    client.close.assert_called()


def test_deploy_target_stage_sudo_and_pfx():
    session = _session()
    server = _server(session)
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="lab2", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair_and_pfx",
        write_mode="stage_sudo",
        remote_dir="/etc/ssl/lab",
        pfx_export_password="s3cret",
        file_owner="root",
        file_group="root",
    )
    client, sftp, run_command = _ssh_mocks()
    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True)
    assert res["ok"] is True
    session.refresh(t)
    assert t.last_deploy_status == "success"


def test_deploy_target_direct_mkdir_fail_and_missing_server():
    session = _session()
    server = _server(session, name="pi-fail")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="lab3", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair",
        write_mode="direct",
        remote_dir="~/fail-mkdir",
    )
    client = MagicMock()
    sftp = MagicMock()
    client.open_sftp.return_value = sftp

    def run_command(client, cmd, timeout=30):
        if "printf" in str(cmd) or "$HOME" in str(cmd):
            return 0, "/home/pi", ""
        if "mkdir" in str(cmd):
            return 1, "", "permission denied"
        return 0, "", ""

    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True)
    assert res["ok"] is False
    assert "mkdir" in (res.get("error") or "").lower() or "permission" in (
        res.get("error") or ""
    ).lower()
    session.refresh(t)
    assert t.last_deploy_status == "failed"

    # server gone
    t2 = certs.create_target(
        session, certificate_id=cert.id, server_id=server.id, layout="pair"
    )
    t2.server_id = 99999
    session.add(t2)
    session.commit()
    assert certs.deploy_target(session, t2.id, force=True)["error"] == "server not found"

    # cert gone
    t3 = certs.create_target(
        session, certificate_id=cert.id, server_id=server.id, layout="pair"
    )
    t3.certificate_id = 99999
    session.add(t3)
    session.commit()
    assert (
        certs.deploy_target(session, t3.id, force=True)["error"] == "certificate not found"
    )


def test_deploy_target_stage_sudo_install_d_fail():
    session = _session()
    server = _server(session, name="pi-sudo")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="lab4", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair",
        write_mode="stage_sudo",
        remote_dir="/etc/ssl/x",
    )
    client = MagicMock()
    sftp = MagicMock()
    client.open_sftp.return_value = sftp
    fobj = MagicMock()
    sftp.file.return_value.__enter__ = lambda s: fobj
    sftp.file.return_value.__exit__ = lambda *a: None

    def run_command(client, cmd, timeout=30):
        c = str(cmd)
        if "printf" in c or "$HOME" in c:
            return 0, "/home/pi", ""
        if "mkdir -p" in c and "cert-stage" in c:
            return 0, "", ""
        if "sudo install -d" in c:
            return 1, "", "need sudoers"
        return 0, "", ""

    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True)
    assert res["ok"] is False
    assert "sudo" in (res.get("error") or "").lower()


def test_deploy_target_post_deploy_fail_and_invalid_mode():
    session = _session()
    server = _server(session, name="pi-post")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="lab5", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair",
        write_mode="direct",
        remote_dir="~/certs",
        post_deploy_command="false",
        file_mode="not-octal",
    )
    client = MagicMock()
    sftp = MagicMock()
    client.open_sftp.return_value = sftp
    fobj = MagicMock()
    sftp.file.return_value.__enter__ = lambda s: fobj
    sftp.file.return_value.__exit__ = lambda *a: None

    def run_command(client, cmd, timeout=30):
        c = str(cmd)
        if "printf" in c or "$HOME" in c:
            return 0, "/home/pi", ""
        if c.strip() == "false" or c == "false":
            return 1, "", "post failed"
        return 0, "", ""

    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True)
    assert res["ok"] is False
    assert "post" in (res.get("error") or "").lower()


def test_deploy_all_and_redistribute_with_edge():
    session = _session()
    server = _server(session, name="pi-all")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="lab6", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session, certificate_id=cert.id, server_id=server.id, layout="pair"
    )
    # disabled target skipped
    t2 = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair",
        enabled=False,
        label="off",
    )

    client, sftp, run_command = _ssh_mocks()
    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            out = certs.deploy_all_targets(session, cert.id, force=True)
    assert out["count"] == 1  # only enabled
    assert out["ok"] is True

    cert.edge_apply_enabled = True
    session.add(cert)
    session.commit()
    with patch.object(certs, "deploy_all_targets", return_value={"ok": True, "count": 1}):
        with patch.object(certs, "edge_certs_writable", return_value=True):
            with patch.object(
                certs,
                "deploy_to_edge_caddy",
                return_value={"ok": True, "fingerprint": "x"},
            ) as edge:
                r = certs.redistribute_after_renew(
                    session, cert.id, force=True, progress=lambda m: None
                )
    assert edge.called
    assert r["ok"] is True
    assert r["edge"]["ok"] is True

    with patch.object(certs, "deploy_all_targets", return_value={"ok": True, "count": 0}):
        with patch.object(certs, "edge_certs_writable", return_value=False):
            r2 = certs.redistribute_after_renew(session, cert.id, force=True)
    assert r2["edge"] is not None
    assert r2["edge"].get("skipped") is True


# ---------------------------------------------------------------------------
# NPM pull + renew + expiring scheduler
# ---------------------------------------------------------------------------


def test_pull_from_npm_and_upsert_existing():
    session = _session()
    full, key = _pem(cn="npm.example.com")
    npm = reg.create_npm(
        session,
        name="NPM",
        base_url="https://npm.example",
        identity="a@b.c",
        password="secret",
    )
    zip_parts = {"fullchain": full, "privkey": key}
    with patch.object(reg, "decrypt_credentials", return_value={"username": "a@b.c", "password": "secret"}):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs.npm_mod, "download_certificate_zip", return_value=b"zip"):
                with patch.object(
                    certs.npm_mod, "parse_certificate_zip", return_value=zip_parts
                ):
                    row = certs.pull_from_npm(
                        session, npm, "50", name="NPM Cert", auto_renew=True
                    )
    assert row.source == "npm"
    assert row.external_id == "50"
    assert row.auto_renew is True
    assert row.source_integration_id == npm.id

    # second pull updates existing
    full2, key2 = _pem(cn="npm.example.com", days=120)
    with patch.object(reg, "decrypt_credentials", return_value={"username": "a@b.c", "password": "secret"}):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs.npm_mod, "download_certificate_zip", return_value=b"zip2"):
                with patch.object(
                    certs.npm_mod,
                    "parse_certificate_zip",
                    return_value={"fullchain": full2, "privkey": key2},
                ):
                    row2 = certs.pull_from_npm(session, npm, "50", name="NPM Cert 2")
    assert row2.id == row.id
    assert row2.name == "NPM Cert 2"

    # wrong type
    kuma = reg.create_kuma(
        session, name="K", base_url="https://kuma.example", api_key="k"
    )
    with pytest.raises(ValueError, match="NPM"):
        certs.pull_from_npm(session, kuma, "1")


def test_renew_npm_certificate_paths(monkeypatch):
    session = _session()
    full, key = _pem(days=10)
    full_new, key_new = _pem(days=90)
    npm = reg.create_npm(
        session,
        name="NPM",
        base_url="https://npm.example",
        identity="a@b.c",
        password="secret",
    )
    cert = certs.upsert_from_pems(
        session,
        name="npm-cert",
        fullchain_pem=full,
        privkey_pem=key,
        source="npm",
        source_integration_id=npm.id,
        external_id="9",
        auto_renew=True,
    )
    old_fp = cert.fingerprint_sha256

    # not npm source
    upload = certs.upsert_from_pems(
        session, name="up", fullchain_pem=full, privkey_pem=key, source="upload"
    )
    assert certs.renew_npm_certificate(session, upload)["ok"] is False

    # already new on pre-pull
    def pull_new(*a, **k):
        return certs.upsert_from_pems(
            session,
            name=cert.name,
            fullchain_pem=full_new,
            privkey_pem=key_new,
            source="npm",
            source_integration_id=npm.id,
            external_id="9",
            auto_renew=True,
            existing=cert,
        )

    with patch.object(reg, "decrypt_credentials", return_value={"username": "u", "password": "p"}):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs, "pull_from_npm", side_effect=pull_new):
                with patch.object(
                    certs,
                    "redistribute_after_renew",
                    return_value={"ok": True, "count": 0},
                ):
                    r = certs.renew_npm_certificate(
                        session, cert, poll_interval_sec=5, poll_attempts=1
                    )
    assert r["ok"] is True
    assert r.get("via") == "pull"

    # renew request + poll success — re-fetch cert with old fp then new
    cert2 = certs.upsert_from_pems(
        session,
        name="npm-cert2",
        fullchain_pem=full,
        privkey_pem=key,
        source="npm",
        source_integration_id=npm.id,
        external_id="10",
        auto_renew=True,
    )
    pulls = {"n": 0}

    def pull_then_new(session, integration, cert_id, **kw):
        pulls["n"] += 1
        if pulls["n"] == 1:
            # pre-pull same material
            return cert2
        return certs.upsert_from_pems(
            session,
            name=cert2.name,
            fullchain_pem=full_new,
            privkey_pem=key_new,
            source="npm",
            source_integration_id=npm.id,
            external_id="10",
            auto_renew=True,
            existing=cert2,
        )

    with patch.object(reg, "decrypt_credentials", return_value={"username": "u", "password": "p"}):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs.npm_mod, "renew_certificate", return_value=None):
                with patch.object(certs, "pull_from_npm", side_effect=pull_then_new):
                    with patch.object(certs.time, "sleep"):  # don't wait
                        with patch.object(
                            certs,
                            "redistribute_after_renew",
                            return_value={"ok": True, "count": 0},
                        ):
                            r2 = certs.renew_npm_certificate(
                                session,
                                cert2,
                                poll_interval_sec=5,
                                poll_attempts=2,
                                progress=lambda m: None,
                            )
    assert r2["ok"] is True
    assert r2.get("via") == "renew"

    # renew API error
    cert3 = certs.upsert_from_pems(
        session,
        name="npm-cert3",
        fullchain_pem=full,
        privkey_pem=key,
        source="npm",
        source_integration_id=npm.id,
        external_id="11",
        auto_renew=True,
    )
    with patch.object(reg, "decrypt_credentials", return_value={"username": "u", "password": "p"}):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs, "pull_from_npm", side_effect=Exception("pre")):
                with patch.object(
                    certs.npm_mod, "renew_certificate", side_effect=RuntimeError("npm down")
                ):
                    r3 = certs.renew_npm_certificate(
                        session, cert3, poll_interval_sec=5, poll_attempts=1
                    )
    assert r3["ok"] is False
    assert "npm down" in (r3.get("error") or "")

    # poll timeout
    cert4 = certs.upsert_from_pems(
        session,
        name="npm-cert4",
        fullchain_pem=full,
        privkey_pem=key,
        source="npm",
        source_integration_id=npm.id,
        external_id="12",
        auto_renew=True,
    )
    with patch.object(reg, "decrypt_credentials", return_value={"username": "u", "password": "p"}):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs, "pull_from_npm", return_value=cert4):
                with patch.object(certs.npm_mod, "renew_certificate", return_value=None):
                    with patch.object(certs.time, "sleep"):
                        r4 = certs.renew_npm_certificate(
                            session, cert4, poll_interval_sec=5, poll_attempts=2
                        )
    assert r4["ok"] is False
    assert "poll" in (r4.get("error") or "").lower() or "exhausted" in (
        r4.get("error") or ""
    ).lower()


def test_check_expiring_and_renew():
    session = _session()
    full, key = _pem(days=5)
    full_far, key_far = _pem(days=100)
    npm = reg.create_npm(
        session,
        name="NPM",
        base_url="https://npm.example",
        identity="a@b.c",
        password="secret",
    )
    expiring = certs.upsert_from_pems(
        session,
        name="soon",
        fullchain_pem=full,
        privkey_pem=key,
        source="npm",
        source_integration_id=npm.id,
        external_id="1",
        auto_renew=True,
        renew_days_before=21,
    )
    far = certs.upsert_from_pems(
        session,
        name="later",
        fullchain_pem=full_far,
        privkey_pem=key_far,
        source="npm",
        source_integration_id=npm.id,
        external_id="2",
        auto_renew=True,
        renew_days_before=21,
    )
    upload = certs.upsert_from_pems(
        session, name="upload", fullchain_pem=full, privkey_pem=key, source="upload"
    )
    upload.auto_renew = True
    session.add(upload)
    session.commit()

    with patch.object(
        certs,
        "renew_npm_certificate",
        return_value={"ok": True, "renewed": True},
    ) as ren:
        with patch("app.services.notifications.upsert_notification"):
            with patch("app.services.notifications.resolve_by_fingerprint"):
                results = certs.check_expiring_and_renew(
                    session, poll_interval_sec=5, poll_attempts=1
                )
    # only expiring npm cert
    assert ren.called
    ids = {r.get("cert_id") for r in results}
    assert expiring.id in ids
    assert far.id not in ids

    # failed renew notifies
    with patch.object(
        certs,
        "renew_npm_certificate",
        return_value={"ok": False, "error": "fail"},
    ):
        with patch("app.services.notifications.upsert_notification") as un:
            with patch("app.services.notifications.resolve_by_fingerprint"):
                certs.check_expiring_and_renew(
                    session, poll_interval_sec=5, poll_attempts=1
                )
    assert un.called


def test_update_target_all_fields_and_upsert_validation():
    session = _session()
    server = _server(session)
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="u", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session, certificate_id=cert.id, server_id=server.id, layout="pair"
    )
    t2 = certs.update_target(
        session,
        t.id,
        label="L",
        remote_dir="/opt/c",
        layout="combined",
        write_mode="stage_sudo",
        fullchain_filename="fc.pem",
        privkey_filename="pk.pem",
        combined_filename="all.pem",
        pfx_filename="c.pfx",
        file_mode="640",
        file_owner="www",
        file_group="ssl",
        pfx_export_password="pw",
        post_deploy_command="echo hi",
        enabled=False,
    )
    assert t2.layout == "combined"
    assert t2.file_owner == "www"
    assert t2.pfx_export_password_encrypted
    assert t2.enabled is False

    with pytest.raises(ValueError, match="fullchain"):
        certs.upsert_from_pems(session, name="x", fullchain_pem="nope", privkey_pem=key)
    with pytest.raises(ValueError, match="privkey"):
        certs.upsert_from_pems(
            session, name="x", fullchain_pem=full, privkey_pem="not-a-key"
        )


def test_edge_certs_writable_exception(monkeypatch):
    monkeypatch.setattr(certs, "edge_certs_dir", lambda: "/no/such/dir/ever")
    assert certs.edge_certs_writable() is False
    st = certs.edge_caddy_status()
    assert st["certs_writable"] is False


def test_deploy_target_direct_pfx_and_home_tilde():
    session = _session()
    server = _server(session, name="pi-pfx")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="pfx-lab", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair_and_pfx",
        write_mode="direct",
        remote_dir="~",
        pfx_export_password="export-me",
        file_owner="pi",
        file_group="pi",
    )
    client, sftp, run_command = _ssh_mocks()
    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True)
    assert res["ok"] is True
    assert res["remote_dir"] == "/home/pi"
    session.refresh(t)
    assert t.last_deploy_status == "success"


def test_deploy_target_stage_mkdir_fail_and_skip_fingerprint():
    session = _session()
    server = _server(session, name="pi-stage-mk")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="stage-mk", fullchain_pem=full, privkey_pem=key
    )
    t = certs.create_target(
        session,
        certificate_id=cert.id,
        server_id=server.id,
        layout="pair",
        write_mode="stage_sudo",
        remote_dir="/etc/ssl/x",
    )
    client = MagicMock()
    sftp = MagicMock()
    client.open_sftp.return_value = sftp

    def run_command(client, cmd, timeout=30):
        c = str(cmd)
        if "printf" in c or "$HOME" in c:
            return 0, "/home/pi", ""
        if "mkdir -p" in c and "cert-stage" in c:
            return 1, "", "disk full"
        return 0, "", ""

    with patch("app.services.certificates.ssh_svc.get_ssh_client", return_value=client):
        with patch("app.services.certificates.ssh_svc.run_command", side_effect=run_command):
            res = certs.deploy_target(session, t.id, force=True)
    assert res["ok"] is False
    assert "stage mkdir" in (res.get("error") or "").lower()

    # skip when fingerprint already deployed
    t.last_deploy_fingerprint = cert.fingerprint_sha256
    t.last_deploy_status = "success"
    session.add(t)
    session.commit()
    skip = certs.deploy_target(session, t.id, force=False)
    assert skip.get("skipped") is True


def test_pull_from_npm_inventory_name_and_renew_missing_integration():
    session = _session()
    full, key = _pem(cn="inv.example.com")
    npm = reg.create_npm(
        session,
        name="NPM-inv",
        base_url="https://npm.example",
        identity="a@b.c",
        password="secret",
    )
    # seed inventory cache so display name resolves without explicit name=
    npm.last_status_json = json.dumps(
        {
            "certificates": [
                {
                    "id": 77,
                    "nice_name": "From Inventory",
                    "domain_names": ["inv.example.com"],
                }
            ]
        }
    )
    session.add(npm)
    session.commit()

    with patch.object(
        reg, "decrypt_credentials", return_value={"username": "a@b.c", "password": "secret"}
    ):
        with patch.object(certs.npm_mod, "get_token", return_value="tok"):
            with patch.object(certs.npm_mod, "download_certificate_zip", return_value=b"z"):
                with patch.object(
                    certs.npm_mod,
                    "parse_certificate_zip",
                    return_value={"fullchain": full, "privkey": key},
                ):
                    row = certs.pull_from_npm(session, npm, "77")
    assert row.name == "From Inventory"

    # renew when integration row deleted
    orphan = certs.upsert_from_pems(
        session,
        name="orphan-npm",
        fullchain_pem=full,
        privkey_pem=key,
        source="npm",
        source_integration_id=99999,
        external_id="1",
        auto_renew=True,
    )
    orphan_res = certs.renew_npm_certificate(session, orphan)
    assert orphan_res["ok"] is False
    assert "integration" in (orphan_res.get("error") or "").lower()


def test_delete_certificate_cascades_targets_and_reload_non2xx():
    session = _session()
    server = _server(session, name="pi-del")
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="del-me", fullchain_pem=full, privkey_pem=key
    )
    certs.create_target(
        session, certificate_id=cert.id, server_id=server.id, layout="pair"
    )
    cid = cert.id
    assert certs.delete_certificate(session, cid) is True
    assert session.get(type(cert), cid) is None
    assert certs.delete_certificate(session, 99999) is False

    # reload: non-2xx status without HTTPError
    from app.config import settings
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        cf = Path(td) / "Caddyfile"
        cf.write_text("localhost:80 {\n  respond ok\n}\n")
        with patch.object(settings, "CADDYFILE_PATH", str(cf)):
            with patch.object(settings, "CADDY_ADMIN_URL", "http://caddy:2019"):
                mock_resp = MagicMock()
                mock_resp.status = 503
                mock_resp.__enter__ = lambda s: mock_resp
                mock_resp.__exit__ = lambda *a: None
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    bad = certs.reload_edge_caddy()
    assert bad["ok"] is False
    assert "503" in (bad.get("error") or "")


def test_deploy_edge_skip_enables_mapping_and_missing_pems(tmp_path, monkeypatch):
    session = _session()
    full, key = _pem()
    cert = certs.upsert_from_pems(
        session, name="edge-skip", fullchain_pem=full, privkey_pem=key
    )
    # fingerprint match but mapping not enabled → skip path enables it
    cert.last_edge_deploy_fingerprint = cert.fingerprint_sha256
    cert.last_edge_deploy_status = "success"
    cert.edge_apply_enabled = False
    session.add(cert)
    session.commit()
    monkeypatch.setattr(certs, "edge_certs_dir", lambda: str(tmp_path))
    out = certs.deploy_to_edge_caddy(session, cert.id, force=False)
    assert out.get("skipped") is True
    session.refresh(cert)
    assert cert.edge_apply_enabled is True

    # PEMs missing on deploy path
    with patch.object(certs, "edge_certs_writable", return_value=True):
        with patch.object(certs, "decrypt_pems", return_value=("", "")):
            miss = certs.deploy_to_edge_caddy(session, cert.id, force=True)
    assert miss["ok"] is False
    assert "PEM" in (miss.get("error") or "")
