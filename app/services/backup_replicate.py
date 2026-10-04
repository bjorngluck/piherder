"""Copy checked paths from the local backup drive to a fleet destination.

Path A: rclone on the herder, after the rsync mirror exists. Drive tokens and
the SMB username/password stay Fernet-encrypted. Selection is checked paths
plus skipped children, never a typed glob from the operator.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..config import settings
from ..models import BackupDestination, Job, Server
from ..security.encryption import decrypt_str, encrypt_str

logger = logging.getLogger(__name__)

JOB_TYPE = "backup_replicate"
_REMOTE_NAME = "dest"
_SELECTABLE = frozenset({"drive", "smb", "onedrive"})
# rclone's well-known config obfuscation key (obscure.Obscure). Not a secret.
_RCLONE_CRYPT_KEY = bytes((
    0x9C, 0x93, 0x5B, 0x48, 0x73, 0x0A, 0x55, 0x4D,
    0x6B, 0xFD, 0x7C, 0x63, 0xC8, 0x86, 0xA9, 0x2B,
    0xD3, 0x90, 0x19, 0x8E, 0xB8, 0x12, 0x8A, 0xFB,
    0xF4, 0xDE, 0x16, 0x2B, 0x8B, 0x95, 0xF6, 0x38,
))
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"
OAUTH_STATE_COOKIE = "ph_drive_oauth"
ONEDRIVE_OAUTH_STATE_COOKIE = "ph_onedrive_oauth"
# The copy writes one folder on the signed-in account's default drive.
# Files.ReadWrite is that user's own files. User.Read is only for the
# account address shown after sign-in. Shared libraries and SharePoint
# sites are not requested.
ONEDRIVE_SCOPE = "offline_access User.Read Files.ReadWrite"
_MICROSOFT_AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_MICROSOFT_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_MICROSOFT_ME = "https://graph.microsoft.com/v1.0/me"
_GRAPH_DEFAULT_DRIVE = "https://graph.microsoft.com/v1.0/me/drive"
# rclone 1.68 refuses to open the remote without both of these.
_ONEDRIVE_DRIVE_TYPES = frozenset({"personal", "business", "documentLibrary"})


def backup_root() -> Path:
    return Path(settings.BACKUP_ROOT or "/backups").resolve()


def parse_selection(raw: str | None) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    checked = [str(p) for p in (data.get("checked") or []) if str(p).strip()]
    skipped = [str(p) for p in (data.get("skipped") or []) if str(p).strip()]
    return normalize_selection(checked, skipped)


def selection_json(checked: list[str], skipped: list[str]) -> str:
    checked, skipped = normalize_selection(checked, skipped)
    return json.dumps({"checked": checked, "skipped": skipped})


def _clean_rel(path: str) -> str:
    text = (path or "").strip().replace("\\", "/").lstrip("/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("path escapes the backup drive")
    return "/".join(parts)


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def normalize_selection(
    checked: list[str], skipped: list[str]
) -> tuple[list[str], list[str]]:
    clean_checked: list[str] = []
    for raw in checked:
        try:
            rel = _clean_rel(raw)
        except ValueError:
            continue
        if rel and rel not in clean_checked:
            clean_checked.append(rel)
    kept: list[str] = []
    for path in clean_checked:
        if any(_under(path, other) and path != other for other in clean_checked):
            continue
        kept.append(path)
    clean_skipped: list[str] = []
    for raw in skipped:
        try:
            rel = _clean_rel(raw)
        except ValueError:
            continue
        if not rel or rel in kept:
            continue
        if not any(_under(rel, parent) for parent in kept):
            continue
        clean_skipped.append(rel)
    final_skip: list[str] = []
    for path in clean_skipped:
        if any(_under(path, other) and path != other for other in clean_skipped):
            continue
        final_skip.append(path)
    return kept, final_skip


def apply_toggle(
    checked: list[str],
    skipped: list[str],
    path: str,
    on: bool,
) -> tuple[list[str], list[str]]:
    rel = _clean_rel(path)
    if not rel:
        raise ValueError("Choose a folder or file inside the backup drive")
    checked, skipped = normalize_selection(checked, skipped)
    if on:
        skipped = [s for s in skipped if s != rel and not _under(s, rel)]
        checked = [c for c in checked if not _under(c, rel)]
        if not any(_under(rel, parent) for parent in checked):
            checked.append(rel)
    else:
        checked = [c for c in checked if c != rel and not _under(c, rel)]
        skipped = [s for s in skipped if s != rel and not _under(s, rel)]
        if any(_under(rel, parent) for parent in checked):
            skipped.append(rel)
    return normalize_selection(checked, skipped)


def path_included(path: str, checked: list[str], skipped: list[str]) -> bool:
    rel = _clean_rel(path) if path else ""
    if not rel:
        return False
    if any(rel == s or _under(rel, s) for s in skipped):
        return False
    return any(rel == c or _under(rel, c) for c in checked)


def selection_state(path: str, checked: list[str], skipped: list[str]) -> str:
    """on, off, or partial. A checked folder stays on for everything inside it."""
    rel = ""
    if path:
        try:
            rel = _clean_rel(path)
        except ValueError:
            return "off"
    if not rel:
        return "partial" if checked else "off"
    if path_included(rel, checked, skipped):
        if any(_under(skip, rel) and skip != rel for skip in skipped):
            return "partial"
        return "on"
    if any(_under(item, rel) and item != rel for item in checked):
        return "partial"
    return "off"


def resolve_under_root(rel: str) -> Path:
    root = backup_root()
    cleaned = _clean_rel(rel)
    target = (root / cleaned).resolve() if cleaned else root
    if target != root and root not in target.parents:
        raise ValueError("path escapes the backup drive")
    return target


def list_directory(rel: str) -> list[dict[str, Any]]:
    folder = resolve_under_root(rel)
    if not folder.is_dir():
        raise FileNotFoundError(rel or "/")
    rows: list[dict[str, Any]] = []
    try:
        children = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    parent = _clean_rel(rel)
    from .backup_profiles import human_size

    for child in children:
        if child.is_symlink():
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        name = child.name
        child_rel = f"{parent}/{name}" if parent else name
        is_dir = child.is_dir()
        rows.append(
            {
                "name": name,
                "path": child_rel,
                "is_dir": is_dir,
                "size": int(st.st_size),
                "size_h": "" if is_dir else human_size(int(st.st_size)),
                "mtime": int(st.st_mtime),
            }
        )
    return rows


def _remote_dir(destination: BackupDestination) -> str:
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    name = str((cfg or {}).get("remote_dir") or "PiHerder").strip().strip("/")
    return name or "PiHerder"


def sync_plan(
    checked: list[str], skipped: list[str]
) -> list[tuple[str, list[str], bool]]:
    """Return (relative path, excludes relative to that path, is_dir-shaped).

    A checked path that is a prefix of another is the directory sync. File
    copies are checked paths that the caller marks with a trailing flag via
    ``file_paths``. This helper treats every checked path as a directory sync
    and every skipped descendant as an exclude. Callers pass files separately.
    """
    plan: list[tuple[str, list[str], bool]] = []
    for folder in checked:
        excludes: list[str] = []
        prefix = folder.rstrip("/") + "/"
        for skip in skipped:
            if skip.startswith(prefix):
                excludes.append(skip[len(prefix):])
        plan.append((folder, excludes, True))
    return plan


def rclone_sync_cmd(
    config_path: str,
    local_dir: str,
    remote_path: str,
    excludes: list[str],
    *,
    use_trash: bool = True,
) -> list[str]:
    cmd = [
        "rclone",
        "sync",
        local_dir,
        f"{_REMOTE_NAME}:{remote_path}",
        "--config",
        config_path,
    ]
    if use_trash:
        cmd.append("--drive-use-trash")
    cmd.extend([
        "--stats-one-line",
        "--stats",
        "0",
    ])
    for rel in excludes:
        pattern = rel.rstrip("/")
        if not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
            continue
        cmd.extend(["--exclude", pattern if "." in Path(pattern).name else f"{pattern}/**"])
    return cmd


def rclone_copy_file_cmd(
    config_path: str,
    local_file: str,
    remote_path: str,
    *,
    use_trash: bool = True,
) -> list[str]:
    cmd = [
        "rclone",
        "copyto",
        local_file,
        f"{_REMOTE_NAME}:{remote_path}",
        "--config",
        config_path,
    ]
    if use_trash:
        cmd.append("--drive-use-trash")
    cmd.extend(["--stats-one-line", "--stats", "0"])
    return cmd


def destination_name(provider: str) -> str:
    if provider == "smb":
        return "LAN NAS / SMB"
    if provider == "onedrive":
        return "OneDrive"
    return "Google Drive"


def normalize_provider(value: str | None) -> str:
    provider = (value or "drive").strip().lower()
    if provider not in _SELECTABLE:
        raise ValueError("provider")
    return provider


def _rclone_crypt(data: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(_RCLONE_CRYPT_KEY), modes.CTR(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _rclone_obscure(secret: str) -> str:
    """Match ``rclone obscure`` so the temp config password is not plaintext."""
    iv = os.urandom(16)
    blob = iv + _rclone_crypt(secret.encode("utf-8"), iv)
    return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def _rclone_reveal(obscured: str) -> str:
    pad = "=" * ((4 - len(obscured) % 4) % 4)
    raw = base64.urlsafe_b64decode(obscured + pad)
    if len(raw) < 16:
        raise ValueError("obscured")
    return _rclone_crypt(raw[16:], raw[:16]).decode("utf-8")


def _load_cfg(destination: BackupDestination) -> dict[str, Any]:
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def _single_line(value: str, *, code: str, required: bool, max_len: int) -> str:
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError(code)
        return ""
    if len(text) > max_len or any(ord(ch) < 32 for ch in text):
        raise ValueError(code)
    return text


def _clean_smb_host(value: str) -> str:
    text = _single_line(value, code="host", required=True, max_len=253)
    if any(ch.isspace() for ch in text) or "/" in text or "\\" in text or "://" in text:
        raise ValueError("host")
    return text


def _clean_smb_share(value: str) -> str:
    text = _single_line(value, code="share", required=True, max_len=80)
    if any(ch in text for ch in ("/", "\\", ":", " ")):
        raise ValueError("share")
    if text in (".", ".."):
        raise ValueError("share")
    return text


def _clean_smb_path(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        cleaned = _clean_rel(text)
    except ValueError:
        raise ValueError("path") from None
    if not cleaned or len(cleaned) > 200:
        raise ValueError("path")
    return cleaned


def _clean_smb_user(value: str) -> str:
    text = _single_line(value, code="user", required=True, max_len=128)
    if "\\" in text or "/" in text:
        raise ValueError("user")
    return text


def _clean_smb_domain(value: str) -> str:
    text = _single_line(value, code="domain", required=False, max_len=64)
    if text and (any(ch.isspace() for ch in text) or any(ch in text for ch in "/\\:")):
        raise ValueError("domain")
    return text


def _redact(text: str, secret: str) -> str:
    if not text or not secret:
        return text or ""
    return text.replace(secret, "[redacted]")


def _config_pass(path: str) -> str:
    """Obscured rclone password from a temp config, so stderr can be scrubbed."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("pass = "):
                    return line.split(" = ", 1)[1].strip()
    except OSError:
        return ""
    return ""


