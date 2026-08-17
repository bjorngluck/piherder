"""Pure helpers for console mobile soft-Tab / IME (v12)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "console-soft-tab.js"


def _node(expr: str):
    if not shutil.which("node"):
        pytest.skip("node not available")
    script = f"""
    const ime = require({json.dumps(str(JS))});
    const out = ({expr});
    process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout or "null")


def test_ime_token_strips_gboard_tld_but_keeps_dotfiles():
    assert _node("ime.imeCompletionToken('do')") == "do"
    assert _node("ime.imeCompletionToken('cd do')") == "do"
    assert _node("ime.imeCompletionToken('.do')") == "do"
    assert _node("ime.imeCompletionToken('.env')") == ".env"
    assert _node("ime.imeCompletionToken('piherder')") == "piherder"


def test_normalize_mobile_cd_glitches():
    assert _node("ime.normalizeMobileLine('cd.do')") == "cd do"
    assert _node("ime.normalizeMobileLine('cd .do')") == "cd do"
    assert _node("ime.normalizeMobileLine('cd./docker')") == "cd docker"
    assert _node("ime.normalizeMobileLine('cd docker')") == "cd docker"
    assert _node("ime.normalizeMobileLine('ls -l')") == "ls -l"


def test_line_already_has_token():
    assert _node("ime.lineAlreadyHasToken('cd do', 'do')") is True
    assert _node("ime.lineAlreadyHasToken('cd ', 'do')") is False
    assert _node("ime.lineAlreadyHasToken('cd docker/', 'do')") is False


def test_consume_ime_echo_exact_and_charwise():
    assert _node(
        "(() => { const s = { skip: 'do' }; return [ime.consumeImeEcho(s, 'do'), s.skip]; })()"
    ) == [True, "do"]
    assert _node(
        "(() => { const s = { skip: 'do' }; "
        "const a = ime.consumeImeEcho(s, 'd'); "
        "const b = ime.consumeImeEcho(s, 'o'); "
        "return [a, b, s.skip === '' || s.skip === 'o']; })()"
    ) == [True, True, True]
    assert _node(
        "(() => { const s = { skip: 'do' }; return ime.consumeImeEcho(s, 'x'); })()"
    ) is False


def test_typed_from_screen_line_strips_prompt():
    assert (
        _node("ime.typedFromScreenLine('piherder@rpi5-4:~/docker$ cd docker/')")
        == "cd docker/"
    )
    assert _node("ime.typedFromScreenLine('# ls')") == "ls"


def test_composition_looks_like_skip():
    assert _node("ime.compositionLooksLikeSkip('doc', 'doc')") is True
    assert _node("ime.compositionLooksLikeSkip('doc', 'do')") is True
    assert _node("ime.compositionLooksLikeSkip('doc', 'pih')") is False
    assert _node("ime.VERSION") == 14
