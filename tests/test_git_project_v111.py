"""Git-rich onboard: parse a host report and refuse a bad remote or ref."""
import os
import subprocess

from app.services.git_project import (
    GitProjectError,
    _inspect_script,
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
    assert "safe.directory='*'" not in script
    assert "safe.directory=/tmp/proj" in script
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


def test_update_requires_a_branch_or_tag():
    try:
        update_project(None, "hailo-frigate-standalone")
        raise AssertionError("empty target accepted")
    except GitProjectError as exc:
        assert "branch or a tag" in str(exc)
