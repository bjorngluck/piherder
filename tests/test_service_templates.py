"""Service template schema, render, and catalog (no SSH)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.security.encryption import encrypt_str, decrypt_str
from app.services.service_templates.schema import (
    TemplateError,
    generate_secret,
    load_template_dir,
    mask_secrets_in_files,
    merge_variable_values,
    render_checklist,
    render_template_files,
    split_secrets,
    validate_project_name,
    definition_to_storage_json,
    definition_from_storage_json,
)


def _repo_templates_root() -> Path:
    return Path(__file__).resolve().parents[1] / "service_templates"


def test_builtin_pack_loads():
    root = _repo_templates_root()
    assert root.is_dir(), "service_templates/ missing"
    slugs = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "template.yaml").is_file():
            continue
        d = load_template_dir(child, source="builtin")
        slugs.append(d.slug)
        assert d.file_contents
        assert d.checksum()
    assert set(slugs) >= {"npm", "uptime-kuma", "pihole", "grafana"}


def test_render_uptime_kuma():
    d = load_template_dir(_repo_templates_root() / "uptime-kuma")
    values = merge_variable_values(
        d,
        {"PROJECT_NAME": "kuma-lab", "KUMA_PORT": "3002", "KUMA_HOSTNAME": "kuma.lab"},
    )
    files = render_template_files(d, values)
    assert "docker-compose.yml" in files
    assert "3002:3001" in files["docker-compose.yml"]
    checklist = render_checklist(d, values)
    assert any("kuma.lab" in c["body"] for c in checklist)


def test_render_secrets_and_mask():
    d = load_template_dir(_repo_templates_root() / "grafana")
    values = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GRAFANA_PORT": "3000",
        },
        auto_generate=False,
    )
    files = render_template_files(d, values)
    assert "s3cret-pass-xyz" in files[".env"]
    public, secrets = split_secrets(d, values)
    assert "GF_SECURITY_ADMIN_PASSWORD" in secrets
    assert "GF_SECURITY_ADMIN_USER" in public
    masked = mask_secrets_in_files(files, secrets)
    assert "s3cret-pass-xyz" not in masked[".env"]
    assert "********" in masked[".env"]


def test_auto_generate_secret():
    d = load_template_dir(_repo_templates_root() / "pihole")
    values = merge_variable_values(d, {"PROJECT_NAME": "pihole"}, auto_generate=True)
    assert values["WEBPASSWORD"]
    assert len(values["WEBPASSWORD"]) >= 16


def test_project_name_validation():
    assert validate_project_name("my-app_1") == "my-app_1"
    with pytest.raises(TemplateError):
        validate_project_name("../etc")
    with pytest.raises(TemplateError):
        validate_project_name("")


def test_storage_roundtrip():
    d = load_template_dir(_repo_templates_root() / "npm")
    raw = definition_to_storage_json(d)
    d2 = definition_from_storage_json(raw, source="import")
    assert d2.slug == "npm"
    assert "docker-compose.yml" in d2.file_contents
    files = render_template_files(
        d2,
        merge_variable_values(
            d2,
            {
                "PROJECT_NAME": "npm",
                "DB_MYSQL_PASSWORD": "a",
                "DB_MYSQL_ROOT_PASSWORD": "b",
            },
            auto_generate=False,
        ),
    )
    assert "jc21/nginx-proxy-manager" in files["docker-compose.yml"]


def test_secret_encrypt_roundtrip():
    secret_map = {"WEBPASSWORD": generate_secret(20)}
    token = encrypt_str(json.dumps(secret_map))
    back = json.loads(decrypt_str(token))
    assert back == secret_map


def test_missing_required_var():
    d = load_template_dir(_repo_templates_root() / "grafana")
    with pytest.raises(TemplateError):
        merge_variable_values(
            d,
            {"PROJECT_NAME": "g", "GF_SECURITY_ADMIN_USER": "admin"},
            auto_generate=False,
        )


def test_boolean_variable_coercion():
    d = load_template_dir(_repo_templates_root() / "grafana")
    values = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GF_USERS_ALLOW_SIGN_UP": "yes",
        },
        auto_generate=False,
    )
    assert values["GF_USERS_ALLOW_SIGN_UP"] == "true"
    values_off = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GF_USERS_ALLOW_SIGN_UP": "0",
        },
        auto_generate=False,
    )
    assert values_off["GF_USERS_ALLOW_SIGN_UP"] == "false"
    files = render_template_files(d, values_off)
    assert "GF_USERS_ALLOW_SIGN_UP=false" in files[".env"] or "false" in files[".env"]


def test_volume_named_and_bind_modes():
    from app.services.service_templates.schema import sync_compose_named_volumes

    d = load_template_dir(_repo_templates_root() / "grafana")
    # Named (default)
    values = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GRAFANA_DATA": "grafana_data",
            "GRAFANA_DATA__mode": "named",
        },
        auto_generate=False,
    )
    assert values["GRAFANA_DATA"] == "grafana_data:/var/lib/grafana"
    assert values["GRAFANA_DATA__source"] == "grafana_data"
    files = render_template_files(d, values)
    assert "grafana_data:/var/lib/grafana" in files["docker-compose.yml"]
    assert re_search_vol(files["docker-compose.yml"], "grafana_data")

    # Relative bind — named volume entry should drop
    values_b = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GRAFANA_DATA": "data",
            "GRAFANA_DATA__mode": "bind_relative",
        },
        auto_generate=False,
    )
    assert values_b["GRAFANA_DATA"] == "./data:/var/lib/grafana"
    files_b = render_template_files(d, values_b)
    assert "./data:/var/lib/grafana" in files_b["docker-compose.yml"]
    # managed named volume removed when using bind
    assert "  grafana_data:" not in files_b["docker-compose.yml"]

    # Absolute bind
    values_a = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GRAFANA_DATA": "/mnt/ssd/grafana",
            "GRAFANA_DATA__mode": "bind_absolute",
        },
        auto_generate=False,
    )
    assert values_a["GRAFANA_DATA"] == "/mnt/ssd/grafana:/var/lib/grafana"

    with pytest.raises(TemplateError):
        merge_variable_values(
            d,
            {
                "PROJECT_NAME": "gf",
                "GF_SECURITY_ADMIN_USER": "admin",
                "GF_SECURITY_ADMIN_PASSWORD": "s3cret",
                "GRAFANA_DATA": "../etc",
                "GRAFANA_DATA__mode": "bind_relative",
            },
            auto_generate=False,
        )

    # Re-merge from already-rendered mount (confirm stash / redeploy)
    rem = merge_variable_values(
        d,
        {
            "PROJECT_NAME": "gf",
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "s3cret-pass-xyz",
            "GRAFANA_DATA": "grafana_data:/var/lib/grafana",
            "GRAFANA_DATA__mode": "named",
            "GRAFANA_DATA__source": "grafana_data",
        },
        auto_generate=False,
    )
    assert rem["GRAFANA_DATA"] == "grafana_data:/var/lib/grafana"

    synced = sync_compose_named_volumes(
        "services:\n  x:\n    image: y\n",
        ["vol_a", "vol_b"],
    )
    assert "volumes:" in synced
    assert "  vol_a:" in synced


def re_search_vol(compose: str, name: str) -> bool:
    return f"  {name}:" in compose


def test_editor_build_and_roundtrip():
    from app.services.service_templates.editor import (
        build_definition_from_editor,
        definition_to_editor_form,
        blank_editor_form,
    )

    d = build_definition_from_editor(
        slug="my-stack",
        name="My Stack",
        description="lab",
        category="other",
        version="1.0.0",
        compose_content='services:\n  web:\n    image: nginx\n    ports:\n      - "{{PORT}}:80"\n',
        env_content="PORT={{PORT}}\n",
        variables_json=json.dumps(
            [
                {"name": "PROJECT_NAME", "default": "my-stack", "required": True},
                {"name": "PORT", "label": "Port", "type": "port", "default": "8080"},
            ]
        ),
        checklist_json=json.dumps([{"title": "DNS", "body": "Point HOST"}]),
        source="user",
    )
    assert d.slug == "my-stack"
    assert "docker-compose.yml" in d.file_contents
    assert ".env" in d.file_contents
    form = definition_to_editor_form(d)
    assert form["slug"] == "my-stack"
    assert "8080" in form["variables_json"] or "PORT" in form["variables_json"]
    blank = blank_editor_form()
    assert blank["compose_content"]


def test_editor_rejects_empty_compose():
    from app.services.service_templates.editor import build_definition_from_editor

    with pytest.raises(TemplateError):
        build_definition_from_editor(
            slug="x",
            name="X",
            compose_content="",
            variables_json="[]",
        )


def test_move_secrets_to_env_and_scan():
    from app.services.service_templates.harden import (
        move_secrets_to_env,
        scan_placeholders,
        suggest_variables_from_content,
    )

    compose = """
