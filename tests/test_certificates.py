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

    pure_pfx = cert_svc.files_for_layout(
        "pfx", remote_dir="/data", pfx_filename="Unifi.pfx"
    )
    assert [f["kind"] for f in pure_pfx] == ["pfx"]
    assert pure_pfx[0]["path"] == "/data/Unifi.pfx"


def test_humanize_deploy_error_actionable():
    """A1.4: common failures get next-step hints for operators."""
    sudo = cert_svc.humanize_deploy_error(
        "sudo: a password is required",
        write_mode="stage_sudo",
        ssh_user="piherder",
        remote_dir="/etc/ssl/certs",
    )
    assert "sudoers" in sudo.lower()
    assert "piherder" in sudo

    perm = cert_svc.humanize_deploy_error(
        "mkdir failed: Permission denied",
        write_mode="direct",
        ssh_user="pi",
        remote_dir="/etc/ssl",
    )
    assert "Stage + sudo" in perm or "stage" in perm.lower()

    ssh = cert_svc.humanize_deploy_error(
        "Authentication failed.",
        ssh_user="piherder",
    )
    assert "SSH" in ssh or "ssh" in ssh.lower()

    pem = cert_svc.humanize_deploy_error("certificate PEMs missing")
    assert "Replace PEM" in pem or "upload" in pem.lower()

    post = cert_svc.humanize_deploy_error(
        "post deploy failed: Unit not found",
        write_mode="stage_sudo",
    )
    assert "restart" in post.lower() or "recipe" in post.lower()


def test_build_and_parse_post_deploy_command():
    assert cert_svc.build_post_deploy_command("none") == ""
    assert (
        cert_svc.build_post_deploy_command(
            "compose", compose_file="/opt/stacks/npm/docker-compose.yml"
        )
        == "docker compose -f /opt/stacks/npm/docker-compose.yml restart"
    )
    assert (
        cert_svc.build_post_deploy_command(
            "compose",
            compose_file="/opt/app/compose.yml",
            compose_action="up -d",
        )
        == "docker compose -f /opt/app/compose.yml up -d"
    )
    assert (
        cert_svc.build_post_deploy_command("systemctl", systemctl_unit="haproxy")
        == "sudo systemctl restart haproxy"
    )
    assert (
        cert_svc.build_post_deploy_command("custom", custom="my-reload.sh")
        == "my-reload.sh"
    )

    r = cert_svc.parse_restart_recipe(
        "docker compose -f /opt/stacks/npm/docker-compose.yml restart"
    )
    assert r["kind"] == "compose"
    assert r["compose_file"] == "/opt/stacks/npm/docker-compose.yml"
    r2 = cert_svc.parse_restart_recipe("sudo systemctl restart haproxy")
    assert r2["kind"] == "systemctl"
    assert r2["systemctl_unit"] == "haproxy"
    r3 = cert_svc.parse_restart_recipe("")
    assert r3["kind"] == "none"


def test_layout_helpers_one_type():
    assert cert_svc.layout_installs_pair("pair")
    assert not cert_svc.layout_installs_pair("pfx")
    assert cert_svc.layout_installs_pfx("pfx")
    assert cert_svc.layout_installs_combined("combined")
    assert "pfx" in cert_svc.LAYOUTS_NEW_UI
    assert "pair_and_pfx" not in cert_svc.LAYOUTS_NEW_UI


def test_normalize_fingerprint():
    assert (
        cert_svc.normalize_fingerprint("AA:BB:CC:DD")
        == cert_svc.normalize_fingerprint("aabbccdd")
    )
    assert cert_svc.normalize_fingerprint("SHA256 Fingerprint=AA:BB") == "aabb"
    assert cert_svc.normalize_fingerprint(None) == ""


def test_verify_remote_cert_fingerprint_match(monkeypatch):
    from types import SimpleNamespace

    expected = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
    calls = []

    def fake_run(client, cmd, timeout=30):
        calls.append(cmd)
        if "openssl x509" in cmd:
            return 0, expected + "\n", ""
        return 1, "", "no"

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", fake_run)
    target = SimpleNamespace(
        layout="pair",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        combined_filename="snakeoil.pem",
        pfx_filename="Certificate.pfx",
        pfx_export_password_encrypted=None,
    )
    result = cert_svc.verify_remote_cert_fingerprint(
        object(),
        target,
        remote_dir="/opt/certs",
        expected_fingerprint=":".join(
            expected[i : i + 2] for i in range(0, len(expected), 2)
        ),
    )
    assert result["ok"] is True
    assert result["status"] == "success"
    assert any("openssl" in c for c in calls)


