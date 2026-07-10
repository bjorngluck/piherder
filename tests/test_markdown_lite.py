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
