from app.services.backup_path_policy import (
    validate_backup_path,
    filter_allowed_sources,
    parse_rules,
    rules_to_json,
    normalize_path,
)


def test_default_allows_home_docker():
    ok, reason = validate_backup_path("/home/bjorn/docker/")
    assert ok, reason


def test_default_denies_etc_and_root():
    ok, reason = validate_backup_path("/etc/passwd")
    assert not ok
    assert "Deny" in reason or "deny" in reason.lower() or "Denied" in reason
    ok2, _ = validate_backup_path("/")
    assert not ok2


def test_relative_rejected():
    ok, reason = validate_backup_path("relative/path")
    assert not ok
    assert "absolute" in reason.lower()


def test_allow_list_restricts():
    rules = {"allow": ["/home/bjorn"], "deny": []}
    ok, _ = validate_backup_path("/home/bjorn/docker", rules)
    assert ok
    ok2, reason = validate_backup_path("/var/lib/docker/volumes", rules)
    assert not ok2
    assert "allow" in reason.lower()


def test_custom_deny_wins():
    rules = {"allow": ["/home"], "deny": ["/home/bjorn/secret"]}
    ok, reason = validate_backup_path("/home/bjorn/secret/data", rules)
    assert not ok
    assert "Denied" in reason


def test_allow_overrides_default_deny_for_etc():
    rules = {"allow": ["/etc/piherder"], "deny": []}
    ok, reason = validate_backup_path("/etc/piherder/config", rules)
    assert ok, reason


def test_filter_allowed_sources():
    sources = [
        {"source": "/home/bjorn/docker", "enabled": True},
        {"source": "/etc", "enabled": True},
    ]
    ok, bad = filter_allowed_sources(sources, None)
    assert len(ok) == 1
    assert len(bad) == 1
    assert bad[0].get("error")


def test_rules_roundtrip():
    raw = rules_to_json(["/home", "/var/lib/docker"], ["/tmp"])
    parsed = parse_rules(raw)
    assert "/home" in parsed["allow"] or normalize_path("/home") in parsed["allow"]
    assert any("tmp" in d for d in parsed["deny"])
