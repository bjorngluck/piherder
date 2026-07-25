"""Compose multi-file editor workspace load (host + template desired + drafts).

Keeps HTTP handlers thin: inventory resolve, live read, desired-state sidecars,
and draft selection live here rather than in the router.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlmodel import Session

from ..models import Server, StackDeployment
from . import docker_management as docker_svc
from .compose_project_files import merge_desired_missing_into_live
from .ssh import docker_base_expanded

logger = logging.getLogger("piherder.compose_editor")


class ComposeEditorNotFound(Exception):
    """Project path cannot be resolved for the editor."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class ComposeEditorWorkspace:
    """Files and metadata for the multi-file compose editor page."""

    project_name: str
    proj: Dict[str, Any]
    live_files: Dict[str, str]
    project_files: Dict[str, str]
    drafts: List[Any] = field(default_factory=list)
    editing_version_id: Optional[int] = None
    live_compose_key: Optional[str] = None
    live_compose: str = ""
    template_dep: Optional[StackDeployment] = None


def get_template_deployment(
    session: Session, server_id: int, project: str
) -> Optional[StackDeployment]:
    try:
        from .service_templates.deploy import get_deployment_for_project

        return get_deployment_for_project(session, server_id, project)
    except Exception as e:
        logger.debug("template deployment lookup failed: %s", e)
        return None


def resolve_project_and_live_files(
    server: Server,
    project: str,
    *,
    inventory_projects: Optional[List[Dict[str, Any]]] = None,
    template_dep: Optional[StackDeployment] = None,
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Locate compose project path and read live host files (single SSH pass).

    Prefer inventory entry; fall back to ``docker_base/project`` when the folder
    exists on disk or a template deployment is linked.
    """
    projects = inventory_projects
    if projects is None:
        try:
            projects = docker_svc.list_compose_projects(server)
        except Exception as e:
            logger.warning(
                "list_compose_projects failed server=%s: %s", getattr(server, "id", "?"), e
            )
            projects = []

    proj = next((p for p in projects if p.get("name") == project), None)
    live_files: Dict[str, str] = {}

    if not proj:
        base = docker_base_expanded(server)
        fallback_path = f"{base}/{project}".replace("//", "/")
        try:
            live_files = docker_svc.get_project_live_files(server, fallback_path) or {}
        except Exception as e:
            logger.warning("compose edit fallback path failed project=%s: %s", project, e)
            raise ComposeEditorNotFound(
                f"Compose project {project!r} not found"
            ) from e
        if live_files or template_dep is not None:
            proj = {"name": project, "path": fallback_path}
        else:
            raise ComposeEditorNotFound(
                f"Compose project {project!r} not found under {base}"
            )
    else:
        live_files = docker_svc.get_project_live_files(server, proj["path"]) or {}

    return proj, live_files


def merge_template_desired_sidecars(
    project_files: Dict[str, str],
    template_dep: Optional[StackDeployment],
    *,
    project: str = "",
) -> Dict[str, str]:
    """Fill editor tabs missing on the host from deployment desired-state files."""
    if template_dep is None:
        return dict(project_files or {})
    try:
        desired = json.loads(template_dep.files_json or "{}") or {}
        if not isinstance(desired, dict):
            desired = {}
        return merge_desired_missing_into_live(project_files, desired)
    except Exception as e:
        logger.warning(
            "merge desired files into editor failed project=%s: %s", project, e
        )
        return dict(project_files or {})


def apply_draft_snapshot(
    project_files: Dict[str, str],
    drafts: List[Any],
    load_draft_id: Optional[str],
) -> tuple[Dict[str, str], Optional[int]]:
    """If load_draft is set, merge that version snapshot into project_files."""
    editing_version_id: Optional[int] = None
    if not load_draft_id:
        return dict(project_files or {}), None
    try:
        dv = next((d for d in drafts if str(d.id) == str(load_draft_id)), None)
        if not dv:
            return dict(project_files or {}), None
        f = docker_svc.parse_version_files(dv)
        out = dict(project_files or {})
        if f:
            out = docker_svc.merge_project_files(out, f)
        if getattr(dv, "is_draft", False):
            editing_version_id = dv.id
        return out, editing_version_id
    except Exception as e:
        logger.debug("apply draft snapshot failed: %s", e)
        return dict(project_files or {}), None


def load_compose_editor_workspace(
    session: Session,
    server: Server,
    project: str,
    *,
    load_draft_id: Optional[str] = None,
) -> ComposeEditorWorkspace:
    """Full editor workspace: inventory/fallback path, live files, desired, drafts."""
    template_dep = get_template_deployment(session, server.id, project)

    projects: List[Dict[str, Any]] = []
    try:
        projects = docker_svc.list_compose_projects(server)
    except Exception as e:
        logger.warning("list_compose_projects failed server=%s: %s", server.id, e)

    proj, live_files = resolve_project_and_live_files(
        server,
        project,
        inventory_projects=projects,
        template_dep=template_dep,
    )

    project_files = merge_template_desired_sidecars(
        dict(live_files), template_dep, project=project
    )
    drafts = list(docker_svc.get_versions(server.id, project, limit=10) or [])
    project_files, editing_version_id = apply_draft_snapshot(
        project_files, drafts, load_draft_id
    )

    live_compose_key = docker_svc.primary_compose_key(live_files)
    live_compose = live_files.get(live_compose_key, "") if live_compose_key else ""

    return ComposeEditorWorkspace(
        project_name=project,
        proj=proj,
        live_files=live_files,
        project_files=project_files,
        drafts=drafts,
        editing_version_id=editing_version_id,
        live_compose_key=live_compose_key,
        live_compose=live_compose or "",
        template_dep=template_dep,
    )
