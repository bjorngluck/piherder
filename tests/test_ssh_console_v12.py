"""v1.2 Stream W — SSH console tickets / grants / limits (no live SSH)."""
from __future__ import annotations

import pytest

from app.services import ssh_console as cons


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()
    yield
    cons.reset_runtime_state_for_tests()


def test_console_disabled_by_default(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", False)
    assert cons.console_enabled() is False
    with pytest.raises(cons.ConsoleDisabled):
        cons.require_enabled()


def test_session_still_valid_demo_allows_viewer(monkeypatch):
    """D5: continuous revalidation must not kill shared demo viewer shells."""
    from types import SimpleNamespace
    from app.services import demo as demo_svc

    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    user = SimpleNamespace(id=1, role="viewer", is_active=True, session_version=0)

    class _S:
        def get(self, _m, _id):
            return user

    ok, reason = cons.session_still_valid(_S(), user_id=1, expected_sv=0)
    assert ok is True
    assert reason == ""


def test_session_still_valid_viewer_lost_outside_demo(monkeypatch):
    from types import SimpleNamespace
    from app.services import demo as demo_svc

    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", False)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    user = SimpleNamespace(id=1, role="viewer", is_active=True, session_version=0)

    class _S:
        def get(self, _m, _id):
            return user

    ok, reason = cons.session_still_valid(_S(), user_id=1, expected_sv=0)
    assert ok is False
    assert reason == "role_lost"


def test_discard_parked_frees_slot(monkeypatch):
    """Closing a shell must free concurrent slots even if only soft-parked."""
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 2)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 10)
    cons.reset_runtime_state_for_tests()

    cons.try_acquire_slot(7)
    cons.try_acquire_slot(7)
    assert cons.slots_remaining(7) == 0

    held = cons.HeldConsole(
        resume_id="resume-tok-abc",
        user_id=7,
        server_id=3,
        session_version=1,
        ticket_payload={"console": True},
        device_id="dev",
        client=None,
        channel=None,
        started_mono=0.0,
        last_activity_mono=0.0,
        held_at_mono=0.0,
        server_hostname="lab",
    )
    # Parked sessions keep the slot from the original WS open
    cons.park_console(held)
    # Still at cap until discard
    assert cons.slots_remaining(7) == 0

    ok = cons.discard_parked_for_user("resume-tok-abc", user_id=7, server_id=3)
    assert ok is True
    assert cons.slots_remaining(7) == 1

    # Wrong user cannot free
    cons.try_acquire_slot(7)
    held2 = cons.HeldConsole(
        resume_id="resume-tok-xyz",
        user_id=7,
        server_id=3,
        session_version=1,
        ticket_payload={},
        device_id="dev",
        client=None,
        channel=None,
        started_mono=0.0,
        last_activity_mono=0.0,
        held_at_mono=0.0,
        server_hostname="lab",
    )
    cons.park_console(held2)
    assert cons.discard_parked_for_user("resume-tok-xyz", user_id=99, server_id=3) is False
    assert cons.discard_parked_for_user("resume-tok-xyz", user_id=7, server_id=3) is True


def test_mint_and_consume_ticket():
    tok = cons.mint_ticket(
        user_id=3,
        server_id=9,
        session_version=2,
        client_ip="10.0.0.5",
        device_id="dev-abc-1234567890",
    )
    assert tok
    payload = cons.consume_ticket(
        tok,
        user_id=3,
        server_id=9,
        session_version=2,
        client_ip="10.0.0.5",
        device_id="dev-abc-1234567890",
    )
    assert payload.get("console") is True
    assert int(payload["sid"]) == 9
    with pytest.raises(cons.ConsoleDenied, match="already used|cannot resume"):
        cons.consume_ticket(
            tok,
            user_id=3,
            server_id=9,
            session_version=2,
            client_ip="10.0.0.5",
            device_id="dev-abc-1234567890",
        )


