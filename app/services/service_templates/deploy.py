"""Apply service templates to hosts; store desired state."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from ...models import Server, StackDeployment, DockerVersion, ServiceTemplate
from ...security.encryption import encrypt_str, decrypt_str
from .. import docker_inventory
from ..docker_versions import (
    create_new_docker_project,
    save_draft_version,
    files_for_sftp,
)
from ..ssh import docker_base_expanded, get_ssh_client, run_command
from .catalog import get_template_definition
from .schema import (
    TemplateDefinition,
    TemplateError,
    files_for_db_storage,
    mask_secrets_in_files,
    merge_variable_values,
    redact_files_for_ui,
    render_checklist,
    render_template_files,
    split_secrets,
    validate_project_name,
)

logger = logging.getLogger(__name__)


def merge_secrets_into_env_files(
    files: Dict[str, str], secrets_map: Dict[str, str]
) -> Dict[str, str]:
    """Ensure .env holds secret keys for Compose; drop ./secrets/* host files."""
    from .harden import parse_env_file, format_env_file

    out = {k: v for k, v in (files or {}).items() if not str(k).startswith("secrets/")}
    if not secrets_map:
        return out
    env_map = parse_env_file(out.get(".env") or "")
    for sk, sv in secrets_map.items():
        if sv is not None and str(sv) != "":
            env_map[sk] = str(sv)
    out[".env"] = format_env_file(env_map, as_placeholders=False)
    return out


def lockdown_host_env_file(server: Server, project_path: str) -> None:
    """chmod 600 .env on the host (home-production locked-down secrets model)."""
    import shlex

    path = f"{project_path}/.env".replace("//", "/")
    q = shlex.quote(path)
    client = get_ssh_client(server)
    try:
        run_command(client, f"test -f {q} && chmod 600 {q} || true", timeout=20)
    finally:
        client.close()


def host_picker_rows(session: Session) -> List[Dict[str, Any]]:
    """Servers with Docker feature on + inventory counts."""
    servers = session.exec(
        select(Server).where(Server.container_patch_enabled == True).order_by(Server.name)  # noqa: E712
    ).all()
    rows = []
    for s in servers:
        meta = docker_inventory.inventory_meta(s)
        rows.append(
            {
                "id": s.id,
                "name": s.name,
                "hostname": s.hostname,
                "ip_address": s.ip_address,
                "docker_base_dir": s.docker_base_dir,
                "project_count": meta.get("project_count"),
                "container_count": meta.get("container_count"),
                "inventory_status": meta.get("status"),
            }
        )
    return rows


def preview_template(
    session: Session,
    *,
    slug: Optional[str] = None,
    template_id: Optional[int] = None,
    values: Dict[str, str],
    auto_generate: bool = True,
) -> Dict[str, Any]:
    definition = get_template_definition(session, slug=slug, template_id=template_id)
    merged = merge_variable_values(definition, values, auto_generate=auto_generate)
    project = validate_project_name(
        merged.get("PROJECT_NAME") or definition.var_map().get("PROJECT_NAME") and definition.var_map()["PROJECT_NAME"].default or definition.slug
    )
    merged["PROJECT_NAME"] = project
    files = render_template_files(definition, merged)
    public, secrets_map = split_secrets(definition, merged)
    masked = redact_files_for_ui(
        files, secret_values=secrets_map, secret_keys=list(secrets_map.keys()), reveal=False
    )
    # Checklist uses public values only (no secret interpolation in UI)
    checklist = render_checklist(definition, {**public, "PROJECT_NAME": project})
    return {
        "definition": definition.to_public_dict(),
        "project_name": project,
        "values_public": public,
        "secret_keys": list(secrets_map.keys()),
        "files_masked": masked,
        "files_raw": files,  # caller must not log / not send to browser
        "secrets": secrets_map,  # caller must not log / not send to browser cleartext
        "checklist": checklist,
    }


def save_desired_state(
    session: Session,
    *,
    server_id: int,
    project_name: str,
    template: ServiceTemplate,
    definition: TemplateDefinition,
    public_vars: Dict[str, str],
    secrets_map: Dict[str, str],
    files: Dict[str, str],
    config_version: Optional[int] = None,
) -> StackDeployment:
    project_name = validate_project_name(project_name)
    existing = session.exec(
        select(StackDeployment).where(
            StackDeployment.server_id == server_id,
            StackDeployment.project_name == project_name,
        )
    ).first()
    secrets_encrypted = encrypt_str(json.dumps(secrets_map, ensure_ascii=False)) if secrets_map else None
    # files_json must NOT hold cleartext secrets — only structure + non-secret content.
    # Secret values live solely in secrets_encrypted (Fernet). Redeploy merges them into .env.
    storage_files = files_for_db_storage(files or {}, secrets_map or {})
    next_ver = 1
    if existing:
        next_ver = int(existing.config_version or 0) + 1
    if config_version is not None:
        next_ver = config_version

    if existing is None:
        dep = StackDeployment(
            server_id=server_id,
            project_name=project_name,
            template_id=template.id,
            template_slug=template.slug,
            template_version=definition.version,
            config_version=next_ver,
            variables_json=json.dumps(public_vars, ensure_ascii=False),
            secrets_encrypted=secrets_encrypted,
            files_json=json.dumps(storage_files, ensure_ascii=False),
            drift_status="unknown",
            last_deployed_at=datetime.utcnow(),
        )
        session.add(dep)
    else:
        existing.template_id = template.id
        existing.template_slug = template.slug
        existing.template_version = definition.version
        existing.config_version = next_ver
        existing.variables_json = json.dumps(public_vars, ensure_ascii=False)
        existing.secrets_encrypted = secrets_encrypted
        existing.files_json = json.dumps(storage_files, ensure_ascii=False)
        existing.drift_status = "unknown"
        existing.last_deployed_at = datetime.utcnow()
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        dep = existing
    session.commit()
    session.refresh(dep)
    return dep


def get_deployment(session: Session, deployment_id: int) -> Optional[StackDeployment]:
    return session.get(StackDeployment, deployment_id)


def list_deployments_for_server(session: Session, server_id: int) -> List[StackDeployment]:
    return list(
        session.exec(
            select(StackDeployment)
            .where(StackDeployment.server_id == server_id)
            .order_by(StackDeployment.project_name)
        ).all()
    )


def deployments_index_by_project(
    session: Session, server_id: int
) -> Dict[str, Dict[str, Any]]:
    """Map compose project_name → lightweight template deployment info for Docker UI."""
    out: Dict[str, Dict[str, Any]] = {}
    for dep in list_deployments_for_server(session, server_id):
        name = (dep.project_name or "").strip()
        if not name:
            continue
        out[name] = {
            "deployment_id": dep.id,
            "template_slug": dep.template_slug,
            "template_version": dep.template_version,
            "config_version": dep.config_version,
            "drift_status": dep.drift_status or "unknown",
            "last_deployed_at": dep.last_deployed_at,
        }
    return out


def annotate_projects_with_deployments(
    projects: List[Dict[str, Any]],
    by_project: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach template_managed metadata onto inventory project dicts (in place + return)."""
    for proj in projects or []:
        name = (proj.get("name") or "").strip()
        meta = by_project.get(name) if name else None
        if meta:
            proj["template_managed"] = True
            proj["template_deployment_id"] = meta.get("deployment_id")
            proj["template_slug"] = meta.get("template_slug")
            proj["template_version"] = meta.get("template_version")
            proj["template_config_version"] = meta.get("config_version")
            proj["template_drift_status"] = meta.get("drift_status")
        else:
            proj["template_managed"] = False
            proj.pop("template_deployment_id", None)
            proj.pop("template_slug", None)
            proj.pop("template_version", None)
            proj.pop("template_config_version", None)
            proj.pop("template_drift_status", None)
    return projects


def get_deployment_for_project(
    session: Session, server_id: int, project_name: str
) -> Optional[StackDeployment]:
    project_name = (project_name or "").strip()
    if not project_name:
        return None
    return session.exec(
        select(StackDeployment).where(
            StackDeployment.server_id == server_id,
            StackDeployment.project_name == project_name,
        )
    ).first()


def decrypt_deployment_secrets(dep: StackDeployment) -> Dict[str, str]:
    if not dep.secrets_encrypted:
        return {}
    try:
        raw = decrypt_str(dep.secrets_encrypted)
        data = json.loads(raw) if raw else {}
        return {str(k): str(v) for k, v in (data or {}).items()}
    except Exception as e:
        logger.warning("Could not decrypt deployment secrets: %s", e)
        return {}


def apply_template_to_host(
    session: Session,
    *,
    server: Server,
    template_slug: Optional[str] = None,
    template_id: Optional[int] = None,
    values: Dict[str, str],
    deploy_now: bool = True,
    auto_generate: bool = True,
) -> Dict[str, Any]:
    """Render, write to host, optional compose up, save desired state + DockerVersion."""
    if not server.container_patch_enabled:
        raise TemplateError("Docker / containers feature is disabled on this host")

    definition = get_template_definition(session, slug=template_slug, template_id=template_id)
    from .catalog import get_template_row

    row = get_template_row(session, slug=template_slug or definition.slug, template_id=template_id)
    if not row:
        raise TemplateError("Template row missing")

    merged = merge_variable_values(definition, values, auto_generate=auto_generate)
    project = validate_project_name(
        merged.get("PROJECT_NAME")
        or (definition.var_map().get("PROJECT_NAME").default if definition.var_map().get("PROJECT_NAME") else definition.slug)
    )
    merged["PROJECT_NAME"] = project
    files = render_template_files(definition, merged)
    public, secrets_map = split_secrets(definition, merged)
    checklist = render_checklist(definition, merged)

    # Home-production model: secrets encrypted in PiHerder; host uses locked-down .env.
    files = merge_secrets_into_env_files(files, secrets_map)

    base = docker_base_expanded(server)
    project_path = f"{base}/{project}".replace("//", "/")

    # Create project on host
    ok = create_new_docker_project(server, project, files_for_sftp(files))
    if not ok:
        raise TemplateError("Failed to create project files on host")

    try:
        lockdown_host_env_file(server, project_path)
    except Exception as e:
        logger.warning("lockdown .env failed: %s", e)

    redeploy_result: Optional[Dict[str, Any]] = None
    if deploy_now:
        try:
            from ..docker_management import redeploy_project

            redeploy_result = redeploy_project(server, project_path, pull=True)
        except Exception as e:
            logger.warning("redeploy after template apply failed: %s", e)
            redeploy_result = {"ok": False, "error": str(e)[:300]}

    dep = save_desired_state(
        session,
        server_id=server.id,
        project_name=project,
        template=row,
        definition=definition,
        public_vars=public,
        secrets_map=secrets_map,
        files=files,
    )

    # Also snapshot into DockerVersion history (non-secret aware — full files for edit UX)
    try:
        save_draft_version(server.id, project, files, session=session)
        from ..docker_versions import get_versions

        vers = get_versions(server.id, project, limit=5, session=session)
        if vers:
            latest = vers[0]
            latest.is_draft = False
            latest.deployed_at = datetime.utcnow()
            session.add(latest)
            session.commit()
    except Exception as e:
        logger.warning("DockerVersion snapshot after template: %s", e)

    try:
        docker_inventory.invalidate_after_mutation(session, server)
    except Exception:
        pass

    return {
        "ok": True,
        "deployment_id": dep.id,
        "config_version": dep.config_version,
        "project_name": project,
        "project_path": project_path,
        "server_id": server.id,
        "template_slug": definition.slug,
        "checklist": checklist,
        "redeploy": redeploy_result,
        "secret_keys": list(secrets_map.keys()),
    }


def redeploy_desired_state(
    session: Session,
    *,
    server: Server,
    deployment: StackDeployment,
    updated_public: Optional[Dict[str, str]] = None,
    updated_secrets: Optional[Dict[str, str]] = None,
    deploy_now: bool = True,
) -> Dict[str, Any]:
    """Update variables/secrets, re-render if template still available, or re-write stored files."""
    files = json.loads(deployment.files_json or "{}")
    public = json.loads(deployment.variables_json or "{}")
    secrets_map = decrypt_deployment_secrets(deployment)

    if updated_public:
        public.update({k: str(v) for k, v in updated_public.items()})
    if updated_secrets:
        secrets_map.update({k: str(v) for k, v in updated_secrets.items() if v is not None and str(v) != ""})

    # Prefer re-render from template if still present
    try:
        if deployment.template_slug:
            definition = get_template_definition(session, slug=deployment.template_slug)
            values = {**public, **secrets_map}
            values["PROJECT_NAME"] = deployment.project_name
            # Re-merge so volume/boolean vars normalize (mount lines, modes, top-level volumes)
            values = merge_variable_values(definition, values, auto_generate=False)
            values["PROJECT_NAME"] = deployment.project_name
            files = render_template_files(definition, values)
            public, secrets_map = split_secrets(definition, values)
    except Exception as e:
        logger.info("Redeploy using stored files (template re-render skipped): %s", e)
        # Patch .env keys if present
        if ".env" in files and secrets_map:
            env_lines = []
            existing_keys = set()
            for line in (files.get(".env") or "").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k = line.split("=", 1)[0].strip()
                    existing_keys.add(k)
                    if k in secrets_map:
                        env_lines.append(f"{k}={secrets_map[k]}")
                    elif k in public:
                        env_lines.append(f"{k}={public[k]}")
                    else:
                        env_lines.append(line)
                else:
                    env_lines.append(line)
            files[".env"] = "\n".join(env_lines) + ("\n" if env_lines else "")

    files = merge_secrets_into_env_files(files, secrets_map)

    base = docker_base_expanded(server)
    project_path = f"{base}/{deployment.project_name}".replace("//", "/")
    from ..docker_versions import write_project_files

    ok, werr = write_project_files(server, project_path, files)
    if not ok:
        raise TemplateError(f"Failed to write files: {werr}")
    try:
        lockdown_host_env_file(server, project_path)
    except Exception as e:
        logger.warning("lockdown .env failed: %s", e)

    redeploy_result = None
    if deploy_now:
        from ..docker_management import redeploy_project

        redeploy_result = redeploy_project(server, project_path, pull=True)

    row = None
    if deployment.template_id:
        row = session.get(ServiceTemplate, deployment.template_id)
    if row is None and deployment.template_slug:
        from .catalog import get_template_row

        row = get_template_row(session, slug=deployment.template_slug)

    if row:
        try:
            definition = get_template_definition(session, template_id=row.id)
        except Exception:
            definition = None
    else:
        definition = None

    if row and definition:
        dep = save_desired_state(
            session,
            server_id=server.id,
            project_name=deployment.project_name,
            template=row,
            definition=definition,
            public_vars=public,
            secrets_map=secrets_map,
            files=files,
        )
    else:
        deployment.config_version = int(deployment.config_version or 0) + 1
        deployment.variables_json = json.dumps(public, ensure_ascii=False)
        deployment.secrets_encrypted = (
            encrypt_str(json.dumps(secrets_map, ensure_ascii=False)) if secrets_map else None
        )
        deployment.files_json = json.dumps(files, ensure_ascii=False)
        deployment.last_deployed_at = datetime.utcnow()
        deployment.updated_at = datetime.utcnow()
        deployment.drift_status = "unknown"
        session.add(deployment)
        session.commit()
        session.refresh(deployment)
        dep = deployment

    try:
        docker_inventory.invalidate_after_mutation(session, server)
    except Exception:
        pass

    return {
        "ok": True,
        "deployment_id": dep.id,
        "config_version": dep.config_version,
        "redeploy": redeploy_result,
    }
