"""Redact / restore host .env (and secrets/*) for Docker compose editor UI.

Cleartext secrets must not appear in the browser without step-up unlock.
On save, masked placeholders are restored from live host content so redeploy
does not wipe passwords.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

from .service_templates.harden import looks_like_secret_name, parse_env_file

SECRET_MASK = "********"


def is_env_filename(name: str) -> bool:
    base = (name or "").replace("\\", "/").split("/")[-1]
    return base == ".env" or base.startswith(".env.")


def is_secrets_path(name: str) -> bool:
    p = (name or "").replace("\\", "/")
    return p.startswith("secrets/") or "/secrets/" in p


def redact_env_content(content: str, *, extra_keys: Optional[Set[str]] = None) -> str:
    """Mask values for secret-like keys (and any extra_keys)."""
    extra = extra_keys or set()
    lines = []
    for line in (content or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        k, _, _v = line.partition("=")
        key = k.strip()
        if key in extra or looks_like_secret_name(key):
            lines.append(f"{key}={SECRET_MASK}")
        else:
            lines.append(line)
    out = "\n".join(lines)
    if (content or "").endswith("\n") and out:
        out += "\n"
    return out


def restore_env_content(submitted: str, live: str) -> str:
    """If submitted still has mask/empty for a key, keep live host value."""
    live_map = parse_env_file(live or "")
    lines = []
    seen: Set[str] = set()
    for line in (submitted or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        seen.add(key)
        val = v  # keep original spacing style after =
        stripped = val.strip()
        if stripped in ("", SECRET_MASK, "********") and key in live_map and live_map[key]:
            # preserve live secret
            lines.append(f"{key}={live_map[key]}")
        else:
            lines.append(line)
    # Keep live-only secret keys if submit dropped them entirely? prefer submit as source of keys
    out = "\n".join(lines)
    if (submitted or "").endswith("\n") and out:
        out += "\n"
    return out


def redact_project_files_for_ui(
    files: Dict[str, str],
    *,
    reveal: bool,
    extra_secret_keys: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """Return a copy safe for the browser (unless reveal / step-up unlock)."""
    if reveal:
        return {k: (v if v is not None else "") for k, v in (files or {}).items()}
    out: Dict[str, str] = {}
    extra = extra_secret_keys or set()
    for path, body in (files or {}).items():
        text = body if body is not None else ""
        if is_secrets_path(path):
            out[path] = SECRET_MASK if text else ""
        elif is_env_filename(path):
            out[path] = redact_env_content(text, extra_keys=extra)
        else:
            out[path] = text
    return out


def restore_project_files_on_save(
    submitted: Dict[str, str],
    live: Dict[str, str],
) -> Dict[str, str]:
    """Merge submitted files onto live, restoring masked secrets from live."""
    out: Dict[str, str] = dict(live or {})
    for path, body in (submitted or {}).items():
        if body is None:
            continue
        text = body if isinstance(body, str) else str(body)
        live_body = (live or {}).get(path) or ""
        if is_secrets_path(path):
            if text.strip() in ("", SECRET_MASK) and live_body:
                out[path] = live_body
            else:
                out[path] = text
        elif is_env_filename(path):
            out[path] = restore_env_content(text, live_body)
        else:
            out[path] = text
    return out


def extra_secret_keys_for_project(
    session: Any,
    server_id: int,
    project_name: str,
) -> Set[str]:
    """Keys known from template deployment secrets (if any)."""
    try:
        from .service_templates.deploy import (
            decrypt_deployment_secrets,
            get_deployment_for_project,
        )

        dep = get_deployment_for_project(session, server_id, project_name)
        if not dep:
            return set()
        return set(decrypt_deployment_secrets(dep).keys())
    except Exception:
        return set()
