#!/usr/bin/env python3
"""Fail when piherder, piherder-mcp, and piherder-ha disagree.

Reads origin/main of the three local clones. See docs/RELEASE_SYNC.md.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPOS = {
    "herder": Path("/home/bjorn/docker/piherder"),
    "mcp": Path("/home/bjorn/piherder-mcp"),
    "ha": Path("/home/bjorn/piherder-ha"),
}

DYNAMIC_BADGE = (
    "img.shields.io/pypi/v/",
    "img.shields.io/github/v/release/",
)
FORBIDDEN = ("still serves", "until tag", "until the tag")


def git_show(repo: Path, spec: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"origin/main:{spec}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def version_of(kind: str, text: str) -> str:
    if kind == "herder":
        match = re.search(r'^APP_VERSION = "([^"]+)"', text, re.M)
    elif kind == "mcp":
        match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    else:
        match = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    if not match:
        raise SystemExit(f"no version constant in {kind}")
    return match.group(1)


def quoted_block(text: str, name: str) -> list[str]:
    """Resolve a tuple of quoted names, including one `*OTHER` spread."""
    match = re.search(rf"(?m)^{re.escape(name)} = \((.*?)\n\)", text, re.S)
    if not match:
        raise SystemExit(f"missing {name}")
    body = match.group(1)
    names = re.findall(r'"([a-z0-9_]+)"', body)
    spread = re.search(r"\*([A-Z0-9_]+)", body)
    if spread:
        names.extend(quoted_block(text, spread.group(1)))
    return names


def latest_tag(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "tag", "--merged", "origin/main", "-l", "v*"],
        check=True,
        capture_output=True,
        text=True,
    )
    tags = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"v(\d+\.\d+\.\d+)", line.strip())
        if match:
            tags.append(tuple(int(part) for part in match.group(1).split(".")))
    if not tags:
        raise SystemExit(f"no semver tags on {repo}")
    best = max(tags)
    return ".".join(str(part) for part in best)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--shipped",
        action="store_true",
        help="also require the newest v* tag on origin/main to match the version constant",
    )
    args = parser.parse_args()
    problems: list[str] = []

    files = {
        "herder_version": git_show(REPOS["herder"], "app/version_info.py"),
        "herder_readme": git_show(REPOS["herder"], "README.md"),
        "herder_jobs": git_show(REPOS["herder"], "app/services/mcp_hosted.py"),
        "mcp_version": git_show(REPOS["mcp"], "src/piherder_mcp/__init__.py"),
        "mcp_readme": git_show(REPOS["mcp"], "README.md"),
        "mcp_jobs": git_show(REPOS["mcp"], "src/piherder_mcp/client.py"),
        "ha_manifest": git_show(REPOS["ha"], "custom_components/piherder/manifest.json"),
        "ha_readme": git_show(REPOS["ha"], "README.md"),
    }
    herder = version_of("herder", files["herder_version"])
    mcp = version_of("mcp", files["mcp_version"])
    ha = version_of("ha", files["ha_manifest"])

    expect = {
        "herder": (
            files["herder_readme"],
            (
                f"badge/release-v{herder}",
                f"badge/HA%20plugin-v{ha}",
                f"badge/MCP-v{mcp}",
            ),
        ),
        "mcp": (
            files["mcp_readme"],
            (
                f"badge/adapter-v{mcp}",
                f"badge/pypi-v{mcp}",
                f"badge/PiHerder-v{herder}",
            ),
        ),
        "ha": (
            files["ha_readme"],
            (
                f"badge/plugin-v{ha}",
                f"badge/PiHerder-v{herder}",
            ),
        ),
    }
    for repo, (readme, needles) in expect.items():
        for needle in needles:
            if needle not in readme:
                problems.append(f"{repo} README is missing {needle}")
        for prefix in DYNAMIC_BADGE:
            if prefix in readme:
                problems.append(f"{repo} README still uses a dynamic badge {prefix}")
    for phrase in FORBIDDEN:
        if phrase in files["mcp_readme"].lower():
            problems.append(f"mcp README contains {phrase!r}")

    herder_jobs = set(quoted_block(files["herder_jobs"], "MCP_JOB_TYPES"))
    mcp_jobs = set(quoted_block(files["mcp_jobs"], "JOB_TYPES"))
    if herder_jobs != mcp_jobs:
        problems.append(
            "job types differ: "
            f"herder only {sorted(herder_jobs - mcp_jobs)} "
            f"adapter only {sorted(mcp_jobs - herder_jobs)}"
        )

    if args.shipped:
        for repo, version in (("herder", herder), ("mcp", mcp), ("ha", ha)):
            tag = latest_tag(REPOS[repo])
            if tag != version:
                problems.append(f"{repo} origin/main is {version} but newest tag is v{tag}")

    if problems:
        print("release sync failed:")
        for item in problems:
            print(f"  {item}")
        return 1
    print(f"release sync ok: herder {herder}, mcp {mcp}, plugin {ha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