def encrypt_account(email: str, private_key: str) -> str:
    return encrypt_str(pack_service_account(email, private_key))


def normalize_private_key(raw: str) -> str:
    """Turn a pasted JSON key, or a key whose newlines are the two characters \\n, into PEM."""
    text = (raw or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict) and data.get("private_key"):
            text = str(data["private_key"]).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            loaded = json.loads(text)
        except Exception:
            loaded = None
        if isinstance(loaded, str):
            text = loaded.strip()
    text = text.replace("\r\n", "\n").replace("\\n", "\n").strip()
    return text


def pack_service_account(email: str, private_key: str) -> str:
    account = (email or "").strip()
    key = normalize_private_key(private_key)
    if "@" not in account or any(ch.isspace() for ch in account):
        raise ValueError("email")
    if "BEGIN" not in key or "PRIVATE KEY" not in key or "END" not in key or "\n" not in key:
        raise ValueError("key")
    return json.dumps(
        {"auth": "service_account", "client_email": account, "private_key": key}
    )


def google_redirect_uri(origin: str) -> str:
    return origin.rstrip("/") + "/backup-copies/google/callback"


def onedrive_redirect_uri(origin: str) -> str:
    return origin.rstrip("/") + "/backup-copies/onedrive/callback"


def google_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": DRIVE_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return _GOOGLE_AUTH + "?" + query


