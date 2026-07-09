"""
Docker versioning service for PiHerder.

Extracted from docker_management.py for maintainability.

Handles:
- DockerVersion model CRUD for compose project history/drafts
- save_draft_version, deploy_version (writes files + triggers redeploy)
- get_versions, prune_old_versions
- create_new_docker_project (mkdir + optional git + initial files)
- get_project_live_files / write_project_files (multi-file SFTP helpers used by versioning flows)
- multi-file merge (compose + override + .env + Dockerfile) so one-file saves don't drop siblings

Re-exported from docker_management.py for full backward compatibility:
  from ..services import docker_management as docker_svc
  docker_svc.get_versions(...) etc. continue to work.

Kept lightweight: free functions, no new abstractions.
"""

import json
import sys
import traceback
from typing import List, Dict, Optional, Any
from datetime import datetime
from sqlmodel import Session, select

from ..models import Server, DockerVersion
from ..database import engine
from ..services.ssh import get_ssh_client, run_command

# Reserved key inside DockerVersion.files JSON — never written to the host.
META_KEY = "__piherder__"

# Default files probed on the remote project directory (Compose auto-loads override + .env).
DEFAULT_PROJECT_FILES = [
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "compose.override.yml",
    "compose.override.yaml",
    "Dockerfile",
    ".env",
    ".env.example",
]

COMPOSE_BASENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

OVERRIDE_BASENAMES = (
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "compose.override.yml",
    "compose.override.yaml",
)


def is_meta_key(name: str) -> bool:
    return (name or "") == META_KEY or (name or "").startswith("__")


def files_for_sftp(files: dict) -> dict:
    """Strip reserved meta keys before writing to the host."""
    out = {}
    for k, v in (files or {}).items():
        if is_meta_key(str(k)):
            continue
        if v is None:
            continue
        out[str(k)] = v if isinstance(v, str) else str(v)
    return out


def primary_compose_key(files: dict) -> Optional[str]:
    """Prefer standard compose basenames that exist in the map."""
    if not files:
        return None
    for k in COMPOSE_BASENAMES:
        if k in files:
            return k
    for k in files:
        if is_meta_key(k):
            continue
        low = k.lower()
        if low.endswith((".yml", ".yaml")) and "override" not in low and "dockerfile" not in low:
            return k
    return None


def file_role(name: str) -> str:
    n = (name or "").lower()
    if n in {b.lower() for b in COMPOSE_BASENAMES} or (
        n.endswith((".yml", ".yaml")) and "override" not in n and n != ".env"
    ):
        if "override" in n:
            return "override"
        return "compose"
    if n in {b.lower() for b in OVERRIDE_BASENAMES} or "override" in n:
        return "override"
    if n == "dockerfile" or n.endswith("/dockerfile") or n.startswith("dockerfile."):
        return "dockerfile"
    if n == ".env" or n.startswith(".env"):
        return "env"
    return "other"


def sort_project_filenames(names: list[str]) -> list[str]:
    order = {"compose": 0, "override": 1, "env": 2, "dockerfile": 3, "other": 4}

    def key(n: str):
        return (order.get(file_role(n), 9), n.lower())

    return sorted([n for n in names if not is_meta_key(n)], key=key)


def merge_project_files(
    base: Optional[dict],
    updates: dict,
    *,
    delete_keys: Optional[list[str]] = None,
) -> dict:
    """Merge file updates into a base snapshot without dropping sibling files.

    Keys in delete_keys are removed (except META_KEY). updates overwrite base.
    """
    out: dict[str, Any] = {}
    if base:
        for k, v in base.items():
            out[str(k)] = v
    for k in delete_keys or []:
        if is_meta_key(str(k)):
            continue
        out.pop(str(k), None)
    for k, v in (updates or {}).items():
        sk = str(k)
        if is_meta_key(sk):
            out[sk] = v
            continue
        if v is None:
            out.pop(sk, None)
        else:
            out[sk] = v if isinstance(v, str) else str(v)
    return out


def parse_version_files(dv: Optional[DockerVersion]) -> dict:
    if not dv or not dv.files:
        return {}
    try:
        data = json.loads(dv.files)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_project_live_files(server: Server, project_path: str, filenames: Optional[List[str]] = None) -> dict:
    """Read current files from host (compose, override, .env, Dockerfile, …). Short-lived SSH."""
    if not filenames:
        filenames = list(DEFAULT_PROJECT_FILES)
    client = get_ssh_client(server)
    sftp = client.open_sftp()
    files = {}
    try:
        for fname in filenames:
            fpath = f"{project_path}/{fname}".replace("//", "/")
            try:
                with sftp.open(fpath, "rb") as f:
                    raw = f.read()
                    files[fname] = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            except (IOError, FileNotFoundError):
                pass
        return files
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        client.close()


