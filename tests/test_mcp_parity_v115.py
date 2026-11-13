"""Hosted MCP tools stay aligned with the adapter and HA service contracts."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.mcp_hosted import tool_catalog
from app.services.mcp_parity import (
    adapter_tool_names,
    catalog_names,
    contract_json,
    ha_service_names,
    name_gap,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_ADAPTER = """
def build_server():
    mcp.tool(annotations=_READ)(health)
    mcp.tool(annotations=_WRITE)(prune_volumes)
    mcp.tool(annotations=_WRITE)(start_move)
"""
_HA = """
trigger_job:
  name: Trigger job
start_move:
  name: Start move
  fields:
    server_id:
      name: Server ID
"""


def test_parsers_read_adapter_and_ha_names():
    assert adapter_tool_names(_ADAPTER) == ["health", "prune_volumes", "start_move"]
    assert ha_service_names(_HA) == ["trigger_job", "start_move"]
    assert name_gap(["health", "summary"], ["health"]) == {
        "missing_from_right": ["summary"],
        "extra_on_right": [],
    }


def test_hosted_schema_matches_the_contract_file():
    recorded = (_FIXTURES / "mcp_public_tools.json").read_text(encoding="utf-8")
    assert contract_json(tool_catalog()) == recorded
    names = catalog_names(tool_catalog())
    assert len(names) == len(set(names))
    parsed = json.loads(recorded)
    assert [tool["name"] for tool in parsed] == names


def test_adapter_contract_is_hosted_except_prune_volumes():
    recorded = [
        line.strip()
        for line in (_FIXTURES / "mcp_adapter_tools.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gap = name_gap(catalog_names(tool_catalog()), recorded)
    assert gap == {"missing_from_right": ["prune_volumes"], "extra_on_right": []}


def test_ha_services_are_a_subset_of_hosted_tools():
    recorded = [
        line.strip()
        for line in (_FIXTURES / "mcp_ha_services.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert recorded
    gap = name_gap(catalog_names(tool_catalog()), recorded)
    assert gap["extra_on_right"] == []
    assert "trigger_job" in recorded
    assert "prune_volumes" in gap["missing_from_right"]
