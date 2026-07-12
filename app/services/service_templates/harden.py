"""Harden compose templates: extract secrets to .env, optional Docker secrets."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Names that strongly suggest a secret
_SECRET_NAME_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|root[_-]?password|mysql_password|credential)",
    re.I,
)

# environment: KEY: "value" or KEY: value (simple scalars only)
_ENV_LINE_RE = re.compile(
    r"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$"
)
# KEY=value in environment list form - "KEY=value"
_ENV_LIST_RE = re.compile(
    r'^([ \t]*)- ["\']?([A-Za-z_][A-Za-z0-9_]*)=([^"\']*)["\']?\s*$'
)

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_ENV_FILE_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def looks_like_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name or ""))


def parse_env_file(content: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (content or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _ENV_FILE_LINE_RE.match(s)
        if not m:
            continue
        out[m.group(1)] = m.group(2)
    return out


def format_env_file(values: Dict[str, str], *, as_placeholders: bool = False) -> str:
    lines = []
    for k in sorted(values.keys()):
        v = values[k]
        if as_placeholders:
            lines.append(f"{k}={{{{{k}}}}}")
        else:
            lines.append(f"{k}={v}")
    return "\n".join(lines) + ("\n" if lines else "")


def scan_placeholders(*texts: str) -> List[str]:
    """Unique {{VAR}} names in order of first appearance."""
    seen: Set[str] = set()
    out: List[str] = []
    for text in texts:
        for m in _PLACEHOLDER_RE.finditer(text or ""):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _strip_yaml_scalar(raw: str) -> str:
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    # drop trailing comments
    if " #" in s:
        s = s.split(" #", 1)[0].rstrip()
    return s


def extract_inline_env_assignments(compose: str) -> List[Tuple[str, str, str]]:
    """Find simple environment KEY: value lines that are not already ${VAR} or {{VAR}}.

    Returns list of (indent, key, value).
    """
    found: List[Tuple[str, str, str]] = []
    for line in (compose or "").splitlines():
        m = _ENV_LINE_RE.match(line)
        if m:
            indent, key, raw_val = m.group(1), m.group(2), m.group(3)
            val = _strip_yaml_scalar(raw_val)
            if not val or val.startswith("${") or val.startswith("{{"):
                continue
            # skip YAML structures
            if val in ("|", ">", ">-", "|-") or val.endswith(":"):
                continue
            found.append((indent, key, val))
            continue
        m2 = _ENV_LIST_RE.match(line)
        if m2:
            indent, key, val = m2.group(1), m2.group(2), m2.group(3)
            if not val or val.startswith("${") or val.startswith("{{"):
                continue
            found.append((indent, key, val))
    return found


def move_secrets_to_env(
    compose: str,
    env_content: str,
    *,
    secret_names: Optional[Set[str]] = None,
    only_secret_looking: bool = True,
) -> Tuple[str, str, Dict[str, str], List[str]]:
    """Rewrite compose inline env secrets to ${KEY} and put KEY={{KEY}} in .env.

    Returns (new_compose, new_env, extracted_defaults, messages).
    extracted_defaults maps KEY -> original plaintext (for variable defaults).
    """
    messages: List[str] = []
    extracted: Dict[str, str] = {}
    existing_env = parse_env_file(env_content)
    assignments = extract_inline_env_assignments(compose)

    to_move: Dict[str, str] = {}
    for _indent, key, val in assignments:
        if secret_names is not None:
            if key not in secret_names:
                continue
        elif only_secret_looking and not looks_like_secret_name(key):
            continue
        to_move[key] = val

    if not to_move:
        messages.append("No inline secret-like environment values found to move.")
        return compose, env_content or "", {}, messages

    new_lines = []
    for line in (compose or "").splitlines():
        m = _ENV_LINE_RE.match(line)
        if m:
            indent, key, raw_val = m.group(1), m.group(2), m.group(3)
            val = _strip_yaml_scalar(raw_val)
            if key in to_move and val == to_move[key]:
                new_lines.append(f"{indent}{key}: ${{{key}}}")
                extracted[key] = val
                continue
        m2 = _ENV_LIST_RE.match(line)
        if m2:
            indent, key, val = m2.group(1), m2.group(2), m2.group(3)
            if key in to_move and val == to_move[key]:
                new_lines.append(f'{indent}- "{key}=${{{key}}}"')
                extracted[key] = val
                continue
        new_lines.append(line)

    new_compose = "\n".join(new_lines) + "\n"

    # Also variable-ise existing .env plaintext secrets
    for k, v in list(existing_env.items()):
        if looks_like_secret_name(k) and not (v.startswith("{{") and v.endswith("}}")):
            if k not in extracted:
                extracted[k] = v
            existing_env[k] = f"{{{{{k}}}}}"

    for k, v in extracted.items():
        existing_env[k] = f"{{{{{k}}}}}"
        messages.append(f"Moved {k} → .env as {{{{{k}}}}} (compose uses ${{{k}}}).")

    # Keep non-secret env keys as placeholders too when they were literals
    rebuilt: Dict[str, str] = {}
    for k, v in existing_env.items():
        if v.startswith("{{") and v.endswith("}}"):
            rebuilt[k] = v
        else:
            rebuilt[k] = f"{{{{{k}}}}}"
            if k not in extracted:
                extracted[k] = v

    new_env = format_env_file(rebuilt, as_placeholders=False)
    if "env_file:" not in new_compose and extracted:
        messages.append("Tip: add env_file: ['.env'] under services if Compose does not auto-load .env.")

    return new_compose, new_env, extracted, messages


# Short-form volume: source:target or source:target:mode
_SHORT_MOUNT_RE = re.compile(
    r"""^([ \t]*)-\s*["']?([^:"'\s][^:"']*?):(/[^:"'\s][^:"']*)(?::([^"'\s]+))?["']?\s*$"""
)
# Host:container port (optional /proto, optional quotes)
_PORT_MAP_RE = re.compile(
    r"""^([ \t]*)-\s*["']?(\d{1,5}):(\d{1,5})(?:/(tcp|udp))?["']?\s*$""",
    re.I,
)
_BOOLISH = frozenset(
    {"true", "false", "yes", "no", "on", "off", "1", "0", "y", "n"}
)
_SECTION_KEY_RE = re.compile(r"^([ \t]*)([A-Za-z0-9_-]+)\s*:\s*(.*)$")