def test_consume_wrong_user():
    tok = cons.mint_ticket(user_id=1, server_id=2, session_version=0)
    with pytest.raises(cons.ConsoleDenied, match="does not match"):
        cons.consume_ticket(tok, user_id=99, server_id=2, session_version=0)


def test_consume_session_version_mismatch():
    tok = cons.mint_ticket(user_id=1, server_id=2, session_version=5)
    with pytest.raises(cons.ConsoleDenied, match="session"):
        cons.consume_ticket(tok, user_id=1, server_id=2, session_version=6)


def test_ticket_bound_to_ip_and_device(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_BIND_IP", True)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_BIND_DEVICE", True)
    tok = cons.mint_ticket(
        user_id=1,
        server_id=2,
        session_version=1,
        client_ip="192.168.1.10",
        device_id="device-token-aaaa",
    )
    with pytest.raises(cons.ConsoleDenied, match="network"):
        cons.consume_ticket(
            tok,
            user_id=1,
            server_id=2,
            session_version=1,
            client_ip="10.0.0.1",
            device_id="device-token-aaaa",
        )
    tok2 = cons.mint_ticket(
        user_id=1,
        server_id=2,
        session_version=1,
        client_ip="192.168.1.10",
        device_id="device-token-aaaa",
    )
    with pytest.raises(cons.ConsoleDenied, match="browser|device"):
        cons.consume_ticket(
            tok2,
            user_id=1,
            server_id=2,
            session_version=1,
            client_ip="192.168.1.10",
            device_id="other-device-bbbb",
        )


def test_binding_still_valid():
    payload = {
        "iph": cons._hash_binding(cons.normalize_ip("1.2.3.4")),
        "did": cons._hash_binding("dev1"),
    }
    ok, _ = cons.binding_still_valid(payload, client_ip="1.2.3.4", device_id="dev1")
    assert ok
    ok, reason = cons.binding_still_valid(payload, client_ip="9.9.9.9", device_id="dev1")
    assert not ok and reason == "ip_changed"
    ok, reason = cons.binding_still_valid(payload, client_ip="1.2.3.4", device_id="x")
    assert not ok and reason == "device_changed"


def test_grant_is_fleet_wide():
    """One 2FA step-up covers every host (server_id not enforced)."""
    g = cons.mint_grant(user_id=4, server_id=11, session_version=1)
    assert cons.grant_valid(g, user_id=4, server_id=11, session_version=1)
    assert cons.grant_valid(g, user_id=4, server_id=99, session_version=1)
    assert not cons.grant_valid(g, user_id=4, server_id=11, session_version=2)
    assert not cons.grant_valid(None, user_id=4, server_id=11, session_version=1)
    assert not cons.grant_valid(g, user_id=9, server_id=11, session_version=1)


def test_slot_limits(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 1)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 2)
    cons.try_acquire_slot(7)
    with pytest.raises(cons.ConsoleDenied, match="your account"):
        cons.try_acquire_slot(7)
    cons.try_acquire_slot(8)
    with pytest.raises(cons.ConsoleDenied, match="instance"):
        cons.try_acquire_slot(9)
    cons.release_slot(7)
    cons.try_acquire_slot(9)
    cons.release_slot(8)
    cons.release_slot(9)


def test_slots_remaining(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_PER_USER", 2)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_MAX_GLOBAL", 10)
    assert cons.slots_remaining(1) == 2
    cons.try_acquire_slot(1)
    assert cons.slots_remaining(1) == 1
    cons.release_slot(1)
    assert cons.slots_remaining(1) == 2


def test_audit_labels():
    from app.services import audit_format as af

    assert "console" in af.action_label("ssh_console_open").lower()
    assert af.action_label("ssh_console_close")


