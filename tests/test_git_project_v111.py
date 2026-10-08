"""Git-rich onboard: parse a host report and refuse a bad remote or ref."""
from app.services.git_project import (
    GitProjectError,
    parse_git_report,
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


def test_update_requires_a_branch_or_tag():
    try:
        update_project(None, "hailo-frigate-standalone")
        raise AssertionError("empty target accepted")
    except GitProjectError as exc:
        assert "branch or a tag" in str(exc)