def _unique_var_name(base: str, used: Set[str]) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", (base or "VAR").upper()).strip("_")
    if not name:
        name = "VAR"
    if name[0].isdigit():
        name = "V_" + name
    if not re.match(r"^[A-Za-z_]", name):
        name = "V_" + name
    candidate = name
    n = 2
    while candidate in used:
        candidate = f"{name}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def classify_volume_source(source: str) -> Tuple[str, str]:
    """Return (mode, normalized_source) for a compose volume left-hand side."""
    src = (source or "").strip()
    if not src:
        return "named", src
    if src.startswith("/") and not src.startswith("//"):
        return "bind_absolute", src
    if src.startswith("./") or src.startswith("../"):
        # relative bind (reject .. later at validate; keep as relative-ish)
        cleaned = src
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        return "bind_relative", cleaned or src
    # bare relative path used as bind in many compose files (./ optional)
    if "/" in src or src in (".", ".."):
        return "bind_relative", src.lstrip("./")
    # named volume
    return "named", src


def _volume_var_base(source: str, target: str, mode: str) -> str:
    if mode == "named" and source:
        return source
    # Prefer last meaningful segment of container path
    parts = [p for p in (target or "").split("/") if p]
    if parts:
        return parts[-1]
    return "data"


def _port_var_base(host_port: str, container_port: str, service: str) -> str:
    svc = re.sub(r"[^A-Za-z0-9]+", "_", (service or "APP").upper()).strip("_") or "APP"
    # Prefer service_PORT when host==container common case
    if host_port == container_port:
        return f"{svc}_PORT"
    return f"{svc}_HOST_PORT_{host_port}"


