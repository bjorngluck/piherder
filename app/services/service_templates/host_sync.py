"""Host ↔ deployment desired-state sync (adopt live files, migrate .env secrets)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlmodel import Session

from ...models import Server, StackDeployment
from ...security.encryption import encrypt_str
from ..docker_versions import get_project_live_files
from ..ssh import docker_base_expanded
from .catalog import get_template_definition
from .harden import looks_like_secret_name, parse_env_file
from .schema import TemplateError, files_for_db_storage

logger = logging.getLogger(__name__)


def project_path_for(server: Server, project_name: str) -> str:
    base = docker_base_expanded(server)
    return f"{base}/{project_name}".replace("//", "/")


def secret_names_for_deployment(
    session: Session,
    deployment: StackDeployment,
    *,
    known_secrets: Optional[Dict[str, str]] = None,
) -> Set[str]:
    """Secret key names from encrypted store + template password/secret vars."""
    names: Set[str] = set((known_secrets or {}).keys())
    try:
        if deployment.template_slug:
            definition = get_template_definition(session, slug=deployment.template_slug)
            for v in definition.variables:
                if v.secret or v.type == "password":
                    names.add(v.name)
    except Exception as e:
        logger.debug("secret_names_for_deployment template: %s", e)
    return names


def refresh_secrets_from_host_env(
    secrets_map: Dict[str, str],
    host_env: Dict[str, str],
    secret_name_set: Set[str],
) -> Dict[str, str]:
    """Update secrets_map from host .env for known + secret-looking keys."""
    out = dict(secrets_map or {})
    for k in list(secret_name_set):
        if k in host_env and str(host_env[k]).strip() != "":
            out[k] = str(host_env[k])
    for k, val in (host_env or {}).items():
        if k in out:
            continue
        if looks_like_secret_name(k) and str(val).strip():
            out[k] = str(val)
    return out


def adopt_host_files_as_desired(
    session: Session,
    *,
    server: Server,
    deployment: StackDeployment,
) -> Dict[str, Any]:
    """Copy live host project files into this deployment's desired state.

    Use after an intentional host-only change (e.g. cadvisor port 8081) so drift
    clears without overwriting the host. Secrets stay in secrets_encrypted;
    compose / sidecars / .env structure are taken from the host.
    """
    from .deploy import decrypt_deployment_secrets

    path = project_path_for(server, deployment.project_name)
    try:
        live = get_project_live_files(server, path) or {}
    except Exception as e:
        raise TemplateError(f"Could not read host files at {path}: {e}") from e
    if not live:
        raise TemplateError(
            f"No project files on host at {path} — nothing to adopt"
        )

    secrets_map = decrypt_deployment_secrets(deployment)
    host_env = parse_env_file(live.get(".env") or "")
    secret_name_set = secret_names_for_deployment(
        session, deployment, known_secrets=secrets_map
    )
    secrets_map = refresh_secrets_from_host_env(secrets_map, host_env, secret_name_set)

    storage_files = files_for_db_storage(dict(live), secrets_map or {})
    next_ver = int(deployment.config_version or 0) + 1
    deployment.config_version = next_ver
    deployment.files_json = json.dumps(storage_files, ensure_ascii=False)
    if secrets_map:
        deployment.secrets_encrypted = encrypt_str(
            json.dumps(secrets_map, ensure_ascii=False)
        )
    deployment.drift_status = "in_sync"
    deployment.updated_at = datetime.utcnow()
    session.add(deployment)
    session.commit()
    session.refresh(deployment)

    return {
        "ok": True,
        "deployment_id": deployment.id,
        "config_version": next_ver,
        "files": sorted(storage_files.keys()),
        "project_path": path,
        "secrets_refreshed": sorted(
            k
            for k in secrets_map
            if k in host_env and str(host_env.get(k) or "").strip()
        ),
    }


def migrate_host_env_into_deployment(
    session: Session,
    *,
    server: Server,
    deployment: StackDeployment,
    secret_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Pull host .env into PiHerder encrypted secrets (and non-secret public vars).

    By default only updates known secret keys already in the deployment (or template).
    Pass secret_keys to limit; empty list means all host keys that match secret names
    from the template definition when available.
    """
    from .deploy import decrypt_deployment_secrets, merge_secrets_into_env_files

    path = project_path_for(server, deployment.project_name)
    live = get_project_live_files(server, path)
    host_env = parse_env_file(live.get(".env") or "")
    if not host_env:
        raise TemplateError(
            f"No .env on host at {path}/.env — create secrets on the host or redeploy from PiHerder first"
        )

    secrets_map = decrypt_deployment_secrets(deployment)
    public = json.loads(deployment.variables_json or "{}") or {}
    secret_name_set = secret_names_for_deployment(
        session, deployment, known_secrets=secrets_map
    )

    if secret_keys is not None:
        targets = [k for k in secret_keys if k]
    else:
        targets = list(secret_name_set) if secret_name_set else list(host_env.keys())

    updated_secrets: Dict[str, str] = {}
    updated_public: Dict[str, str] = {}
    skipped: List[str] = []
    for k in targets:
        if k not in host_env:
            skipped.append(k)
            continue
        val = str(host_env[k])
        if k in secret_name_set or (k not in public and k in secrets_map):
            updated_secrets[k] = val
        elif looks_like_secret_name(k):
            updated_secrets[k] = val
        else:
            if k in public or k == "PROJECT_NAME":
                updated_public[k] = val
            else:
                skipped.append(k)

    if not updated_secrets and not updated_public:
        raise TemplateError(
            "No matching keys to import from host .env "
            f"(looked for {len(targets)} secret/public keys; host has {len(host_env)} keys)"
        )

    secrets_map.update(updated_secrets)
    public.update(updated_public)
    deployment.variables_json = json.dumps(public, ensure_ascii=False)
    deployment.secrets_encrypted = (
        encrypt_str(json.dumps(secrets_map, ensure_ascii=False)) if secrets_map else None
    )
    deployment.updated_at = datetime.utcnow()
    try:
        files = json.loads(deployment.files_json or "{}") or {}
        files = merge_secrets_into_env_files(files, secrets_map)
        deployment.files_json = json.dumps(
            files_for_db_storage(files, secrets_map), ensure_ascii=False
        )
    except Exception as e:
        logger.debug("env migrate files refresh: %s", e)
    session.add(deployment)
    session.commit()
    session.refresh(deployment)

    return {
        "ok": True,
        "deployment_id": deployment.id,
        "imported_secrets": sorted(updated_secrets.keys()),
        "imported_public": sorted(updated_public.keys()),
        "skipped": skipped[:30],
        "host_env_keys": len(host_env),
    }
