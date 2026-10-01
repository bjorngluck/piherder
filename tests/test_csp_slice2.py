"""CSP Slice 2: no product inline handlers; script nonces stay; attr policy is none."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.templates import templates

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
JS = ROOT / "app" / "static" / "js" / "csp-events.js"
HANDLER = re.compile(r"(?<![\w.])(onclick|onchange|onsubmit|onerror)\s*=\s*")


def _iter_html_handlers(text: str):
    for m in HANDLER.finditer(text):
        qpos = m.end()
        if qpos >= len(text) or text[qpos] not in "\"'":
            continue
        yield m.group(1)


def _allowlist() -> set[str]:
    raw = JS.read_text(encoding="utf-8")
    block = re.search(r"var ALLOW = \{(.+?)\n  \};", raw, re.S)
    assert block, "csp-events.js ALLOW block missing"
    return set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:", block.group(1)))


def test_templates_have_no_inline_event_handlers():
    hits = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for name in _iter_html_handlers(text):
            hits.append(f"{path.relative_to(ROOT)}:{name}")
    theme = ROOT / "app" / "static" / "theme-test.html"
    for name in _iter_html_handlers(theme.read_text(encoding="utf-8")):
        hits.append(f"app/static/theme-test.html:{name}")
    assert hits == []


def test_csp_events_has_no_eval_and_covers_template_calls():
    raw = JS.read_text(encoding="utf-8")
    assert "eval(" not in raw
    assert "new Function" not in raw
    allow = _allowlist()
    used = set()
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        used.update(re.findall(r'data-ph-(?:click|change|backdrop-call)="([^"]+)"', text))
        for blob in re.findall(r"data-ph-seq='(\[.*?\])'", text):
            for step in json.loads(blob):
                used.add(step["fn"])
    missing = sorted(used - allow)
    assert missing == [], missing


def test_product_templates_parse():
    for path in TEMPLATES.rglob("*.html"):
        rel = path.relative_to(TEMPLATES).as_posix()
        templates.env.get_template(rel)


def test_backup_stop_payload_is_json():
    src = (
        '{{ {"activePollJobId": running_backup_job.id, '
        '"activePollSource": (rj.source_filter or "")} | tojson }}'
    )
    out = templates.env.from_string(src).render(
        running_backup_job=type("J", (), {"id": 7})(),
        rj=type("R", (), {"source_filter": "proj/a"})(),
    )
    assert json.loads(out) == {"activePollJobId": 7, "activePollSource": "proj/a"}


def test_static_csp_events_is_served():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/static/js/csp-events.js")
    assert res.status_code == 200
    assert "data-ph-click" in res.text
    assert "script-src-attr" not in res.text