def new_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def oauth_client(destination: BackupDestination) -> dict[str, str]:
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    secret = ""
    enc = str(cfg.get("oauth_client_secret_encrypted") or "")
    if enc:
        try:
            secret = decrypt_str(enc)
        except Exception:
            secret = ""
    return {"client_id": str(cfg.get("oauth_client_id") or "").strip(), "client_secret": secret}


def store_oauth_client(
    destination: BackupDestination,
    *,
    client_id: str,
    client_secret: str,
    folder: str,
    schedule: str | None,
    after_host_backup: bool,
    copy_herder_backup: bool = False,
) -> None:
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    client_id = (client_id or "").strip()
    if client_id and (any(ch.isspace() for ch in client_id) or "://" in client_id):
        raise ValueError("client")
    cfg["remote_dir"] = folder
    cfg["shared_with_me"] = False
    cfg["copy_herder_backup"] = bool(copy_herder_backup)
    cfg["oauth_client_id"] = client_id
    secret = (client_secret or "").strip()
    if secret:
        cfg["oauth_client_secret_encrypted"] = encrypt_str(secret)
    destination.config_json = json.dumps(cfg)
    destination.schedule = schedule or None
    destination.after_host_backup = after_host_backup
    if destination.credentials_encrypted:
        try:
            token = json.loads(decrypt_token(destination))
        except Exception:
            token = None
        if isinstance(token, dict) and token.get("refresh_token"):
            if client_id:
                token["client_id"] = client_id
            if secret:
                token["client_secret"] = secret
            destination.credentials_encrypted = encrypt_str(json.dumps(token))


def exchange_google_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    request = urllib.request.Request(_GOOGLE_TOKEN, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        logger.warning("Google token exchange failed: %s", detail)
        raise ValueError("google") from exc
    except Exception as exc:
        raise ValueError("google") from exc
    if not isinstance(payload, dict) or not payload.get("refresh_token"):
        raise ValueError("google")
    return payload


def microsoft_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": ONEDRIVE_SCOPE,
            "prompt": "consent",
            "state": state,
        }
    )
    return _MICROSOFT_AUTH + "?" + query


def exchange_microsoft_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": ONEDRIVE_SCOPE,
        }
    ).encode()
    request = urllib.request.Request(_MICROSOFT_TOKEN, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        logger.warning("Microsoft token exchange failed: %s", detail)
        raise ValueError("microsoft") from exc
    except Exception as exc:
        raise ValueError("microsoft") from exc
    if not isinstance(payload, dict) or not payload.get("refresh_token"):
        raise ValueError("microsoft")
    return payload


def microsoft_account_email(access_token: str) -> str:
    request = urllib.request.Request(
        _MICROSOFT_ME,
        headers={"Authorization": "Bearer " + access_token},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("mail") or payload.get("userPrincipalName") or "").strip()


def google_account_email(access_token: str) -> str:
    request = urllib.request.Request(
        _GOOGLE_USERINFO,
        headers={"Authorization": "Bearer " + access_token},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("email") or "").strip()


def pack_oauth_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    access_token: str,
    email: str,
    expires_in: int | None,
) -> str:
    expiry = "2000-01-01T00:00:00Z"
    if expires_in:
        expiry = (datetime.utcnow() + timedelta(seconds=int(expires_in))).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(
        {
            "auth": "oauth",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "access_token": access_token or "",
            "token_type": "Bearer",
            "expiry": expiry,
            "email": email,
        }
    )


def discard_rclone_config(path: str) -> None:
    if not path:
        return
    for extra in (path, path + ".sa.json"):
        try:
            os.remove(extra)
        except OSError:
            pass


