"""Single source of truth for app version and public project links."""
from __future__ import annotations

import re
from typing import Optional, Tuple

# Keep in lockstep with pyproject.toml / FastAPI app.version / metrics APP_VERSION
APP_VERSION = "0.8.0.dev0"

GITHUB_OWNER = "bjorngluck"
GITHUB_REPO = "piherder"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_URL = f"{GITHUB_URL}/releases"
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
DOCS_URL = "https://piherder-docs.hacknow.info/"
SPONSOR_URL = f"https://github.com/sponsors/{GITHUB_OWNER}"
LICENSE_NAME = "MIT"
LICENSE_URL = f"{GITHUB_URL}/blob/main/LICENSE"

# Short “why” for the About page (matches README tone)
ABOUT_TAGLINE = (
    "Secure fleet management for Raspberry Pi and Linux hosts — "
    "backups, patching, containers, and control with zero plaintext secrets."
)

ABOUT_STORY = (
    "PiHerder started as battle-tested shell scripts for Raspberry Pi clusters and a "
    "homelab — then grew into an auditable web UI so operators can focus on building "
    "and securing systems instead of babysitting cron. Secrets stay encrypted at rest; "
    "privileged actions are logged. Open source under the MIT license."
)


def get_app_version() -> str:
    """Running product version (must stay aligned with pyproject.toml)."""
    # Prefer the constant so UI/banner match the tree even when an older
    # editable/egg install is on PYTHONPATH inside a long-lived container.
    return APP_VERSION


def parse_version_tuple(raw: str) -> Tuple[Tuple[int, ...], str]:
    """Return ((major, minor, patch, …), prerelease_suffix).

    Examples:
      0.5.0.dev0 → ((0, 5, 0), 'dev0')
      v0.4.0 → ((0, 4, 0), '')
      0.5.0-rc.1 → ((0, 5, 0), 'rc.1')
    """
    s = (raw or "").strip()
    if s.lower().startswith("v"):
        s = s[1:]
    # Split numeric core from pre-release (.devN, -rc, +local)
    m = re.match(r"^(\d+(?:\.\d+)*)(?:[-._]?(.*))?$", s)
    if not m:
        return ((0,), s.lower())
    nums = tuple(int(x) for x in m.group(1).split("."))
    pre = (m.group(2) or "").strip(".-_").lower()
    return nums, pre


def is_remote_newer(current: str, remote: str) -> bool:
    """True if remote release tag is a newer product version than current."""
    c_nums, c_pre = parse_version_tuple(current)
    r_nums, r_pre = parse_version_tuple(remote)
    if r_nums > c_nums:
        return True
    if r_nums < c_nums:
        return False
    # Same numeric base: a clean release is newer than .dev / rc of the same numbers
    if c_pre and not r_pre:
        return True
    if not c_pre and r_pre:
        return False
    # Both pre: lexical compare is good enough for rc1 vs rc2 / dev0 vs dev1
    if c_pre and r_pre:
        return r_pre > c_pre
    return False


def release_notes_url(tag: str) -> str:
    t = (tag or "").strip()
    if not t:
        return GITHUB_RELEASES_URL
    if not t.startswith("v") and re.match(r"^\d", t):
        t = f"v{t}"
    return f"{GITHUB_URL}/releases/tag/{t}"
