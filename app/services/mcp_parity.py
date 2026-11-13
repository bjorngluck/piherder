"""Compare hosted MCP tools with the stdio adapter and the HA plugin.

The hosted catalog is the schema this repo ships. The adapter and the plugin
live in other repositories. Checked-in name lists are the contract CI compares.
A new name on either side fails that compare.
"""
from __future__ import annotations

import json
import re
from typing import Any

_ADAPTER_TOOL = re.compile(r"mcp\.tool\([^)]*\)\(([A-Za-z_][A-Za-z0-9_]*)\)")
_YAML_KEY = re.compile(r"^([a-z][a-z0-9_]*):\s*$", re.MULTILINE)


def public_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": tool["inputSchema"],
    }


def public_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_tool(tool) for tool in catalog]


def catalog_names(catalog: list[dict[str, Any]]) -> list[str]:
    return [str(tool["name"]) for tool in catalog]


def adapter_tool_names(source: str) -> list[str]:
    """Names registered with mcp.tool(...)(function) in the stdio adapter."""
    return _ADAPTER_TOOL.findall(source)


def ha_service_names(source: str) -> list[str]:
    """Top-level keys in the Home Assistant services file."""
    return [match.group(1) for match in _YAML_KEY.finditer(source)]


def name_gap(left: list[str], right: list[str]) -> dict[str, list[str]]:
    """Names in left but not right, and names in right but not left."""
    left_set = set(left)
    right_set = set(right)
    return {
        "missing_from_right": sorted(left_set - right_set),
        "extra_on_right": sorted(right_set - left_set),
    }


def contract_json(catalog: list[dict[str, Any]]) -> str:
    return json.dumps(public_catalog(catalog), indent=2, sort_keys=True) + "\n"
