"""Unit tests for Web Push helpers (no real browser / FCM)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import push as push_svc
from app.models import PushPreference, PushSubscription, Notification


def test_is_push_configured_false_without_keys(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", None)
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", None)
    assert push_svc.is_push_configured() is False


def test_is_push_configured_true(monkeypatch):
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:a@b.c")
    assert push_svc.is_push_configured() is True
    assert push_svc.vapid_public_key() == "pub"


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
    monkeypatch.setattr(push_svc, "is_push_configured", lambda: False)
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
    monkeypatch.setattr(push_svc, "is_push_configured", lambda: True)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:a@b.c")

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
    args, kwargs = mock_webpush.call_args
    assert kwargs.get("data") or (args[1] if len(args) > 1 else None)


def test_send_skips_disabled_pref(monkeypatch):
    monkeypatch.setattr(push_svc, "is_push_configured", lambda: True)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:a@b.c")

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
    monkeypatch.setattr(push_svc, "is_push_configured", lambda: True)
    monkeypatch.setattr(push_svc.settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(push_svc.settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(push_svc.settings, "VAPID_CONTACT", "mailto:a@b.c")

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