def test_same_site_browser_request():
    from starlette.requests import Request

    def req(headers: dict) -> Request:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/servers/1/console/ticket",
            "raw_path": b"/servers/1/console/ticket",
            "query_string": b"",
            "headers": [
                (b"host", b"ph.example.com"),
                *[(k.lower().encode(), v.encode()) for k, v in headers.items()],
            ],
            "client": ("1.2.3.4", 1234),
            "server": ("ph.example.com", 443),
        }
        return Request(scope)

    assert cons.same_site_browser_request(
        req({"origin": "https://ph.example.com"})
    )
    assert cons.same_site_browser_request(
        req({"referer": "https://ph.example.com/servers/1/console"})
    )
    assert not cons.same_site_browser_request(
        req({"origin": "https://evil.example"})
    )
    assert not cons.same_site_browser_request(
        req({"sec-fetch-site": "cross-site", "origin": "https://ph.example.com"})
    )
    assert not cons.same_site_browser_request(req({}))  # no Origin/Referer


def test_grant_disabled_when_every_shell(monkeypatch):
    monkeypatch.setattr(
        cons.settings, "PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL", True
    )
    g = cons.mint_grant(user_id=1, server_id=2, session_version=0)
    assert not cons.grant_valid(g, user_id=1, server_id=2, session_version=0)


def test_websocket_origin_allowed():
    class WS:
        def __init__(self, headers):
            self.headers = headers

    assert cons.websocket_origin_allowed(
        WS({"host": "ph.example.com", "origin": "https://ph.example.com"})
    )
    assert not cons.websocket_origin_allowed(
        WS({"host": "ph.example.com", "origin": "https://evil.example"})
    )
    assert not cons.websocket_origin_allowed(WS({"host": "ph.example.com"}))


def test_backup_codes_disallowed_by_default():
    assert cons.allow_backup_codes() is False
    assert cons.prefer_passkey() is True


def test_revalidate_default_is_tight():
    assert cons.revalidate_sec() <= 15


def test_hold_park_claim_resume(monkeypatch):
    """Detached PTY can be claimed once with matching bindings."""
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    rid = cons.mint_resume_id()
    # Fake channel/client
    class Ch:
        def close(self):
            pass

        def recv_ready(self):
            return False

        def exit_status_ready(self):
            return False

    class Cli:
        def close(self):
            pass

    held = cons.HeldConsole(
        resume_id=rid,
        user_id=3,
        server_id=9,
        session_version=2,
        ticket_payload={
            "iph": cons._hash_binding(cons.normalize_ip("10.0.0.1")),
            "did": cons._hash_binding("dev-token-aaaa"),
        },
        device_id="dev-token-aaaa",
        client=Cli(),
        channel=Ch(),
        started_mono=cons.time.monotonic(),
        last_activity_mono=cons.time.monotonic(),
        held_at_mono=cons.time.monotonic(),
        server_hostname="lab",
    )
    cons.park_console(held)
    assert cons.held_count() == 1
    got = cons.claim_resume(
        rid,
        user_id=3,
        server_id=9,
        session_version=2,
        device_id="dev-token-aaaa",
        client_ip="10.0.0.1",
    )
    assert got.resume_id == rid
    assert cons.held_count() == 0
    # second claim fails
    with pytest.raises(cons.ConsoleDenied):
        cons.claim_resume(
            rid,
            user_id=3,
            server_id=9,
            session_version=2,
            device_id="dev-token-aaaa",
            client_ip="10.0.0.1",
        )


def test_hold_rejects_wrong_user(monkeypatch):
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)

    class Ch:
        def close(self):
            pass

        def recv_ready(self):
            return False

        def exit_status_ready(self):
            return False

    class Cli:
        def close(self):
            pass

    rid = cons.mint_resume_id()
    held = cons.HeldConsole(
        resume_id=rid,
        user_id=1,
        server_id=2,
        session_version=0,
        ticket_payload={},
        device_id="d",
        client=Cli(),
        channel=Ch(),
        started_mono=cons.time.monotonic(),
        last_activity_mono=cons.time.monotonic(),
        held_at_mono=cons.time.monotonic(),
        server_hostname="h",
    )
    cons.park_console(held)
    with pytest.raises(cons.ConsoleDenied, match="match"):
        cons.claim_resume(
            rid, user_id=99, server_id=2, session_version=0, device_id="d"
        )
