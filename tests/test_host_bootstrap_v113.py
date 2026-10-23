"""Host tool bootstrap and feature preflight."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, Job, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import host_deps as hd
from app.services import ssh_onboarding as onboard
from app.services.ssh_onboarding import build_feature_bootstrap_script


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _release(os_id: str, codename: str, arch: str = "arm64") -> str:
    return (
        f'ID={os_id}\nVERSION_CODENAME={codename}\nPRETTY_NAME="{os_id} test"\n'
    ), arch + "\n"


def test_os_release_maps_official_docker_repos():
    ubuntu = hd.classify_os(*_release("ubuntu", "noble", "amd64"))
    assert ubuntu["supported_os"] is True
    assert ubuntu["docker_repo_url"] == "https://download.docker.com/linux/ubuntu"
    assert ubuntu["arch"] == "amd64"
    debian = hd.classify_os(*_release("debian", "bookworm", "arm64"))
    assert debian["docker_repo_url"] == "https://download.docker.com/linux/debian"
    raspi = hd.classify_os(*_release("raspbian", "bookworm", "armhf"))
    assert raspi["docker_repo_url"] == "https://download.docker.com/linux/raspbian"
    assert raspi["arch"] == "armhf"
    rejected = hd.classify_os(*_release("alpine", "3-20", "amd64"))
    assert rejected["supported_os"] is False
    assert rejected["docker_repo_url"] == ""
    assert rejected["os_id"] == "alpine"
    junk = hd.classify_os("ID=debian\nVERSION_CODENAME=bookworm;rm\n", "arm64\n")
    assert junk["codename"] == ""
    assert junk["supported_os"] is False


def test_preflight_is_feature_gated(monkeypatch):
    calls = []

    def _run(_client, cmd, timeout=15):
        calls.append(cmd)
        if "piherder_ok" in cmd:
            return 0, "piherder_ok\nLinux\n", ""
        if "os-release" in cmd:
            return 0, _release("debian", "bookworm")[0], ""
        if "print-architecture" in cmd:
            return 0, "arm64\n", ""
        if "rsync" in cmd:
            return 1, "", "missing"
        return 0, "", ""

    monkeypatch.setattr(hd, "get_ssh_client", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(hd, "run_command", _run)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda _c: {"is_haos": False})
    server = SimpleNamespace(
        hostname="host-a.example.com",
        ssh_username="pi",
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=False,
        os_patch_enabled=False,
    )
    result = hd.run_host_deps_check(server)
    ids = {row["id"] for row in result["checks"]}
    assert "rsync" in ids
    assert "docker_ce" not in ids
    assert "docker_compose" not in ids
    assert result["bootstrap"]["install_rsync"] is True
    assert result["bootstrap"]["install_docker_ce"] is False
    assert result["os"]["docker_repo_url"] == "https://download.docker.com/linux/debian"
    assert not any("dpkg-query" in cmd for cmd in calls)
    assert not any("docker compose version" in cmd for cmd in calls)


def test_docker_preflight_reports_conflicts_and_missing_ce(monkeypatch):
    def _run(_client, cmd, timeout=15):
        if "piherder_ok" in cmd:
            return 0, "piherder_ok\nLinux\n", ""
        if "os-release" in cmd:
            return 0, _release("ubuntu", "noble", "amd64")[0], ""
        if "print-architecture" in cmd:
            return 0, "amd64\n", ""
        if "dpkg-query -W -f='${Package}" in cmd:
            return 0, "docker.io\tii \n", ""
        if "docker-ce" in cmd:
            return 1, "", "missing"
        if cmd.strip() == "docker compose version":
            return 1, "", "missing"
        if cmd.strip() == "id -nG":
            return 0, "pi sudo\n", ""
        if cmd.strip() == "docker info":
            return 1, "", "denied"
        if cmd.strip() == "docker ps -q":
            return 0, "abc\n", ""
        if "command -v docker" in cmd or "docker version" in cmd:
            return 0, "/usr/bin/docker\n", ""
        return 0, "", ""

    monkeypatch.setattr(hd, "get_ssh_client", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(hd, "run_command", _run)
    monkeypatch.setattr("app.services.haos.probe_haos_identity", lambda _c: {"is_haos": False})
    server = SimpleNamespace(
        hostname="host-a.example.com",
        ssh_username="pi",
        os_type="debian",
        backup_enabled=False,
        container_patch_enabled=True,
        os_patch_enabled=False,
    )
    result = hd.run_host_deps_check(server)
    assert result["bootstrap"]["install_rsync"] is False
    assert result["bootstrap"]["install_docker_ce"] is True
    assert result["bootstrap"]["conflicting_packages"] == ["docker.io"]
    assert result["bootstrap"]["running_containers"] == 1
    assert result["bootstrap"]["needs_docker_group"] is True
    assert result["bootstrap"]["bootstrap_available"] is True
    assert any(row["id"] == "docker_conflicts" for row in result["checks"])


def test_script_preflight_gpg_remove_and_group():
    script = build_feature_bootstrap_script(
        "debian",
        "bookworm",
        "pi",
        install_rsync=True,
        install_docker_ce=True,
    )
    assert 'curl -fsSLI "https://download.docker.com/linux/${OS_ID}/dists/${CODENAME}/Release"' in script
    prep = script.index("apt-get install -y ca-certificates curl gnupg")
    suite = script.index('curl -fsSLI "https://download.docker.com/linux/${OS_ID}/dists/${CODENAME}/Release"')
    guarded = script.index("dpkg-query -W -f='${db:Status-Abbrev}'")
    assert prep < suite < guarded
    assert "for _pkg in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do" in script
    assert 'ii*) _conflict_pkgs=' in script
    assert "apt-get remove -y $_conflict_pkgs" in script
    assert "apt-get remove -y docker.io docker-compose docker-compose-v2" not in script
    assert "purge" not in script
    assert "/var/lib/docker" not in script
    assert "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" in script
    assert "gpg --batch --show-keys --with-colons" in script
    assert "GNUPGHOME=$(mktemp -d)" in script
    assert 'rm -rf "$GNUPGHOME"' in script
    assert suite < script.index("GNUPGHOME=$(mktemp -d)")
    assert "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin" in script
    assert 'usermod -aG docker "$TARGET_USER"' in script
    assert "get.docker.com" not in script
    assert "curl | sh" not in script
    assert "sudoers" not in script
    assert "DEBIAN_FRONTEND=noninteractive apt-get install -y rsync" in script
    with pytest.raises(ValueError):
        build_feature_bootstrap_script("alpine", "edge", "pi", install_rsync=True, install_docker_ce=False)
    with pytest.raises(ValueError):
        build_feature_bootstrap_script("debian", "bookworm;rm", "pi", install_rsync=True, install_docker_ce=False)
    rsync_only = build_feature_bootstrap_script(
        "raspbian", "bookworm", "pi", install_rsync=True, install_docker_ce=False
    )
    assert "docker-ce" not in rsync_only
    assert "https://download.docker.com" not in rsync_only


def test_build_host_bootstrap_script_follows_server_flags():
    backup_only = SimpleNamespace(
        hostname="host-a.example.com",
        ssh_username="pi",
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=False,
    )
    poisoned = _deps(install_rsync=True, install_docker=True, conflicts=["docker.io"], running=2)
    script = onboard.build_host_bootstrap_script(
        backup_only, "debian", "bookworm", deps=poisoned
    )
    assert "apt-get install -y rsync" in script
    assert "docker-ce" not in script
    assert "download.docker.com" not in script
    docker_on = SimpleNamespace(
        hostname="host-b.example.com",
        ssh_username="pi",
        os_type="debian",
        backup_enabled=False,
        container_patch_enabled=True,
    )
    docker_script = onboard.build_host_bootstrap_script(
        docker_on, "ubuntu", "noble", deps=_deps(install_docker=True)
    )
    assert "docker-ce" in docker_script
    assert "apt-get install -y rsync" not in docker_script
    with pytest.raises(TypeError):
        onboard.build_host_bootstrap_script(
            backup_only,
            "debian",
            "bookworm",
            deps=poisoned,
            install_docker_ce=True,
        )


class _Chan:
    def shutdown_write(self):
        return None

    def recv_exit_status(self):
        return 0


class _IO:
    def __init__(self, text: str = ""):
        self.channel = _Chan()
        self.buf = ""
        self._text = text

    def write(self, data):
        self.buf += data

    def flush(self):
        return None

    def read(self):
        return self._text.encode()


class _Client:
    def __init__(self):
        self.closed = False
        self.commands = []
        self.stdin = None

    def exec_command(self, cmd, timeout=None):
        del timeout
        self.commands.append(cmd)
        stdin = _IO()
        self.stdin = stdin
        text = "1000\n" if cmd.strip() == "id -u" else "ok\n"
        return stdin, _IO(text), _IO("")

    def close(self):
        self.closed = True


def _deps(*, install_rsync=False, install_docker=False, conflicts=None, running=0):
    return {
        "os": {
            "os_id": "debian",
            "codename": "bookworm",
            "pretty_name": "Debian",
            "arch": "arm64",
            "docker_repo_url": "https://download.docker.com/linux/debian",
            "supported_os": True,
        },
        "bootstrap": {
            "install_rsync": install_rsync,
            "install_docker_ce": install_docker,
            "remove_docker_conflicts": bool(conflicts),
            "conflicting_packages": list(conflicts or []),
            "running_containers": running,
            "needs_docker_group": install_docker,
            "bootstrap_available": install_rsync or install_docker,
        },
    }


def test_backup_only_refuses_docker_even_if_status_asks_for_it(monkeypatch):
    seen = {"script": "", "opens": 0}

    def _open(_server, identity=None):
        del identity
        seen["opens"] += 1
        return _Client()

    def _check(_session, _server):
        return _deps(install_rsync=True, install_docker=True, conflicts=["docker.io"], running=2)

    monkeypatch.setattr(onboard.ssh_service, "get_ssh_client", _open)
    monkeypatch.setattr(onboard, "check_and_persist", _check, raising=False)
    monkeypatch.setattr("app.services.host_deps.check_and_persist", _check)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    def _exec(client, script, sudo_password, timeout=300):
        del timeout
        seen["script"] = script
        assert sudo_password == "one-time-secret"
        assert "one-time-secret" not in script
        client.close()
        return 0, "ok", ""

    monkeypatch.setattr(onboard, "_exec_elevated", _exec)
    server = SimpleNamespace(
        id=3,
        hostname="host-a.example.com",
        ssh_username="pi",
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=False,
    )
    result = onboard.bootstrap_host_tools(
        server,
        MagicMock(),
        confirm_docker_bootstrap=True,
        sudo_password="one-time-secret",
    )
    assert result.ok is True
    assert result.details["features"] == ["rsync"]
    assert "docker-ce" not in seen["script"]
    assert "apt-get install -y rsync" in seen["script"]
    assert "one-time-secret" not in result.message
    assert seen["opens"] == 1


def test_elevated_sudo_uses_dash_h(monkeypatch):
    def _run(_client, cmd, timeout=15):
        del timeout
        if (cmd or "").strip() == "id -u":
            return 0, "1000\n", ""
        if (cmd or "").strip() == "sudo -H -n true":
            return 1, "", "a password is required"
        return 0, "", ""

    monkeypatch.setattr(onboard.ssh_service, "run_command", _run)
    plain = _Client()
    rc, _out, _err = onboard._exec_elevated(plain, "echo hi\n", None)
    assert rc == 0
    assert plain.commands == ["sudo -H -n bash -s"]
    with_password = _Client()
    rc, out, err = onboard._exec_elevated(with_password, "echo hi\n", "one-time-secret")
    assert rc == 0
    assert with_password.commands == ["sudo -H -S -p '' bash -s"]
    assert with_password.stdin.buf.startswith("one-time-secret\n")
    assert "one-time-secret" not in out
    assert "one-time-secret" not in err


def test_docker_confirm_and_fresh_ssh(monkeypatch):
    clients = []

    def _open(_server, identity=None):
        del identity
        if clients:
            assert clients[0].closed is True
        client = _Client()
        clients.append(client)
        return client

    def _run(client, cmd, timeout=15):
        del client, timeout
        if cmd.strip() == "id -u":
            return 0, "1000\n", ""
        if cmd.strip() == "sudo -H -n true":
            return 1, "", "a password is required"
        if cmd.strip() in {"docker info", "docker compose version"}:
            return 0, "ok\n", ""
        return 0, "", ""

    checks = {"n": 0}

    def _check(_session, _server):
        checks["n"] += 1
        return _deps(install_docker=True, conflicts=["docker.io"], running=1)

    monkeypatch.setattr(onboard.ssh_service, "get_ssh_client", _open)
    monkeypatch.setattr(onboard.ssh_service, "run_command", _run)
    monkeypatch.setattr("app.services.host_deps.check_and_persist", _check)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    server = SimpleNamespace(
        id=4,
        hostname="host-b.example.com",
        ssh_username="pi",
        os_type="debian",
        backup_enabled=False,
        container_patch_enabled=True,
    )
    refused = onboard.bootstrap_host_tools(server, MagicMock(), confirm_docker_bootstrap=False)
    assert refused.ok is False
    assert refused.details.get("need_confirm") is True
    assert "root-equivalent" in refused.message
    assert "docker.io" in refused.message or "replaced" in refused.message
    assert clients == []

    done = onboard.bootstrap_host_tools(
        server,
        MagicMock(),
        confirm_docker_bootstrap=True,
        sudo_password="one-time-secret",
    )
    assert done.ok is True
    assert done.details["features"] == ["docker_ce"]
    assert len(clients) == 2
    assert clients[0].closed is True
    assert clients[1].closed is True
    elevated = [cmd for cmd in clients[0].commands if "bash -s" in cmd]
    assert elevated == ["sudo -H -S -p '' bash -s"]
    stdin = clients[0].stdin.buf
    assert stdin.startswith("one-time-secret\n")
    assert "one-time-secret" not in stdin.split("\n", 1)[1]
    assert "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" in stdin
    assert "apt-get remove -y" in stdin
    assert "purge" not in stdin
    assert checks["n"] == 3


def test_bootstrap_route_audits_without_the_password(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    captured = {}

    def _boot(server, session, *, confirm_docker_bootstrap=False, sudo_password=None, bootstrap_username=None):
        del server, session
        captured["confirm"] = confirm_docker_bootstrap
        captured["password"] = sudo_password
        captured["bootstrap_username"] = bootstrap_username
        captured["keys"] = set()
        return onboard.OnboardingResult(
            ok=True,
            message="Bootstrap finished",
            details={"os_id": "ubuntu", "codename": "noble", "features": ["rsync"]},
        )

    monkeypatch.setattr(onboard, "bootstrap_host_tools", _boot)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = User(
                email="boot@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="operator",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            viewer = User(
                email="view@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="viewer",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            host = Server(
                name="host-a",
                hostname="host-a.example.com",
                ssh_username="pi",
                backup_enabled=True,
                container_patch_enabled=False,
            )
            session.add(user)
            session.add(viewer)
            session.add(host)
            session.commit()
            session.refresh(user)
            session.refresh(viewer)
            session.refresh(host)
            sid = host.id
            op_cookie = create_user_access_token(user)
            view_cookie = create_user_access_token(viewer)
        paths = set((app.openapi().get("paths") or {}))
        assert "/servers/{server_id}/host-deps/bootstrap" in paths
        assert "/servers/{server_id}/ssh/bootstrap-tools" not in paths
        assert not any(path.startswith("/api/") and "bootstrap" in path for path in paths)
        assert not any(path.startswith("/mcp") and "bootstrap" in path for path in paths)

        client.cookies.set("access_token", view_cookie)
        denied = client.post(
            f"/servers/{sid}/host-deps/bootstrap",
            data={"install_docker_ce": "on", "sudo_password": "one-time-secret"},
            follow_redirects=False,
        )
        assert denied.status_code == 403

        client.cookies.set("access_token", op_cookie)
        ok = client.post(
            f"/servers/{sid}/host-deps/bootstrap",
            data={
                "install_docker_ce": "on",
                "install_rsync": "on",
                "sudo_password": "one-time-secret",
            },
            follow_redirects=False,
        )
        assert ok.status_code == 303
        assert "show_ssh_key=1" in ok.headers["location"]
        assert "bootstrap_queued" in ok.headers["location"]
        assert "bootstrap_job=" in ok.headers["location"]
        assert captured["confirm"] is False
        assert captured["password"] == "one-time-secret"
        assert "install_docker_ce" not in captured
        with Session(engine) as session:
            rows = list(session.exec(select(AuditLog).where(AuditLog.action == "host_bootstrap")).all())
        assert len(rows) == 1
        blob = (rows[0].details or "") + (rows[0].output_snippet or "")
        assert "one-time-secret" not in blob
        assert "rsync" in blob
        assert "ubuntu" in blob
        with Session(engine) as session:
            jobs = list(session.exec(select(Job).where(Job.job_type == "host_bootstrap")).all())
        assert len(jobs) == 1
        assert "one-time-secret" not in (jobs[0].details or "")
        assert "sudo_password" not in (jobs[0].details or "")
    finally:
        app.dependency_overrides.clear()


def test_elevated_nopasswd_does_not_feed_bash(monkeypatch):
    def _run(_client, cmd, timeout=15):
        del timeout
        if (cmd or "").strip() == "id -u":
            return 0, "1000\n", ""
        if (cmd or "").strip() == "sudo -H -n true":
            return 0, "", ""
        return 1, "", ""

    monkeypatch.setattr(onboard.ssh_service, "run_command", _run)
    client = _Client()
    rc, out, err = onboard._exec_elevated(client, "echo hi\n", "one-time-secret")
    assert rc == 0
    assert client.commands == ["sudo -H -n bash -s"]
    assert "one-time-secret" not in (client.stdin.buf or "")
    assert "one-time-secret" not in out
    assert "one-time-secret" not in err


def test_bootstrap_username_configures_fleet_user(monkeypatch):
    seen = {}

    def _connect(server, session, ssh_user, sudo_password):
        del server, session
        seen["user"] = ssh_user
        seen["password"] = sudo_password
        return _Client()

    def _exec(client, script, sudo_password, timeout=300):
        del sudo_password, timeout
        seen["script"] = script
        client.close()
        return 0, "ok", ""

    monkeypatch.setattr(onboard, "_connect_bootstrap_client", _connect)
    monkeypatch.setattr(onboard, "_exec_elevated", _exec)
    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist",
        lambda *_a, **_k: _deps(install_rsync=True),
    )
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    server = SimpleNamespace(
        id=9,
        hostname="host-a.example.com",
        ssh_username="piherder",
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=False,
    )
    result = onboard.bootstrap_host_tools(
        server,
        MagicMock(),
        sudo_password="one-time-secret",
        bootstrap_username="pi",
    )
    assert result.ok is True
    assert seen["user"] == "pi"
    assert seen["password"] == "one-time-secret"
    assert "TARGET_USER=piherder" in seen["script"]
    assert result.details["bootstrap_username"] == "pi"
    assert "one-time-secret" not in result.message


def test_other_user_without_password_asks_for_one(monkeypatch):
    monkeypatch.setattr(onboard, "_bootstrap_private_key", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.host_deps.check_and_persist",
        lambda *_a, **_k: _deps(install_rsync=True),
    )
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    server = SimpleNamespace(
        id=10,
        hostname="host-a.example.com",
        ssh_username="piherder",
        os_type="debian",
        backup_enabled=True,
        container_patch_enabled=False,
    )
    result = onboard.bootstrap_host_tools(server, MagicMock(), bootstrap_username="pi")
    assert result.ok is False
    assert "Enter the SSH and sudo password for pi" in result.message


def test_host_bootstrap_job_skips_ssh_wait(monkeypatch):
    from app.services import jobs_exclusive as exclusive

    engine = _engine()
    monkeypatch.setattr("app.database.engine", engine)
    with Session(engine) as session:
        host = Server(name="host-a", hostname="host-a.example.com", ssh_username="piherder")
        session.add(host)
        session.commit()
        session.refresh(host)
        job = Job(server_id=host.id, job_type="host_bootstrap", status="pending", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid, sid = job.id, host.id
    monkeypatch.setattr(exclusive, "_ssh_ready", lambda _sid: False)

    def _wait(*_a, **_k):
        raise AssertionError("host_bootstrap waited for fleet SSH")

    seen = {}
    monkeypatch.setattr(exclusive, "_wait_for_host", _wait)
    monkeypatch.setattr(
        exclusive,
        "_execute",
        lambda job_type, job_id, server_id, audit_id, payload: seen.update(
            job_type=job_type, payload=payload
        ),
    )

    class _Task:
        request = SimpleNamespace(id="task-1")

        def retry(self, countdown=0):
            raise AssertionError("retried")

    out = exclusive.run_exclusive_job(
        _Task(), jid, sid, 0, "host_bootstrap", {"bootstrap_username": "pi"}
    )
    assert out["status"] == "ok"
    assert seen["job_type"] == "host_bootstrap"
    assert seen["payload"]["bootstrap_username"] == "pi"
    assert "host_bootstrap" in exclusive.EXCLUSIVE_CELERY_TYPES


def test_bootstrap_async_job_and_modal_stay_open(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session

    def _boot(server, session, *, confirm_docker_bootstrap=False, sudo_password=None, bootstrap_username=None):
        del server, session, confirm_docker_bootstrap
        assert sudo_password == "one-time-secret"
        assert bootstrap_username == "pi"
        return onboard.OnboardingResult(
            ok=False,
            message="login failed",
            details={"os_id": "debian", "codename": "bookworm", "features": [], "bootstrap_username": "pi"},
        )

    monkeypatch.setattr(onboard, "bootstrap_host_tools", _boot)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = User(
                email="boot2@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="operator",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            host = Server(
                name="host-b",
                hostname="host-b.example.com",
                ssh_username="piherder",
                ssh_public_key=None,
                backup_enabled=True,
                container_patch_enabled=False,
                host_deps_json=(
                    '{"os":{"os_id":"debian","supported_os":true},'
                    '"bootstrap":{"bootstrap_available":true,"install_rsync":true},'
                    '"checks":[{"status":"fail","label":"rsync","message":"missing"}]}'
                ),
            )
            session.add(user)
            session.add(host)
            session.commit()
            session.refresh(user)
            session.refresh(host)
            sid = host.id
            cookie = create_user_access_token(user)
        client.cookies.set("access_token", cookie)
        bad_user = client.post(
            f"/servers/{sid}/host-deps/bootstrap",
            data={"bootstrap_username": "bad user"},
            headers={"X-PiHerder-Async": "1"},
        )
        assert bad_user.status_code == 400
        assert "Invalid bootstrap SSH username" in bad_user.text

        queued = client.post(
            f"/servers/{sid}/host-deps/bootstrap",
            data={"bootstrap_username": "pi", "sudo_password": "one-time-secret"},
            headers={"X-PiHerder-Async": "1"},
        )
        assert queued.status_code == 200
        body = queued.json()
        assert body["job_type"] == "host_bootstrap"
        assert body["job_id"]
        assert body["poll_url"].endswith(f"/jobs/{body['job_id']}")
        with Session(engine) as session:
            job = session.get(Job, body["job_id"])
            assert job is not None
            assert job.status == "failed"
            blob = job.details or ""
            assert "one-time-secret" not in blob
            assert "sudo_password" not in blob
            rows = list(session.exec(select(AuditLog).where(AuditLog.action == "host_bootstrap")).all())
        audit_blob = "".join((row.details or "") + (row.output_snippet or "") for row in rows)
        assert "one-time-secret" not in audit_blob
        assert "login failed" in audit_blob

        page = client.get(f"/servers/{sid}?show_ssh_key=1&error=bootstrap_fail&detail=bad-login")
        assert page.status_code == 200
        html = page.text
        assert "showSshAccessModal()" in html
        assert 'data-testid="host-bootstrap-username"' in html
        assert 'data-testid="host-bootstrap-job-panel"' in html
        assert "bad-login" in html
        card = html.split('data-testid="host-bootstrap-card"', 1)[1].split("</form>", 1)[0]
        assert "bad-login" in card
        form = html.split('data-testid="host-bootstrap-form"', 1)[1].split("</form>", 1)[0]
        assert "data-wait-title" not in form
    finally:
        app.dependency_overrides.clear()
