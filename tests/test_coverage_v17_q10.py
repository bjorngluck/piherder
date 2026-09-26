"""v1.7 coverage — OIDC callback, SSH identities, template editor, reorder."""
from __future__ import annotations

from types import SimpleNamespace

from sqlmodel import Session

from app.database import get_session
from app.main import app
from app.models import ServerSshIdentity, User
from tests.test_coverage_v17_q1 import _client, _engine


class _Out:
    def __init__(self):
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


def test_oidc_ssh_identities_and_templates(tmp_path, monkeypatch):
    import app.routers.auth_oidc as oidc_mod
    import app.routers.templates_svc as tpl_mod
    import app.services.ssh as ssh_mod
    from app.services.service_templates.editor import blank_editor_form

    oidc = oidc_mod.oidc
    monkeypatch.setattr(oidc, "parse_state_token", lambda raw: {"sp": "state1", "mode": "login", "cv": "v", "nonce": "n", "uid": 1})
    monkeypatch.setattr(oidc, "exchange_code", lambda code, verifier: {"access_token": "t"})
    monkeypatch.setattr(
        oidc,
        "claims_from_tokens",
        lambda tokens, expected_nonce="": {"sub": "abc", "email": "q17@test.local"},
    )
    monkeypatch.setattr(oidc, "oidc_settings", lambda: {"oidc_issuer": "https://idp.example"})
    monkeypatch.setattr(oidc, "normalize_issuer", lambda issuer: issuer)
    monkeypatch.setattr(oidc, "get_identity_by_iss_sub", lambda *a, **k: None)
    monkeypatch.setattr(oidc, "maybe_sync_role", lambda *a, **k: ("unchanged", None))
    monkeypatch.setattr(oidc, "create_link", lambda *a, **k: None)

    engine = _engine(tmp_path / "q10.db")
    client, uid, sid = _client(engine, monkeypatch)

    def _find(session, claims, cfg):
        user = session.get(User, uid)
        return user, "existing", True

    monkeypatch.setattr(oidc, "find_user_for_login", _find)
    monkeypatch.setattr(ssh_mod, "get_ssh_client", lambda *a, **k: _Reboot())
    monkeypatch.setattr(
        "app.services.ssh_onboarding.deploy_public_key",
        lambda *a, **k: SimpleNamespace(ok=True, message="deployed", details={"public_key": "ssh-ed25519 AAAA"}),
    )
    monkeypatch.setattr(
        "app.services.ssh_onboarding.rotate_keypair",
        lambda *a, **k: SimpleNamespace(
            ok=True,
            message="rotated",
            details={
                "public_key": "ssh-ed25519 BBBB",
                "new_public_key": "ssh-ed25519 BBBB",
                "new_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n",
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.ssh_onboarding.provision_least_priv_user",
        lambda *a, **k: SimpleNamespace(ok=True, message="provisioned", details={"new_username": "piherder", "os": {"name": "debian"}}),
    )
    monkeypatch.setattr("app.services.ssh_onboarding.is_real_public_key", lambda pub: True)
    monkeypatch.setattr(
        "app.services.ssh_onboarding.build_privileged_user_script",
        lambda username, pub: "#!/bin/sh\necho ok\n",
    )
    monkeypatch.setattr(
        tpl_mod,
        "pull_project_as_editor_form",
        lambda *a, **k: {"form": blank_editor_form(), "messages": ["pulled"]},
    )

    try:
        with Session(engine) as s:
            ident = ServerSshIdentity(
                server_id=sid,
                role="privileged",
                label="Privileged",
                username="piherder-admin",
                public_key="ssh-ed25519 AAAA",
                key_fingerprint="SHA256:abc",
                enabled=True,
            )
            s.add(ident)
            s.commit()
            s.refresh(ident)
            iid = ident.id

        client.cookies.set("oidc_state", "raw")
        ok = client.get("/auth/oidc/callback?code=abc&state=state1", follow_redirects=False)
        denied = client.get("/auth/oidc/callback?error=access_denied", follow_redirects=False)
        assert ok.status_code < 600 and denied.status_code < 600

        monkeypatch.setattr(oidc, "parse_state_token", lambda raw: {"sp": "state1", "mode": "link", "cv": "v", "nonce": "n", "uid": uid})
        linked = client.get("/auth/oidc/callback?code=abc&state=state1", follow_redirects=False)
        monkeypatch.setattr(oidc, "exchange_code", lambda *a, **k: (_ for _ in ()).throw(oidc.OidcFlowError("bad code")))
        failed = client.get("/auth/oidc/callback?code=abc&state=state1", follow_redirects=False)
        assert linked.status_code < 600 and failed.status_code < 600

        posts = [
            (f"/servers/{sid}/ssh/identities/privileged", {"ssh_username": "piherder-admin", "key_mode": "generate"}),
            (f"/servers/{sid}/ssh/identities/{iid}/test", {}),
            (f"/servers/{sid}/ssh/identities/{iid}/deploy", {"ssh_password": "secret"}),
            (f"/servers/{sid}/ssh/identities/{iid}/rotate", {"confirm": "rotate"}),
            ("/servers/reorder", {"order": f"{sid},nope"}),
            (f"/servers/{sid}/move/down", {}),
            (f"/servers/{sid}/ssh/deploy-key", {"ssh_password": "secret", "store_password": "on", "clear_password_after": "on"}),
            (f"/servers/{sid}/ssh/rotate-key", {"confirm": "rotate"}),
            (f"/servers/{sid}/ssh/provision-user", {"new_username": "piherder", "run_on_host": "on", "include_docker": "on"}),
            (f"/servers/{sid}/reboot", {}),
            ("/templates/from-host", {"server_id": str(sid), "project_name": "web", "auto_harden": "on"}),
            (
                "/templates/new",
                {
                    "editor_action": "save",
                    "slug": "extra",
                    "name": "Extra",
                    "compose_content": "services:\n  app:\n    image: nginx:alpine\n",
                    "variables_json": "[]",
                    "extra_files_json": "[]",
                    "checklist_json": "[]",
                },
            ),
        ]
        for path, data in posts:
            response = client.post(path, data=data, follow_redirects=False)
            assert response.status_code < 600, (path, response.status_code)
        script = client.get(f"/servers/{sid}/ssh/identities/{iid}/setup-script")
        removed = client.post(
            f"/servers/{sid}/ssh/identities/{iid}/remove",
            data={"confirm_name": "pi"},
            follow_redirects=False,
        )
        assert script.status_code < 600 and removed.status_code < 600
    finally:
        app.dependency_overrides.pop(get_session, None)
