"""From-host: include relative config files + parameterize host literals."""
from __future__ import annotations

from app.services.service_templates.harden import (
    build_variables_for_host_project,
    discover_relative_config_files,
    looks_like_config_file,
    parameterize_host_literals,
    parameterize_compose_volumes_and_ports,
)
from app.services.service_templates.editor import (
    build_definition_from_editor,
    definition_to_editor_form,
    parse_extra_files_json,
)
from app.services.service_templates.schema import (
    merge_variable_values,
    render_template_files,
)


def test_looks_like_config_file():
    assert looks_like_config_file("promtail-config.yaml")
    assert looks_like_config_file("./promtail-config.yaml")
    assert looks_like_config_file("config/app.json")
    assert not looks_like_config_file("data")
    assert not looks_like_config_file("./plugins")
    assert not looks_like_config_file("/var/log")


def test_discover_relative_config_files_promtail_style():
    compose = """
services:
  promtail:
    image: grafana/promtail:latest
    volumes:
      - ./promtail-config.yaml:/etc/promtail/config.yml
      - /var/log:/var/log:ro
      - ./data:/data
    ports:
      - "9080:9080"
"""
    found = discover_relative_config_files(compose)
    assert found == ["promtail-config.yaml"]


def test_config_file_mount_not_volume_var():
    compose = """
services:
  promtail:
    volumes:
      - ./promtail-config.yaml:/etc/promtail/config.yml
      - promtail_data:/data
"""
    new_c, extra, msgs = parameterize_compose_volumes_and_ports(compose)
    assert "./promtail-config.yaml:/etc/promtail/config.yml" in new_c
    assert not any(
        v.get("default") == "promtail-config.yaml" or "promtail-config" in (v.get("name") or "").lower()
        for v in extra
        if v.get("type") == "volume"
    )
    assert any("Config file mount kept" in m for m in msgs)
    assert any(v.get("type") == "volume" for v in extra)


def test_parameterize_host_literals_node_and_remote_url():
    body = """
server:
  http_listen_port: 9080
clients:
  - url: http://rpi5-2.hacknow.info:3100/loki/api/v1/push
scrape_configs:
  - job_name: system-rpi5-1
    static_configs:
      - labels:
          host: rpi5-1
"""
    out, vars_, msgs = parameterize_host_literals(
        body, node_name="rpi5-1", host_fqdn="rpi5-1.hacknow.info"
    )
    assert "{{NODE_NAME}}" in out
    assert "rpi5-1" not in out.replace("{{NODE_NAME}}", "")
    assert "system-{{NODE_NAME}}" in out
    assert "host: {{NODE_NAME}}" in out
    assert any(v["name"] == "NODE_NAME" for v in vars_)
    assert any(v["name"] == "LOKI_URL" for v in vars_)
    assert "{{LOKI_URL}}" in out
    assert "rpi5-2.hacknow.info" not in out


def test_build_variables_includes_extra_files():
    compose = """
services:
  promtail:
    image: grafana/promtail:latest
    volumes:
      - ./promtail-config.yaml:/etc/promtail/config.yml
    command: -config.file=/etc/promtail/config.yml
"""
    promtail = """
clients:
  - url: http://rpi5-2.hacknow.info:3100/loki/api/v1/push
scrape_configs:
  - job_name: system-rpi5-1
    static_configs:
      - labels:
          host: rpi5-1
"""
    new_c, variables, msgs, extra = build_variables_for_host_project(
        compose,
        "",
        project_name_default="grafana-monitoring",
        parameterize=True,
        extra_file_texts={"promtail-config.yaml": promtail},
        node_name="rpi5-1",
        host_fqdn="rpi5-1.hacknow.info",
    )
    assert "promtail-config.yaml" in extra
    assert "{{NODE_NAME}}" in extra["promtail-config.yaml"]
    by = {v["name"]: v for v in variables}
    assert "NODE_NAME" in by
    assert by["NODE_NAME"]["default"] == "rpi5-1"
    assert "LOKI_URL" in by


def test_extra_files_round_trip_render():
    definition = build_definition_from_editor(
        slug="grafana-monitoring",
        name="Grafana Monitoring",
        compose_content=(
            "services:\n"
            "  promtail:\n"
            "    image: grafana/promtail:latest\n"
            "    volumes:\n"
            "      - ./promtail-config.yaml:/etc/promtail/config.yml\n"
        ),
        env_content="",
        extra_files_json=(
            '[{"path": "promtail-config.yaml", "content": '
            '"clients:\\n  - url: {{LOKI_URL}}\\n'
            'scrape_configs:\\n  - job_name: system-{{NODE_NAME}}\\n'
            '    static_configs:\\n      - labels:\\n          host: {{NODE_NAME}}\\n"}]'
        ),
        variables_json=(
            "["
            '{"name":"PROJECT_NAME","label":"Project","type":"string","default":"grafana-monitoring","required":true},'
            '{"name":"NODE_NAME","label":"Node","type":"string","default":"rpi5-1","required":true},'
            '{"name":"LOKI_URL","label":"Loki","type":"url","default":"http://loki:3100/loki/api/v1/push","required":true}'
            "]"
        ),
        checklist_json="[]",
        source="user",
    )
    assert "promtail-config.yaml" in definition.file_contents
    form = definition_to_editor_form(definition)
    extra = parse_extra_files_json(form["extra_files_json"])
    assert len(extra) == 1
    assert extra[0]["path"] == "promtail-config.yaml"

    values = merge_variable_values(
        definition,
        {
            "PROJECT_NAME": "grafana-monitoring",
            "NODE_NAME": "rpi5-3",
            "LOKI_URL": "http://central:3100/loki/api/v1/push",
        },
    )
    files = render_template_files(definition, values)
    assert "host: rpi5-3" in files["promtail-config.yaml"]
    assert "system-rpi5-3" in files["promtail-config.yaml"]
    assert "http://central:3100/loki/api/v1/push" in files["promtail-config.yaml"]
    assert "./promtail-config.yaml:/etc/promtail/config.yml" in files["docker-compose.yml"]