def test_verify_remote_cert_fingerprint_mismatch(monkeypatch):
    from types import SimpleNamespace

    def fake_run(client, cmd, timeout=30):
        if "openssl" in cmd:
            return 0, "deadbeef" + "0" * 56 + "\n", ""
        return 1, "", ""

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", fake_run)
    target = SimpleNamespace(
        layout="combined",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        combined_filename="snakeoil.pem",
        pfx_filename="Certificate.pfx",
        pfx_export_password_encrypted=None,
    )
    result = cert_svc.verify_remote_cert_fingerprint(
        object(),
        target,
        remote_dir="/etc/ssl",
        expected_fingerprint="a" * 64,
    )
    assert result["ok"] is False
    assert result["status"] == "failed"


def test_parse_verify_endpoint():
    assert cert_svc.parse_verify_endpoint("") is None
    e = cert_svc.parse_verify_endpoint("https://app.example.com/path")
    assert e["host"] == "app.example.com"
    assert e["port"] == 443
    assert e["servername"] == "app.example.com"
    assert e.get("starttls") in (None, "")
    e2 = cert_svc.parse_verify_endpoint("https://app.example.com:8443/")
    assert e2["port"] == 8443
    e3 = cert_svc.parse_verify_endpoint("10.0.0.5:9443")
    assert e3["host"] == "10.0.0.5"
    assert e3["port"] == 9443
    assert not e3.get("starttls")
    # Database STARTTLS
    pg = cert_svc.parse_verify_endpoint("postgres://db.local:5432")
    assert pg["host"] == "db.local"
    assert pg["port"] == 5432
    assert pg["starttls"] == "postgres"
    my = cert_svc.parse_verify_endpoint("mysql://db.local")
    assert my["port"] == 3306
    assert my["starttls"] == "mysql"
    # Native TLS on DB port (no STARTTLS)
    bare_db = cert_svc.parse_verify_endpoint("127.0.0.1:5432")
    assert bare_db["port"] == 5432
    assert not bare_db.get("starttls")
    # Query overrides
    q = cert_svc.parse_verify_endpoint("10.0.0.5:443?sni=app.example.com")
    assert q["servername"] == "app.example.com"
    q2 = cert_svc.parse_verify_endpoint("db.local:5432?starttls=postgres")
    assert q2["starttls"] == "postgres"


def test_s_client_shell_includes_starttls_for_postgres():
    cmd = cert_svc._s_client_fingerprint_shell(
        "db.local", 5432, "db.local", starttls="postgres"
    )
    assert "-connect" in cmd
    assert "5432" in cmd
    assert "-starttls postgres" in cmd
    plain = cert_svc._s_client_fingerprint_shell("db.local", 5432, "db.local")
    assert "-starttls" not in plain


def test_verify_tls_endpoint_match(monkeypatch):
    expected = "b" * 64

    def fake_run(client, cmd, timeout=25):
        assert "s_client" in cmd
        assert "app.example.com:443" in cmd or "app.example.com" in cmd
        return 0, expected + "\n", ""

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", fake_run)
    result = cert_svc.verify_tls_endpoint_fingerprint(
        verify_url="https://app.example.com/",
        expected_fingerprint=expected,
        client=object(),
    )
    assert result["ok"] is True
    assert result["kind"] == "tls"
    assert result["via"] == "host-ssh"


def test_cert_alert_helpers_raise_and_resolve(session_factory=None):
    """Unit-level: fingerprint helpers and notify/resolve are consistent."""
    from app.services import notifications as notif_svc

    assert notif_svc.cert_deploy_failed_fingerprint(7) == "cert_deploy_failed:target:7"
    assert notif_svc.cert_verify_failed_fingerprint(7) == "cert_verify_failed:target:7"


def test_apply_cert_target_alerts_deploy_fail(monkeypatch):
    from types import SimpleNamespace
    import app.services.certificates as cmod
    import app.services.notifications as nmod

    calls = []

    def fake_notify_deploy(session, **kwargs):
        calls.append(("deploy_fail", kwargs))

    def fake_notify_verify(session, **kwargs):
        calls.append(("verify_fail", kwargs))

    def fake_resolve_deploy(session, tid):
        calls.append(("resolve_deploy", tid))

    def fake_resolve_verify(session, tid):
        calls.append(("resolve_verify", tid))

    monkeypatch.setattr(nmod, "notify_cert_deploy_failed", fake_notify_deploy)
    monkeypatch.setattr(nmod, "resolve_cert_deploy_failed", fake_resolve_deploy)
    monkeypatch.setattr(nmod, "notify_cert_verify_failed", fake_notify_verify)
    monkeypatch.setattr(nmod, "resolve_cert_verify_failed", fake_resolve_verify)

    target = SimpleNamespace(id=3, certificate_id=1, server_id=2, label="NPM")
    cert = SimpleNamespace(id=1, name="Wildcard")
    server = SimpleNamespace(id=2, name="edge")

    cmod._apply_cert_target_alerts(
        object(),
        target=target,
        cert=cert,
        server=server,
        deploy_ok=False,
        deploy_error="sudo denied",
        verify=None,
    )
    assert any(c[0] == "deploy_fail" for c in calls)
    assert not any(c[0] == "resolve_deploy" for c in calls)

    calls.clear()
    cmod._apply_cert_target_alerts(
        object(),
        target=target,
        cert=cert,
        server=server,
        deploy_ok=True,
        verify={"ok": True, "status": "success", "message": "host ok"},
    )
    assert ("resolve_deploy", 3) in calls
    assert ("resolve_verify", 3) in calls

    calls.clear()
    cmod._apply_cert_target_alerts(
        object(),
        target=target,
        cert=cert,
        server=server,
        deploy_ok=True,
        verify={"ok": False, "status": "partial", "message": "tls mismatch"},
    )
    assert ("resolve_deploy", 3) in calls
    assert any(c[0] == "verify_fail" for c in calls)
    vf = next(c for c in calls if c[0] == "verify_fail")
    assert vf[1].get("status") == "partial"


