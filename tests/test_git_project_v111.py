"""Git-rich onboard: parse a host report and refuse a bad remote or ref."""
import os
import subprocess
from pathlib import Path

from types import SimpleNamespace

from app.services.git_project import (
    GitProjectError,
    _attach_script,
    _inspect_script,
    _update_script,
    drift_from_report,
    drift_script,
    parse_git_report,
    redact_git_url,
    update_project,
    validate_ref,
    validate_url,
)


def test_parse_marks_a_newer_tag_and_local_files():
    report = parse_git_report(
        "\n".join(
            [
                "repo=1",
                "url=https://github.com/msorenss/hailo-frigate-standalone.git",
                "branch=main",
                "head=a53d7b2",
                "default=main",
                "behind=4",
                "ahead=0",
                "dirty=compose.yaml",
                "dirty=config/frigate/config.yml.example",
                "refbranch=main",
                "refbranch=HEAD",
                "tag=v0.18.0",
                "tag=0.18.0-5.40 newer",
                "fetch=ok",
            ]
        )
    )
    assert report["repo"] is True
    assert report["behind"] == 4
    assert report["dirty"] == [
        "compose.yaml",
        "config/frigate/config.yml.example",
    ]
    assert "HEAD" not in report["branches"]
    newer = [row["name"] for row in report["tags"] if row["newer"]]
    assert newer == ["0.18.0-5.40"]


def test_url_and_ref_rules():
    assert validate_url("https://github.com/bjorngluck/piherder.git").startswith("https://")
    assert validate_url("git@github.com:bjorngluck/piherder.git")
    assert validate_url("ssh://git@github.com/bjorngluck/piherder.git")
    try:
        validate_url("https://user:token@github.com/bjorngluck/piherder.git")
        raise AssertionError("https userinfo accepted")
    except GitProjectError as exc:
        assert "credential helper" in str(exc)
        assert "user:token" not in str(exc)
    try:
        validate_url("https://example.com/repo.git;touch /tmp/x")
        raise AssertionError("shell suffix accepted")
    except GitProjectError:
        pass
    try:
        validate_ref("main;rm")
        raise AssertionError("bad ref accepted")
    except GitProjectError:
        pass
    assert validate_ref("release/frigate-0.18.0") == "release/frigate-0.18.0"


def test_display_hides_a_token_in_an_existing_remote():
    shown = redact_git_url("https://builder:ghp_secret@github.com/acme/app.git")
    assert "ghp_secret" not in shown
    assert shown == "https://github.com/acme/app.git"
    report = parse_git_report("url=https://builder:ghp_secret@github.com/acme/app.git")
    assert report["url"] == "https://github.com/acme/app.git"
    assert redact_git_url("ssh://git@github.com/acme/app.git") == "ssh://git@github.com/acme/app.git"


def test_inspect_script_trusts_only_the_project_folder():
    script = _inspect_script("/tmp/proj")
    assert "safe.directory" not in script
    assert "result=unowned" in script
    assert "core.hooksPath=/dev/null" in script
    assert "core.sshCommand=ssh" in script
    assert "^{{commit}}" not in script
    assert "${{default" not in script
    assert 'name^{commit}' in script


def test_inspect_script_marks_a_newer_tag(tmp_path):
    repo = tmp_path / "proj"
    bare = tmp_path / "origin.git"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "PiHerder",
        "GIT_AUTHOR_EMAIL": "piherder@example.com",
        "GIT_COMMITTER_NAME": "PiHerder",
        "GIT_COMMITTER_EMAIL": "piherder@example.com",
    }

    def git(*args, cwd=repo):
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )

    repo.mkdir()
    git("init", "-b", "main")
    (repo / "readme").write_text("v1\n")
    git("add", "readme")
    git("commit", "-m", "v1")
    git("tag", "v1")
    (repo / "readme").write_text("v2\n")
    git("add", "readme")
    git("commit", "-m", "v2")
    git("tag", "v2")
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    git("remote", "add", "origin", str(bare))
    git("push", "-u", "origin", "main", "--tags")
    git("remote", "set-head", "origin", "-a")
    git("checkout", "--detach", "v1")

    script = _inspect_script(str(repo))
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    report = parse_git_report(proc.stdout)
    assert report["default_branch"] == "main"
    newer = [row["name"] for row in report["tags"] if row["newer"]]
    assert newer == ["v2"]
    assert report["fetch_ok"] is True

    dash = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
    assert dash.returncode == 0, dash.stderr
    assert parse_git_report(dash.stdout)["default_branch"] == "main"


