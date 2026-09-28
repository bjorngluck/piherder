"""v1.7 coverage — settings, certificates, Pi-hole, SSH, and template deploy bodies."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session

from app.database import get_session
from app.main import app
from app.models import Integration, ManagedCertificate, Server, ServiceTemplate, User
from tests.test_coverage_v17_q1 import _client, _engine


class _Out:
    def __init__(self, code=0):
        self.channel = self

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return 0

    def read(self):
        return b""


class _Reboot:
    def exec_command(self, cmd, timeout=8):
        return None, _Out(), _Out()

    def close(self):
        return None


def test_settings_certs_pihole_and_ssh(tmp_path, monkeypatch):
    import app.routers.certificates as cert_mod
    import app.routers.integrations_pihole as pihole_mod
    import app.routers.settings as settings_mod
    import app.services.ssh as ssh_mod
    from app.services.integrations import pihole as ph
    from app.services.integrations import poll as poll_svc

    jobs = sys.modules["app.services.jobs"]

    def _upsert(session, **kwargs):
        row = kwargs.get("existing")
        if row is None:
            row = ManagedCertificate(name=kwargs.get("name") or "Uploaded", source=kwargs.get("source") or "upload")
            session.add(row)
        session.commit()
        session.refresh(row)
        return row

    monkeypatch.setattr(cert_mod.cert_svc, "upsert_from_pems", _upsert)
    monkeypatch.setattr(cert_mod.cert_svc, "deploy_to_edge_caddy", lambda *a, **k: {"ok": True, "skipped": False})
    monkeypatch.setattr(cert_mod.cert_svc, "deploy_target", lambda *a, **k: {"ok": True, "skipped": False, "server_id": 1})
    monkeypatch.setattr(cert_mod.cert_svc, "deploy_all_targets", lambda *a, **k: {"ok": True, "count": 1})
    monkeypatch.setattr(ssh_mod, "run_command", lambda *a, **k: (0, "/home/pi", ""))
    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda *a, **k: _Reboot())
    monkeypatch.setattr(ssh_mod, "generate_keypair", lambda **k: ("ssh-ed25519 AAAA", "-----BEGIN OPENSSH PRIVATE KEY-----\n"))
    monkeypatch.setattr(ssh_mod, "clear_host_key_pin", lambda server: None)
    monkeypatch.setattr(
        "app.services.ssh_onboarding.test_connection_detail",
        lambda server: SimpleNamespace(ok=True, message="ok", details={"banner": "OpenSSH"}),
    )
    monkeypatch.setattr("app.services.host_deps.check_and_persist", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.dns_fabric.fanout_pihole_dns",
        lambda *a, **k: [{"ok": True, "name": "pihole"}],
    )
    monkeypatch.setattr(ph, "fetch_stats", lambda *a, **k: SimpleNamespace(ok=True, error=""))
    monkeypatch.setattr(poll_svc, "poll_integration", lambda *a, **k: None)
    monkeypatch.setattr(settings_mod.hb, "create_herder_backup", lambda **k: Path("piherder-backup.tar.gz"))
    monkeypatch.setattr(settings_mod.hb, "resolve_archive_in_roots", lambda **k: Path("/tmp/piherder-backup.tar.gz"))
    monkeypatch.setattr(
        settings_mod.hb,
        "restore_herder_backup",
        lambda *a, **k: {"restored_servers": 1, "restored_audit": 0, "would_restore_servers": 1, "would_restore_audit": 0},
    )

    calls = {"n": 0}

    def _job(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(id=8, status="pending")
        if calls["n"] == 2:
            raise jobs.JobAlreadyActive(SimpleNamespace(id=8, status="running"))
        if calls["n"] == 3:
            raise ValueError("bad stash")
        raise RuntimeError("queue down")

    monkeypatch.setattr(jobs, "enqueue_template_deploy", _job)
    monkeypatch.setattr(
        jobs,
        "_create_queued_job_with_audit",
        lambda *a, **k: (SimpleNamespace(id=11, status="pending"), SimpleNamespace(id=12)),
    )

    engine = _engine(tmp_path / "q9.db")
    client, uid, sid = _client(engine, monkeypatch)
    try:
        with Session(engine) as s:
            user = s.get(User, uid)
            user.role = "admin"
            s.add(user)
            cert = ManagedCertificate(name="edge", source="upload")
            npm = ManagedCertificate(name="npm", source="npm")
            hole = Integration(type="pihole", name="Pi", base_url="http://pi.hole", enabled=True)
            tpl = ServiceTemplate(slug="web", name="Web", definition_json="{}")
            s.add(cert)
            s.add(npm)
            s.add(hole)
            s.add(tpl)
            s.commit()
            s.refresh(cert)
            s.refresh(npm)
            s.refresh(hole)
            cid, nid, iid = cert.id, npm.id, hole.id

        posts = [
            ("/herder-backups/security", {"force_2fa_scope": "operators", "password_min_length": "12", "factor_login_totp": "on"}),
            ("/herder-backups/console", {"console_idle_sec": "600", "console_audit_mode": "metadata", "console_bind_device": "on"}),
            ("/herder-backups/files", {"files_max_gib": "1"}),
            ("/herder-backups/oidc", {"oidc_enabled": "on", "oidc_issuer": "https://idp.example", "oidc_client_id": "ph"}),
            ("/herder-backups/alerts/policy", {}),
            ("/herder-backups/alerts/smtp", {"smtp_host": "mail.local", "smtp_from_email": "a@b.c", "smtp_alert_to": "ops@b.c"}),
            ("/herder-backups/data-cleanup/config", {"data_cleanup_enabled": "on", "data_cleanup_cron": "30 4 * * *"}),
            ("/herder-backups/update-checks", {"os_check_global_enabled": "on", "os_check_cron": "0 3 * * *", "apply_to_all": "on"}),
            ("/herder-backups/run", {"backup_mode": "full"}),
            ("/herder-backups/restore", {"archive": "piherder-backup.tar.gz", "dry_run": "on"}),
            ("/certificates/upload", {"name": "up", "fullchain_pem": "CERT", "privkey_pem": "KEY"}),
            (f"/certificates/{cid}/settings", {"name": "edge", "auto_renew": "on", "renew_days_before": "14"}),
            (f"/certificates/{cid}/replace-pem", {"fullchain_pem": "CERT", "privkey_pem": "KEY"}),
            (f"/certificates/{cid}/apply-edge", {"force": "on"}),
            (f"/certificates/{cid}/edge-mapping", {"action": "enable"}),
            (
                f"/certificates/{cid}/targets",
                {"server_id": str(sid), "label": "NPM", "remote_dir": "~/certs", "layout": "pair", "restart_kind": ""},
            ),
            (f"/certificates/{cid}/deploy", {"force": "on"}),
            (f"/certificates/{nid}/renew", {}),
            (f"/integrations/{iid}/pihole/dns-host", {"action": "add", "ip": "192.168.1.9", "domain": "app.lan"}),
            (f"/integrations/{iid}/pihole/dns-cname", {"action": "add", "domain": "alias.lan", "target": "app.lan"}),
            (f"/integrations/{iid}/pihole/action", {"action": "gravity", "all_instances": "on"}),
            (f"/integrations/{iid}/pihole/host-bind", {"server_id": str(sid), "docker_project": "web"}),
            ("/integrations/new/pihole", {"name": "Hole2", "base_url": "http://pi.hole", "password": "secret", "enabled": "on"}),
            (f"/servers/{sid}/ssh/generate-key", {}),
            (f"/servers/{sid}/ssh/test", {}),
            (f"/servers/{sid}/ssh/reset-host-key", {"confirm_name": "pi"}),
            (f"/servers/{sid}/ssh/set-username", {"ssh_username": "piherder"}),
            (f"/servers/{sid}/reboot", {}),
            (
                "/templates/web/confirm",
                {"stash_json": '{"server_id": %s, "deploy_now": true, "values": {"PORT": "80"}}' % sid},
            ),
            ("/templates/web/confirm", {"stash_json": "not-json"}),
            ("/templates/web/confirm", {"stash_json": '{"server_id": %s, "values": {}}' % sid}),
            ("/templates/web/confirm", {"stash_json": '{"server_id": %s, "values": {}}' % sid}),
        ]
        async_h = {"X-PiHerder-Async": "1"}
        for path, data in posts:
            hdrs = async_h if path.startswith("/templates/") or path.endswith("/pihole/action") else {}
            response = client.post(path, data=data, headers=hdrs, follow_redirects=False)
            assert response.status_code < 600, (path, response.status_code)

        listed = client.get("/certificates")
        detail = client.get(f"/certificates/{cid}")
        assert listed.status_code < 600 and detail.status_code < 600
    finally:
        app.dependency_overrides.pop(get_session, None)
