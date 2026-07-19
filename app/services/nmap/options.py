"""Curated nmap scan options (not free-form CLI).

Intensity ladder stays primary. Operators may tune a small allowlisted set of
knobs: script preset, timing, top-ports, UDP, optional port list.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Deep-scan NSE presets (see scan._script_args_for_preset)
SCRIPT_PRESET_NONE = "none"
SCRIPT_PRESET_CPE = "cpe"  # stock vulners CPE/version match (online API)
SCRIPT_PRESET_OFFLINE = "offline"  # pack vulscan only
SCRIPT_PRESET_FULL = "full"  # stock vuln category + vulscan + helpers

SCRIPT_PRESETS = (
    SCRIPT_PRESET_NONE,
    SCRIPT_PRESET_CPE,
    SCRIPT_PRESET_OFFLINE,
    SCRIPT_PRESET_FULL,
)

SCRIPT_PRESET_LABELS = {
    SCRIPT_PRESET_NONE: "No vuln scripts",
    SCRIPT_PRESET_CPE: "CPE / version (vulners)",
    SCRIPT_PRESET_OFFLINE: "Offline tables (vulscan)",
    SCRIPT_PRESET_FULL: "Full (stock vuln + vulscan)",
}

# nmap -T3..-T5 only (avoid paranoid/insane extremes as product defaults)
TIMING_MIN = 3
TIMING_MAX = 5
DEFAULT_TIMING = 4
DEFAULT_TOP_PORTS = 100
TOP_PORTS_MIN = 1
TOP_PORTS_MAX = 1000

# Inventory / detailed port selection
PORT_MODE_TOP = "top"  # --top-ports N (inventory default)
PORT_MODE_ALL = "all"  # -p- all TCP ports
PORT_MODE_LIST = "list"  # explicit curated -p list
PORT_MODES = (PORT_MODE_TOP, PORT_MODE_ALL, PORT_MODE_LIST)
PORT_MODE_LABELS = {
    PORT_MODE_TOP: "Top ports (N most common)",
    PORT_MODE_ALL: "All ports (-p-)",
    PORT_MODE_LIST: "Custom port list",
}

_PORT_LIST_RE = re.compile(r"^[0-9,\-\s]+$")


def normalize_port_mode(raw: str | None, *, port_list: str | None = None) -> str:
    s = (raw or "").strip().lower()
    if s in PORT_MODES:
        return s
    if s in ("all ports", "full", "-p-", "p-"):
        return PORT_MODE_ALL
    if port_list:
        return PORT_MODE_LIST
    return PORT_MODE_TOP


def normalize_script_preset(
    raw: str | None,
    *,
    vuln_scripts_fallback: bool = False,
) -> str:
    """Normalize preset; *vuln_scripts_fallback* maps legacy bool on/off → full/none."""
    s = (raw or "").strip().lower()
    if s in SCRIPT_PRESETS:
        return s
    if s in ("on", "1", "true", "yes"):
        return SCRIPT_PRESET_FULL
    if s in ("off", "0", "false", "no", ""):
        return SCRIPT_PRESET_FULL if vuln_scripts_fallback else SCRIPT_PRESET_NONE
    return SCRIPT_PRESET_FULL if vuln_scripts_fallback else SCRIPT_PRESET_NONE


def preset_wants_scripts(preset: str) -> bool:
    return normalize_script_preset(preset) != SCRIPT_PRESET_NONE


def normalize_timing(raw: Any, *, default: int | None = DEFAULT_TIMING) -> int | None:
    """Return 3–5 or None (omit -T)."""
    if raw is None or raw == "":
        return default
    try:
        t = int(raw)
    except (TypeError, ValueError):
        return default
    if t < TIMING_MIN or t > TIMING_MAX:
        return default
    return t


def normalize_top_ports(raw: Any, *, default: int = DEFAULT_TOP_PORTS) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(TOP_PORTS_MIN, min(TOP_PORTS_MAX, n))


def normalize_port_list(raw: str | None) -> str | None:
    """Allowlisted port list for -p (digits, commas, hyphens). Empty → None."""
    s = (raw or "").strip().replace(" ", "")
    if not s:
        return None
    if not _PORT_LIST_RE.match(s):
        return None
    # reject empty tokens
    parts = [p for p in s.split(",") if p]
    if not parts:
        return None
    cleaned: list[str] = []
    for p in parts:
        if "-" in p:
            a, _, b = p.partition("-")
            if not a.isdigit() or not b.isdigit():
                return None
            lo, hi = int(a), int(b)
            if lo < 1 or hi > 65535 or lo > hi:
                return None
            cleaned.append(f"{lo}-{hi}")
        else:
            if not p.isdigit():
                return None
            n = int(p)
            if n < 1 or n > 65535:
                return None
            cleaned.append(str(n))
    return ",".join(cleaned) if cleaned else None


def parse_scan_options(data: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a dict of curated scan options (schedule / form / job)."""
    data = data or {}
    legacy_vuln = bool(data.get("vuln_scripts"))
    preset = normalize_script_preset(
        data.get("script_preset"),
        vuln_scripts_fallback=legacy_vuln,
    )
    # If explicit vuln_scripts False and no preset, force none
    if "script_preset" not in data and "vuln_scripts" in data and not legacy_vuln:
        preset = SCRIPT_PRESET_NONE
    timing = data.get("timing", DEFAULT_TIMING)
    # Allow explicit null timing to mean "nmap default"
    if data.get("timing") is None and "timing" in data:
        timing_out: int | None = None
    else:
        timing_out = normalize_timing(timing, default=DEFAULT_TIMING)
    use_syn = data.get("use_syn", None)
    if use_syn is not None:
        use_syn = bool(use_syn)
    port_list = normalize_port_list(
        str(data.get("port_list") or data.get("ports") or "") or None
    )
    port_mode = normalize_port_mode(data.get("port_mode"), port_list=port_list)
    # list mode without a valid list falls back to top
    if port_mode == PORT_MODE_LIST and not port_list:
        port_mode = PORT_MODE_TOP
    return {
        "script_preset": preset,
        "vuln_scripts": preset_wants_scripts(preset),  # back-compat flag
        "timing": timing_out,
        "top_ports": normalize_top_ports(
            data.get("top_ports"), default=DEFAULT_TOP_PORTS
        ),
        "include_udp": bool(data.get("include_udp")),
        "port_list": port_list,
        "port_mode": port_mode,
        "use_syn": use_syn,
    }


