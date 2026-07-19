"""LAN discovery (nmap) — parse, allowlist, argv, vuln pack, upsert helpers."""
from __future__ import annotations

from .allowlist import target_allowed, validate_cidrs
from .argv import INTENSITIES, build_nmap_argv
from .options import SCRIPT_PRESETS, SCRIPT_PRESET_LABELS, parse_scan_options
from .parse import ParsedHost, ParsedPort, ParsedScript, parse_nmap_xml
from .paths import artifact_dir, vuln_pack_status, vuln_root
from .script_classify import classify_script_result, classify_scripts
from .upsert import device_identity_key, upsert_hosts_from_parse

__all__ = [
    "INTENSITIES",
    "ParsedHost",
    "ParsedPort",
    "ParsedScript",
    "SCRIPT_PRESETS",
    "SCRIPT_PRESET_LABELS",
    "artifact_dir",
    "build_nmap_argv",
    "classify_script_result",
    "classify_scripts",
    "device_identity_key",
    "parse_nmap_xml",
    "parse_scan_options",
    "target_allowed",
    "upsert_hosts_from_parse",
    "validate_cidrs",
    "vuln_pack_status",
    "vuln_root",
]

# Submodules: config, schedules, scan, runtime, options, script_classify
