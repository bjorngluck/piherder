"""Service template schema load, validate, and render (Phase 1).

Templates use ``{{VAR}}`` substitution only — no code execution.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# {{VAR_NAME}} — letters, digits, underscore
_VAR_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

# Safe project folder names
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

SCHEMA_VERSION = 1


class TemplateError(ValueError):
    """Invalid template definition or render inputs."""


# Known variable types (UI + validation)
VAR_TYPES = frozenset(
    {"string", "int", "port", "password", "email", "url", "boolean", "volume"}
)
VOLUME_MODES = frozenset({"named", "bind_relative", "bind_absolute"})
# Relative project bind: ./name or name (normalized to ./name)
_REL_BIND_RE = re.compile(r"^\.?/?[A-Za-z0-9._][A-Za-z0-9._/-]{0,200}$")
_NAMED_VOL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_ABS_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{0,500}$")


@dataclass
class TemplateVar:
    name: str
    label: str = ""
    type: str = "string"  # string | int | port | password | email | url | boolean | volume
    default: str = ""
    required: bool = True
    secret: bool = False
    generate: bool = False
    help: str = ""
    # boolean: values written into {{VAR}} after checkbox/select
    true_value: str = "true"
    false_value: str = "false"
    # volume: container mount target; default is name/path; mode chosen at deploy
    volume_target: str = ""
    volume_default_mode: str = "named"  # named | bind_relative | bind_absolute

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "label": self.label or self.name,
            "type": self.type,
            "default": self.default,
            "required": self.required,
            "secret": self.secret,
            "generate": self.generate,
            "help": self.help,
        }
        if self.type == "boolean" or self.true_value != "true" or self.false_value != "false":
            d["true_value"] = self.true_value
            d["false_value"] = self.false_value
        if self.type == "volume" or self.volume_target or self.volume_default_mode != "named":
            d["volume_target"] = self.volume_target
            d["volume_default_mode"] = self.volume_default_mode or "named"
        return d


@dataclass
class TemplateFileSpec:
    path: str
    from_path: Optional[str] = None  # source under files/

    def source_name(self) -> str:
        return self.from_path or self.path


@dataclass
class ChecklistItem:
    title: str
    body: str = ""


@dataclass
class TemplateDefinition:
    schema_version: int
    slug: str
    name: str
    description: str = ""
    category: str = "other"
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    variables: List[TemplateVar] = field(default_factory=list)
    files: List[TemplateFileSpec] = field(default_factory=list)
    checklist: List[ChecklistItem] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    # raw file contents: path -> text (after load)
    file_contents: Dict[str, str] = field(default_factory=dict)
    source: str = "builtin"  # builtin | import | git
    root_path: Optional[str] = None

    def secret_var_names(self) -> List[str]:
        return [v.name for v in self.variables if v.secret]

    def non_secret_var_names(self) -> List[str]:
        return [v.name for v in self.variables if not v.secret]

    def var_map(self) -> Dict[str, TemplateVar]:
        return {v.name: v for v in self.variables}

    def to_public_dict(self) -> dict:
        """Metadata for UI (no file bodies)."""
        return {
            "schema_version": self.schema_version,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "tags": list(self.tags),
            "variables": [v.to_dict() for v in self.variables],
            "files": [{"path": f.path, "from": f.from_path} for f in self.files],
            "checklist": [{"title": c.title, "body": c.body} for c in self.checklist],
            "options": dict(self.options or {}),
            "source": self.source,
        }

    def checksum(self) -> str:
        payload = {
            "def": self.to_public_dict(),
            "files": {k: self.file_contents.get(k, "") for k in sorted(self.file_contents)},
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def generate_secret(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def validate_project_name(name: str) -> str:
    n = (name or "").strip()
    if not _PROJECT_NAME_RE.match(n):
        raise TemplateError(
            "Project name must be 1–64 chars: letters, digits, . _ - (start alnum)"
        )
    return n


def _parse_yaml_or_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise TemplateError("Empty template.yaml")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)
    except Exception:
        # Fallback: try JSON
        try:
            data = json.loads(text)
        except Exception as e:
            raise TemplateError(f"Could not parse template.yaml: {e}") from e
    if not isinstance(data, dict):
        raise TemplateError("template.yaml root must be a mapping")
    return data


def parse_definition_dict(data: dict, *, source: str = "builtin") -> TemplateDefinition:
    if not isinstance(data, dict):
        raise TemplateError("Definition must be a dict")
    sv = int(data.get("schema_version") or 0)
    if sv != SCHEMA_VERSION:
        raise TemplateError(f"Unsupported schema_version {sv} (need {SCHEMA_VERSION})")
    slug = str(data.get("slug") or "").strip()
    name = str(data.get("name") or "").strip()
    if not slug or not name:
        raise TemplateError("slug and name are required")
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", slug):
        raise TemplateError("slug must be lowercase alnum/hyphen")

    variables: List[TemplateVar] = []
    for raw in data.get("variables") or []:
        if not isinstance(raw, dict):
            continue
        vname = str(raw.get("name") or "").strip()
        if not vname:
            continue
        variables.append(_parse_template_var(raw, vname))

    files: List[TemplateFileSpec] = []
    for raw in data.get("files") or []:
        if isinstance(raw, str):
            files.append(TemplateFileSpec(path=raw))
        elif isinstance(raw, dict):
            path = str(raw.get("path") or "").strip()
            if not path:
                continue
            from_path = raw.get("from")
            files.append(
                TemplateFileSpec(
                    path=path,
                    from_path=str(from_path).strip() if from_path else None,
                )
            )
    if not files:
        raise TemplateError("At least one file is required")

    checklist: List[ChecklistItem] = []
    for raw in data.get("checklist") or []:
        if isinstance(raw, dict) and raw.get("title"):
            checklist.append(
                ChecklistItem(
                    title=str(raw["title"]),
                    body=str(raw.get("body") or ""),
                )
            )

    return TemplateDefinition(
        schema_version=sv,
        slug=slug,
        name=name,
        description=str(data.get("description") or ""),
        category=str(data.get("category") or "other"),
        version=str(data.get("version") or "1.0.0"),
        tags=[str(t) for t in (data.get("tags") or [])],
        variables=variables,
        files=files,
        checklist=checklist,
        options=dict(data.get("options") or {}) if isinstance(data.get("options"), dict) else {},
        source=source,
    )


def load_template_dir(root: Path, *, source: str = "builtin") -> TemplateDefinition:
    """Load template.yaml + files/ from a directory."""
    root = Path(root)
    meta_path = root / "template.yaml"
    if not meta_path.is_file():
        # allow template.yml / template.json
        for alt in ("template.yml", "template.json"):
            p = root / alt
            if p.is_file():
                meta_path = p
                break
        else:
            raise TemplateError(f"No template.yaml in {root}")

    data = _parse_yaml_or_json(meta_path.read_text(encoding="utf-8"))
    definition = parse_definition_dict(data, source=source)
    definition.root_path = str(root)

    files_dir = root / "files"
    contents: Dict[str, str] = {}
    for spec in definition.files:
        src = files_dir / spec.source_name()
        if not src.is_file():
            # also allow file at root of template
            alt = root / spec.source_name()
            if alt.is_file():
                src = alt
            else:
                raise TemplateError(f"Missing template file: {spec.source_name()}")
        # prevent path escape
        try:
            src.resolve().relative_to(root.resolve())
        except ValueError as e:
            raise TemplateError(f"File path outside template root: {spec.source_name()}") from e
        contents[spec.path] = src.read_text(encoding="utf-8")
    definition.file_contents = contents
    return definition


def render_text(template: str, values: Dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise TemplateError(f"Missing value for {{{{ {key} }}}}")
        return values[key]

    return _VAR_RE.sub(repl, template or "")


def _parse_template_var(raw: dict, vname: str) -> TemplateVar:
    vtype = str(raw.get("type") or "string").strip().lower() or "string"
    if vtype not in VAR_TYPES:
        vtype = "string"

    default_raw = raw.get("default")
    volume_default_mode = str(raw.get("volume_default_mode") or "named").strip() or "named"
    volume_target = str(raw.get("volume_target") or "").strip()
    default = ""

    if isinstance(default_raw, dict):
        # Structured volume default: {mode, name|path}
        volume_default_mode = str(
            default_raw.get("mode") or volume_default_mode or "named"
        ).strip()
        default = str(
            default_raw.get("name")
            or default_raw.get("path")
            or default_raw.get("value")
            or ""
        )
        if not volume_target:
            volume_target = str(default_raw.get("target") or "").strip()
    elif isinstance(default_raw, bool):
        default = "true" if default_raw else "false"
    elif default_raw is None:
        default = ""
    else:
        default = str(default_raw)

    if volume_default_mode not in VOLUME_MODES:
        volume_default_mode = "named"

    secret = bool(raw.get("secret", False))
    # Booleans and volumes are never secrets
    if vtype in ("boolean", "volume"):
        secret = False

    true_value = str(raw.get("true_value") if raw.get("true_value") is not None else "true")
    false_value = str(raw.get("false_value") if raw.get("false_value") is not None else "false")

    return TemplateVar(
        name=vname,
        label=str(raw.get("label") or vname),
        type=vtype,
        default=default,
        required=bool(raw.get("required", True)),
        secret=secret,
        generate=bool(raw.get("generate", False)) and secret,
        help=str(raw.get("help") or ""),
        true_value=true_value,
        false_value=false_value,
        volume_target=volume_target,
        volume_default_mode=volume_default_mode,
    )


def _volume_source_from_provided(
    var: TemplateVar,
    provided: Dict[str, str],
    raw: Optional[str],
) -> str:
    """Prefer explicit __source; peel already-rendered mount; else raw/default."""
    explicit = provided.get(f"{var.name}__source")
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    r = str(raw).strip() if raw is not None else ""
    tgt = (var.volume_target or "").strip()
    if r and tgt and r.endswith(":" + tgt):
        return r[: -(len(tgt) + 1)]
    if r:
        return r
    return str(var.default or "").strip()


def coerce_boolean_value(var: TemplateVar, raw: Optional[str]) -> str:
    """Map form/string input to true_value or false_value."""
    if raw is None or str(raw).strip() == "":
        # Fall back to default interpreted as bool-ish
        raw = var.default
    s = str(raw).strip().lower()
    truthy = {"1", "true", "yes", "on", "y", var.true_value.lower()}
    if s in truthy:
        return var.true_value
    # Explicit false-ish or anything else when defaulted
    if s in {"0", "false", "no", "off", "n", var.false_value.lower(), ""}:
        return var.false_value
    # Unknown string: treat as false unless it equals true_value
    return var.false_value


def validate_volume_source(mode: str, source: str, *, var_name: str) -> str:
    """Validate and normalize volume source path/name for a mode."""
    mode = (mode or "named").strip()
    if mode not in VOLUME_MODES:
        raise TemplateError(f"{var_name}: invalid volume mode {mode!r}")
    src = (source or "").strip()
    if not src:
        raise TemplateError(f"{var_name}: volume name or path is required")
    if ".." in src or "\n" in src or "\r" in src:
        raise TemplateError(f"{var_name}: path must not contain '..' or newlines")
    if mode == "named":
        if not _NAMED_VOL_RE.match(src):
            raise TemplateError(
                f"{var_name}: named volume must be 1–64 chars alnum/._- (start alnum)"
            )
        return src
    if mode == "bind_relative":
        # Allow data, ./data, data/sub — normalize to ./…
        cleaned = src.lstrip("./")
        if not cleaned or cleaned.startswith("/"):
            raise TemplateError(f"{var_name}: relative bind must be under the project folder")
        candidate = f"./{cleaned}"
        if not _REL_BIND_RE.match(candidate):
            raise TemplateError(f"{var_name}: invalid relative bind path")
        return candidate
    # bind_absolute
    if not src.startswith("/"):
        raise TemplateError(f"{var_name}: host path must be absolute (start with /)")
    if not _ABS_PATH_RE.match(src):
        raise TemplateError(f"{var_name}: invalid absolute host path")
    return src


def build_volume_mount(var: TemplateVar, mode: str, source: str) -> Tuple[str, Optional[str]]:
    """Return (compose short-form mount, named_volume_name or None).

    Mount string is ``source:target`` suitable for ``- {{VAR}}`` in compose.
    """
    target = (var.volume_target or "").strip()
    if not target:
        raise TemplateError(
            f"{var.name}: volume_target is required on the template (container path)"
        )
    if not target.startswith("/"):
        raise TemplateError(f"{var.name}: volume_target must be an absolute container path")
    src = validate_volume_source(mode, source, var_name=var.name)
    mount = f"{src}:{target}"
    named = src if mode == "named" else None
    return mount, named


def merge_variable_values(
    definition: TemplateDefinition,
    provided: Dict[str, str],
    *,
    auto_generate: bool = True,
) -> Dict[str, str]:
    """Fill defaults / generate secrets; validate required.

    Extra keys from the form:
    - ``{NAME}__mode`` for volume variables (named | bind_relative | bind_absolute)
    """
    out: Dict[str, str] = {}
    named_volumes: List[str] = []
    managed_volume_names: List[str] = []

    for var in definition.variables:
        raw = provided.get(var.name)

        if var.type == "boolean":
            # Empty + not required → false_value; required still coerces (checkbox may omit)
            if (raw is None or str(raw).strip() == "") and var.default == "" and not var.required:
                out[var.name] = var.false_value
            else:
                out[var.name] = coerce_boolean_value(var, None if raw is None else str(raw))
            continue

        if var.type == "volume":
            mode = str(
                provided.get(f"{var.name}__mode")
                or var.volume_default_mode
                or "named"
            ).strip()
            source = _volume_source_from_provided(var, provided, raw)
            if not source:
                if var.required:
                    raise TemplateError(f"Variable {var.name} is required")
                out[var.name] = ""
                out[f"{var.name}__mode"] = mode
                continue
            mount, named = build_volume_mount(var, mode, source)
            out[var.name] = mount
            out[f"{var.name}__mode"] = mode
            # Keep raw source for redeploy / re-merge (confirm stash may already hold mount)
            out[f"{var.name}__source"] = validate_volume_source(mode, source, var_name=var.name)
            if var.default:
                # Track default named volume so we can drop unused top-level entries
                try:
                    if (var.volume_default_mode or "named") == "named":
                        managed_volume_names.append(
                            validate_volume_source("named", var.default, var_name=var.name)
                        )
                except TemplateError:
                    pass
            if named:
                named_volumes.append(named)
                managed_volume_names.append(named)
            continue

        if raw is None or str(raw).strip() == "":
            if auto_generate and var.generate and var.secret:
                out[var.name] = generate_secret()
            elif var.default != "":
                out[var.name] = str(var.default)
            elif var.required:
                raise TemplateError(f"Variable {var.name} is required")
            else:
                out[var.name] = ""
        else:
            out[var.name] = str(raw)

        if var.type == "port" and out[var.name]:
            try:
                p = int(out[var.name])
                if p < 1 or p > 65535:
                    raise ValueError
            except ValueError as e:
                raise TemplateError(f"{var.name}: must be a port 1–65535") from e
        if var.type == "int" and out[var.name] != "":
            try:
                int(out[var.name])
            except ValueError as e:
                raise TemplateError(f"{var.name}: must be an integer") from e

    # Always allow PROJECT_NAME override even if not in variables
    if "PROJECT_NAME" in provided and provided["PROJECT_NAME"].strip():
        out["PROJECT_NAME"] = validate_project_name(provided["PROJECT_NAME"])
    elif "PROJECT_NAME" in out and out["PROJECT_NAME"]:
        out["PROJECT_NAME"] = validate_project_name(out["PROJECT_NAME"])

    # Internal keys for compose post-process (not template placeholders)
    out["__named_volumes"] = json.dumps(sorted(set(named_volumes)))
    out["__managed_volume_names"] = json.dumps(sorted(set(managed_volume_names)))
    return out


def sync_compose_named_volumes(
    compose_text: str,
    named_volumes: List[str],
    *,
    managed_names: Optional[List[str]] = None,
) -> str:
    """Ensure top-level ``volumes:`` lists active named volumes; drop unused managed names.

    Non-managed volume keys (hard-coded in the template) are left alone.
    """
    text = compose_text or ""
    named = [n for n in named_volumes if n]
    managed = set(managed_names or []) | set(named)

    # Find top-level volumes: block (start of line, not indented under services)
    # Simple approach: if block missing and we need named vols, append.
    vol_header = re.search(r"(?m)^volumes:\s*$", text)
    if not vol_header:
        if not named:
            return text
        block = "\nvolumes:\n" + "".join(f"  {n}:\n" for n in sorted(set(named)))
        if text and not text.endswith("\n"):
            text += "\n"
        return text + block

    # Split into before volumes, volumes body, after (next top-level key)
    start = vol_header.start()
    after_header = vol_header.end()
    # Next top-level key at column 0 (letter)
    next_top = re.search(r"(?m)^[A-Za-z]", text[after_header:])
    if next_top:
        body_end = after_header + next_top.start()
        before = text[:start]
        body = text[after_header:body_end]
        after = text[body_end:]
    else:
        before = text[:start]
        body = text[after_header:]
        after = ""

    # Parse existing volume keys (2-space indent name:)
    existing: List[str] = []
    other_lines: List[str] = []
    for line in body.splitlines(keepends=True):
        m = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*(?:#.*)?$", line.rstrip("\n"))
        if m:
            existing.append(m.group(1))
        elif line.strip() == "":
            continue
        else:
            # Nested config under a volume or comments — keep non-managed only via rebuild
            other_lines.append(line)

    # Keep non-managed existing names; replace managed set with active named
    keep = [n for n in existing if n not in managed]
    final = sorted(set(keep) | set(named))
    if not final and not other_lines:
        # Drop empty volumes block
        return before.rstrip() + ("\n" + after if after else "\n")

    new_body = "".join(f"  {n}:\n" for n in final)
    # Preserve unknown nested lines only if we still have volumes (rare)
    if other_lines and final:
        new_body += "".join(other_lines)

    return before + "volumes:\n" + new_body + after


def render_template_files(
    definition: TemplateDefinition,
    values: Dict[str, str],
) -> Dict[str, str]:
    """Return rendered path -> content (host-ready, including secrets in .env)."""
    # Strip internal keys from substitution map
    sub_values = {
        k: v
        for k, v in values.items()
        if not k.startswith("__") and not k.endswith("__mode") and not k.endswith("__source")
    }
    rendered: Dict[str, str] = {}
    for path, body in definition.file_contents.items():
        rendered[path] = render_text(body, sub_values)

    named: List[str] = []
    managed: List[str] = []
    try:
        named = json.loads(values.get("__named_volumes") or "[]")
        managed = json.loads(values.get("__managed_volume_names") or "[]")
    except Exception:
        pass
    # Also collect from volume vars if merge put mounts but no meta (legacy)
    if not named:
        for var in definition.variables:
            if var.type != "volume":
                continue
            mode = values.get(f"{var.name}__mode") or var.volume_default_mode
            mount = values.get(var.name) or ""
            if mode == "named" and ":" in mount:
                named.append(mount.split(":", 1)[0])

    for key in ("docker-compose.yml", "compose.yml", "compose.yaml"):
        if key in rendered:
            rendered[key] = sync_compose_named_volumes(
                rendered[key], named, managed_names=managed
            )
    return rendered


def split_secrets(
    definition: TemplateDefinition,
    values: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return (public_vars, secret_vars).

    Drops internal render meta (``__named_volumes``, …). Keeps ``NAME__mode`` /
    ``NAME__source`` for volume redeploy prefills.
    """
    secret_names = set(definition.secret_var_names())
    public: Dict[str, str] = {}
    secrets_map: Dict[str, str] = {}
    for k, v in values.items():
        if k.startswith("__"):
            continue
        if k in secret_names:
            secrets_map[k] = v
        else:
            public[k] = v
    return public, secrets_map


