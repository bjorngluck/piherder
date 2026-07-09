"""Per-server backup source path allow/deny rules.

Deny always wins. Allow (when non-empty) is a prefix whitelist.
Dangerous defaults are always denied unless explicitly allowed AND not in deny.
"""
from __future__ import annotations

import json
import os
from typing import Any

# Always blocked unless the operator adds an explicit allow *and* removes from deny.
DEFAULT_DENY_PREFIXES = (
    "/",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/run",
    "/tmp",
    "/var/run",
    "/etc",
    "/root",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)

# Paths that match DEFAULT_DENY as exact "/" only — "/" is special (deny whole FS root as source).
# For other defaults we deny as prefix (e.g. /etc, /etc/foo).


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        t = val.strip()
        if not t:
            return []
        if t.startswith("["):
            try:
                parsed = json.loads(t)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        # newline / comma separated
        parts: list[str] = []
        for chunk in t.replace(",", "\n").splitlines():
            c = chunk.strip()
            if c:
                parts.append(c)
        return parts
    return []


def normalize_path(path: str) -> str:
    """Normalize for comparison: absolute, no trailing slash (except root)."""
    p = (path or "").strip()
    if not p:
        return ""
    # Expand ~ for policy checks (rsync may expand later)
    if p.startswith("~/") or p == "~":
        p = os.path.expanduser(p)
    # Collapse // and resolve . / .. where possible without requiring the path exists
    p = os.path.normpath(p)
    if not p.startswith("/"):
        # Relative paths are rejected by validation (must be absolute)
        return p
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def parse_rules(raw: str | dict | None) -> dict:
    """Return {allow: [...], deny: [...]} from Server.backup_path_rules JSON or dict."""
    if raw is None or raw == "":
        return {"allow": [], "deny": []}
    if isinstance(raw, dict):
        return {
            "allow": _as_list(raw.get("allow")),
            "deny": _as_list(raw.get("deny")),
        }
    try:
        data = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return {"allow": [], "deny": []}
    return {
        "allow": _as_list(data.get("allow")),
        "deny": _as_list(data.get("deny")),
    }


def rules_to_json(allow: list[str] | None = None, deny: list[str] | None = None) -> str:
    return json.dumps(
        {
            "allow": [normalize_path(a) for a in (allow or []) if a],
            "deny": [normalize_path(d) for d in (deny or []) if d],
        },
        separators=(",", ":"),
    )


def _matches_prefix(path: str, prefix: str) -> bool:
    path_n = normalize_path(path)
    pref = normalize_path(prefix)
    if not path_n or not pref:
        return False
    if pref == "/":
        # Only exact root is "the whole filesystem" as a source — not every path under /
        return path_n == "/"
    if path_n == pref:
        return True
    return path_n.startswith(pref + "/")


def effective_deny_list(rules: dict | None) -> list[str]:
    rules = rules or {"allow": [], "deny": []}
    custom = [normalize_path(d) for d in rules.get("deny") or []]
    # Defaults + custom, unique preserve order
    seen = set()
    out: list[str] = []
    for d in list(DEFAULT_DENY_PREFIXES) + custom:
        n = normalize_path(d)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def validate_backup_path(path: str, rules: dict | str | None = None) -> tuple[bool, str]:
    """Return (ok, reason). reason empty when ok."""
    raw = (path or "").strip()
    if not raw:
        return False, "Path is empty"
    p = normalize_path(raw)
    if not p.startswith("/"):
        return False, "Path must be absolute (start with /)"
    if ".." in p.split("/"):
        return False, "Path must not contain .."
    if "\x00" in raw:
        return False, "Invalid path"

    parsed = parse_rules(rules) if not isinstance(rules, dict) else rules
    allow = [normalize_path(a) for a in (parsed.get("allow") or []) if a]
    deny = effective_deny_list(parsed)

    # Explicit allow can override default deny *only* for that allow prefix match,
    # but never overrides custom deny entries.
    custom_deny = [normalize_path(d) for d in (parsed.get("deny") or []) if d]
    for d in custom_deny:
        if _matches_prefix(p, d):
            return False, f"Denied by policy: {d}"

    # Default deny unless allowed
    for d in DEFAULT_DENY_PREFIXES:
        if _matches_prefix(p, d):
            # Allow override for non-root dangerous paths if under an allow prefix
            if d == "/" and p == "/":
                return False, "Backing up filesystem root is not allowed"
            if allow and any(_matches_prefix(p, a) for a in allow):
                # Still block pure OS roots unless allow is more specific than default deny?
                # If allow includes /etc, operator explicitly wants it.
                continue
            return False, f"Denied by default policy: {d} (add an allow rule to override)"

    # Whitelist mode when allow is non-empty
    if allow:
        if not any(_matches_prefix(p, a) for a in allow):
            return False, "Path is outside allow list"

    return True, ""


def filter_allowed_sources(
    sources: list[dict],
    rules: dict | str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split sources into (allowed, rejected) where rejected items include error reason."""
    ok: list[dict] = []
    bad: list[dict] = []
    for s in sources or []:
        src = (s.get("source") if isinstance(s, dict) else str(s)) or ""
        good, reason = validate_backup_path(src, rules)
        if good:
            ok.append(s if isinstance(s, dict) else {"source": src, "enabled": True})
        else:
            item = dict(s) if isinstance(s, dict) else {"source": src}
            item["error"] = reason
            item["skipped"] = True
            bad.append(item)
    return ok, bad