def write_project_files(server: Server, project_path: str, files: dict) -> tuple[bool, str]:
    """Write (multiple) files to host via SFTP. New short-lived session. Uses tmp+rename to avoid corruption.
    Returns (success, error_message_or_empty). Reserved meta keys are skipped.
    """
    payload = files_for_sftp(files)
    if not payload:
        return False, "no files to write"
    client = get_ssh_client(server)
    sftp = client.open_sftp()
    err = ""
    try:
        for fname, content in payload.items():
            # Disallow path traversal in file names
            if not fname or "/" in fname or "\\" in fname or fname in (".", ".."):
                return False, f"invalid file name: {fname!r}"
            fpath = f"{project_path}/{fname}".replace("//", "/")
            tmp = fpath + ".tmp"
            data = content.encode("utf-8") if isinstance(content, str) else content
            with sftp.open(tmp, "wb") as f:
                f.write(data)
            # Pre-remove target to ensure rename succeeds on all SFTP servers (some do not overwrite via rename).
            try:
                sftp.remove(fpath)
            except Exception:
                pass
            sftp.rename(tmp, fpath)
        return True, ""
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        try:
            print("[write_project_files] failed:", err, file=sys.stderr)
            traceback.print_exc()
        except Exception:
            pass
        return False, err
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def get_versions(server_id: int, project_name: str, limit: int = 10, session: Optional["Session"] = None) -> List[DockerVersion]:
    """Return up to 'limit' recent versions for a project (newest first)."""
    if session is None:
        with Session(engine) as s:
            return get_versions(server_id, project_name, limit, s)
    stmt = (
        select(DockerVersion)
        .where(DockerVersion.server_id == server_id, DockerVersion.project_name == project_name)
        .order_by(DockerVersion.version.desc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def save_draft_version(server_id: int, project_name: str, files: dict, session: Optional["Session"] = None, update_existing_draft_id: Optional[int] = None) -> DockerVersion:
    """Save current browser edits as draft.
    - If update_existing_draft_id provided and it is a draft for this project: update in-place (same version num).
    - Else: create a brand new draft version (increments). Never mutates live (!is_draft) records.
    Prunes old history after create.
    """
    if session is None:
        with Session(engine) as s:
            dv = save_draft_version(server_id, project_name, files, s, update_existing_draft_id)
            s.commit()
            s.refresh(dv)
            return dv

    if update_existing_draft_id:
        existing = session.get(DockerVersion, update_existing_draft_id)
        if existing and existing.server_id == server_id and existing.project_name == project_name and existing.is_draft:
            existing.files = json.dumps(files, ensure_ascii=False)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        # fallthrough: if not a valid draft to update, create new (protects live)

    max_v = session.exec(
        select(DockerVersion.version)
        .where(DockerVersion.server_id == server_id, DockerVersion.project_name == project_name)
        .order_by(DockerVersion.version.desc())
    ).first() or 0
    new_v = max_v + 1

    dv = DockerVersion(
        server_id=server_id,
        project_name=project_name,
        version=new_v,
        files=json.dumps(files, ensure_ascii=False),
        is_draft=True,
    )
    session.add(dv)
    session.commit()
    session.refresh(dv)

    prune_old_versions(server_id, project_name, session, keep=10)
    return dv


def deploy_version(server_id: int, version_id: int, server: Server, project_path: str, session: Optional["Session"] = None) -> bool:
    """Write the files of this version to the host (fresh SSH), mark deployed, trigger redeploy."""
    if session is None:
        with Session(engine) as s:
            return deploy_version(server_id, version_id, server, project_path, s)

    dv = session.get(DockerVersion, version_id)
    if not dv or dv.server_id != server_id:
        return False

    files = json.loads(dv.files) if dv.files else {}
    if not files:
        return False

    ok, _werr = write_project_files(server, project_path, files)
    if ok:
        dv.is_draft = False
        dv.deployed_at = datetime.utcnow()
        session.add(dv)
        session.commit()
        try:
            # Lazy import to avoid top-level circular import with docker_management re-exports
            from .docker_management import redeploy_project
            # best effort clear compose cache after writing files to host so next list sees fresh
            try:
                from . import docker_management as _dm
                _dm._CACHE.clear()
            except Exception:
                pass
            redeploy_project(server, project_path, pull=True)
        except Exception:
            pass
    return ok


def prune_old_versions(server_id: int, project_name: str, session: "Session", keep: int = 10):
    all_vers = session.exec(
        select(DockerVersion)
        .where(DockerVersion.server_id == server_id, DockerVersion.project_name == project_name)
        .order_by(DockerVersion.version.desc())
    ).all()
    for old in all_vers[keep:]:
        session.delete(old)
    session.commit()


def create_new_docker_project(
    server: Server, project_name: str, base_files: dict, git_url: Optional[str] = None
) -> bool:
    """Create dir on host (fresh SSH), optional git clone into it, write initial files.
    base_files e.g. {"docker-compose.yml": "...", "Dockerfile": "..."}
    """
    from .ssh import docker_base_expanded
    base = docker_base_expanded(server)
    full_path = f"{base}/{project_name}".replace("//", "/")
    client = get_ssh_client(server)
    try:
        run_command(client, f"mkdir -p {full_path}", timeout=30)
        if git_url:
            # clone into the dir (assumes empty or use --depth etc)
            run_command(client, f"cd {full_path} && git clone {git_url} . || true", timeout=180)
        ok, _werr = write_project_files(server, full_path, base_files)
        return ok
    finally:
        client.close()
