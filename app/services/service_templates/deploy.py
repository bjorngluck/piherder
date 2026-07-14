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
        "project_name": dep.project_name,
        "server_id": server.id,
        "project_path": project_path,
    }


def apply_last_known_config(
    session: Session,
    *,
    server: Server,
    deployment: StackDeployment,
    deploy_now: bool = True,
) -> Dict[str, Any]:
    """Re-apply stored desired state to the host (after wipe / DR) without form edits."""
    return redeploy_desired_state(
        session,
        server=server,
        deployment=deployment,
        updated_public=None,
        updated_secrets=None,
        deploy_now=deploy_now,
    )


def _normalize_compose_text(text: str) -> str:
    """Stable compare: strip trailing whitespace per line, collapse blank runs."""
    lines = [(ln.rstrip()) for ln in (text or "").replace("\r\n", "\n").split("\n")]
    # Drop trailing empty lines
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _project_path_for(server: Server, project_name: str) -> str:
    base = docker_base_expanded(server)
    return f"{base}/{project_name}".replace("//", "/")


def check_deployment_drift(
    session: Session,
    *,
    server: Server,
    deployment: StackDeployment,
) -> Dict[str, Any]:
    """Compare host compose/.env to PiHerder desired-state files; update drift_status.

    Returns status in_sync | drifted | unknown (SSH/missing path failures).
    """
    from ..docker_versions import get_project_live_files, primary_compose_key

    desired = json.loads(deployment.files_json or "{}") or {}
    # Merge secrets for host-equivalent .env compare (host has cleartext secrets)
    secrets_map = decrypt_deployment_secrets(deployment)
    desired_full = merge_secrets_into_env_files(dict(desired), secrets_map)

    path = _project_path_for(server, deployment.project_name)
    try:
        live = get_project_live_files(server, path)
    except Exception as e:
        logger.warning("drift check SSH failed dep=%s: %s", deployment.id, e)
        deployment.drift_status = "unknown"
        deployment.updated_at = datetime.utcnow()
        session.add(deployment)
        session.commit()
        return {
            "status": "unknown",
            "error": str(e)[:200],
            "deployment_id": deployment.id,
            "project_path": path,
            "diffs": [],
        }

    if not live:
        deployment.drift_status = "drifted"
        deployment.updated_at = datetime.utcnow()
        session.add(deployment)
        session.commit()
        return {
            "status": "drifted",
            "reason": "no_files_on_host",
            "deployment_id": deployment.id,
            "project_path": path,
            "diffs": [{"file": "*", "detail": "no compose/.env found on host"}],
        }

    diffs: List[Dict[str, str]] = []
    # Compare primary compose (+ override if stored)
    for key in list(desired_full.keys()):
        if key.startswith("secrets/"):
            continue
        want = desired_full.get(key) or ""
        # .env: compare keys that exist in desired (host may have extra)
        if key == ".env":
            from .harden import parse_env_file

            want_map = parse_env_file(want)
            have_map = parse_env_file(live.get(".env") or "")
            missing = [k for k in want_map if k not in have_map]
            changed = [
                k
                for k in want_map
                if k in have_map and str(want_map[k]) != str(have_map[k])
            ]
            if missing or changed:
                diffs.append(
                    {
                        "file": ".env",
                        "detail": (
                            f"keys missing={len(missing)} changed={len(changed)}"
                            + (f" ({', '.join((missing + changed)[:6])})" if (missing or changed) else "")
                        ),
                    }
                )
            continue
        have = live.get(key)
        if have is None:
            # try alternate compose basenames for primary compose
            if key in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                alt = primary_compose_key(live)
                if alt and alt != key:
                    have = live.get(alt)
                    if have is not None:
                        key_label = f"{key} (host: {alt})"
                    else:
                        key_label = key
                else:
                    key_label = key
            else:
                key_label = key
            if have is None:
                diffs.append({"file": key, "detail": "missing on host"})
                continue
        else:
            key_label = key
        if _normalize_compose_text(want) != _normalize_compose_text(have or ""):
            diffs.append({"file": key_label, "detail": "content differs"})

    status = "in_sync" if not diffs else "drifted"
    deployment.drift_status = status
    deployment.updated_at = datetime.utcnow()
    session.add(deployment)
    session.commit()
    session.refresh(deployment)

    # Notify / resolve
    try:
        from .. import notifications as notif_svc

        fp = f"template_drift:deployment:{deployment.id}"
        if status == "drifted":
            notif_svc.upsert_notification(
                session,
                fingerprint=fp,
                type="template_drift",
                title=f"Config drift: {deployment.project_name}",
                body=(
                    f"Host differs from desired state V{deployment.config_version} "
                    f"({len(diffs)} file(s))"
                )[:400],
                link_url=f"/templates/deployments/{deployment.id}",
                severity="warning",
                server_id=server.id,
                payload={"diffs": diffs[:20], "deployment_id": deployment.id},
            )
        else:
            notif_svc.resolve_by_fingerprint(session, fp)
    except Exception as e:
        logger.debug("drift notify: %s", e)

    return {
        "status": status,
        "deployment_id": deployment.id,
        "project_path": path,
        "diffs": diffs,
        "config_version": deployment.config_version,
    }