services:
  app:
    environment:
      DB_PASSWORD: s3cret
      LOG_LEVEL: info
"""
    c2, e2, extracted, msgs = move_secrets_to_env(compose, "")
    assert "s3cret" not in c2
    assert "${DB_PASSWORD}" in c2
    assert "DB_PASSWORD={{DB_PASSWORD}}" in e2
    assert extracted["DB_PASSWORD"] == "s3cret"
    # non-secret left alone
    assert "LOG_LEVEL: info" in c2

    assert "FOO" in scan_placeholders("x={{FOO}} y={{BAR}}", "{{FOO}}")
    vars_ = suggest_variables_from_content(c2, e2, project_name_default="npm")
    names = {v["name"] for v in vars_}
    assert "PROJECT_NAME" in names
    assert "DB_PASSWORD" in names
    db = next(v for v in vars_ if v["name"] == "DB_PASSWORD")
    assert db["secret"] is True


def test_from_host_parameterizes_volumes_ports_booleans():
    from app.services.service_templates.harden import (
        build_variables_for_host_project,
        parameterize_compose_volumes_and_ports,
    )
    from app.services.service_templates.schema import (
        merge_variable_values,
        parse_definition_dict,
        render_template_files,
        SCHEMA_VERSION,
    )

    compose = """
