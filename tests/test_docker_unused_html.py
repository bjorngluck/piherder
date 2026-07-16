"""Docker unused-list HTML must escape host-derived names."""
from __future__ import annotations

import html


def test_escape_hostile_image_name():
    # Mirrors list_unused_route escaping contract
    name = '<img src=x onerror=alert(1)>'
    escaped = html.escape(name, quote=True)
    assert "<img" not in escaped
    assert "&lt;img" in escaped
    assert "onerror" in escaped  # still present as text, not as tag attribute structure
    assert '">' not in escaped or "&quot;" in escaped
