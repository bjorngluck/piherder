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
    """SSH-read project files and return an editor form dict + messages.

    Handles common edge cases (v0.5.0 A):
    - Multi-file compose (primary + override merged note)
    - Missing ``.env`` (uses ``.env.example`` or empty with a clear message)
    - Odd layouts still fail with path + basenames tried
    """
    project_name = (project_name or "").strip()
    if not project_name or "/" in project_name or ".." in project_name:
        raise TemplateError(
            "Invalid project name — use the compose folder name only "
            "(no path separators)"
        )

    base = docker_base_expanded(server)
    path = f"{base}/{project_name}".replace("//", "/")
    try:
        files = get_project_live_files(server, path)
    except Exception as e:
        raise TemplateError(
            f"Could not read {server.name}:{path} over SSH — {e}"
        ) from e
    if not files:
        raise TemplateError(
            f"No files found under {path} on {server.name}. "
            "Confirm Docker base dir, project folder name, and Force refresh inventory."
        )

    compose_key = primary_compose_key(files)
    if not compose_key:
        # try any yml
        for k in COMPOSE_BASENAMES:
            if k in files:
                compose_key = k
                break
    if not compose_key or compose_key not in files:
        found = ", ".join(sorted(files.keys())[:12]) or "(none)"
        raise TemplateError(
            f"Could not find docker-compose.yml / compose.yml under {path}. "
            f"Files present: {found}"
        )

    compose = files.get(compose_key) or ""
    # Multi-file: note override if present (editor stores single compose body; override kept as hint)
    override_keys = [
        k
        for k in files
        if "override" in k.lower() and k.lower().endswith((".yml", ".yaml"))
    ]
    env = files.get(".env") or ""
    env_from_example = False
    if not env and files.get(".env.example"):
        env = files.get(".env.example") or ""
        env_from_example = True
    messages: List[str] = [
        f"Loaded {compose_key}"
        + (" + .env" if files.get(".env") else "")
        + (f" + {override_keys[0]}" if override_keys else "")
        + f" from {server.name}:{path}"
    ]
    if env_from_example:
        messages.append(
            "No .env on host — used .env.example as a starting point. "
            "Review secrets before deploy (values may be placeholders)."
        )
    elif not env:
        messages.append(
            "No .env or .env.example on host — variables will be inferred from compose only. "
            "Add secrets at deploy time."
        )
    if override_keys:
        messages.append(
            f"Compose override present ({', '.join(override_keys)}). "
            "Primary file is parameterized; re-check override mounts after import."
        )
    # Attach secondary compose files into editor notes if multiple compose basenames
    extra_compose = [
        k
        for k in files
        if k != compose_key
        and k.lower().endswith((".yml", ".yaml"))
        and "override" not in k.lower()
    ]
    if extra_compose:
        messages.append(
            "Additional compose files found: "
            + ", ".join(extra_compose)
            + " — only the primary file is imported into the template body "
            "(sidecar configs from relative mounts are included separately)."
        )

    # Sidecar config files bind-mounted from the project dir (e.g. promtail-config.yaml)
    from .harden import (
        _short_host_label,
        build_variables_for_host_project,
        discover_relative_config_files,
        looks_like_secret_name,
    )
    from .editor import redact_env_plaintext_secrets

    config_names = discover_relative_config_files(compose)
    extra_files: Dict[str, str] = {}
    if config_names:
        missing = [n for n in config_names if n not in files]
        if missing:
            try:
                more = get_project_live_files(server, path, filenames=missing)
                files.update(more or {})
            except Exception as e:
                messages.append(
                    f"Could not read some config files from host ({', '.join(missing)}): {e}"
                )
        for name in config_names:
            body = files.get(name)
            if body is None:
                messages.append(
                    f"Referenced config file {name!r} not found under {path} — mount kept, content missing."
                )
                continue
            extra_files[name] = body
        if extra_files:
            messages.append(
                "Included additional project file(s): "
                + ", ".join(sorted(extra_files.keys()))
                + " (from relative bind mounts in compose)."
            )

    extracted_defaults: Dict[str, str] = {}
    if auto_harden_env:
        compose, env, extracted_defaults, msgs = move_secrets_to_env(compose, env)
        messages.extend(msgs)

    slug = project_to_slug(project_name)
    node = _short_host_label(
        getattr(server, "hostname", "") or "",
        getattr(server, "name", "") or "",
    )
    host_fqdn = (getattr(server, "hostname", "") or "").strip()
    # Only treat hostname as FQDN when it has a dot
    if "." not in host_fqdn:
        host_fqdn = ""

    # Volumes, host ports, booleans, env keys, host names → deploy variables
    compose, variables, param_msgs, extra_files = build_variables_for_host_project(
        compose,
        env,
        project_name_default=project_name,
        parameterize=True,
        extra_file_texts=extra_files,
        node_name=node,
        host_fqdn=host_fqdn,
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

    extra_files_json = json.dumps(
        [{"path": p, "content": extra_files[p]} for p in sorted(extra_files.keys())],
        indent=2,
        ensure_ascii=False,
    )

    form = {
        "slug": slug,
        "name": project_name.replace("-", " ").replace("_", " ").title(),
        "description": f"Templated from {server.name}:{project_name}",
        "category": "other",
        "version": "1.0.0",
        "compose_content": compose,
        "env_content": redact_env_plaintext_secrets(env, reveal=False),
        "extra_files_json": extra_files_json,
        "variables_json": json.dumps(variables, indent=2, ensure_ascii=False),
        "checklist_json": json.dumps(
            [
                {
                    "title": "DNS (manual)",
                    "body": "Create A/AAAA records for this service pointing at the host IP.",
                },
                {
                    "title": "Review secrets, host labels & storage",
                    "body": (
                        "Enter secret values at deploy (encrypted in PiHerder). "
                        "Confirm NODE_NAME / remote URLs, volume modes, and ports. "
                        "Host .env is written mode 600; extra config files are rendered next to compose."
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
