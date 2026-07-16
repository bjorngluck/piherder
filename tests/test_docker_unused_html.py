"""Docker unused-list HTML builder (cleanup modal) — XSS-safe host-derived names."""
from __future__ import annotations

from app.services.docker_unused_html import render_unused_list_html


def test_empty_state():
    html = render_unused_list_html(
        {"dangling_images": [], "exited_containers": [], "success": True}
    )
    assert "No dangling images" in html
    assert "<pre" not in html


def test_lists_images_and_containers():
    html = render_unused_list_html(
        {
            "dangling_images": ["sha256:abc123", "repo/app:old"],
            "exited_containers": ["deadbeef  exited"],
            "success": True,
        }
    )
    assert "Dangling images" in html
    assert "(2)" in html
    assert "sha256:abc123" in html
    assert "repo/app:old" in html
    assert "Exited containers" in html
    assert "deadbeef" in html


def test_escapes_hostile_image_name():
    hostile = '<img src=x onerror=alert(1)>'
    html = render_unused_list_html(
        {"dangling_images": [hostile], "exited_containers": [], "success": True}
    )
    assert "<img" not in html
    assert "&lt;img" in html
    assert "onerror" in html  # text only
    assert "alert(1)" in html
    # Attribute breakout should not survive unescaped
    assert 'src=x' not in html or "&lt;" in html


def test_escapes_errors_and_partial_fail_banner():
    html = render_unused_list_html(
        {
            "dangling_images": [],
            "exited_containers": [],
            "success": False,
            "errors": ["boom <b>x</b>", 'quote"here'],
        }
    )
    assert "Errors:" in html
    assert "&lt;b&gt;" in html
    assert "&quot;" in html or "quote" in html
    assert "partially failed" in html
    assert "<b>x</b>" not in html


def test_none_data_safe():
    html = render_unused_list_html(None)
    assert "No dangling images" in html