def mask_secrets_in_files(
    files: Dict[str, str],
    secret_values: Dict[str, str],
) -> Dict[str, str]:
    """Replace secret substrings with ******** for preview UI."""
    if not secret_values:
        return dict(files or {})
    out: Dict[str, str] = {}
    # Longest values first so nested/overlapping secrets redact cleanly
    vals = sorted(
        (str(v) for v in (secret_values or {}).values() if v and len(str(v)) >= 4),
        key=len,
        reverse=True,
    )
    for path, body in (files or {}).items():
        text = body if body is not None else ""
        for val in vals:
            text = text.replace(val, "********")
        out[path] = text
    return out


def _mask_env_file_body(content: str, secret_keys: Optional[set] = None) -> str:
    """Mask values for secret-like keys (and any known secret key names)."""
    from .harden import looks_like_secret_name

    secret_keys = secret_keys or set()
    lines = []
    for line in (content or "").splitlines():
        raw = line
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            lines.append(raw)
            continue
        k, _, _v = line.partition("=")
        key = k.strip()
        if key in secret_keys or looks_like_secret_name(key):
            lines.append(f"{key}=********")
        else:
            lines.append(raw)
    return "\n".join(lines) + ("\n" if lines else "")


def redact_files_for_ui(
    files: Dict[str, str],
    *,
    secret_values: Optional[Dict[str, str]] = None,
    secret_keys: Optional[List[str]] = None,
    reveal: bool = False,
) -> Dict[str, str]:
    """Strict UI redaction: never return cleartext secrets unless reveal (step-up 2FA).

    Handles:
    - substring values from secret_values
    - secrets/* path bodies (legacy docker-secrets files)
    - .env keys that look secret or are listed in secret_keys
    """
    if reveal:
        return dict(files or {})

    secret_values = {str(k): str(v) for k, v in (secret_values or {}).items() if v is not None}
    keys = set(secret_keys or []) | set(secret_values.keys())
    out = mask_secrets_in_files(files or {}, secret_values)

    for path in list(out.keys()):
        p = str(path).replace("\\", "/")
        base = p.split("/")[-1]
        if p.startswith("secrets/") or "/secrets/" in p or base.startswith("secrets/"):
            out[path] = "********"
            continue
        if base == ".env" or p.endswith("/.env") or p == ".env":
            out[path] = _mask_env_file_body(out[path], keys)
    return out