services:
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./plugins:/var/lib/grafana/plugins
      - /mnt/ssd/grafana:/backup
  db:
    image: postgres
    ports:
      - 5432:5432
    volumes:
      - pg_data:/var/lib/postgresql/data

volumes:
  grafana_data:
  pg_data:
"""
    env = "GF_USERS_ALLOW_SIGN_UP=false\nADMIN_PASSWORD=s3cret\n"
    new_c, extra, msgs = parameterize_compose_volumes_and_ports(compose, project_name="grafana")
    assert "{{GRAFANA_DATA}}" in new_c or "grafana_data" not in new_c.split("volumes:")[1].split("volumes:")[0]
    assert "{{" in new_c
    assert "- grafana_data:/var/lib/grafana" not in new_c
    assert any(v["type"] == "volume" for v in extra)
    assert any(v["type"] == "port" for v in extra)

    new_c2, variables, msgs2 = build_variables_for_host_project(
        compose, env, project_name_default="grafana"
    )
    by = {v["name"]: v for v in variables}
    vol_types = [v for v in variables if v["type"] == "volume"]
    assert len(vol_types) >= 3
    # named
    named = next(v for v in vol_types if v.get("volume_default_mode") == "named" and "grafana" in v["name"].lower() or v.get("default") == "grafana_data")
    assert named["volume_target"] == "/var/lib/grafana"
    # relative bind
    rel = next(v for v in vol_types if v.get("volume_default_mode") == "bind_relative")
    assert rel["default"] in ("plugins", "./plugins") or "plugin" in rel["default"]
    # absolute
    abs_v = next(v for v in vol_types if v.get("volume_default_mode") == "bind_absolute")
    assert abs_v["default"].startswith("/")
    # port
    ports = [v for v in variables if v["type"] == "port"]
    assert any(v["default"] == "3000" for v in ports)
    # boolean from env
    assert by.get("GF_USERS_ALLOW_SIGN_UP", {}).get("type") == "boolean"
    # secret from env name
    assert by.get("ADMIN_PASSWORD", {}).get("secret") is True

    # Round-trip: definition render with defaults
    meta = {
        "schema_version": SCHEMA_VERSION,
        "slug": "grafana-host",
        "name": "Grafana Host",
        "variables": variables,
        "files": [{"path": "docker-compose.yml"}, {"path": ".env"}],
    }
    d = parse_definition_dict(meta, source="user")
    d.file_contents = {
        "docker-compose.yml": new_c2,
        ".env": "GF_USERS_ALLOW_SIGN_UP={{GF_USERS_ALLOW_SIGN_UP}}\nADMIN_PASSWORD={{ADMIN_PASSWORD}}\n",
    }
    provided = {v["name"]: v.get("default") or "" for v in variables if v["type"] != "password"}
    provided["ADMIN_PASSWORD"] = "x"
    # volume modes from defaults
    for v in variables:
        if v["type"] == "volume":
            provided[f"{v['name']}__mode"] = v.get("volume_default_mode") or "named"
            provided[v["name"]] = v.get("default") or ""
    values = merge_variable_values(d, provided, auto_generate=False)
    files = render_template_files(d, values)
    assert "grafana_data:/var/lib/grafana" in files["docker-compose.yml"]
    assert "./plugins:" in files["docker-compose.yml"] or "plugins:" in files["docker-compose.yml"]
    assert "/mnt/ssd/grafana:" in files["docker-compose.yml"]
    assert "3000:3000" in files["docker-compose.yml"]


def test_docker_secrets_rewrite():
    from app.services.service_templates.harden import rewrite_compose_for_docker_secrets

    c, msgs = rewrite_compose_for_docker_secrets(
        "services:\n  app:\n    image: x\n",
        ["DB_PASSWORD"],
    )
    assert "secrets:" in c
    assert "file: ./secrets/DB_PASSWORD" in c
    assert msgs


def test_list_host_projects_includes_containers():
    from app.services.service_templates.from_host import list_host_projects_for_picker
    from app.models import Server
    from unittest.mock import patch

    server = Server(id=1, name="pi", hostname="pi.local")
    inv = {
        "v": 1,
        "projects": [
            {
                "name": "npm",
                "container_count": 2,
                "running_count": 2,
                "services": ["app", "db"],
                "containers": [
                    {"name": "npm-app-1", "compose_service": "app", "running": True},
                    {"name": "npm-db-1", "compose_service": "db", "running": True},
                ],
            }
        ],
    }
    with patch(
        "app.services.docker_inventory.parse_inventory",
        return_value=inv,
    ):
        rows = list_host_projects_for_picker(server)
    assert len(rows) == 1
    assert rows[0]["name"] == "npm"
    assert rows[0]["services_label"]
    assert len(rows[0]["containers"]) == 2


def test_apply_scan_and_harden_form_tools():
    from app.services.service_templates.editor import (
        apply_harden_env_to_form,
        apply_scan_vars_to_form,
        redact_secret_variable_dicts,
    )

    form = {
        "slug": "npm",
        "name": "NPM",
        "compose_content": "services:\n  db:\n    environment:\n      MYSQL_PASSWORD: abc\n",
        "env_content": "",
        "variables_json": "[]",
        "checklist_json": "[]",
    }
    form, msgs = apply_harden_env_to_form(form, reveal_secrets=False)
    assert "MYSQL_PASSWORD={{MYSQL_PASSWORD}}" in form["env_content"]
    assert "abc" not in form["variables_json"]
    form, msgs = apply_scan_vars_to_form(form, reveal_secrets=False)
    assert "MYSQL_PASSWORD" in form["variables_json"]
    assert "abc" not in form["variables_json"]

    redacted = redact_secret_variable_dicts(
        [{"name": "MYSQL_PASSWORD", "secret": True, "default": "abc"}],
        reveal=False,
    )
    assert redacted[0]["default"] == ""
    shown = redact_secret_variable_dicts(
        [{"name": "MYSQL_PASSWORD", "secret": True, "default": "abc"}],
        reveal=True,
    )
    assert shown[0]["default"] == "abc"


def test_redact_files_for_ui_masks_env_and_secrets_dir():
    from app.services.service_templates.schema import (
        files_for_db_storage,
        redact_files_for_ui,
    )

    files = {
        "docker-compose.yml": "services:\n  db:\n    environment:\n      MYSQL_PASSWORD: supersecret99\n",
        ".env": "MYSQL_PASSWORD=supersecret99\nMYSQL_USER=npm\n",
        "secrets/MYSQL_PASSWORD": "supersecret99",
        "secrets/MYSQL_ROOT_PASSWORD": "root-secret-xx",
    }
    secret_values = {
        "MYSQL_PASSWORD": "supersecret99",
        "MYSQL_ROOT_PASSWORD": "root-secret-xx",
    }
    masked = redact_files_for_ui(
        files, secret_values=secret_values, secret_keys=list(secret_values), reveal=False
    )
    assert "supersecret99" not in masked[".env"]
    assert "supersecret99" not in masked["docker-compose.yml"]
    assert masked["secrets/MYSQL_PASSWORD"] == "********"
    assert masked["secrets/MYSQL_ROOT_PASSWORD"] == "********"
    assert "MYSQL_PASSWORD=********" in masked[".env"]
    # reveal keeps bodies (for step-up UI after merge)
    shown = redact_files_for_ui(files, secret_values=secret_values, reveal=True)
    assert shown["secrets/MYSQL_PASSWORD"] == "supersecret99"

    stored = files_for_db_storage(files, secret_values)
    assert "secrets/MYSQL_PASSWORD" not in stored
    assert "supersecret99" not in (stored.get(".env") or "")
    assert "supersecret99" not in (stored.get("docker-compose.yml") or "")
    assert "MYSQL_PASSWORD=" in (stored.get(".env") or "")


def test_annotate_projects_with_template_deployments():
    from app.services.service_templates.deploy import annotate_projects_with_deployments

    projects = [
        {"name": "npm", "path": "/home/x/docker/npm"},
        {"name": "other", "path": "/home/x/docker/other"},
    ]
    by = {
        "npm": {
            "deployment_id": 7,
            "template_slug": "nginx-proxy-manager",
            "template_version": "1.0.0",
            "config_version": 3,
            "drift_status": "in_sync",
        }
    }
    annotate_projects_with_deployments(projects, by)
    assert projects[0]["template_managed"] is True
    assert projects[0]["template_deployment_id"] == 7
    assert projects[0]["template_slug"] == "nginx-proxy-manager"
    assert projects[0]["template_config_version"] == 3
    assert projects[1]["template_managed"] is False
    assert "template_deployment_id" not in projects[1]


def test_secrets_unlock_token_is_step_up_not_login_2fa():
    """Having TOTP enabled is not enough — need secrets_unlock cookie after re-check."""
    from unittest.mock import MagicMock

    from app.models import User
    from app.security.auth import (
        SECRETS_UNLOCK_COOKIE,
        create_secrets_unlock_token,
        decode_token_payload,
        secrets_unlock_active,
    )

    user = User(
        id=42,
        email="op@example.com",
        hashed_password="x",
        role="operator",
        totp_enabled=True,
    )
    # No cookie → locked even with 2FA on the account
    bare = MagicMock()
    bare.cookies = {}
    assert secrets_unlock_active(bare, user) is False

    tok = create_secrets_unlock_token(user.id)
    payload = decode_token_payload(tok)
    assert payload is not None
    assert payload.get("secrets_unlock") is True
    assert int(payload["sub"]) == 42

    unlocked = MagicMock()
    unlocked.cookies = {SECRETS_UNLOCK_COOKIE: tok}
    assert secrets_unlock_active(unlocked, user) is True

    # Wrong user id must not unlock
    other = User(
        id=99,
        email="other@example.com",
        hashed_password="x",
        role="operator",
        totp_enabled=True,
    )
    assert secrets_unlock_active(unlocked, other) is False
