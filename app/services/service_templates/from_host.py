"""Create a template draft from an existing Docker project on a fleet host."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...models import Server
from ..docker_versions import (
    COMPOSE_BASENAMES,
    get_project_live_files,
    primary_compose_key,
)
from ..ssh import docker_base_expanded
from .harden import move_secrets_to_env
from .schema import TemplateError

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def project_to_slug(project_name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (project_name or "").lower()).strip("-")
    s = re.sub(r"-+", "-", s)
    if not s or not _SLUG_RE.match(s):
        s = "imported-stack"
    return s[:63]


def pull_project_as_editor_form(
    server: Server,
    project_name: str,
    *,
    auto_harden_env: bool = True,
) -> Dict[str, Any]:
    """SSH-read project files and return an editor form dict + messages."""
    project_name = (project_name or "").strip()
    if not project_name or "/" in project_name or ".." in project_name:
        raise TemplateError("Invalid project name")

    base = docker_base_expanded(server)
    path = f"{base}/{project_name}".replace("//", "/")
    files = get_project_live_files(server, path)
    if not files:
        raise TemplateError(f"No compose files found under {path}")

    compose_key = primary_compose_key(files)
    if not compose_key:
        # try any yml
        for k in COMPOSE_BASENAMES:
            if k in files:
                compose_key = k
                break
    if not compose_key or compose_key not in files:
        raise TemplateError("Could not find docker-compose.yml / compose.yml on host")

    compose = files.get(compose_key) or ""
    env = files.get(".env") or files.get(".env.example") or ""
    messages: List[str] = [f"Loaded {compose_key}" + (" and .env" if env else "") + f" from {server.name}:{path}"]

    extracted_defaults: Dict[str, str] = {}
    if auto_harden_env:
        compose, env, extracted_defaults, msgs = move_secrets_to_env(compose, env)
        messages.extend(msgs)

    slug = project_to_slug(project_name)
    # Volumes, host ports, booleans, env keys → deploy variables; rewrite compose mounts/ports
    from .harden import (
        build_variables_for_host_project,
        looks_like_secret_name,
    )
    from .editor import redact_env_plaintext_secrets

    compose, variables, param_msgs = build_variables_for_host_project(
        compose,
        env,
        project_name_default=project_name,
        parameterize=True,
    )
    messages.extend(param_msgs)

    # Never leave cleartext secrets as template variable defaults (enter at deploy / 2FA edit).
    for v in variables:
        name = v.get("name") or ""
        vtype = v.get("type") or "string"
        # Volumes / booleans / ports stay non-secret with host-derived defaults
        if vtype in ("volume", "boolean", "port"):
            v["secret"] = False
            v["generate"] = False
            continue
        if v.get("secret") or looks_like_secret_name(name):
            v["secret"] = True
            if name in extracted_defaults and extracted_defaults[name]:
                # Keep only if caller later re-injects with reveal; default empty for UI safety
                v["default"] = ""
                v["generate"] = False
                help_extra = (
                    " (value captured from host — enter at deploy; not stored in template cleartext)"
                )
                v["help"] = ((v.get("help") or "") + help_extra).strip()
            else:
                v["default"] = ""

    import json

    form = {
        "slug": slug,
        "name": project_name.replace("-", " ").replace("_", " ").title(),
        "description": f"Templated from {server.name}:{project_name}",
        "category": "other",
        "version": "1.0.0",
        "compose_content": compose,
        "env_content": redact_env_plaintext_secrets(env, reveal=False),
        "variables_json": json.dumps(variables, indent=2, ensure_ascii=False),
        "checklist_json": json.dumps(
            [
                {
                    "title": "DNS (manual)",
                    "body": "Create A/AAAA records for this service pointing at the host IP.",
                },
                {
                    "title": "Review secrets & storage",
                    "body": (
                        "Enter secret values at deploy (encrypted in PiHerder). "
                        "Confirm volume mode (named / project folder / host path) and ports. "
                        "Host .env is written mode 600."
                    ),
                },
            ],
            indent=2,
        ),
        "source": "user",
        "use_docker_secrets": False,
    }
    if extracted_defaults:
        messages.append(
            f"Detected {len(extracted_defaults)} secret-like value(s) — not stored as template defaults. "
            "Enter them when you deploy (or edit with 2FA if you paste defaults)."
        )
        # Stash for optional one-shot deploy convenience is intentionally not returned as cleartext
        form["_extracted_secret_keys"] = list(extracted_defaults.keys())
    return {
        "form": form,
        "messages": messages,
        "server_id": server.id,
        "project_name": project_name,
        "path": path,
        # Only for server-side deploy paths that need them immediately — not put in HTML form defaults
        "extracted_secrets": extracted_defaults,
    }


def list_host_projects_for_picker(server: Server) -> List[Dict[str, Any]]:
    """Use docker inventory snapshot if present — include services/containers for UI."""
    from .. import docker_inventory

    inv = docker_inventory.parse_inventory(server) or {}
    projects = inv.get("projects") or inv.get("stacks") or []
    out: List[Dict[str, Any]] = []
    if not isinstance(projects, list):
        return out
    for p in projects:
        if isinstance(p, str):
            out.append(
                {
                    "name": p,
                    "container_count": None,
                    "running_count": None,
                    "services_label": "",
                    "containers": [],
                }
            )
            continue
        if not isinstance(p, dict):
            continue
        name = p.get("name") or p.get("project") or p.get("dir")
        if not name:
            continue
        containers_raw = p.get("containers") or []
        containers = []
        service_names = []
        for c in containers_raw:
            if not isinstance(c, dict):
                continue
            cname = c.get("name") or c.get("compose_service") or ""
            svc = c.get("compose_service") or c.get("service") or ""
            if svc and svc not in service_names:
                service_names.append(str(svc))
            if cname:
                containers.append(
                    {
                        "name": str(cname),
                        "service": str(svc) if svc else "",
                        "running": bool(c.get("running")),
                    }
                )
        # services list on project if present
        for s in p.get("services") or []:
            if isinstance(s, str) and s not in service_names:
                service_names.append(s)
            elif isinstance(s, dict):
                sn = s.get("name") or s.get("service")
                if sn and str(sn) not in service_names:
                    service_names.append(str(sn))

        out.append(
            {
                "name": str(name),
                "container_count": p.get("container_count")
                if p.get("container_count") is not None
                else len(containers),
                "running_count": p.get("running_count"),
                "services_label": ", ".join(service_names[:12]),
                "containers": containers,
            }
        )
    # Stable sort by name
    out.sort(key=lambda r: (r.get("name") or "").lower())
    return out