def write_rclone_config(token_json: str, *, shared_with_me: bool = False) -> str:
    payload = json.loads(token_json)
    if not isinstance(payload, dict):
        raise ValueError("Google account details are missing")
    lines = [f"[{_REMOTE_NAME}]", "type = drive", "scope = drive"]
    sa_path = ""
    if payload.get("private_key") and payload.get("client_email"):
        key = normalize_private_key(str(payload["private_key"]))
        creds = {
            "type": "service_account",
            "client_email": payload["client_email"],
            "private_key": key,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        handle = tempfile.NamedTemporaryFile("w", prefix="ph-rclone-", suffix=".conf", delete=False)
        sa_path = handle.name + ".sa.json"
        try:
            with open(sa_path, "w", encoding="utf-8") as sa:
                json.dump(creds, sa)
            os.chmod(sa_path, 0o600)
            lines.append("service_account_file = " + sa_path)
            handle.write("\n".join(lines) + "\n")
            if shared_with_me or payload.get("shared_with_me"):
                handle.write("shared_with_me = true\n")
            handle.flush()
            os.chmod(handle.name, 0o600)
        except Exception:
            discard_rclone_config(handle.name)
            raise
        finally:
            handle.close()
        return handle.name
    if payload.get("refresh_token") or payload.get("access_token"):
        token = {
            "access_token": payload.get("access_token") or "",
            "token_type": payload.get("token_type") or "Bearer",
            "refresh_token": payload.get("refresh_token") or "",
            "expiry": payload.get("expiry") or "2000-01-01T00:00:00Z",
        }
        lines.append("token = " + json.dumps(token, separators=(",", ":")))
        if payload.get("client_id"):
            lines.append("client_id = " + str(payload["client_id"]))
        if payload.get("client_secret"):
            lines.append("client_secret = " + str(payload["client_secret"]))
    else:
        raise ValueError("Google account details are missing")
    if (shared_with_me or payload.get("shared_with_me")) and payload.get("private_key"):
        lines.append("shared_with_me = true")
    handle = tempfile.NamedTemporaryFile("w", prefix="ph-rclone-", suffix=".conf", delete=False)
    try:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.chmod(handle.name, 0o600)
    finally:
        handle.close()
    return handle.name


def onedrive_default_drive(access_token: str) -> tuple[str, str]:
    """Graph id and type for the signed-in account's default drive.

    rclone 1.68 will not open the remote until both are in the config.
    """
    token = (access_token or "").strip()
    if not token:
        raise ValueError("OneDrive did not return the default drive")
    request = urllib.request.Request(
        _GRAPH_DEFAULT_DRIVE,
        headers={"Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        logger.warning("OneDrive drive lookup failed: %s", exc)
        raise ValueError("OneDrive did not return the default drive") from exc
    if not isinstance(payload, dict):
        raise ValueError("OneDrive did not return the default drive")
    drive_id = str(payload.get("id") or "").strip()
    drive_type = str(payload.get("driveType") or "").strip()
    if (
        not drive_id
        or any(ch.isspace() for ch in drive_id)
        or len(drive_id) > 512
        or drive_type not in _ONEDRIVE_DRIVE_TYPES
    ):
        raise ValueError("OneDrive did not return the default drive")
    return drive_id, drive_type


def write_onedrive_rclone_config(
    token_json: str,
    *,
    drive_id: str = "",
    drive_type: str = "",
) -> str:
    """Temp rclone config for the signed-in account's default drive."""
    payload = json.loads(token_json)
    if not isinstance(payload, dict) or not (
        payload.get("refresh_token") or payload.get("access_token")
    ):
        raise ValueError("Microsoft account details are missing")
    token = {
        "access_token": payload.get("access_token") or "",
        "token_type": payload.get("token_type") or "Bearer",
        "refresh_token": payload.get("refresh_token") or "",
        "expiry": payload.get("expiry") or "2000-01-01T00:00:00Z",
    }
    if not drive_id or not drive_type:
        drive_id, drive_type = onedrive_default_drive(str(token["access_token"]))
    drive_id = str(drive_id).strip()
    drive_type = str(drive_type).strip()
    if (
        not drive_id
        or any(ch.isspace() for ch in drive_id)
        or len(drive_id) > 512
        or drive_type not in _ONEDRIVE_DRIVE_TYPES
    ):
        raise ValueError("OneDrive did not return the default drive")
    lines = [f"[{_REMOTE_NAME}]", "type = onedrive"]
    lines.append("token = " + json.dumps(token, separators=(",", ":")))
    lines.append("drive_id = " + drive_id)
    lines.append("drive_type = " + drive_type)
    if payload.get("client_id"):
        lines.append("client_id = " + str(payload["client_id"]))
    if payload.get("client_secret"):
        lines.append("client_secret = " + str(payload["client_secret"]))
    handle = tempfile.NamedTemporaryFile("w", prefix="ph-rclone-", suffix=".conf", delete=False)
    try:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.chmod(handle.name, 0o600)
    except Exception:
        discard_rclone_config(handle.name)
        raise
    finally:
        handle.close()
    return handle.name


def encrypt_token(token_json: str) -> str:
    payload = json.loads(token_json)
    if not isinstance(payload, dict):
        raise ValueError("Drive token must be JSON")
    if not (payload.get("refresh_token") or payload.get("access_token")):
        raise ValueError("Drive token must be the JSON from rclone authorize drive")
    return encrypt_str(json.dumps(payload))


def decrypt_token(destination: BackupDestination) -> str:
    raw = destination.credentials_encrypted or ""
    if not raw:
        raise ValueError("No account saved")
    return decrypt_str(raw)


def credential_public(destination: BackupDestination) -> dict[str, Any]:
    """Email and kind for the form. Never the private key."""
    if not (destination.credentials_encrypted or "").strip():
        return {"saved": False, "email": "", "kind": ""}
    try:
        data = json.loads(decrypt_token(destination))
    except Exception:
        return {"saved": True, "email": "", "kind": "saved"}
    if not isinstance(data, dict):
        return {"saved": True, "email": "", "kind": "saved"}
    if data.get("client_email"):
        return {
            "saved": True,
            "email": str(data["client_email"]),
            "kind": "service_account",
        }
    if data.get("auth") == "oauth" or data.get("refresh_token"):
        return {
            "saved": bool(data.get("refresh_token") or data.get("access_token")),
            "email": str(data.get("email") or ""),
            "kind": "oauth",
        }
    return {"saved": True, "email": "", "kind": "saved"}


def drive_folder(destination: BackupDestination) -> str:
    return _remote_dir(destination)


def shared_with_me(destination: BackupDestination) -> bool:
    """A service account has no My Drive. The folder is always one shared with it."""
    if credential_public(destination).get("kind") == "service_account":
        return True
    try:
        cfg = json.loads(destination.config_json or "{}")
    except Exception:
        cfg = {}
    return bool(isinstance(cfg, dict) and cfg.get("shared_with_me"))


def decrypt_smb_secret(destination: BackupDestination) -> dict[str, str]:
    raw = destination.credentials_encrypted or ""
    if not raw:
        raise ValueError("password")
    try:
        data = json.loads(decrypt_str(raw))
    except Exception as exc:
        raise ValueError("password") from exc
    if not isinstance(data, dict):
        raise ValueError("password")
    return {
        "username": str(data.get("username") or ""),
        "password": str(data.get("password") or ""),
    }


def smb_public(destination: BackupDestination) -> dict[str, Any]:
    """Host, share, and username for the form. Never the password."""
    cfg = _load_cfg(destination)
    out: dict[str, Any] = {
        "host": str(cfg.get("host") or ""),
        "share": str(cfg.get("share") or ""),
        "path": str(cfg.get("remote_dir") or ""),
        "domain": str(cfg.get("domain") or ""),
        "username": "",
        "password_saved": False,
        "guest": False,
    }
    if not (destination.credentials_encrypted or "").strip():
        return out
    try:
        secret = decrypt_smb_secret(destination)
    except ValueError:
        return out
    out["username"] = secret["username"]
    out["password_saved"] = bool(secret["password"])
    # Both empty together is a saved guest share. A missing blob is not.
    out["guest"] = secret["username"] == "" and secret["password"] == ""
    return out


def store_smb(
    destination: BackupDestination,
    *,
    host: str,
    share: str,
    path: str,
    username: str,
    password: str,
    domain: str,
    schedule: str | None,
    after_host_backup: bool,
    copy_herder_backup: bool = False,
) -> None:
    """Save one SMB destination.

    Both username and password empty is guest access. A blank password with a
    username keeps a password already stored. One of the two alone is refused.
    """
    if (destination.provider or "smb") not in ("smb", ""):
        raise ValueError("provider")
    host_clean = _clean_smb_host(host)
    share_clean = _clean_smb_share(share)
    path_clean = _clean_smb_path(path)
    domain_clean = _clean_smb_domain(domain)
    user_text = (username or "").strip()
    pass_text = (password or "").strip()
    existing_pass = ""
    if (destination.credentials_encrypted or "").strip():
        try:
            existing_pass = decrypt_smb_secret(destination)["password"]
        except ValueError:
            existing_pass = ""
    if pass_text and not user_text:
        raise ValueError("user")
    if user_text and not pass_text:
        if not existing_pass:
            raise ValueError("password")
        user_clean = _clean_smb_user(user_text)
        secret = existing_pass
    elif user_text and pass_text:
        if len(pass_text) > 256 or any(ord(ch) < 32 for ch in pass_text):
            raise ValueError("password")
        user_clean = _clean_smb_user(user_text)
        secret = pass_text
    else:
        user_clean = ""
        secret = ""
    cfg = _load_cfg(destination)
    cfg["host"] = host_clean
    cfg["share"] = share_clean
    cfg["remote_dir"] = path_clean
    cfg["domain"] = domain_clean
    cfg["copy_herder_backup"] = bool(copy_herder_backup)
    cfg.pop("password", None)
    cfg.pop("username", None)
    cfg.pop("pass", None)
    destination.config_json = json.dumps(cfg)
    destination.credentials_encrypted = encrypt_str(
        json.dumps({"username": user_clean, "password": secret})
    )
    destination.provider = "smb"
    if not (destination.name or "").strip() or destination.name == "Google Drive":
        destination.name = "LAN NAS / SMB"
    destination.schedule = schedule or None
    destination.after_host_backup = after_host_backup


def smb_remote_root(destination: BackupDestination) -> str:
    cfg = _load_cfg(destination)
    share = _clean_smb_share(str(cfg.get("share") or ""))
    sub = _clean_smb_path(str(cfg.get("remote_dir") or ""))
    if sub:
        return f"{share}/{sub}"
    return share


def write_smb_rclone_config(destination: BackupDestination) -> str:
    """Temp rclone config, mode 0600. Caller deletes it after the run."""
    cfg = _load_cfg(destination)
    host = _clean_smb_host(str(cfg.get("host") or ""))
    secret = decrypt_smb_secret(destination)
    user = secret["username"]
    password = secret["password"]
    if bool(user) != bool(password):
        raise ValueError("password" if user else "user")
    domain = _clean_smb_domain(str(cfg.get("domain") or ""))
    lines = [
        f"[{_REMOTE_NAME}]",
        "type = smb",
        f"host = {host}",
    ]
    if user:
        lines.append(f"user = {_clean_smb_user(user)}")
        lines.append(f"pass = {_rclone_obscure(password)}")
    else:
        # rclone's SMB backend needs a user. Guest lives only in this temp file.
        # The Settings fields and the Fernet blob stay empty.
        lines.append("user = Guest")
    if domain:
        lines.append(f"domain = {domain}")
    handle = tempfile.NamedTemporaryFile("w", prefix="ph-rclone-", suffix=".conf", delete=False)
    try:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.chmod(handle.name, 0o600)
    except Exception:
        discard_rclone_config(handle.name)
        raise
    finally:
        handle.close()
    return handle.name


def _probe_smb(destination: BackupDestination) -> dict[str, str]:
    """List the share. Does not copy."""
    public = smb_public(destination)
    authed = bool(public["username"] and public["password_saved"])
    if not (public["host"] and public["share"] and (authed or public.get("guest"))):
        return {"ok": "0", "code": "account"}
    config_path = ""
    try:
        root = smb_remote_root(destination)
        config_path = write_smb_rclone_config(destination)
        cmd = [
            "rclone",
            "lsd",
            f"{_REMOTE_NAME}:{root}",
            "--config",
            config_path,
            "--max-depth",
            "1",
            "--timeout",
            "20s",
            "--contimeout",
            "15s",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        except FileNotFoundError:
            return {"ok": "0", "code": "rclone"}
        except subprocess.TimeoutExpired:
            return {"ok": "0", "code": "timeout"}
        if proc.returncode == 0:
            return {"ok": "1", "code": "ok"}
        err = f"{proc.stderr or ''} {proc.stdout or ''}".lower()
        if any(
            word in err
            for word in ("logon", "authentication", "unauthorized", "access_denied", "permission", "auth")
        ):
            return {"ok": "0", "code": "auth"}
        if any(
            word in err
            for word in ("bad_network_name", "not found", "object_name_not_found", "no such file")
        ):
            return {"ok": "0", "code": "folder"}
        if any(
            word in err
            for word in ("unreachable", "connection refused", "no route", "timed out", "host is down")
        ):
            return {"ok": "0", "code": "host"}
        return {"ok": "0", "code": "rclone"}
    except Exception:
        return {"ok": "0", "code": "account"}
    finally:
        if config_path:
            discard_rclone_config(config_path)


def probe(destination: BackupDestination) -> dict[str, str]:
    """Check the saved account. Does not copy anything."""
    from .demo import demo_mode

    if demo_mode():
        return {"ok": "0", "code": "demo"}
    if destination.provider == "smb":
        return _probe_smb(destination)
    if destination.provider not in ("drive", "onedrive"):
        return {"ok": "0", "code": "provider"}
    if not (destination.credentials_encrypted or "").strip():
        return {"ok": "0", "code": "account"}
    folder = _remote_dir(destination)
    config_path = ""
    try:
        token_json = decrypt_token(destination)
        if destination.provider == "onedrive":
            config_path = write_onedrive_rclone_config(token_json)
        else:
            config_path = write_rclone_config(
                token_json,
                shared_with_me=shared_with_me(destination),
            )
        cmd = [
            "rclone",
            "lsd",
            f"{_REMOTE_NAME}:{folder}",
            "--config",
            config_path,
            "--max-depth",
            "1",
            "--timeout",
            "20s",
            "--contimeout",
            "15s",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        except FileNotFoundError:
            return {"ok": "0", "code": "rclone"}
        except subprocess.TimeoutExpired:
            return {"ok": "0", "code": "timeout"}
        if proc.returncode == 0:
            return {"ok": "1", "code": "ok"}
        err = f"{proc.stderr or ''} {proc.stdout or ''}".lower()
        if "not found" in err or "404" in err:
            return {"ok": "0", "code": "folder"}
        if any(word in err for word in ("unauthorized", "invalid", "403", "401", "permission", "auth")):
            return {"ok": "0", "code": "auth"}
        return {"ok": "0", "code": "rclone"}
    except Exception:
        return {"ok": "0", "code": "auth"}
    finally:
        if config_path:
            discard_rclone_config(config_path)


def clean_drive_folder(value: str) -> str:
    name = (value or "").strip().strip("/")
    if not name:
        return "PiHerder"
    parts = [part for part in name.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("folder")
    return "/".join(parts) or "PiHerder"


def host_folder_name(server: Server) -> str:
    folder = (server.backup_folder_name or server.hostname or "").replace("/", "_").strip()
    return folder


def paths_for_scope(
    checked: list[str], skipped: list[str], scope: str | None
) -> tuple[list[str], list[str]]:
    if not scope:
        return checked, skipped
    prefix = scope.strip().strip("/")
    if not prefix:
        return checked, skipped
    scoped_checked = [p for p in checked if p == prefix or _under(p, prefix) or _under(prefix, p)]
    # If the host folder itself is checked, keep it. If only a child is checked, keep the child.
    # If a parent above the host is checked, narrow the sync to the host folder.
    narrowed: list[str] = []
    for path in scoped_checked:
        if _under(prefix, path) and path != prefix:
            narrowed.append(prefix)
        else:
            narrowed.append(path)
    scoped_skip = [p for p in skipped if p == prefix or _under(p, prefix) or any(_under(p, c) for c in narrowed)]
    return normalize_selection(narrowed, scoped_skip)


def _run_rclone(cmd: list[str], secret: str = "") -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
    except FileNotFoundError:
        return 127, "rclone is not installed in this image"
    err = (proc.stderr or proc.stdout or "").strip()
    if "token" in err.lower() and "ya29" in err:
        err = "rclone failed (token redacted)"
    err = _redact(err, secret)
    return proc.returncode, err[-2000:]


def _finish_copy(copied: list[str], errors: list[str]) -> dict[str, Any]:
    if errors and not copied:
        return {"ok": False, "error": errors[0], "errors": errors}
    if errors:
        return {"ok": False, "error": errors[0], "copied": copied, "errors": errors}
    return {"ok": True, "copied": copied}


def _execute_smb(destination: BackupDestination, scope: str | None) -> dict[str, Any]:
    secret = ""
    config_path = ""
    try:
        try:
            secret = decrypt_smb_secret(destination).get("password") or ""
        except ValueError:
            secret = ""
        checked, skipped = parse_selection(destination.selection_json)
        checked, skipped = paths_for_scope(checked, skipped, scope)
        if not checked:
            return {"ok": False, "error": "Nothing selected on the backup drive"}
        config_path = write_smb_rclone_config(destination)
        obscured = _config_pass(config_path)
        remote_root = smb_remote_root(destination)
        errors: list[str] = []
        copied: list[str] = []
        for rel, excludes, _is_dir in sync_plan(checked, skipped):
            local = resolve_under_root(rel)
            if not local.exists():
                errors.append(f"{rel} is not on the backup drive")
                continue
            remote = f"{remote_root}/{rel}"
            if local.is_dir():
                cmd = rclone_sync_cmd(
                    config_path, str(local), remote, excludes, use_trash=False
                )
            else:
                cmd = rclone_copy_file_cmd(
                    config_path, str(local), remote, use_trash=False
                )
            rc, err = _run_rclone(cmd, secret)
            err = _redact(err, obscured)
            if rc != 0:
                errors.append(_redact(f"{rel}: {err or 'rclone failed'}"[:500], secret))
            else:
                copied.append(rel)
        return _finish_copy(copied, errors)
    except Exception as exc:
        return {"ok": False, "error": _redact(str(exc)[:500], secret)}
    finally:
        if config_path:
            discard_rclone_config(config_path)


def execute(destination: BackupDestination, scope: str | None = None) -> dict[str, Any]:
    from .demo import demo_mode

    if demo_mode():
        return {"ok": False, "error": "Demo does not upload"}
    if destination.provider == "smb":
        return _execute_smb(destination, scope)
    if destination.provider not in ("drive", "onedrive"):
        return {"ok": False, "error": f"Provider {destination.provider} is not built"}
    checked, skipped = parse_selection(destination.selection_json)
    checked, skipped = paths_for_scope(checked, skipped, scope)
    if not checked:
        return {"ok": False, "error": "Nothing selected on the backup drive"}
    config_path = ""
    use_trash = destination.provider == "drive"
    client_secret = ""
    refresh = ""
    try:
        token_json = decrypt_token(destination)
        token_payload = json.loads(token_json)
        if isinstance(token_payload, dict):
            client_secret = str(token_payload.get("client_secret") or "")
            refresh = str(token_payload.get("refresh_token") or "")
        if destination.provider == "onedrive":
            config_path = write_onedrive_rclone_config(token_json)
        else:
            config_path = write_rclone_config(
                token_json,
                shared_with_me=shared_with_me(destination),
            )
        remote_root = _remote_dir(destination)
        errors: list[str] = []
        copied: list[str] = []
        for rel, excludes, _is_dir in sync_plan(checked, skipped):
            local = resolve_under_root(rel)
            if not local.exists():
                errors.append(f"{rel} is not on the backup drive")
                continue
            remote = f"{remote_root}/{rel}"
            if local.is_dir():
                cmd = rclone_sync_cmd(
                    config_path, str(local), remote, excludes, use_trash=use_trash
                )
            else:
                cmd = rclone_copy_file_cmd(
                    config_path, str(local), remote, use_trash=use_trash
                )
            rc, err = _run_rclone(cmd, client_secret)
            err = _redact(err, refresh)
            if rc != 0:
                errors.append(f"{rel}: {err or 'rclone failed'}"[:500])
            else:
                copied.append(rel)
        return _finish_copy(copied, errors)
    except Exception as exc:
        return {"ok": False, "error": _redact(_redact(str(exc)[:500], client_secret), refresh)}
    finally:
        if config_path:
            discard_rclone_config(config_path)


def copies_herder_backup(destination: BackupDestination | None) -> bool:
    """True when this destination should receive the self-backup archive."""
    if destination is None:
        return False
    return bool(_load_cfg(destination).get("copy_herder_backup"))


def herder_archive_remote(destination: BackupDestination, filename: str) -> str:
    """Remote path for one self-backup file. Beside the host-backup folders."""
    name = Path(filename).name
    sub = f"herder/{name}"
    if destination.provider == "smb":
        return f"{smb_remote_root(destination)}/{sub}"
    return f"{_remote_dir(destination)}/{sub}"


def _open_destination_rclone(destination: BackupDestination) -> tuple[str, str, str, bool]:
    """Temp rclone config plus secrets to redact. Caller deletes the config.

    The last flag is Drive trash. OneDrive and SMB do not use it.
    """
    if destination.provider == "smb":
        secret = ""
        try:
            secret = decrypt_smb_secret(destination).get("password") or ""
        except ValueError:
            secret = ""
        return write_smb_rclone_config(destination), secret, "", False
    token_json = decrypt_token(destination)
    secret = ""
    refresh = ""
    try:
        payload = json.loads(token_json)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        secret = str(payload.get("client_secret") or "")
        refresh = str(payload.get("refresh_token") or "")
    if destination.provider == "onedrive":
        config_path = write_onedrive_rclone_config(token_json)
    else:
        config_path = write_rclone_config(
            token_json,
            shared_with_me=shared_with_me(destination),
        )
    return config_path, secret, refresh, destination.provider == "drive"


def execute_herder_archive(destination: BackupDestination, name: str) -> dict[str, Any]:
    """Copy one local self-backup archive. Never deletes that local file."""
    from .demo import demo_mode
    from .herder_backup import resolve_archive_in_roots

    if demo_mode():
        return {"ok": False, "error": "Demo does not upload"}
    if destination.provider not in _SELECTABLE:
        return {"ok": False, "error": f"Provider {destination.provider} is not built"}
    archive = resolve_archive_in_roots(name=Path(name or "").name)
    if archive is None:
        return {"ok": False, "error": "That self-backup archive is not on this PiHerder"}
    config_path = ""
    secret = ""
    refresh = ""
    try:
        config_path, secret, refresh, use_trash = _open_destination_rclone(destination)
        remote = herder_archive_remote(destination, archive.name)
        cmd = rclone_copy_file_cmd(
            config_path, str(archive), remote, use_trash=use_trash
        )
        rc, err = _run_rclone(cmd, secret)
        err = _redact(err, refresh)
        if destination.provider == "smb":
            err = _redact(err, _config_pass(config_path))
        if rc != 0:
            return {"ok": False, "error": (err or "rclone failed")[:500]}
        return {"ok": True, "copied": [f"herder/{archive.name}"]}
    except Exception as exc:
        return {"ok": False, "error": _redact(_redact(str(exc)[:500], secret), refresh)}
    finally:
        if config_path:
            discard_rclone_config(config_path)


def credentials_saved(destination: BackupDestination | None) -> bool:
    if destination is None:
        return False
    return bool((destination.credentials_encrypted or "").strip())


def has_saved_destination(destination: BackupDestination | None) -> bool:
    """True when this provider has something Remove should clear.

    A blank row from get_or_create is not a saved destination.
    """
    if destination is None:
        return False
    if credentials_saved(destination):
        return True
    if (destination.schedule or "").strip() or destination.after_host_backup:
        return True
    cfg = _load_cfg(destination)
    for key in ("host", "share", "oauth_client_id", "oauth_client_secret_encrypted"):
        if str(cfg.get(key) or "").strip():
            return True
    checked, skipped = parse_selection(destination.selection_json)
    return bool(checked or skipped)


def find_destination(session: Session, provider: str) -> BackupDestination | None:
    key = (provider or "").strip().lower()
    if key not in _SELECTABLE:
        return None
    return session.exec(
        select(BackupDestination).where(BackupDestination.provider == key)
    ).first()


def _destinations_for(session: Session, provider: str) -> list[BackupDestination]:
    key = normalize_provider(provider)
    return list(
        session.exec(
            select(BackupDestination).where(BackupDestination.provider == key)
        ).all()
    )


def refresh_copy_schedule() -> None:
    """Rebuild backup-copy crons from the rows that are still saved."""
    try:
        from ..main import HAS_SCHEDULER, scheduler
        from .scheduler import sync_backup_copy_schedule

        sync_backup_copy_schedule(scheduler, HAS_SCHEDULER)
    except Exception:
        logger.warning("Backup copy schedule refresh failed", exc_info=True)


def remove_destination(session: Session, provider: str, *, confirm: str) -> dict[str, Any]:
    """Hard-delete one provider's destination. The other provider stays.

    Fernet ciphertext goes with the row. Remote Drive or SMB files are not touched.
    """
    from .demo import raise_if_demo

    raise_if_demo("settings_write")
    if (confirm or "").strip() != "remove":
        raise ValueError("confirm")
    key = normalize_provider(provider)
    rows = _destinations_for(session, key)
    if not rows:
        raise ValueError("missing")
    removed_ids = [int(row.id) for row in rows if row.id]
    for row in rows:
        session.delete(row)
    session.commit()
    refresh_copy_schedule()
    return {
        "removed": True,
        "provider": key,
        "destination_ids": removed_ids,
        "remote_files": "kept",
    }


def get_or_create(session: Session, provider: str = "drive") -> BackupDestination:
    provider = normalize_provider(provider)
    row = find_destination(session, provider)
    if row:
        return row
    now = datetime.utcnow()
    row = BackupDestination(
        name=destination_name(provider),
        provider=provider,
        enabled=True,
        selection_json=selection_json([], []),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _active_replicate(session: Session, destination_id: int) -> Job | None:
    """Pending or running host-folder copy for this destination.

    A self-backup hop on the same destination is a different rclone call
    (one archive under ``herder/``). It does not take this slot.
    """
    rows = session.exec(
        select(Job)
        .where(
            Job.job_type == JOB_TYPE,
            Job.status.in_(["pending", "running"]),
        )
        .order_by(Job.created_at.desc())
    ).all()
    for job in rows:
        try:
            data = json.loads(job.details or "{}")
        except Exception:
            data = {}
        if str(data.get("herder_archive") or "").strip():
            continue
        if int(data.get("destination_id") or 0) == int(destination_id):
            return job
    return None


def _refused_copy_job(destination: BackupDestination, server_id: int | None) -> Job:
    """In-memory failure. Not stored, and rclone is not started."""
    return Job(
        server_id=server_id,
        job_type=JOB_TYPE,
        status="failed",
        finished_at=datetime.utcnow(),
        details=json.dumps(
            {
                "error": "removed",
                "destination_id": destination.id,
                "done": True,
            }
        ),
    )


def enqueue(
    session: Session,
    destination: BackupDestination,
    *,
    server_id: int | None = None,
    scope: str | None = None,
    user_id: int | None = None,
) -> Job:
    from .demo import demo_mode
    from .jobs.service import _initial_job_details

    if not credentials_saved(destination):
        return _refused_copy_job(destination, server_id)
    # Host-folder copies share one slot. A self-backup copy does not use it.
    active = _active_replicate(session, int(destination.id or 0))
    if active:
        return active
    job = Job(server_id=server_id, job_type=JOB_TYPE, status="pending")
    if destination.provider == "smb":
        queued = "SMB copy queued…"
    elif destination.provider == "onedrive":
        queued = "OneDrive copy queued…"
    else:
        queued = "Drive copy queued…"
    job.details = _initial_job_details(
        queued,
        destination_id=destination.id,
        scope=scope,
        user_id=user_id,
        queued_at=datetime.utcnow().isoformat(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    if demo_mode():
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        job.details = json.dumps(
            {"error": "Demo does not upload", "destination_id": destination.id, "done": True}
        )
        session.add(job)
        session.commit()
        return job
    from ..tasks import replicate_backup

    try:
        async_result = replicate_backup.delay(job.id)
        job.celery_task_id = async_result.id
        session.add(job)
        session.commit()
    except Exception as exc:
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        job.details = json.dumps({"error": str(exc)[:500], "destination_id": destination.id})
        session.add(job)
        session.commit()
    return job


def _active_herder_copy(session: Session, destination_id: int, archive_name: str) -> Job | None:
    rows = session.exec(
        select(Job)
        .where(
            Job.job_type == JOB_TYPE,
            Job.status.in_(["pending", "running"]),
        )
        .order_by(Job.created_at.desc())
    ).all()
    for job in rows:
        try:
            data = json.loads(job.details or "{}")
        except Exception:
            data = {}
        if int(data.get("destination_id") or 0) != int(destination_id):
            continue
        if str(data.get("herder_archive") or "") == archive_name:
            return job
    return None


def enqueue_herder_archive(
    session: Session,
    destination: BackupDestination,
    archive_name: str,
    *,
    user_id: int | None = None,
) -> Job:
    """Queue one self-backup file. A host-folder copy on this destination does not block it."""
    from .demo import demo_mode
    from .herder_backup import is_safe_archive_basename
    from .jobs.service import _initial_job_details

    name = Path(archive_name or "").name
    if not credentials_saved(destination) or not is_safe_archive_basename(name):
        return _refused_copy_job(destination, None)
    active = _active_herder_copy(session, int(destination.id or 0), name)
    if active:
        return active
    if destination.provider == "smb":
        queued = "Self-backup copy to the LAN share queued…"
    elif destination.provider == "onedrive":
        queued = "Self-backup copy to OneDrive queued…"
    else:
        queued = "Self-backup copy to Drive queued…"
    job = Job(server_id=None, job_type=JOB_TYPE, status="pending")
    job.details = _initial_job_details(
        queued,
        destination_id=destination.id,
        herder_archive=name,
        user_id=user_id,
        queued_at=datetime.utcnow().isoformat(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    if demo_mode():
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        job.details = json.dumps(
            {
                "error": "Demo does not upload",
                "destination_id": destination.id,
                "herder_archive": name,
                "done": True,
            }
        )
        session.add(job)
        session.commit()
        return job
    from ..tasks import replicate_backup

    try:
        async_result = replicate_backup.delay(job.id)
        job.celery_task_id = async_result.id
        session.add(job)
        session.commit()
    except Exception as exc:
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        job.details = json.dumps(
            {
                "error": str(exc)[:500],
                "destination_id": destination.id,
                "herder_archive": name,
            }
        )
        session.add(job)
        session.commit()
    return job


def enqueue_after_herder_backup(session: Session, archive_path: str | Path) -> list[Job]:
    """Queue the new archive for destinations that opted in. Others are left alone."""
    from .herder_backup import is_safe_archive_basename

    name = Path(str(archive_path or "")).name
    if not is_safe_archive_basename(name):
        return []
    rows = session.exec(
        select(BackupDestination).where(BackupDestination.enabled == True)  # noqa: E712
    ).all()
    queued: list[Job] = []
    for dest in rows:
        if not credentials_saved(dest) or not copies_herder_backup(dest):
            continue
        queued.append(enqueue_herder_archive(session, dest, name))
    return queued


def enqueue_after_host_backup(session: Session, server: Server) -> None:
    folder = host_folder_name(server)
    if not folder:
        return
    rows = session.exec(
        select(BackupDestination).where(
            BackupDestination.enabled == True,  # noqa: E712
            BackupDestination.after_host_backup == True,  # noqa: E712
        )
    ).all()
    for dest in rows:
        if not credentials_saved(dest):
            continue
        checked, skipped = parse_selection(dest.selection_json)
        scoped, _ = paths_for_scope(checked, skipped, folder)
        if not scoped:
            continue
        enqueue(session, dest, server_id=server.id, scope=folder)
