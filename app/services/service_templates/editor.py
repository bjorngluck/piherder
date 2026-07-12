"""Build / serialize template definitions for the create/edit UI."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .schema import (
    SCHEMA_VERSION,
    ChecklistItem,
    TemplateDefinition,
    TemplateError,
    TemplateFileSpec,
    TemplateVar,
    parse_definition_dict,
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def parse_variables_json(raw: str) -> List[TemplateVar]:
    """Parse variables from JSON array (UI editor).

    Example item::
        {"name": "PORT", "label": "Host port", "type": "port", "default": "8080",
         "required": true, "secret": false, "generate": false, "help": ""}
    """
    text = (raw or "").strip()
    if not text:
        # Minimal default so deploy always has a project name
        return [
            TemplateVar(
                name="PROJECT_NAME",
                label="Project folder name",
                type="string",
                default="my-app",
                required=True,
            )
        ]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TemplateError(f"Variables JSON invalid: {e}") from e
    if not isinstance(data, list):
        raise TemplateError("Variables must be a JSON array")
    out: List[TemplateVar] = []
    for i, raw_v in enumerate(data):
        if not isinstance(raw_v, dict):
            raise TemplateError(f"Variable #{i + 1} must be an object")
        name = str(raw_v.get("name") or "").strip()
        if not name:
            raise TemplateError(f"Variable #{i + 1} needs a name")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise TemplateError(f"Invalid variable name: {name}")
        from .schema import _parse_template_var

        out.append(_parse_template_var(raw_v, name))
    names = [v.name for v in out]
    if "PROJECT_NAME" not in names:
        out.insert(
            0,
            TemplateVar(
                name="PROJECT_NAME",
                label="Project folder name",
                type="string",
                default="my-app",
                required=True,
            ),
        )
    return out


def parse_checklist_json(raw: str) -> List[ChecklistItem]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TemplateError(f"Checklist JSON invalid: {e}") from e
    if not isinstance(data, list):
        raise TemplateError("Checklist must be a JSON array")
    items: List[ChecklistItem] = []
    for raw_c in data:
        if not isinstance(raw_c, dict):
            continue
        title = str(raw_c.get("title") or "").strip()
        if not title:
            continue
        items.append(ChecklistItem(title=title, body=str(raw_c.get("body") or "")))
    return items


def build_definition_from_editor(
    *,
    slug: str,
    name: str,
    description: str = "",
    category: str = "other",
    version: str = "1.0.0",
    compose_content: str,
    env_content: str = "",
    variables_json: str = "[]",
    checklist_json: str = "[]",
    source: str = "user",
    use_docker_secrets: bool = False,
) -> TemplateDefinition:
    """Assemble a full TemplateDefinition from the create/edit form fields."""
    slug = (slug or "").strip().lower()
    name = (name or "").strip()
    if not slug or not _SLUG_RE.match(slug):
        raise TemplateError("Slug must be lowercase letters, digits, hyphens (e.g. my-app)")
    if not name:
        raise TemplateError("Name is required")
    compose = (compose_content or "").strip()
    if not compose:
        raise TemplateError("docker-compose.yml content is required")

    variables = parse_variables_json(variables_json)
    checklist = parse_checklist_json(checklist_json)

    files: List[TemplateFileSpec] = [TemplateFileSpec(path="docker-compose.yml")]
    file_contents: Dict[str, str] = {"docker-compose.yml": compose + ("\n" if not compose.endswith("\n") else "")}

    env = (env_content or "").strip()
    if env:
        files.append(TemplateFileSpec(path=".env", from_path=".env.sample"))
        file_contents[".env"] = env + ("\n" if not env.endswith("\n") else "")

    options: Dict[str, Any] = {
        "use_docker_secrets": bool(use_docker_secrets),
        "supports_docker_secrets": True,
    }

    # Validate via parse path for consistency
    meta = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "name": name,
        "description": description or "",
        "category": (category or "other").strip() or "other",
        "version": (version or "1.0.0").strip() or "1.0.0",
        "tags": [],
        "variables": [v.to_dict() for v in variables],
        "files": [{"path": f.path, "from": f.from_path} for f in files],
        "checklist": [{"title": c.title, "body": c.body} for c in checklist],
        "options": options,
    }
    definition = parse_definition_dict(meta, source=source)
    definition.file_contents = file_contents
    return definition


def redact_secret_variable_dicts(
    variables: List[Any],
    *,
    reveal: bool,
) -> List[Dict[str, Any]]:
    """Strip plaintext defaults from secret variables unless reveal (2FA)."""
    out: List[Dict[str, Any]] = []
    for raw in variables or []:
        if hasattr(raw, "to_dict"):
            d = raw.to_dict()
        elif isinstance(raw, dict):
            d = dict(raw)
        else:
            continue
        secret = bool(d.get("secret")) or looks_like_secret_name(str(d.get("name") or ""))
        if secret:
            d["secret"] = True
            if not reveal:
                d["default"] = ""
        out.append(d)
    return out


def looks_like_secret_name(name: str) -> bool:
    from .harden import looks_like_secret_name as _looks

    return _looks(name)


def redact_env_plaintext_secrets(env_content: str, *, reveal: bool) -> str:
    """Replace non-placeholder secret values in .env template with {{KEY}}."""
    if reveal or not (env_content or "").strip():
        return env_content or ""
    from .harden import parse_env_file, format_env_file, looks_like_secret_name as _looks

    env_map = parse_env_file(env_content)
    rebuilt: Dict[str, str] = {}
    for k, v in env_map.items():
        if v.startswith("{{") and v.endswith("}}"):
            rebuilt[k] = v
        elif _looks(k):
            rebuilt[k] = f"{{{{{k}}}}}"
        else:
            rebuilt[k] = v
    return format_env_file(rebuilt, as_placeholders=False)


def redact_form_secrets(form: Dict[str, Any], *, reveal: bool) -> Dict[str, Any]:
    """Prepare editor form for UI — never show secret defaults without reveal."""
    form = dict(form)
    try:
        variables = json.loads(form.get("variables_json") or "[]")
        if not isinstance(variables, list):
            variables = []
    except Exception:
        variables = []
    variables = redact_secret_variable_dicts(variables, reveal=reveal)
    form["variables_json"] = json.dumps(variables, indent=2, ensure_ascii=False)
    form["env_content"] = redact_env_plaintext_secrets(
        form.get("env_content") or "", reveal=reveal
    )
    return form


def preserve_secret_defaults_on_save(
    new_variables: List[Any],
    previous_variables: Optional[List[Any]],
) -> List[Dict[str, Any]]:
    """If UI sent empty secret default (redacted), keep previous stored default."""
    prev: Dict[str, str] = {}
    for raw in previous_variables or []:
        d = raw.to_dict() if hasattr(raw, "to_dict") else (raw if isinstance(raw, dict) else None)
        if not d:
            continue
        name = str(d.get("name") or "")
        if name and d.get("secret") and d.get("default"):
            prev[name] = str(d["default"])
    out: List[Dict[str, Any]] = []
    for raw in new_variables or []:
        d = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        name = str(d.get("name") or "")
        if d.get("secret") and not str(d.get("default") or "") and name in prev:
            d["default"] = prev[name]
        out.append(d)
    return out


def definition_to_editor_form(
    definition: TemplateDefinition, *, reveal_secrets: bool = False
) -> Dict[str, Any]:
    """Flatten a definition for the edit form."""
    compose = definition.file_contents.get("docker-compose.yml") or definition.file_contents.get(
        "compose.yml"
    ) or ""
    # Prefer .env content if present
    env = definition.file_contents.get(".env") or definition.file_contents.get(".env.sample") or ""
    opts = definition.options or {}
    variables = redact_secret_variable_dicts(
        [v.to_dict() for v in definition.variables], reveal=reveal_secrets
    )
    form = {
        "slug": definition.slug,
        "name": definition.name,
        "description": definition.description or "",
        "category": definition.category or "other",
        "version": definition.version or "1.0.0",
        "compose_content": compose,
        "env_content": redact_env_plaintext_secrets(env, reveal=reveal_secrets),
        "variables_json": json.dumps(variables, indent=2, ensure_ascii=False),
        "checklist_json": json.dumps(
            [{"title": c.title, "body": c.body} for c in definition.checklist],
            indent=2,
            ensure_ascii=False,
        ),
        "source": definition.source,
        "use_docker_secrets": bool(opts.get("use_docker_secrets")),
    }
    return form


def blank_editor_form() -> Dict[str, Any]:
    return {
        "slug": "",
        "name": "",
        "description": "",
        "category": "other",
        "version": "1.0.0",
        "compose_content": (
            "services:\n"
            "  app:\n"
            "    image: nginx:alpine\n"
            "    ports:\n"
            '      - "{{HOST_PORT}}:80"\n'
            "    restart: unless-stopped\n"
        ),
        "env_content": "",
        "variables_json": json.dumps(
            [
                {
                    "name": "PROJECT_NAME",
                    "label": "Project folder name",
                    "type": "string",
                    "default": "my-app",
                    "required": True,
                    "secret": False,
                    "generate": False,
                    "help": "",
                },
                {
                    "name": "HOST_PORT",
                    "label": "Host port",
                    "type": "port",
                    "default": "8080",
                    "required": True,
                    "secret": False,
                    "generate": False,
                    "help": "",
                },
            ],
            indent=2,
        ),
        "checklist_json": json.dumps(
            [
                {
                    "title": "DNS (manual)",
                    "body": "Create A/AAAA records for this service pointing at the host IP.",
                }
            ],
            indent=2,
        ),
        "source": "user",
        "use_docker_secrets": False,
    }


def apply_harden_env_to_form(
    form: Dict[str, Any], *, reveal_secrets: bool = False
) -> Tuple[Dict[str, Any], List[str]]:
    """Move inline secrets to .env placeholders; do not surface cleartext defaults without reveal."""
    from .harden import move_secrets_to_env, suggest_variables_from_content

    compose, env, extracted, messages = move_secrets_to_env(
        form.get("compose_content") or "",
        form.get("env_content") or "",
    )
    form = dict(form)
    form["compose_content"] = compose
    # Always store .env as placeholders in the template definition UI
    form["env_content"] = redact_env_plaintext_secrets(env, reveal=False)
    try:
        existing = json.loads(form.get("variables_json") or "[]")
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    by_name = {str(v.get("name")): v for v in existing if isinstance(v, dict) and v.get("name")}
    suggested = suggest_variables_from_content(
        compose, env, project_name_default=form.get("slug") or "my-app"
    )
    for s in suggested:
        name = s["name"]
        is_secret = bool(s.get("secret")) or looks_like_secret_name(name)
        if name in by_name:
            by_name[name]["secret"] = by_name[name].get("secret") or is_secret
            if is_secret:
                if reveal_secrets and name in extracted:
                    by_name[name]["default"] = extracted[name]
                # never paste extracted secrets into the form without reveal
            elif name in extracted and not by_name[name].get("default"):
                by_name[name]["default"] = extracted[name]
        else:
            if is_secret:
                s["secret"] = True
                s["default"] = extracted[name] if (reveal_secrets and name in extracted) else ""
            elif name in extracted:
                s["default"] = extracted[name]
            by_name[name] = s
    if extracted and not reveal_secrets:
        messages.append(
            "Secret values were not loaded into defaults (enable 2FA and re-run to show cleartext). "
            "Placeholders are in .env; enter secrets at deploy or after 2FA."
        )
    elif extracted and reveal_secrets:
        messages.append("Secret defaults filled for 2FA session — review before Save.")
    form["variables_json"] = json.dumps(list(by_name.values()), indent=2, ensure_ascii=False)
    form = redact_form_secrets(form, reveal=reveal_secrets)
    return form, messages


def apply_docker_secrets_to_form(form: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    from .harden import rewrite_compose_for_docker_secrets, looks_like_secret_name

    form = dict(form)
    try:
        variables = json.loads(form.get("variables_json") or "[]")
    except Exception:
        variables = []
    secret_keys = []
    for v in variables if isinstance(variables, list) else []:
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "")
        if v.get("secret") or looks_like_secret_name(name):
            if name and name != "PROJECT_NAME":
                secret_keys.append(name)
                v["secret"] = True
    compose, messages = rewrite_compose_for_docker_secrets(
        form.get("compose_content") or "", secret_keys
    )
    form["compose_content"] = compose
    form["variables_json"] = json.dumps(variables, indent=2, ensure_ascii=False)
    form["use_docker_secrets"] = True
    messages.append("Flag use_docker_secrets enabled — deploy will write ./secrets/<KEY> files.")
    return form, messages


def apply_scan_vars_to_form(
    form: Dict[str, Any], *, reveal_secrets: bool = False
) -> Tuple[Dict[str, Any], List[str]]:
    """Scan compose/.env and parameterize hard-coded volumes + host ports."""
    from .harden import build_variables_for_host_project, looks_like_secret_name as _looks

    form = dict(form)
    project_default = form.get("slug") or form.get("name") or "my-app"
    new_compose, suggested, param_msgs = build_variables_for_host_project(
        form.get("compose_content") or "",
        form.get("env_content") or "",
        project_name_default=str(project_default),
        parameterize=True,
    )
    form["compose_content"] = new_compose
    try:
        existing = json.loads(form.get("variables_json") or "[]")
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    by_name = {str(v.get("name")): v for v in existing if isinstance(v, dict) and v.get("name")}
    added = 0
    updated = 0
    for s in suggested:
        name = s["name"]
        vtype = s.get("type") or "string"
        is_secret = (
            vtype not in ("volume", "boolean", "port")
            and (bool(s.get("secret")) or _looks(name))
        )
        if is_secret:
            s["secret"] = True
            if not reveal_secrets:
                s["default"] = ""
        if name not in by_name:
            by_name[name] = s
            added += 1
        else:
            # Upgrade existing with volume/port/boolean metadata from scan
            cur = by_name[name]
            if vtype in ("volume", "port", "boolean") and cur.get("type") != vtype:
                for k, val in s.items():
                    if k == "default" and cur.get("default") and is_secret:
                        continue
                    if val is not None and val != "":
                        cur[k] = val
                updated += 1
            elif is_secret:
                cur["secret"] = True
                if not reveal_secrets:
                    pass
            elif vtype == "volume":
                for k in (
                    "type",
                    "volume_target",
                    "volume_default_mode",
                    "default",
                    "help",
                    "label",
                ):
                    if s.get(k) not in (None, ""):
                        cur[k] = s[k]
                cur["secret"] = False
                cur["generate"] = False
                updated += 1
    form["variables_json"] = json.dumps(list(by_name.values()), indent=2, ensure_ascii=False)
    form = redact_form_secrets(form, reveal=reveal_secrets)
    msgs = list(param_msgs or [])
    msgs.append(
        f"Scan complete: {added} new variable(s), {updated} upgraded, {len(by_name)} total "
        "(volumes, ports, booleans, env)."
    )
    return form, msgs
