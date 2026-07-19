"""Tests for in-app API.md rendering helper."""
from __future__ import annotations

from app.services.markdown_lite import markdown_to_html


def test_markdown_headings_and_code():
    md = "# Title\n\n## Section\n\n```bash\ncurl -H 'x'\n```\n"
    html = markdown_to_html(md)
    assert "<h1" in html and "Title" in html
    assert "<h2" in html and "Section" in html
    assert "<pre" in html and "curl" in html


def test_markdown_table_and_inline():
    md = "| A | B |\n|---|---|\n| **x** | `y` |\n"
    html = markdown_to_html(md)
    assert "<table" in html
    assert "<strong>x</strong>" in html
    assert "<code" in html and "y" in html


def test_markdown_escapes_html():
    html = markdown_to_html("Hello <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_lists_and_links():
    md = "- one\n- two\n\nSee [docs](https://example.com/a).\n"
    html = markdown_to_html(md)
    assert "<li" in html
    assert 'href="https://example.com/a"' in html
    assert "docs" in html


def test_load_repo_markdown_missing_and_found(tmp_path, monkeypatch):
    from app.services import markdown_lite as ml

    missing = ml.load_repo_markdown("docs/definitely-not-there-xyz.md")
    assert "missing" in missing.lower() or "Could not load" in missing

    f = tmp_path / "sample.md"
    f.write_text("# Hello\n\nBody.\n", encoding="utf-8")
    # Point candidates at tmp by monkeypatching Path resolution via relative open
    text = ml.load_repo_markdown(str(f))
    assert "Hello" in text
