#!/usr/bin/env python3
"""Fail when a workflow action or a Dockerfile FROM is not pinned.

Actions must use a 40 character commit SHA. FROM must use a sha256 digest.
Comments are ignored. The report names the file and line, not a secret.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USES_RE = re.compile(r"uses:\s*(\S+)")
FROM_RE = re.compile(r"^FROM\s+(\S+)", re.IGNORECASE)
SHA_RE = re.compile(r"@[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def unpinned_uses(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0]
        match = USES_RE.search(line)
        if not match:
            continue
        ref = match.group(1).strip().strip("'\"")
        if not SHA_RE.search(ref):
            found.append((number, ref))
    return found


def unpinned_from(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = FROM_RE.match(stripped)
        if not match:
            continue
        ref = match.group(1)
        if ref.lower() == "scratch":
            continue
        if not DIGEST_RE.search(ref):
            found.append((number, ref))
    return found


def scan_repo(root: Path | None = None) -> list[str]:
    base = root or ROOT
    problems: list[str] = []
    workflow_dir = base / ".github" / "workflows"
    if workflow_dir.is_dir():
        for path in sorted(workflow_dir.glob("*.yml")):
            for number, ref in unpinned_uses(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(base)}:{number} unpinned action {ref}")
    for path in sorted(base.glob("Dockerfile*")):
        for number, ref in unpinned_from(path.read_text(encoding="utf-8")):
            problems.append(f"{path.relative_to(base)}:{number} unpinned FROM {ref}")
    return problems


def main() -> int:
    problems = scan_repo()
    if not problems:
        print("supply pins ok")
        return 0
    for line in problems:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
