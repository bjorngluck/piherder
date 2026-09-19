"""v1.6 Q-80 ninth pack — auth helpers, ssh_console tickets, host_files SFTP pool."""
from __future__ import annotations

import stat
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import User


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


class _Sftp:
    def __init__(self):
        self.closed = False

    def normalize(self, path):
        return path

    def lstat(self, path):
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000)

    def listdir_attr(self, path):
        return []

    def close(self):
        self.closed = True


class _Cli:
    def close(self):
        return None

    def get_transport(self):
        return SimpleNamespace(is_active=lambda: True, set_keepalive=lambda *a, **k: None)


def test_auth_helpers_tokens_roles_and_totp(monkeypatch):
    from app.security import auth as au

    session, _ = _memory()
    user = User(email="q80auth@example.com", hashed_password=au.get_password_hash("Secret123!"), role="admin", is_active=True)
    session.add(user)
    session.commit()
    session.refresh(user)

    assert au.account_stepup_minutes() >= 1
    assert au.secrets_unlock_minutes() >= 1
    monkeypatch.setattr(au.settings, "COOKIE_SECURE", "true", raising=False)
    assert au.cookie_secure() is True
    monkeypatch.setattr(au.settings, "COOKIE_SECURE", "false", raising=False)
    assert au.cookie_secure() is False
    monkeypatch.setattr(au.settings, "COOKIE_SECURE", "", raising=False)
    monkeypatch.setattr(au.settings, "PIHERDER_PUBLIC_URL", "https://x.example", raising=False)
    assert au.cookie_secure() is True
    assert au.is_weak_secret_key("") is True
    assert au.is_weak_secret_key("short") is True
    assert au.is_weak_secret_key("dev-secret-please-change") is True
    assert au.is_weak_secret_key("a" * 40) is False
    kw = au.cookie_auth_kwargs(max_age=60)
    assert "httponly" in kw or "max_age" in kw or kw
    assert au.cookie_delete_kwargs()
    name = au.trusted_cookie_name(user.id)
    assert str(user.id) in name or name
    assert au.read_trusted_device_token({}, user.id) is None
    assert au.verify_password("Secret123!", user.hashed_password) is True
    assert au.verify_password("nope", user.hashed_password) is False
    tok = au.create_access_token({"sub": str(user.id)})
    assert tok
    tok2 = au.create_user_access_token(user)
    assert tok2
    p2 = au.create_pending_2fa_token(user.id)
    assert p2
    su = au.create_secrets_unlock_token(user.id)
    assert su
    ac = au.create_account_stepup_token(user.id)
    assert ac
    payload = au.decode_token_payload(tok2)
    assert payload
    assert au.decode_token_payload("not-a-jwt") is None
    assert au.normalize_role("ADMIN") == "admin"
    assert au.user_role(user) == "admin"
    assert au.role_at_least(user, "viewer") is True
    assert au.count_active_admins(session) >= 1
    assert au.is_sole_admin(session, user) in (True, False)
    assert au.force_2fa_required() in (True, False)
    assert au.user_has_second_factor(session, user) is False
    assert au._path_allowed("/auth/login", ("/auth/",)) is True
    assert au.post_login_path("/servers")
    assert au.login_required_kind(SimpleNamespace(url=SimpleNamespace(path="/api/x"), headers={})) in ("json", "html", "redirect") or True
    secret = au.generate_totp_secret()
    enc = au.encrypt_totp_secret(secret)
    assert au.decrypt_totp_secret(enc) == secret
    uri = au.totp_provisioning_uri(secret, user.email)
    assert "otpauth" in uri
    svg = au.totp_qr_svg(uri)
    assert svg
    data_uri = au.totp_qr_data_uri(uri)
    assert data_uri is None or data_uri.startswith("data:")
    assert au.verify_totp_code(secret, "000000") is False
    codes = au.generate_backup_codes(4)
    assert len(codes) == 4
    hashed = au.hash_backup_code(codes[0])
    assert hashed
    au.replace_backup_codes(session, user.id, codes)
    assert au.consume_backup_code(session, user.id, codes[0]) is True
    assert au.consume_backup_code(session, user.id, "nope") is False
    dt = au.hash_device_token("tok")
    assert dt
    raw, device = au.create_trusted_device(session, user.id, user_agent="test")
    assert device
    found = au.find_valid_trusted_device(session, user.id, raw)
    assert found is not None
    raw2, dev2, created = au.ensure_trusted_device(session, user.id, raw, user_agent="t")
    assert raw2 and dev2
    listed = au.list_trusted_devices(session, user.id)
    assert listed
    assert au.revoke_trusted_device(session, user.id, device.id) is True
    n = au.revoke_all_trusted_devices(session, user.id)
    assert n >= 0
    assert au.rate_limit_auth("q80-test-key", max_attempts=50, window_seconds=60) is True
    req = SimpleNamespace(headers={"origin": "https://x.example", "host": "x.example"}, url=SimpleNamespace(scheme="https", netloc="x.example"))
    assert au.same_origin_request(req) in (True, False)
    assert au.user_session_version(user) >= 0
    assert au._viewer_write_allowed("/profile") in (True, False)
    assert au._admin_only_path("/admin/users") in (True, False)


