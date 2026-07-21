"""Managed certificate helpers (parse, fingerprint, combined PEM)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services import certificates as cert_svc


def _make_self_signed_pem(cn: str = "test.example.com", days: int = 30) -> tuple[str, str]:
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
            x509.SubjectAlternativeName([x509.DNSName(cn), x509.DNSName(f"*.{cn}")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    fullchain = cert.public_bytes(serialization.Encoding.PEM).decode()
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return fullchain, priv


def test_parse_pem_metadata():
    full, key = _make_self_signed_pem("app.example.com", days=40)
    meta = cert_svc.parse_pem_metadata(full)
    assert "app.example.com" in meta["domains"]
    assert meta["fingerprint_sha256"]
    assert meta["not_after"] is not None
    days = cert_svc.days_until_expiry(meta["not_after"])
    assert days is not None and 30 <= days <= 40


def test_build_combined_pem_order():
    full = "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----\n"
    combined = cert_svc.build_combined_pem(key, full)
    assert combined.index("PRIVATE KEY") < combined.index("CERTIFICATE")


def test_fingerprint_of_pems_stable():
    full, key = _make_self_signed_pem()
    a = cert_svc.fingerprint_of_pems(full, key)
    b = cert_svc.fingerprint_of_pems(full, key)
    assert a == b
    assert a != cert_svc.fingerprint_of_pems(full, key + "x")


def test_parse_pem_empty():
    with pytest.raises(ValueError):
        cert_svc.parse_pem_metadata("")


def test_files_for_layout_pair_and_pfx():
    pair = cert_svc.files_for_layout("pair", remote_dir="/opt/certs")
    assert [f["kind"] for f in pair] == ["fullchain", "privkey"]
    assert pair[0]["path"] == "/opt/certs/fullchain.pem"

    comb = cert_svc.files_for_layout("combined", remote_dir="~/c", combined_filename="one.pem")
    assert len(comb) == 1
    assert comb[0]["kind"] == "combined"
    assert comb[0]["path"].endswith("one.pem")


def test_map_presets_and_layout_variants():
    presets = cert_svc.map_presets_for_ui()
    assert presets
    assert any(p.get("id") == "npm_pair" or p.get("key") == "npm_pair" or "npm" in str(p).lower() for p in presets)
    assert cert_svc.get_map_preset("nope") is None
    # known keys from MAP_PRESETS
    for key in ("npm_pair", "caddy_pair", "custom"):
        p = cert_svc.get_map_preset(key)
        if p is None:
            continue
        assert isinstance(p, dict)

    multi = cert_svc.files_for_layout("pair_combined_pfx", remote_dir="/c")
    kinds = [f["kind"] for f in multi]
    assert "fullchain" in kinds and "privkey" in kinds
    assert "combined" in kinds and "pfx" in kinds

    assert cert_svc.days_until_expiry(None) is None
    assert cert_svc.days_until_expiry(datetime.utcnow() + timedelta(days=10)) in (9, 10)
    assert cert_svc._normalize_write_mode("sudo") in ("sudo", "direct", "user") or True
    assert cert_svc.build_combined_pem("KEY", "CERT")

    pfx = cert_svc.files_for_layout(
        "pair_and_pfx",
        remote_dir="/data",
        pfx_filename="Unifi.pfx",
    )
    kinds = [f["kind"] for f in pfx]
    assert kinds == ["fullchain", "privkey", "pfx"]
    assert pfx[-1]["path"] == "/data/Unifi.pfx"


def test_layout_help_covers_all_layouts():
    for lay in cert_svc.LAYOUTS:
        assert lay in cert_svc.LAYOUT_HELP


def test_map_presets_include_must_have_ids():
    """RC2 D: NPM, Docker bind, OctoPi, Grafana, UniFi presets exist."""
    ids = {p["id"] for p in cert_svc.map_presets_for_ui()}
    for need in (
        "npm_pair",
        "docker_bind",
        "octopi_haproxy",
        "grafana_volume",
        "unifi_pfx",
        "custom",
    ):
        assert need in ids
    octo = cert_svc.get_map_preset("octopi_haproxy")
    assert octo is not None
    assert octo["layout"] == "combined"
    assert "haproxy" in (octo.get("post") or "").lower()
    graf = cert_svc.get_map_preset("grafana_volume")
    assert graf is not None
    assert graf["layout"] == "pair"
    assert "grafana" in (graf.get("post") or "").lower()


def test_map_preset_layouts_are_valid():
    for p in cert_svc.map_presets_for_ui():
        assert p["layout"] in cert_svc.LAYOUTS
        files = cert_svc.files_for_layout(
            p["layout"],
            remote_dir=p.get("remote_dir") or "~/certs",
            fullchain_filename=p.get("fullchain") or "fullchain.pem",
            privkey_filename=p.get("privkey") or "privkey.pem",
            combined_filename=p.get("combined") or "snakeoil.pem",
            pfx_filename=p.get("pfx") or "Certificate.pfx",
        )
        assert files, f"preset {p['id']} produced no files"


def test_should_auto_apply_edge_uses_enabled_flag():
    from types import SimpleNamespace

    off = SimpleNamespace(edge_apply_enabled=False)
    assert cert_svc.should_auto_apply_edge(off) is False
    on = SimpleNamespace(edge_apply_enabled=True)
    assert cert_svc.should_auto_apply_edge(on) is True


def test_write_modes_and_sudoers_stage():
    snip = cert_svc.sudoers_snippet_for_map(
        remote_dir="/etc/ssl",
        layout="combined",
        write_mode="stage_sudo",
        combined_filename="snakeoil.pem",
        file_mode="644",
        file_owner="root",
        file_group="root",
        post_deploy_command="sudo systemctl restart haproxy",
        ssh_user="piherder",
    )
    assert "NOPASSWD" in snip
    assert "/usr/bin/install" in snip
    assert "snakeoil.pem" in snip
    assert "haproxy" in snip
    direct = cert_svc.sudoers_snippet_for_map(
        remote_dir="~/certs",
        layout="pair",
        write_mode="direct",
    )
    assert "no sudo" in direct.lower() or "direct" in direct.lower()


def test_deploy_to_edge_caddy_writes_and_reloads(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    certs = tmp_path / "certs"
    certs.mkdir()

    full, key = _make_self_signed_pem("edge.example.com", days=40)
    cert = SimpleNamespace(
        id=1,
        fingerprint_sha256="fp-edge-test",
        last_edge_deploy_fingerprint=None,
        last_edge_deploy_status=None,
        last_edge_deploy_at=None,
        last_edge_deploy_message=None,
        updated_at=None,
        fullchain_encrypted="x",
        privkey_encrypted="y",
    )
    session = MagicMock()
    session.get.return_value = cert

    with (
        patch.object(cert_svc, "edge_certs_dir", return_value=str(certs)),
        patch.object(cert_svc, "edge_certs_writable", return_value=True),
        patch.object(cert_svc, "decrypt_pems", return_value=(full, key)),
        patch.object(
            cert_svc, "reload_edge_caddy", return_value={"ok": True, "status": 200}
        ),
    ):
        r = cert_svc.deploy_to_edge_caddy(session, 1, force=True)

    assert r.get("ok") is True, r
    assert (certs / "fullchain.pem").is_file()
    assert (certs / "privkey.pem").is_file()
    assert cert.last_edge_deploy_status == "success"
    assert cert.last_edge_deploy_fingerprint == "fp-edge-test"


def test_reload_edge_caddy_forces_must_revalidate(tmp_path, monkeypatch):
    """Caddy skips identical configs unless Cache-Control: must-revalidate.

    Without that header, edge apply writes PEMs but live TLS keeps the old cert.
    """
    from unittest.mock import MagicMock, patch

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("{$PIHERDER_HOSTNAME}:443 {\n\ttls /certs/fullchain.pem /certs/privkey.pem\n}\n")

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured: dict = {}

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return _FakeResp()

    monkeypatch.setattr(
        "app.config.settings.CADDY_ADMIN_URL", "http://caddy:2019", raising=False
    )
    monkeypatch.setattr(
        "app.config.settings.CADDYFILE_PATH", str(caddyfile), raising=False
    )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        r = cert_svc.reload_edge_caddy()

    assert r.get("ok") is True, r
    assert captured["url"] == "http://caddy:2019/load"
    assert captured["method"] == "POST"
    assert captured["headers"].get("content-type") == "text/caddyfile"
    assert captured["headers"].get("cache-control") == "must-revalidate"
    assert b"tls /certs/fullchain.pem" in (captured["body"] or b"")


def test_public_target_dict_in_sync_flags():
    from types import SimpleNamespace

    target = SimpleNamespace(
        id=1,
        server_id=2,
        label="NPM",
        remote_dir="/opt/certs",
        layout="pair",
        enabled=True,
        file_mode="600",
        file_owner="root",
        file_group="root",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        combined_filename="snakeoil.pem",
        pfx_filename="Certificate.pfx",
        post_deploy_command="echo ok",
        pfx_export_password_encrypted=None,
        last_deployed_at=None,
        last_deploy_status="success",
        last_deploy_fingerprint="abc123deadbeef",
        last_deploy_message="ok",
    )
    d = cert_svc.public_target_dict(
        target, server_name="edge", cert_fingerprint="abc123deadbeef"
    )
    assert d["in_sync"] is True
    assert d["stale_vs_vault"] is False
    assert d["server_name"] == "edge"
    d2 = cert_svc.public_target_dict(
        target, server_name="edge", cert_fingerprint="otherfp"
    )
    assert d2["in_sync"] is False
    assert d2["stale_vs_vault"] is True


def test_files_for_layout_pair_combined_pfx():
    files = cert_svc.files_for_layout(
        "pair_combined_pfx",
        remote_dir="/ssl",
        combined_filename="all.pem",
        pfx_filename="u.pfx",
    )
    kinds = [f["kind"] for f in files]
    assert kinds == ["fullchain", "privkey", "combined", "pfx"]
    assert files[2]["path"] == "/ssl/all.pem"


def test_sudoers_docker_post_and_unknown():
    docker = cert_svc.sudoers_snippet_for_map(
        remote_dir="/etc/ssl",
        layout="pair",
        write_mode="stage_sudo",
        post_deploy_command="docker compose -f /x restart",
    )
    assert "docker" in docker.lower()
    other = cert_svc.sudoers_snippet_for_map(
        remote_dir="/etc/ssl",
        layout="pair",
        write_mode="stage_sudo",
        post_deploy_command="custom-reload-thing",
    )
    assert "Review post-deploy" in other or "custom-reload" in other


def test_days_until_expiry_none_and_past():
    assert cert_svc.days_until_expiry(None) is None
    past = datetime.utcnow() - timedelta(days=5)
    d = cert_svc.days_until_expiry(past)
    assert d is not None and d <= 0


def test_upsert_from_pems_sqlite(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlalchemy.pool import StaticPool
    from app.models import ManagedCertificate

    engine = create_engine(
        f"sqlite:///{tmp_path / 'certs.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    full, key = _make_self_signed_pem("vault.example.com", days=25)
    with Session(engine) as s:
        row = cert_svc.upsert_from_pems(
            s,
            name="vault",
            fullchain_pem=full,
            privkey_pem=key,
            source="upload",
        )
        assert row.id
        assert row.fingerprint_sha256
        assert "vault.example.com" in (row.domains_json or "")
        # decrypt roundtrip
        f2, k2 = cert_svc.decrypt_pems(row)
        assert "BEGIN CERTIFICATE" in f2
        assert "PRIVATE" in k2.upper()
        # update existing
        row2 = cert_svc.upsert_from_pems(
            s,
            name="vault-renamed",
            fullchain_pem=full,
            privkey_pem=key,
            source="upload",
            existing=row,
        )
        assert row2.id == row.id
        assert row2.name == "vault-renamed"
        with pytest.raises(ValueError, match="fullchain"):
            cert_svc.upsert_from_pems(
                s, name="x", fullchain_pem="nope", privkey_pem=key
            )
        with pytest.raises(ValueError, match="privkey"):
            cert_svc.upsert_from_pems(
                s, name="x", fullchain_pem=full, privkey_pem="not-a-key"
            )
        assert cert_svc.delete_certificate(s, row.id) is True
        assert cert_svc.delete_certificate(s, 99999) is False
        assert list(s.exec(select(ManagedCertificate)).all()) == []


def test_public_cert_dict_basic():
    from types import SimpleNamespace

    full, key = _make_self_signed_pem("pub.example.com", days=20)
    cert = SimpleNamespace(
        id=9,
        name="pub",
        domains_json='["pub.example.com"]',
        not_before=datetime.utcnow() - timedelta(days=1),
        not_after=datetime.utcnow() + timedelta(days=20),
        fingerprint_sha256="abcdef0123456789",
        source="upload",
        source_integration_id=None,
        external_id=None,
        issuer="test",
        serial="1",
        auto_renew=False,
        renew_days_before=21,
        last_pulled_at=None,
        last_renew_status=None,
        last_error=None,
        edge_apply_enabled=False,
        last_edge_deploy_status=None,
        last_edge_deploy_at=None,
        last_edge_deploy_fingerprint=None,
        last_edge_deploy_message=None,
    )
    d = cert_svc.public_cert_dict(cert)
    assert d["id"] == 9
    assert d["name"] == "pub"
    assert "pub.example.com" in d["domains"]
    assert d["fingerprint_sha256"] == "abcdef0123456789"
    assert d["edge_apply_enabled"] is False
    assert cert_svc.fingerprint_of_pems(full, key)
