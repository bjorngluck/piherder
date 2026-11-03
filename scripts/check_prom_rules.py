#!/usr/bin/env python3
"""Run promtool check rules on the starter Prometheus rules."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "contrib" / "prometheus" / "piherder.rules.yml"
CACHED = Path("/tmp/promtool-v2.55.1/promtool")


def promtool_bin() -> str | None:
    env = (os.environ.get("PROMTOOL") or "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("promtool")
    if found:
        return found
    if CACHED.is_file():
        return str(CACHED)
    return None


def main() -> int:
    tool = promtool_bin()
    if tool is None:
        print("promtool is not installed")
        return 2
    if not RULES.is_file():
        print(f"missing {RULES}")
        return 1
    proc = subprocess.run(
        [tool, "check", "rules", str(RULES)],
        check=False,
        text=True,
        capture_output=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        print("promtool check rules ok")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