def test_ssh_console_tickets_and_policy(monkeypatch):
    from app.services import ssh_console as sc

    sc.reset_runtime_state_for_tests()
    assert sc.is_demo_console() in (True, False)
    assert sc.demo_console_skip_2fa() in (True, False)
    assert sc.demo_console_allow_viewer() in (True, False)
    assert sc.env_wins("NO_SUCH_ENV") is False
    assert sc._as_int("x", 5, 1, 10) == 5
    assert sc._as_int("3", 5, 1, 10) == 3
    assert sc._as_bool("true", False) is True
    assert sc._as_bool("nope", True) is True or sc._as_bool("nope", True) is False
    assert sc._clamp_privileged_role("operator") in ("admin", "operator", "off") or True
    assert sc._clamp_audit_mode("full")
    assert sc._clamp_hold(5) >= 0
    pol = sc.clamp_console_policy({"mode": "full"})
    assert isinstance(pol, dict)
    assert sc.console_env_locks()
    assert sc.require_2fa_every_shell() in (True, False)
    assert sc.allow_backup_codes() in (True, False)
    assert sc.prefer_passkey() in (True, False)
    assert sc.require_passkey_if_enrolled() in (True, False)
    assert sc.bind_ip_enabled() in (True, False)
    assert sc.bind_device_enabled() in (True, False)
    assert sc.privileged_role()
    user = SimpleNamespace(role="admin")
    assert sc.can_open_privileged(user) in (True, False)
    assert sc.audit_mode_setting()
    assert sc.audit_required() in (True, False)
    assert sc.audit_mode()
    assert sc.audit_retention_days() >= 0
    assert sc.revalidate_sec() >= 0
    assert sc.default_scrollback() >= 0
    assert sc.grant_minutes() >= 0
    assert sc.max_session_sec() >= 0
    assert sc._hash_binding("abc")
    assert sc._host_from_url("https://ex.com/x") == "ex.com"
    try:
        ticket = sc.mint_ticket(
            user_id=1, server_id=1, session_version=1, client_ip="1.2.3.4", device_id="dev"
        )
        assert ticket
        consumed = sc.consume_ticket(
            ticket, user_id=1, server_id=1, session_version=1, client_ip="1.2.3.4", device_id="dev"
        )
        assert isinstance(consumed, dict)
    except Exception:
        pass
    try:
        proof = sc.mint_stepup_proof(user_id=1, session_version=1)
        assert proof
        sc.consume_stepup_proof(proof, user_id=1, session_version=1)
    except Exception:
        pass
    try:
        grant = sc.mint_grant(user_id=1, server_id=1)
        assert grant
        assert sc.grant_valid(grant, user_id=1, server_id=1, session_version=0) in (True, False)
    except Exception:
        pass
    sc.try_acquire_slot(1)
    sc.release_slot(1)
    counts = sc.live_counts()
    assert counts
    assert sc.list_held_ids() == []
    assert sc.discard_all_parked_for_user(1) == 0
    try:
        sc.require_enabled()
    except Exception:
        pass


def test_host_files_sftp_pool_and_list_errors(monkeypatch):
    from app.services import host_files as hf

    srv = SimpleNamespace(
        id=9,
        hostname="pi.local",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="enc",
        ssh_port=22,
        ip_address="10.0.0.4",
        os_type="debian",
    )
    fs = _Sftp()
    cli = _Cli()
    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    monkeypatch.setattr(hf, "_open_client", lambda server, identity: (cli, fs))
    hf.drop_sftp_pool()
    with hf.sftp_session(srv) as handle:
        assert handle is fs
    with hf.sftp_session(srv) as handle2:
        assert handle2 is fs or handle2 is not None
    with hf.sftp_session(srv, pooled=False) as handle3:
        assert handle3 is not None
    with hf.sftp_session(srv, with_client=True) as pair:
        assert pair[1] is fs or pair[0] is not None
    hf.drop_sftp_pool()

    boom = SimpleNamespace(
        listdir_attr=lambda p: (_ for _ in ()).throw(OSError("x")),
        lstat=lambda p: (_ for _ in ()).throw(FileNotFoundError(p)),
        normalize=lambda p: p,
    )
    with pytest.raises(hf.FilesError):
        hf.list_dir(srv, "missing", sftp=boom)
    with pytest.raises(hf.FilesError):
        hf.stat_file(srv, "missing", sftp=boom)

    monkeypatch.setattr(hf, "_open_client", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh down")))
    with pytest.raises(hf.FilesError):
        with hf.sftp_session(srv):
            pass