def check_all_deployments_drift(session: Session) -> Dict[str, Any]:
    """Scheduled sweep: check every stack deployment (best-effort)."""
    deps = list(session.exec(select(StackDeployment)).all())
    results = {"checked": 0, "in_sync": 0, "drifted": 0, "unknown": 0, "errors": 0}
    for dep in deps:
        server = session.get(Server, dep.server_id)
        if not server:
            continue
        try:
            r = check_deployment_drift(session, server=server, deployment=dep)
            results["checked"] += 1
            st = r.get("status") or "unknown"
            if st in results:
                results[st] += 1
            else:
                results["unknown"] += 1
        except Exception as e:
            logger.warning("drift sweep dep=%s: %s", dep.id, e)
            results["errors"] += 1
    return results


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
    from ..docker_versions import get_project_live_files
    from .harden import parse_env_file

    path = _project_path_for(server, deployment.project_name)
    live = get_project_live_files(server, path)
    host_env = parse_env_file(live.get(".env") or "")
    if not host_env:
        raise TemplateError(
            f"No .env on host at {path}/.env — create secrets on the host or redeploy from PiHerder first"
        )

    secrets_map = decrypt_deployment_secrets(deployment)
    public = json.loads(deployment.variables_json or "{}") or {}

    # Determine which keys are secrets
    secret_name_set = set(secrets_map.keys())
    try:
        if deployment.template_slug:
            definition = get_template_definition(session, slug=deployment.template_slug)
            for v in definition.variables:
                if v.secret or v.type == "password":
                    secret_name_set.add(v.name)
    except Exception:
        pass

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
        elif k in secret_name_set or k.endswith(("_PASSWORD", "_SECRET", "_TOKEN", "_KEY")):
            updated_secrets[k] = val
        else:
            # Only update public if already tracked as a variable
            if k in public or k == "PROJECT_NAME":
                updated_public[k] = val
            else:
                skipped.append(k)

    if not updated_secrets and not updated_public:
        raise TemplateError(
            "No matching keys to import from host .env "
            f"(looked for {len(targets)} secret/public keys; host has {len(host_env)} keys)"
        )

    # Persist secrets + public without necessarily redeploying compose
    secrets_map.update(updated_secrets)
    public.update(updated_public)
    deployment.variables_json = json.dumps(public, ensure_ascii=False)
    deployment.secrets_encrypted = (
        encrypt_str(json.dumps(secrets_map, ensure_ascii=False)) if secrets_map else None
    )
    deployment.updated_at = datetime.utcnow()
    # Refresh stored files .env structure (placeholders for secrets)
    try:
        files = json.loads(deployment.files_json or "{}") or {}
        files = merge_secrets_into_env_files(files, secrets_map)
        from .schema import files_for_db_storage

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


def volume_fields_for_ui(
    public: Dict[str, Any],
    definition: Optional[TemplateDefinition],
) -> List[Dict[str, Any]]:
    """Build volume editor rows from stored public vars + template metadata."""
    rows: List[Dict[str, Any]] = []
    if not definition:
        return rows
    for var in definition.variables:
        if var.type != "volume":
            continue
        mode = str(public.get(f"{var.name}__mode") or var.volume_default_mode or "named")
        source = str(public.get(f"{var.name}__source") or "")
        raw = str(public.get(var.name) or "")
        tgt = (var.volume_target or "").strip()
        if not source and raw:
            # raw is often "source:target" mount
            if tgt and raw.endswith(":" + tgt):
                source = raw[: -(len(tgt) + 1)]
            elif ":" in raw:
                source = raw.rsplit(":", 1)[0]
            else:
                source = raw
        rows.append(
            {
                "name": var.name,
                "label": var.label or var.name,
                "mode": mode,
                "source": source,
                "volume_target": tgt,
                "help": var.help or "",
                "required": bool(var.required),
            }
        )
    return rows


def public_vars_excluding_volume_meta(public: Dict[str, Any], definition: Optional[TemplateDefinition]) -> Dict[str, str]:
    """Public variables for simple text fields (skip volume mounts + __mode/__source)."""
    skip = set()
    if definition:
        for var in definition.variables:
            if var.type == "volume":
                skip.add(var.name)
                skip.add(f"{var.name}__mode")
                skip.add(f"{var.name}__source")
    out = {}
    for k, v in (public or {}).items():
        if k in skip or k.startswith("__"):
            continue
        if k.endswith("__mode") or k.endswith("__source"):
            continue
        out[str(k)] = "" if v is None else str(v)
    return out


def matching_backup_sources_for_deployment(
    server: Server, deployment: StackDeployment
) -> List[Dict[str, Any]]:
    """Backup sources whose path overlaps this stack (for restore after wipe)."""
    from .. import backup_profiles
    from ..backup_restore import list_restore_candidates

    project = (deployment.project_name or "").strip()
    if not project:
        return []
    base = docker_base_expanded(server)
    project_path = f"{base}/{project}".replace("//", "/").rstrip("/")
    candidates = list_restore_candidates(server)
    out = []
    for c in candidates:
        src = (c.get("source") or "").rstrip("/")
        if not src:
            continue
        # Match exact project path, parent docker base, or source containing project name
        if (
            src == project_path
            or src.startswith(project_path + "/")
            or project_path.startswith(src + "/")
            or src.endswith("/" + project)
            or f"/{project}/" in src + "/"
        ):
            out.append(c)
    return out
