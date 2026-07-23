"""E11 partial: OOTB vs operator source badges (pure)."""
from __future__ import annotations

from app.services.service_templates.catalog import is_ootb_source, source_badge


def test_source_badge_ootb():
    for src in ("builtin", "starter", "Builtin"):
        b = source_badge(src)
        assert b["kind"] == "ootb"
        assert b["label"] == "OOTB"
        assert b["cls"] == "status-running"
        assert "PiHerder" in b["title"]
        assert is_ootb_source(src)


def test_source_badge_user_owned():
    b = source_badge("user")
    assert b["kind"] == "user"
    assert b["label"] == "Yours"
    assert b["cls"] == "feature-off"
    assert not is_ootb_source("user")

    imp = source_badge("import")
    assert imp["label"] == "Imported"
    assert imp["kind"] == "user"

    git = source_badge("git")
    assert git["label"] == "Git"
    assert git["kind"] == "user"


def test_source_badge_defaults_unknown():
    b = source_badge(None)
    assert b["kind"] == "user"
    assert b["label"] == "Yours"
    assert not is_ootb_source("")
    assert not is_ootb_source(None)