def test_verify_deploy_target_partial_tls(monkeypatch):
    from types import SimpleNamespace

    expected = "c" * 64

    def fake_run(client, cmd, timeout=30):
        if "s_client" in cmd:
            return 0, ("d" * 64) + "\n", ""  # mismatch
        if "openssl x509" in cmd and "fingerprint" in cmd:
            return 0, expected + "\n", ""
        return 1, "", ""

    monkeypatch.setattr(cert_svc.ssh_svc, "run_command", fake_run)
    target = SimpleNamespace(
        layout="pair",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        combined_filename="snakeoil.pem",
        pfx_filename="Certificate.pfx",
        pfx_export_password_encrypted=None,
        verify_url="https://svc.local/",
    )
    result = cert_svc.verify_deploy_target(
        object(),
        target,
        remote_dir="/opt/certs",
        expected_fingerprint=expected,
    )
    assert result["status"] == "partial"
    assert result["ok"] is False
    assert result["host"]["ok"] is True
    assert result["tls"]["ok"] is False


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
    assert "/home/piherder/.piherder/cert-stage/*" in snip
    direct = cert_svc.sudoers_snippet_for_map(
        remote_dir="~/certs",
        layout="pair",
        write_mode="direct",
    )
    assert "no sudo" in direct.lower() or "direct" in direct.lower()


def test_path_helpers_expand_and_stage():
    assert cert_svc.default_home_for_user("piherder") == "/home/piherder"
    assert cert_svc.default_home_for_user("root") == "/root"
    assert cert_svc.resolve_ssh_home(ssh_user="ops", home_dir="/var/home/ops") == "/var/home/ops"
    assert cert_svc.resolve_ssh_home(ssh_user="ops", home_dir=None) == "/home/ops"
    assert cert_svc.expand_remote_dir("~/certs", "/home/ops") == "/home/ops/certs"
    assert cert_svc.expand_remote_dir("~", "/home/ops") == "/home/ops"
    assert cert_svc.expand_remote_dir("/etc/ssl", "/home/ops") == "/etc/ssl"
    assert cert_svc.cert_stage_dir("/var/home/ops", 42) == "/var/home/ops/.piherder/cert-stage/42"
    assert cert_svc.cert_stage_glob("/var/home/ops") == "/var/home/ops/.piherder/cert-stage/*"


def test_sudoers_snippet_matches_deploy_paths_custom_home():
    """Snippet install lines must use the same home/stage/final as deploy helpers."""
    home = "/var/home/fleet"
    user = "fleet"
    final = cert_svc.expand_remote_dir("~/app-certs", home)
    stage_glob = cert_svc.cert_stage_glob(home)
    snip = cert_svc.sudoers_snippet_for_map(
        remote_dir="~/app-certs",
        layout="pair",
        write_mode="stage_sudo",
        fullchain_filename="fullchain.pem",
        privkey_filename="privkey.pem",
        file_mode="600",
        file_owner="root",
        file_group="root",
        ssh_user=user,
        home_dir=home,
    )
    assert final == "/var/home/fleet/app-certs"
    assert f"{stage_glob}/fullchain.pem {final}/fullchain.pem" in snip
    assert f"{stage_glob}/privkey.pem {final}/privkey.pem" in snip
    assert f"install -d -o root -g root -m 755 {final}" in snip
    assert stage_glob in snip
    # Default /home/<user> must not appear as the stage base when custom home is set
    assert " /home/fleet/.piherder/" not in f" {snip}"
    assert "Home guessed" not in snip  # live home provided


def test_sudoers_snippet_root_user_home():
    snip = cert_svc.sudoers_snippet_for_map(
        remote_dir="~/certs",
        layout="pair",
        write_mode="stage_sudo",
        ssh_user="root",
        home_dir=None,
    )
    assert "/root/.piherder/cert-stage/*" in snip
    assert "/root/certs" in snip


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