def files_for_db_storage(
    files: Dict[str, str],
    secrets_map: Dict[str, str],
) -> Dict[str, str]:
    """Persist rendered files without secret values (secrets live in secrets_encrypted)."""
    from .harden import format_env_file, parse_env_file

    # Drop host docker-secrets style files; home production uses locked .env
    out = {
        k: v
        for k, v in (files or {}).items()
        if not str(k).replace("\\", "/").startswith("secrets/")
    }
    # Scrub any remaining secret substrings from compose etc.
    out = mask_secrets_in_files(out, secrets_map or {})
    env = parse_env_file(out.get(".env") or "")
    for k in secrets_map or {}:
        # Keep key present for redeploy merge; value only in secrets_encrypted
        env[str(k)] = ""
    if env:
        out[".env"] = format_env_file(env, as_placeholders=False)
    return out


def render_checklist(
    definition: TemplateDefinition,
    values: Dict[str, str],
) -> List[Dict[str, str]]:
    items = []
    for c in definition.checklist:
        try:
            title = render_text(c.title, values)
            body = render_text(c.body, values)
        except TemplateError:
            title, body = c.title, c.body
        items.append({"title": title, "body": body})
    return items


def definition_to_storage_json(definition: TemplateDefinition) -> str:
    """Serialize full definition including file bodies for DB import source."""
    payload = definition.to_public_dict()
    payload["file_contents"] = definition.file_contents
    return json.dumps(payload, ensure_ascii=False)


def definition_from_storage_json(raw: str, *, source: str = "import") -> TemplateDefinition:
    data = json.loads(raw)
    file_contents = data.pop("file_contents", None) or {}
    # to_public_dict shape uses "from" in files
    if "files" in data:
        norm_files = []
        for f in data["files"]:
            if isinstance(f, dict):
                norm_files.append({"path": f.get("path"), "from": f.get("from")})
            else:
                norm_files.append(f)
        data["files"] = norm_files
    # variables already dict list
    definition = parse_definition_dict(data, source=source)
    definition.file_contents = {str(k): str(v) for k, v in file_contents.items()}
    if not definition.file_contents:
        raise TemplateError("Stored template missing file_contents")
    return definition
