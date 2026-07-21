"""LAN discovery (nmap) — parse, allowlist, argv, vuln pack, upsert helpers."""
from __future__ import annotations

from .allowlist import target_allowed, validate_cidrs
from .argv import INTENSITIES, build_nmap_argv
from .options import SCRIPT_PRESETS, SCRIPT_PRESET_LABELS, parse_scan_options
from .parse import ParsedHost, ParsedPort, ParsedScript, parse_nmap_xml
from .paths import artifact_dir, vuln_pack_status, vuln_root
from .device_classify import (
    KIND_CHOICES,
    MAP_ROLE_GATEWAY,
    MAP_ROLE_LABELS,
    VALID_KINDS,
    VALID_MAP_ROLES,
    classify_device,
    profile_from_device,
)
from .script_classify import (
    classify_script_result,
    classify_scripts,
    ports_with_findings,
)
from .upsert import device_identity_key, upsert_hosts_from_parse

__all__ = [
    "INTENSITIES",
    "KIND_CHOICES",
    "MAP_ROLE_GATEWAY",
    "MAP_ROLE_LABELS",
    "ParsedHost",
    "ParsedPort",
    "ParsedScript",
    "SCRIPT_PRESETS",
    "SCRIPT_PRESET_LABELS",
    "VALID_KINDS",
    "VALID_MAP_ROLES",
    "artifact_dir",
    "build_nmap_argv",
    "classify_device",
    "classify_script_result",
    "classify_scripts",
    "ports_with_findings",
    "device_identity_key",
    "parse_nmap_xml",
    "parse_scan_options",
    "profile_from_device",
    "target_allowed",
    "upsert_hosts_from_parse",
    "validate_cidrs",
    "vuln_pack_status",
    "vuln_root",
]

# Submodules: config, schedules, scan, runtime, options, script_classify, device_classify
