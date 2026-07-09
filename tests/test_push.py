"""Unit tests for Web Push helpers (no real browser / FCM)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import push as push_svc
from app.models import PushPreference, PushSubscription, Notification, PushVapidConfig
from app.security.encryption import encrypt_str


def test_is_push_configured_false_without_keys(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", None)
    monkeypatch.setattr(push_svc, "resolve_vapid_keys", lambda session=None: None)
    assert push_svc.is_push_configured() is False


def test_is_push_configured_true_from_env(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:a@b.c")
    assert push_svc.is_push_configured() is True
    assert push_svc.vapid_public_key() == "pub"
    assert push_svc._env_credentials().source == "env"


def test_preference_allows():
    pref = PushPreference(
        user_id=1,
        push_enabled=True,
        backup_failed=True,
        os_updates=False,
        reboot_pending=True,
        container_updates=True,
        herder_backup_failed=True,
    )
    assert push_svc.preference_allows(pref, "backup_failed") is True
    assert push_svc.preference_allows(pref, "os_updates") is False
    pref.push_enabled = False
    assert push_svc.preference_allows(pref, "backup_failed") is False


def test_send_for_notification_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(push_svc, "ensure_vapid_keys", lambda session: None)
    session = MagicMock()
    n = Notification(
        id=1,
        type="backup_failed",
        title="fail",
        fingerprint="backup_failed:server:1",
        severity="critical",
    )
    assert push_svc.send_for_notification(session, n) == 0


def _session_with_subs_and_prefs(sub, pref):
    session = MagicMock()
    exec_results = [[sub], [pref]]

    def _exec(stmt):
        m = MagicMock()
        m.all.return_value = exec_results.pop(0) if exec_results else []
        return m

    session.exec.side_effect = _exec
    return session


def test_send_for_notification_filters_and_calls_webpush(monkeypatch):
    creds = push_svc.VapidCredentials(
        public_key="pub",
        private_key="-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----",
        contact="mailto:a@b.c",
        source="env",
    )
    monkeypatch.setattr(push_svc, "ensure_vapid_keys", lambda session: creds)
    monkeypatch.setattr(push_svc, "_vapid_private_for_webpush", lambda pem: "vapid-obj")

    sub = PushSubscription(
        id=10,
        user_id=1,
        endpoint="https://push.example/x",
        p256dh="k",
        auth="a",
        created_at=datetime.utcnow(),
    )
    pref = PushPreference(
        user_id=1,
        push_enabled=True,
        backup_failed=True,
        os_updates=True,
        reboot_pending=True,
        container_updates=True,
        herder_backup_failed=True,
    )
    session = _session_with_subs_and_prefs(sub, pref)

    n = Notification(
        id=1,
        type="backup_failed",
        title="Backup failed",
        body="oops",
        link_url="/servers/1/backups",
        fingerprint="backup_failed:server:1",
        severity="critical",
    )

    mock_webpush = MagicMock()
    fake_mod = SimpleNamespace(webpush=mock_webpush, WebPushException=Exception)

    with patch.dict("sys.modules", {"pywebpush": fake_mod}):
        sent = push_svc.send_for_notification(session, n)

    assert sent == 1
    assert mock_webpush.called
    assert mock_webpush.call_args.kwargs.get("vapid_private_key") == "vapid-obj"
    assert "vapid_public_key" not in mock_webpush.call_args.kwargs


def test_send_skips_disabled_pref(monkeypatch):
    creds = push_svc.VapidCredentials(
        public_key="pub", private_key="priv", contact="mailto:a@b.c", source="env"
    )
    monkeypatch.setattr(push_svc, "ensure_vapid_keys", lambda session: creds)
    monkeypatch.setattr(push_svc, "_vapid_private_for_webpush", lambda pem: "vapid-obj")

    sub = PushSubscription(
        id=10,
        user_id=1,
        endpoint="https://push.example/x",
        p256dh="k",
        auth="a",
        created_at=datetime.utcnow(),
    )
    pref = PushPreference(
        user_id=1,
        push_enabled=True,
        backup_failed=False,
        os_updates=True,
        reboot_pending=True,
        container_updates=True,
        herder_backup_failed=True,
    )
    session = _session_with_subs_and_prefs(sub, pref)
    mock_webpush = MagicMock()
    fake_mod = SimpleNamespace(webpush=mock_webpush, WebPushException=Exception)
    n = Notification(
        id=1,
        type="backup_failed",
        title="fail",
        fingerprint="x",
        severity="critical",
    )
    with patch.dict("sys.modules", {"pywebpush": fake_mod}):
        sent = push_svc.send_for_notification(session, n)
    assert sent == 0
    assert not mock_webpush.called


def test_send_removes_dead_subscription(monkeypatch):
    creds = push_svc.VapidCredentials(
        public_key="pub", private_key="priv", contact="mailto:a@b.c", source="env"
    )
    monkeypatch.setattr(push_svc, "ensure_vapid_keys", lambda session: creds)
    monkeypatch.setattr(push_svc, "_vapid_private_for_webpush", lambda pem: "vapid-obj")

    sub = PushSubscription(
        id=99,
        user_id=1,
        endpoint="https://push.example/dead",
        p256dh="k",
        auth="a",
        created_at=datetime.utcnow(),
    )
    pref = PushPreference(
        user_id=1,
        push_enabled=True,
        backup_failed=True,
        os_updates=True,
        reboot_pending=True,
        container_updates=True,
        herder_backup_failed=True,
    )
    session = _session_with_subs_and_prefs(sub, pref)
    session.get.return_value = sub

    class WebPushException(Exception):
        def __init__(self, msg="gone"):
            super().__init__(msg)
            self.response = SimpleNamespace(status_code=410)

    def boom(*a, **k):
        raise WebPushException("gone")

    fake_mod = SimpleNamespace(webpush=boom, WebPushException=WebPushException)
    n = Notification(
        id=1,
        type="backup_failed",
        title="fail",
        fingerprint="x",
        severity="critical",
    )
    with patch.dict("sys.modules", {"pywebpush": fake_mod}):
        sent = push_svc.send_for_notification(session, n)

    assert sent == 0
    session.delete.assert_called()


def test_ensure_vapid_env_wins_over_db(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", "env-pub")
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", "env-priv")
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:env@x")
    session = MagicMock()
    creds = push_svc.ensure_vapid_keys(session)
    assert creds is not None
    assert creds.source == "env"
    assert creds.public_key == "env-pub"
    session.exec.assert_not_called()


def test_ensure_vapid_loads_existing_row(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:c@x")

    row = PushVapidConfig(
        id=1,
        public_key="db-pub",
        private_key_encrypted=encrypt_str("db-priv-pem"),
        contact="mailto:c@x",
        source="generated",
        created_at=datetime.utcnow(),
    )
    session = MagicMock()
    m = MagicMock()
    m.first.return_value = row
    session.exec.return_value = m

    creds = push_svc.ensure_vapid_keys(session)
    assert creds is not None
    assert creds.public_key == "db-pub"
    assert creds.private_key == "db-priv-pem"
    assert creds.source == "generated"


def test_ensure_vapid_generates_when_empty(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:gen@x")
    monkeypatch.setattr(
        push_svc,
        "_generate_key_pair",
        lambda: ("gen-pub", "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----"),
    )

    session = MagicMock()
    # first select: no row; after store we decrypt via _row_to_credentials on returned row
    empty = MagicMock()
    empty.first.return_value = None
    session.exec.return_value = empty

    stored = []

    def add(obj):
        stored.append(obj)
        obj.id = 1

    session.add.side_effect = add

    def refresh(obj):
        pass

    session.refresh.side_effect = refresh

    creds = push_svc.ensure_vapid_keys(session)
    assert creds is not None
    assert creds.public_key == "gen-pub"
    assert "PRIVATE KEY" in creds.private_key
    assert creds.source == "generated"
    assert session.add.called
    assert session.commit.called


def test_default_contact_from_hostname(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", None)
    monkeypatch.setattr(push_svc.settings, "PIHERDER_HOSTNAME", "piherder.hacknow.com")
    assert push_svc._default_contact() == "mailto:admin@piherder.hacknow.com"


def test_send_test_to_user_no_subscription(monkeypatch):
    from app.models import User

    creds = push_svc.VapidCredentials(
        public_key="pub", private_key="priv", contact="mailto:a@b.c", source="env"
    )
    monkeypatch.setattr(push_svc, "ensure_vapid_keys", lambda session: creds)
    monkeypatch.setattr(push_svc, "list_subscriptions", lambda session, uid: [])
    user = User(id=1, email="u@x", hashed_password="x")
    result = push_svc.send_test_to_user(MagicMock(), user)
    assert result["ok"] is False
    assert result["error"] == "no_subscription"


def test_send_test_to_user_delivers(monkeypatch):
    from app.models import User

    creds = push_svc.VapidCredentials(
        public_key="pub", private_key="priv", contact="mailto:a@b.c", source="env"
    )
    monkeypatch.setattr(push_svc, "ensure_vapid_keys", lambda session: creds)
    monkeypatch.setattr(push_svc, "_vapid_private_for_webpush", lambda pem: "vapid-obj")
    sub = PushSubscription(
        id=1,
        user_id=1,
        endpoint="https://push.example/t",
        p256dh="k",
        auth="a",
        created_at=datetime.utcnow(),
    )
    monkeypatch.setattr(push_svc, "list_subscriptions", lambda session, uid: [sub])
    mock_webpush = MagicMock()
    fake_mod = SimpleNamespace(webpush=mock_webpush, WebPushException=Exception)
    user = User(id=1, email="u@x", hashed_password="x")
    session = MagicMock()
    with patch.dict("sys.modules", {"pywebpush": fake_mod}):
        result = push_svc.send_test_to_user(session, user)
    assert result["ok"] is True
    assert result["sent"] == 1
    assert mock_webpush.called