def test_unowned_checkout_is_refused(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "id").write_text("#!/bin/sh\necho 1000\n")
    (bindir / "stat").write_text("#!/bin/sh\necho 999\n")
    os.chmod(bindir / "id", 0o755)
    os.chmod(bindir / "stat", 0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    scripts = [
        _inspect_script(str(repo)),
        _attach_script(str(repo), "https://github.com/acme/app.git"),
        _update_script(str(repo), "branch", "main", "stop"),
        drift_script(str(repo)),
    ]
    for script in scripts:
        assert "safe.directory" not in script
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        assert parse_git_report(proc.stdout)["result"] == "unowned"
    template = Path(__file__).resolve().parents[1] / "app/templates/docker_repository.html"
    assert "not owned by the SSH user" in template.read_text()


def test_drift_from_report_counts_behind_ahead_and_tracked_edits():
    behind = drift_from_report(
        parse_git_report("repo=1\nbranch=main\nbehind=2\nahead=0\n")
    )
    assert behind["git_drift"] is True
    assert behind["git_behind"] == 2
    clean = drift_from_report(
        parse_git_report("repo=1\nbranch=main\nbehind=0\nahead=0\n")
    )
    assert clean["git_drift"] is False
    assert clean["git_dirty"] == 0
    edited = drift_from_report(
        parse_git_report("repo=1\nbranch=main\ndirty=compose.yaml\n")
    )
    assert edited["git_drift"] is True
    assert edited["git_dirty"] == 1
    assert drift_from_report(parse_git_report("repo=0\n")) == {}
    assert drift_from_report(parse_git_report("result=unowned\n")) == {}
    assert drift_from_report(parse_git_report("result=missing\n")) == {}


def test_drift_script_does_not_fetch_or_change_the_tree(tmp_path):
    script = drift_script(str(tmp_path / "app"))
    assert "git fetch" not in script
    assert "reset" not in script
    assert "checkout" not in script
    assert "compose" not in script
    assert "result=unowned" in script
    assert "'??')" in script

    repo = tmp_path / "app"
    bare = tmp_path / "origin.git"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "PiHerder",
        "GIT_AUTHOR_EMAIL": "piherder@example.com",
        "GIT_COMMITTER_NAME": "PiHerder",
        "GIT_COMMITTER_EMAIL": "piherder@example.com",
    }

    def git(*args, cwd=repo):
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )

    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    repo.mkdir()
    git("init", "-b", "main")
    (repo / "readme").write_text("v1\n")
    git("add", "readme")
    git("commit", "-m", "v1")
    git("remote", "add", "origin", str(bare))
    git("push", "-u", "origin", "main")
    (repo / "readme").write_text("v2\n")
    git("add", "readme")
    git("commit", "-m", "v2")
    (repo / "compose.yaml").write_text("services: {}\n")
    git("add", "compose.yaml")
    (repo / "notes.txt").write_text("local only\n")

    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    fields = drift_from_report(parse_git_report(proc.stdout))
    assert fields["git_drift"] is True
    assert fields["git_ahead"] == 1
    assert fields["git_behind"] == 0
    assert fields["git_dirty"] == 1
    assert "notes.txt" not in proc.stdout
    assert "git fetch" not in proc.stdout


def test_compose_list_stores_git_drift_and_survives_a_probe_error(monkeypatch):
    from app.services import docker_management as dm

    commands = []

    def run(_client, cmd, timeout=15):
        commands.append(cmd)
        if "core.hooksPath" in cmd:
            if commands.count(cmd) > 1 or getattr(run, "fail", False):
                raise TimeoutError("probe failed")
            return 0, "repo=1\nbranch=main\nbehind=2\ndirty=compose.yaml\n", ""
        if "find " in cmd:
            return 0, "/srv/app/docker-compose.yml\n", ""
        if "docker compose ps" in cmd:
            return 0, "", ""
        if "ls -1" in cmd:
            return 0, "docker-compose.yml\n", ""
        if "cat " in cmd:
            return 0, "services:\n  web:\n    image: nginx:1\n", ""
        return 0, "", ""

    class Client:
        def close(self):
            return None

    monkeypatch.setattr(dm, "get_ssh_client", lambda *a, **k: Client())
    monkeypatch.setattr(dm, "run_command", run)
    server = SimpleNamespace(id=1)
    rows = dm._list_compose_uncached(server, "/srv", light=False)
    assert rows[0]["name"] == "app"
    assert rows[0]["git_drift"] is True
    assert rows[0]["git_behind"] == 2
    assert rows[0]["git_dirty"] == 1
    git_cmds = [cmd for cmd in commands if "core.hooksPath" in cmd]
    assert git_cmds
    assert "git fetch" not in git_cmds[0]
    assert "compose up" not in git_cmds[0]

    commands.clear()
    run.fail = True
    rows = dm._list_compose_uncached(server, "/srv", light=False)
    assert rows[0]["name"] == "app"
    assert "git_drift" not in rows[0]


def test_update_requires_a_branch_or_tag():
    try:
        update_project(None, "hailo-frigate-standalone")
        raise AssertionError("empty target accepted")
    except GitProjectError as exc:
        assert "branch or a tag" in str(exc)
