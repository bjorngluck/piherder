"""Settings copy: backup tab keeps two words, Status names hosted MCP."""
from __future__ import annotations

from pathlib import Path

from app.templates import templates

ROOT = Path(__file__).resolve().parents[1]


def test_backup_tab_uses_a_non_breaking_space():
    """The tab is an inline-flex button, which drops a normal space."""
    src = (ROOT / "app/templates/herder_backups.html").read_text()
    assert "{{ ph_brand() }}&nbsp;backup</button>" in src
    html = templates.env.from_string(
        '<button class="btn">{{ ph_brand() }}&nbsp;backup</button>'
    ).render()
    assert 'aria-label="PiHerder"' in html
    assert "&nbsp;backup" in html
    assert "> backup<" not in html


def test_status_web_card_names_hosted_mcp():
    html = templates.env.get_template("partials/settings_status.html").render(
        is_admin=True,
        tab="status",
        stack_report={
            "overall": "ok",
            "checked_at": "2026-10-09T00:00:00Z",
            "components": [
                {
                    "id": "web",
                    "label": "Web (FastAPI)",
                    "status": "ok",
                    "message": "process answering",
                    "detail": {},
                },
            ],
        },
    )
    assert "Hosted MCP is this same process, at" in html
    assert "POST /mcp" in html
    assert "process answering" in html
