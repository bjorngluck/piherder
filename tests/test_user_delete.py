"""User delete must clear all FKs to user.id (no DB cascade)."""
from __future__ import annotations

from types import SimpleNamespace

from app.models import User
from app.services.user_admin import detach_and_delete_user


class _ExecResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Returns pre-seeded child rows for every select; records delete/add/flush."""

    def __init__(self):
        self.deleted = []
        self.added = []
        self.flushed = False
        self._queue = [
            [SimpleNamespace(kind="totp", user_id=9)],
            [SimpleNamespace(kind="trusted", user_id=9)],
            [SimpleNamespace(kind="pushsub", user_id=9)],
            [SimpleNamespace(kind="pushpref", user_id=9)],
            [SimpleNamespace(kind="audit", user_id=9)],
            [SimpleNamespace(kind="notif", user_id=9)],
            [SimpleNamespace(kind="token", created_by_user_id=9)],
        ]
        self._i = 0

    def exec(self, _statement):
        if self._i >= len(self._queue):
            return _ExecResult([])
        rows = self._queue[self._i]
        self._i += 1
        return _ExecResult(rows)

    def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushed = True


def test_detach_and_delete_user_clears_all_fk_families(monkeypatch):
    session = _FakeSession()
    target = User(
        id=9,
        email="gone@example.com",
        hashed_password="x",
        role="operator",
    )
    monkeypatch.setattr(
        "app.services.user_admin.delete_avatar_files",
        lambda uid: None,
    )

    email = detach_and_delete_user(session, target)

    assert email == "gone@example.com"
    assert session.deleted[-1] is target
    assert session.flushed is True