def parameterize_compose_volumes_and_ports(
    compose: str,
    *,
    project_name: str = "app",
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """Rewrite hard-coded short mounts and host ports to {{VAR}} and return variable defs.

    Handles short-form only:
      - named_vol:/container/path
      - ./data:/container/path
      - /host/path:/container/path
      - "8080:80" / 53:53/udp

    Long-form ``type: volume`` mounts are left as-is (with a message).
    """
    messages: List[str] = []
    if not (compose or "").strip():
        return compose or "", [], messages

    used_names: Set[str] = {"PROJECT_NAME"}
    volume_vars: Dict[str, Dict[str, Any]] = {}  # mount_key -> var def
    # key = f"{mode}|{source}|{target}" for reuse across services
    port_vars: Dict[str, Dict[str, Any]] = {}  # host_port -> var
    long_form_seen = False

    lines = (compose or "").splitlines()
    out: List[str] = []
    section: Optional[str] = None  # volumes | ports | other
    section_indent = -1
    current_service = "app"

    for line in lines:
        # Track service name under services:
        sm = _SECTION_KEY_RE.match(line)
        if sm:
            indent = len(sm.group(1).replace("\t", "  "))
            key = sm.group(2)
            rest = (sm.group(3) or "").strip()
            # top-level or services children
            if indent == 0:
                section = None
                section_indent = -1
                if key not in ("services", "volumes", "networks", "secrets", "configs", "name", "version"):
                    pass
            elif indent == 2 and key and rest in ("", "{}", "null", "~"):
                # likely service key under services
                current_service = key
                section = None
                section_indent = -1
            elif key in ("volumes", "ports") and rest == "":
                section = key
                section_indent = indent
                out.append(line)
                continue
            elif section and indent <= section_indent:
                section = None
                section_indent = -1

        if section == "volumes":
            if re.match(r"^[ \t]+-\s*type\s*:", line):
                long_form_seen = True
                out.append(line)
                continue
            # Already parameterized full mount line: - {{VAR}}
            if re.match(r"""^[ \t]+-\s*\{\{[A-Za-z_][A-Za-z0-9_]*\}\}\s*$""", line):
                out.append(line)
                continue
            m = _SHORT_MOUNT_RE.match(line)
            if m:
                indent, source, target, _opts = m.group(1), m.group(2), m.group(3), m.group(4)
                mode, norm_src = classify_volume_source(source)
                if mode == "bind_relative" and ".." in (source or ""):
                    messages.append(f"Skipped volume with '..' path: {source}")
                    out.append(line)
                    continue
                mount_key = f"{mode}|{norm_src}|{target}"
                if mount_key not in volume_vars:
                    base = _volume_var_base(norm_src, target, mode)
                    if mode == "named":
                        vname = _unique_var_name(norm_src, used_names)
                    else:
                        vname = _unique_var_name(f"{base}_DATA", used_names)
                    volume_vars[mount_key] = {
                        "name": vname,
                        "label": f"Storage ({target})",
                        "type": "volume",
                        "default": norm_src,
                        "required": True,
                        "secret": False,
                        "generate": False,
                        "help": f"From host compose · mode {mode} · container {target}",
                        "volume_target": target,
                        "volume_default_mode": mode,
                    }
                    messages.append(
                        f"Volume → {{{{{vname}}}}} ({mode}: {norm_src} → {target})"
                    )
                vname = volume_vars[mount_key]["name"]
                out.append(f"{indent}- {{{{{vname}}}}}")
                continue
            out.append(line)
            continue

        if section == "ports":
            # Already has {{VAR}}
            if re.search(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", line):
                out.append(line)
                continue
            pm = _PORT_MAP_RE.match(line)
            if pm:
                indent, host_p, cont_p, proto = (
                    pm.group(1),
                    pm.group(2),
                    pm.group(3),
                    pm.group(4),
                )
                if host_p not in port_vars:
                    base = _port_var_base(host_p, cont_p, current_service)
                    vname = _unique_var_name(base, used_names)
                    port_vars[host_p] = {
                        "name": vname,
                        "label": f"Host port ({current_service} → {cont_p}"
                        + (f"/{proto}" if proto else "")
                        + ")",
                        "type": "port",
                        "default": host_p,
                        "required": True,
                        "secret": False,
                        "generate": False,
                        "help": f"Container port {cont_p}"
                        + (f"/{proto}" if proto else "")
                        + f" · was host {host_p}",
                    }
                    messages.append(f"Port → {{{{{vname}}}}} (host {host_p} → container {cont_p})")
                vname = port_vars[host_p]["name"]
                suffix = f"/{proto}" if proto else ""
                # Keep quotes if original had them for safety with {{}}
                out.append(f'{indent}- "{{{{{vname}}}}}:{cont_p}{suffix}"')
                continue
            out.append(line)
            continue

        out.append(line)

    if long_form_seen:
        messages.append(
            "Long-form volume mounts (type: volume/bind) were left as-is — convert to short form to templatize."
        )

    new_compose = "\n".join(out)
    if compose.endswith("\n") and not new_compose.endswith("\n"):
        new_compose += "\n"
    elif not new_compose.endswith("\n") and new_compose:
        new_compose += "\n"

    extra_vars = list(volume_vars.values()) + list(port_vars.values())
    if not extra_vars and not messages:
        messages.append("No hard-coded short volumes or host ports found to parameterize.")
    return new_compose, extra_vars, messages


def _infer_type_for_env_var(name: str, default: str, *, secret: bool) -> str:
    if secret:
        return "password"
    d = (default or "").strip().lower()
    if d in _BOOLISH:
        return "boolean"
    if re.fullmatch(r"\d{1,5}", (default or "").strip() or ""):
        # port-like numeric env
        n = int(default.strip())
        if 1 <= n <= 65535 and (
            "PORT" in name.upper() or name.upper().endswith("_PORT")
        ):
            return "port"
    if "PORT" in name.upper() and re.fullmatch(r"\d{1,5}", (default or "").strip() or ""):
        return "port"
    if name.upper().endswith("_URL") or name.upper().endswith("_URI"):
        return "url"
    if "EMAIL" in name.upper():
        return "email"
    return "string"


def suggest_variables_from_content(
    compose: str,
    env_content: str,
    *,
    project_name_default: str = "my-app",
    extra_vars: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build variable dicts from placeholders, .env keys, volumes/ports, and inline env."""
    placeholders = scan_placeholders(compose, env_content)
    env_map = parse_env_file(env_content)
    # If env has real values (not placeholders), use as defaults
    defaults: Dict[str, str] = {}
    for k, v in env_map.items():
        if v.startswith("{{") and v.endswith("}}"):
            defaults[k] = ""
        else:
            defaults[k] = v
        if k not in placeholders:
            placeholders.append(k)

    for _i, key, val in extract_inline_env_assignments(compose):
        if key not in placeholders:
            placeholders.append(key)
        if key not in defaults:
            defaults[key] = val

    vars_out: List[Dict[str, Any]] = []
    names_seen: Set[str] = set()

    def add(name: str, **kwargs: Any) -> None:
        if name in names_seen:
            # Merge richer volume/port metadata onto existing shell entry
            if kwargs.get("type") in ("volume", "port", "boolean"):
                for row in vars_out:
                    if row["name"] == name:
                        for k, v in kwargs.items():
                            if k == "default" and row.get("default") and not v:
                                continue
                            if v is not None and v != "":
                                row[k] = v
                        if kwargs.get("type") == "volume":
                            row["secret"] = False
                            row["generate"] = False
                        break
            return
        names_seen.add(name)
        secret = looks_like_secret_name(name) or bool(kwargs.get("secret"))
        if kwargs.get("type") in ("volume", "boolean"):
            secret = False
        vtype = kwargs.get("type")
        if not vtype:
            vtype = _infer_type_for_env_var(name, str(kwargs.get("default", defaults.get(name, ""))), secret=secret)
        default = kwargs.get("default", defaults.get(name, ""))
        row: Dict[str, Any] = {
            "name": name,
            "label": kwargs.get("label") or name.replace("_", " ").title(),
            "type": vtype,
            "default": default if default is not None else "",
            "required": kwargs.get("required", True),
            "secret": secret,
            "generate": bool(kwargs.get("generate", secret and not defaults.get(name))),
            "help": kwargs.get("help", ""),
        }
        if vtype == "boolean":
            row["true_value"] = kwargs.get("true_value") or "true"
            row["false_value"] = kwargs.get("false_value") or "false"
            # Normalize default to true/false values when possible
            dlow = str(row["default"] or "").strip().lower()
            if dlow in ("1", "true", "yes", "on", "y"):
                row["default"] = row["true_value"]
            elif dlow in ("0", "false", "no", "off", "n", ""):
                row["default"] = row["false_value"]
            row["secret"] = False
            row["generate"] = False
        if vtype == "volume":
            row["volume_target"] = kwargs.get("volume_target") or ""
            row["volume_default_mode"] = kwargs.get("volume_default_mode") or "named"
            row["secret"] = False
            row["generate"] = False
        vars_out.append(row)

    add(
        "PROJECT_NAME",
        label="Project folder name",
        default=project_name_default,
        secret=False,
        generate=False,
        type="string",
    )

    # Prefer structured extras (volumes/ports) first so types stick
    for ev in extra_vars or []:
        if not isinstance(ev, dict) or not ev.get("name"):
            continue
        add(str(ev["name"]), **{k: v for k, v in ev.items() if k != "name"})

    for name in placeholders:
        if name == "PROJECT_NAME":
            continue
        add(name)

    return vars_out


def build_variables_for_host_project(
    compose: str,
    env_content: str,
    *,
    project_name_default: str = "my-app",
    parameterize: bool = True,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """Full from-host path: parameterize volumes/ports, then suggest all variables.

    Returns (compose, variables, messages).
    """
    messages: List[str] = []
    new_compose = compose or ""
    extra: List[Dict[str, Any]] = []
    if parameterize:
        new_compose, extra, msgs = parameterize_compose_volumes_and_ports(
            new_compose, project_name=project_name_default
        )
        messages.extend(msgs)
    variables = suggest_variables_from_content(
        new_compose,
        env_content,
        project_name_default=project_name_default,
        extra_vars=extra,
    )
    vol_n = sum(1 for v in variables if v.get("type") == "volume")
    port_n = sum(1 for v in variables if v.get("type") == "port")
    bool_n = sum(1 for v in variables if v.get("type") == "boolean")
    if vol_n or port_n or bool_n:
        messages.append(
            f"Variables ready: {vol_n} volume(s), {port_n} port(s), {bool_n} boolean(s), "
            f"{len(variables)} total."
        )
    return new_compose, variables, messages


def rewrite_compose_for_docker_secrets(
    compose: str,
    secret_keys: List[str],
) -> Tuple[str, List[str]]:
    """Add Compose secrets: file mapping and attach to services (best-effort).

    Secrets files will live at ./secrets/<KEY> relative to project.
    Environment stays as ${KEY}; deploy path should write secret files and a
    thin .env that does not contain those keys when use_docker_secrets is on.
    """
    messages: List[str] = []
    keys = [k for k in secret_keys if k and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k)]
    if not keys:
        messages.append("No secret keys selected for Docker secrets.")
        return compose, messages

    # Skip if already has secrets: block with our keys
    block_lines = ["", "secrets:"]
    for k in keys:
        fname = k.lower()
        block_lines.append(f"  {fname}:")
        block_lines.append(f"    file: ./secrets/{k}")
    block = "\n".join(block_lines) + "\n"

    text = compose or ""
    if re.search(r"(?m)^secrets:\s*$", text):
        # append under existing secrets
        lines = text.splitlines()
        out = []
        i = 0
        inserted = False
        while i < len(lines):
            out.append(lines[i])
            if not inserted and re.match(r"^secrets:\s*$", lines[i]):
                for k in keys:
                    fname = k.lower()
                    out.append(f"  {fname}:")
                    out.append(f"    file: ./secrets/{k}")
                inserted = True
            i += 1
        text = "\n".join(out) + "\n"
        messages.append("Extended existing secrets: block.")
    else:
        text = text.rstrip() + "\n" + block
        messages.append("Added top-level secrets: block (file-based Compose secrets).")

    # Attach secrets to each service that references ${KEY}
    # Minimal: add under each `  servicename:` that has environment referencing keys
    for k in keys:
        fname = k.lower()
        # If service doesn't list the secret, operator may still mount via env from file
        messages.append(
            f"Secret file path: ./secrets/{k} (deploy will write this). "
            f"Wire service.secrets: [{fname}] if the image reads /run/secrets/{fname}."
        )

    return text, messages


def split_env_for_docker_secrets(
    env_content: str,
    secret_keys: List[str],
) -> Tuple[str, Dict[str, str]]:
    """Return (.env without secrets, secret_file_map KEY->placeholder or value)."""
    env_map = parse_env_file(env_content)
    secrets_map: Dict[str, str] = {}
    public: Dict[str, str] = {}
    secret_set = set(secret_keys)
    for k, v in env_map.items():
        if k in secret_set or looks_like_secret_name(k):
            secrets_map[k] = v
        else:
            public[k] = v
    return format_env_file(public, as_placeholders=False), secrets_map