def form_scan_options(
    *,
    script_preset: str | None = None,
    vuln_scripts: bool = False,
    timing: str | int | None = ...,
    top_ports: str | int | None = None,
    include_udp: bool = False,
    port_list: str | None = None,
    port_mode: str | None = None,
    use_syn: bool | None = None,
) -> dict[str, Any]:
    """Build options from HTML form fields.

    *timing* defaults to DEFAULT_TIMING when omitted; pass ``None`` explicitly
    to omit ``-T`` from argv.
    """
    data: dict[str, Any] = {
        "script_preset": script_preset
        if script_preset is not None
        else (SCRIPT_PRESET_FULL if vuln_scripts else SCRIPT_PRESET_NONE),
        "vuln_scripts": vuln_scripts,
        "top_ports": top_ports if top_ports is not None else DEFAULT_TOP_PORTS,
        "include_udp": include_udp,
        "port_list": port_list,
        "port_mode": port_mode,
        "use_syn": use_syn,
    }
    if timing is not ...:
        data["timing"] = timing
    else:
        data["timing"] = DEFAULT_TIMING
    return parse_scan_options(data)


def dump_scan_options(opts: dict[str, Any]) -> dict[str, Any]:
    """Compact JSON-serializable options for job details / schedule options_json."""
    norm = parse_scan_options(opts)
    out: dict[str, Any] = {
        "script_preset": norm["script_preset"],
        "vuln_scripts": bool(norm["vuln_scripts"]),
        "include_udp": bool(norm["include_udp"]),
        "top_ports": int(norm["top_ports"]),
        "port_mode": norm.get("port_mode") or PORT_MODE_TOP,
    }
    if norm.get("timing") is not None:
        out["timing"] = int(norm["timing"])
    if norm.get("port_list"):
        out["port_list"] = norm["port_list"]
    if norm.get("use_syn") is not None:
        out["use_syn"] = bool(norm["use_syn"])
    return out
