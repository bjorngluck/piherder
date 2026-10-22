"""Push denylist matches added lines only and never prints the pattern."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_push_denylist.py"
_spec = importlib.util.spec_from_file_location("check_push_denylist", _PATH)
deny = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(deny)

DIFF = """\
diff --git a/docs/note.md b/docs/note.md
index 111..222 100644
--- a/docs/note.md
+++ b/docs/note.md
@@ -2,0 +3,2 @@
+kept line
+hello FLEETWORD there
@@ -9 +10,0 @@
-removed FLEETWORD
"""


def test_patterns_skip_comments_blanks_and_short_terms():
    patterns, short = deny.parse_patterns("\n# note\n\nab\nFLEETWORD\n")
    assert patterns == ["FLEETWORD"]
    assert short == 1


def test_added_line_matches_and_a_removed_line_does_not():
    lines = deny.added_lines(DIFF)
    assert [(p, n) for p, n, _t in lines] == [("docs/note.md", 3), ("docs/note.md", 4)]
    assert deny.matching_locations(lines, ["FLEETWORD"]) == [("docs/note.md", 4)]
    assert deny.matching_locations(lines, ["a.b"]) == []


def test_upper_case_hit_matches():
    assert deny.matching_locations(deny.added_lines(DIFF), ["fleetword"]) == [
        ("docs/note.md", 4)
    ]


def test_literal_dot_is_not_a_regex():
    lines = [("a.txt", 1, "axb")]
    assert deny.matching_locations(lines, ["a.b"]) == []


def test_diff_spec_uses_the_event():
    assert deny.diff_spec("pull_request", base_sha="abc") == "abc...HEAD"
    assert deny.diff_spec("push", before="111", sha="222") == "111..222"
    assert deny.diff_spec("push", before="0" * 40, sha="222") == "origin/main...HEAD"
    assert deny.diff_spec("") == "origin/main...HEAD"


def test_empty_secret_does_not_scan(monkeypatch, capsys):
    monkeypatch.delenv("PIHERDER_PUSH_DENYLIST", raising=False)
    assert deny.main() == 0
    out = capsys.readouterr().out
    assert "manual grep remains" in out
    assert "FLEETWORD" not in out


def test_match_report_omits_the_pattern_and_the_line(monkeypatch, capsys):
    monkeypatch.setenv("PIHERDER_PUSH_DENYLIST", "FLEETWORD")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("PIHERDER_DENYLIST_BEFORE", "aaa")
    monkeypatch.setenv("GITHUB_SHA", "bbb")
    monkeypatch.setattr(deny, "load_diff", lambda spec: DIFF)
    assert deny.main() == 1
    out = capsys.readouterr().out
    assert "denylist match: docs/note.md:4" in out
    assert "FLEETWORD" not in out
    assert "hello" not in out
