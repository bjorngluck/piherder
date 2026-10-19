#!/usr/bin/env python3
"""Fail when an added diff line contains a push-denylist term.

Patterns live in PIHERDER_PUSH_DENYLIST, one literal per line. They are not
regular expressions and they are not printed. An empty or missing value does
not scan. The manual grep remains until a maintainer sets the secret.

Scans added lines only. A pull request uses the base commit. A push uses
before..after. A new branch (before is all zeros) uses origin/main...HEAD.
"""

from __future__ import annotations

import os
import subprocess
import sys

MIN_PATTERN = 4


def parse_patterns(raw: str | None) -> tuple[list[str], int]:
    """Return literal patterns and how many short lines were ignored."""
    patterns: list[str] = []
    short = 0
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if len(text) < MIN_PATTERN:
            short += 1
            continue
        patterns.append(text)
    return patterns, short


def diff_spec(
    event: str,
    *,
    base_sha: str = "",
    before: str = "",
    sha: str = "HEAD",
) -> str:
    if event == "pull_request" and base_sha:
        return f"{base_sha}...HEAD"
    if event == "push" and before and set(before) != {"0"}:
        return f"{before}..{sha or 'HEAD'}"
    return "origin/main...HEAD"


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, new line number, text) for lines this diff adds."""
    path: str | None = None
    new_line = 0
    found: list[tuple[str, int, str]] = []
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            spec = raw[4:].strip()
            if spec.startswith("b/"):
                path = spec[2:]
            elif spec == "/dev/null":
                path = None
            else:
                path = spec
            continue
        if raw.startswith("@@"):
            parts = raw.split(" ")
            plus = next((p for p in parts if p.startswith("+")), "+0")
            new_line = int(plus[1:].split(",")[0] or "0")
            continue
        if raw.startswith("+"):
            if path:
                found.append((path, new_line, raw[1:]))
            new_line += 1
            continue
        if raw.startswith("-") or raw.startswith("\\"):
            continue
        new_line += 1
    return found


def matching_locations(
    lines: list[tuple[str, int, str]], patterns: list[str]
) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for path, number, text in lines:
        if any(pattern in text for pattern in patterns):
            hits.append((path, number))
    return hits


def load_diff(spec: str) -> str:
    result = subprocess.run(
        ["git", "diff", "-U0", "--no-color", "--diff-filter=ACMR", spec],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = (result.stderr or "git diff failed").strip().splitlines()
        print(err[-1] if err else "git diff failed", file=sys.stderr)
        raise SystemExit(2)
    return result.stdout


def main() -> int:
    patterns, short = parse_patterns(os.environ.get("PIHERDER_PUSH_DENYLIST"))
    if short:
        print(f"push denylist ignored {short} short pattern(s)")
    if not patterns:
        print("push denylist secret is unset. The manual grep remains.")
        return 0
    spec = diff_spec(
        os.environ.get("GITHUB_EVENT_NAME", ""),
        base_sha=os.environ.get("PIHERDER_DENYLIST_BASE", ""),
        before=os.environ.get("PIHERDER_DENYLIST_BEFORE", ""),
        sha=os.environ.get("GITHUB_SHA", "HEAD"),
    )
    hits = matching_locations(added_lines(load_diff(spec)), patterns)
    if not hits:
        print(f"push denylist clean ({spec})")
        return 0
    for path, number in hits:
        print(f"denylist match: {path}:{number}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
