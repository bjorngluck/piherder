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
    timing: int | None = 4,
    port_list: str | None = None,
    port_mode: str | None = None,
    exclude_hosts: Sequence[str] | None = None,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    """Build argv list starting with ``nmap``.

    *vuln_scripts* adds a simple ``vuln,vulners`` script set for unit tests /
    fallback. Production deep scans prefer pack-aware script args from
    ``scan._script_args_for_preset`` (appended by the runner).

    *timing*: nmap ``-T3``..``-T5`` or None to omit.
    *port_mode*: ``top`` | ``all`` | ``list`` (see options.PORT_MODES).
    *port_list*: curated ``-p`` list when mode is list (or legacy override).
    *exclude_hosts*: IPs/CIDRs passed as nmap ``--exclude``.
    """
    from .allowlist import nmap_exclude_args
    from .options import PORT_MODE_ALL, PORT_MODE_LIST, PORT_MODE_TOP, normalize_port_mode

    intensity = (intensity or INTENSITY_DISCOVERY).strip().lower()
    if intensity not in INTENSITIES:
        intensity = INTENSITY_DISCOVERY

    argv: list[str] = ["nmap", "-oX", output_xml]
    if skip_dns:
        argv.append("-n")

    scan_flag = "-sS" if use_syn else "-sT"
    ports_override = (port_list or "").strip() or None
    mode = normalize_port_mode(port_mode, port_list=ports_override)
    # Legacy: explicit port_list without mode → list
    if ports_override and not port_mode:
        mode = PORT_MODE_LIST
    # detailed/deep historically default to all ports (-p-); only use top when
    # the operator explicitly picks port_mode=top
    if (
        intensity in (INTENSITY_DETAILED, INTENSITY_DEEP)
        and not port_mode
        and not ports_override
    ):
        mode = PORT_MODE_ALL

    if intensity == INTENSITY_DISCOVERY:
        # Host discovery only. On the same L2 (host-network worker) nmap uses ARP
        # and records MAC addresses; reverse DNS fills hostnames unless -n (skip_dns).
        argv.extend(["-sn", "-PR"])
    elif intensity == INTENSITY_INVENTORY:
        argv.extend([scan_flag, "-sV"])
        if mode == PORT_MODE_LIST and ports_override:
            argv.extend(["-p", ports_override])
        elif mode == PORT_MODE_ALL:
            argv.append("-p-")
        else:
            argv.extend(
                ["--top-ports", str(max(1, min(1000, int(top_ports or 100))))]
            )
    elif intensity == INTENSITY_DETAILED:
        argv.extend([scan_flag, "-sV"])
        if mode == PORT_MODE_LIST and ports_override:
            argv.extend(["-p", ports_override])
        elif mode == PORT_MODE_TOP:
            argv.extend(
                ["--top-ports", str(max(1, min(1000, int(top_ports or 100))))]
            )
        else:
            # default detailed = all ports
            argv.append("-p-")
    elif intensity == INTENSITY_DEEP:
        argv.extend([scan_flag, "-sV"])
        if mode == PORT_MODE_LIST and ports_override:
            argv.extend(["-p", ports_override])
        elif mode == PORT_MODE_TOP:
            argv.extend(
                ["--top-ports", str(max(1, min(1000, int(top_ports or 100))))]
            )
        else:
            argv.append("-p-")
        if vuln_scripts:
            # Fallback only — runner usually injects pack-aware --script
            argv.extend(["--script", "vuln,vulners"])

    # Timing: not useful on pure -sn discovery
    if intensity != INTENSITY_DISCOVERY and timing is not None:
        try:
            t = int(timing)
            if 3 <= t <= 5:
                argv.append(f"-T{t}")
        except (TypeError, ValueError):
            pass

    if include_udp and intensity != INTENSITY_DISCOVERY:
        # UDP is expensive — only when explicitly requested
        argv.append("-sU")

    # Host excludes (do not reject whole CIDR targets — nmap skips these hosts)
    argv.extend(nmap_exclude_args(exclude_hosts))

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
