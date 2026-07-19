"""Map intensity profiles to nmap argv (never includes flood/brute scripts)."""
from __future__ import annotations

from typing import Sequence

# Product intensities — see FEATURE_PLAN_LAN_NMAP.md
INTENSITY_DISCOVERY = "discovery"
INTENSITY_INVENTORY = "inventory"
INTENSITY_DETAILED = "detailed"
INTENSITY_DEEP = "deep"

INTENSITIES = (
    INTENSITY_DISCOVERY,
    INTENSITY_INVENTORY,
    INTENSITY_DETAILED,
    INTENSITY_DEEP,
)

# Scripts we never pass (DoS / brute / external malware reputation)
_FORBIDDEN_SCRIPT_FRAGMENTS = (
    "brute",
    "slowloris",
    "dos",
    "http-google-malware",
    "malware",
)


def build_nmap_argv(
    intensity: str,
    targets: Sequence[str],
    *,
    output_xml: str,
    skip_dns: bool = True,
    use_syn: bool = False,
    include_udp: bool = False,
    vuln_scripts: bool = False,
    top_ports: int = 100,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    """Build argv list starting with ``nmap``.

    *vuln_scripts* only adds stock ``vuln`` / vulners script names — caller must
    gate on pack presence and operator flag.
    """
    intensity = (intensity or INTENSITY_DISCOVERY).strip().lower()
    if intensity not in INTENSITIES:
        intensity = INTENSITY_DISCOVERY

    argv: list[str] = ["nmap", "-oX", output_xml]
    if skip_dns:
        argv.append("-n")

    scan_flag = "-sS" if use_syn else "-sT"

    if intensity == INTENSITY_DISCOVERY:
        # Host discovery only
        argv.extend(["-sn"])
    elif intensity == INTENSITY_INVENTORY:
        argv.extend([scan_flag, "-sV", f"--top-ports", str(max(1, min(1000, int(top_ports))))])
    elif intensity == INTENSITY_DETAILED:
        argv.extend([scan_flag, "-sV", "-p-", "-T4"])
    elif intensity == INTENSITY_DEEP:
        argv.extend([scan_flag, "-sV", "-p-", "-T4"])
        if vuln_scripts:
            # Stock vuln category; vulners NSE uses data from mounted volume when present
            argv.extend(["--script", "vuln,vulners"])

    if include_udp and intensity != INTENSITY_DISCOVERY:
        # UDP is expensive — only when explicitly requested
        argv.append("-sU")

    if extra_args:
        for a in extra_args:
            s = (a or "").strip()
            if not s:
                continue
            low = s.lower()
            if any(f in low for f in _FORBIDDEN_SCRIPT_FRAGMENTS):
                continue
            argv.append(s)

    target_count = 0
    for t in targets:
        s = (t or "").strip()
        if s:
            argv.append(s)
            target_count += 1

    if target_count == 0:
        raise ValueError("nmap argv requires at least one target")
    return argv
