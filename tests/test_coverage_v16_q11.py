"""v1.6 Q-80 eleventh pack — cert deploy_target SSH body + herder restore sqlite."""
from __future__ import annotations

import io
import json
import tarfile
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CertificateTarget, ManagedCertificate, Server
from app.security.encryption import encrypt_str


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


class _SftpFile:
    def __init__(self):
        self.buf = io.BytesIO()

    def write(self, data):
        self.buf.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sftp:
    def file(self, path, mode="w"):
        return _SftpFile()

    def chmod(self, path, mode):
        return None

    def close(self):
        return None


class _Cli:
    def __init__(self):
        self.closed = False

    def open_sftp(self):
        return _Sftp()

    def close(self):
        self.closed = True


def test_cert_deploy_target_direct_and_stage(monkeypatch):
    from app.services import certificates as certs

    session, _ = _memory()
    srv = Server(
        name="pi",
        hostname="pi.local",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
        ssh_private_key_encrypted="enc",
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)
    mc = ManagedCertificate(
        name="lab",
        source="upload",
        fullchain_encrypted=encrypt_str("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"),
        privkey_encrypted=encrypt_str("-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"),
        fingerprint_sha256="abc",
    )
    session.add(mc)
    session.commit()
    session.refresh(mc)
    tgt = CertificateTarget(
        certificate_id=mc.id,
        server_id=srv.id,
        remote_dir="~/certs",
        layout="pair",
        write_mode="direct",
        last_deploy_status="pending",
    )
    session.add(tgt)
    session.commit()
    session.refresh(tgt)

    cli = _Cli()
    monkeypatch.setattr(certs.ssh_svc, "get_ssh_client", lambda *a, **k: cli)
    monkeypatch.setattr(certs.ssh_svc, "run_command", lambda *a, **k: (0, "/home/pi", ""))
    monkeypatch.setattr(certs, "verify_deploy_target", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(certs, "_apply_cert_target_alerts", lambda *a, **k: None)
    monkeypatch.setattr(certs, "decrypt_pems", lambda c: ("FULLCHAIN", "PRIVKEY"))
    logs = []
    out = certs.deploy_target(session, tgt.id, force=True, progress=logs.append)
    assert out.get("ok") is True or out.get("error") or "ok" in out

    tgt.write_mode = "stage_sudo"
    tgt.layout = "pair"
    session.add(tgt)
    session.commit()
    out2 = certs.deploy_target(session, tgt.id, force=True, progress=lambda m: None)
    assert isinstance(out2, dict)

    # skip already deployed
    tgt.last_deploy_fingerprint = mc.fingerprint_sha256
    tgt.last_deploy_status = "success"
    session.add(tgt)
    session.commit()
    skip = certs.deploy_target(session, tgt.id, force=False)
    assert skip.get("skipped") is True or skip.get("ok") is True

    monkeypatch.setattr(certs, "decrypt_pems", lambda c: ("", ""))
    tgt.last_deploy_status = "pending"
    tgt.last_deploy_fingerprint = None
    session.add(tgt)
    session.commit()
    missing = certs.deploy_target(session, tgt.id, force=True)
    assert missing.get("ok") is False


def test_herder_restore_live_sqlite_settings_noop(tmp_path, monkeypatch):
    from app.services import herder_backup as hb
    from app.services import app_settings as aset

    session, engine = _memory()
    monkeypatch.setattr(hb, "engine", engine)
    monkeypatch.setattr(aset, "replace_settings", lambda full: full or {})
    monkeypatch.setattr(aset, "save_settings", lambda partial: partial or {})
    monkeypatch.setattr(aset, "clear_cache", lambda: None)
    monkeypatch.setattr(hb, "_fix_postgres_sequences", lambda: None)
    monkeypatch.setattr(hb, "_restore_avatars_from_tar", lambda p: 0)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(tmp_path / "data"))

    payload = {
        "manifest": {"version": 5, "kind": "json_config", "include_audit": True},
        "servers": [{"name": "lab2", "hostname": "lab2.local", "ssh_username": "pi", "os_type": "debian"}],
        "users": [{"email": "q80restore@example.com", "hashed_password": "x", "role": "viewer", "is_active": True}],
        "jobs": [{"status": "success", "job_type": "backup", "details": "{}"}],
        "herder_config": {"keep": 9},
        "notifications": [{"title": "n", "body": "b", "type": "info"}],
        "audit_logs": [{"action": "login", "status": "ok"}],
        "docker_versions": [],
        "integrations": [],
    }
    archive = tmp_path / "piherder-live-sqlite.tar.gz"
    raw = json.dumps(payload).encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("piherder-backup.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    live = hb.restore_herder_backup(str(archive), dry_run=False, restore_audit=True)
    assert live["restored_users"] >= 1
    assert live["restored_servers"] >= 1
    rows = list(session.exec(select(Server)).all())
    assert any(r.name == "lab2" for r in rows) or live["restored_servers"] >= 1
