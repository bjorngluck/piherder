"""Privilege helpers for nmap scan types (SYN / raw sockets).

Stock nmap gates SYN (-sS), UDP (-sU), and OS detect (-O) on **geteuid()==0**,
not file capabilities. setcap on the binary is not enough — the worker must run
as root (Dockerfile.nmap) for true SYN. Otherwise we fall back to -sT.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_ROOT_REQUIRED_MARKERS = (
    "requires root privileges",
    "requires root",
    "you requested a scan type which requires root",
    "tcp/ip fingerprinting",
    "only root",
)


def is_root_required_error(stderr: str | None) -> bool:
    """True when nmap quit because the scan type needs elevated privileges."""
    text = (stderr or "").lower()
    return any(m in text for m in _ROOT_REQUIRED_MARKERS)


def can_syn_scan() -> bool:
    """Whether -sS is usable. nmap requires euid 0 (not merely CAP_NET_RAW)."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def resolve_use_syn(want_syn: bool) -> tuple[bool, Optional[str]]:
    """Return (effective_use_syn, downgrade_reason).

    Operators may prefer SYN for speed; without root we fall back to TCP
    connect (-sT) so inventory/detailed/deep still complete.
    """
    if not want_syn:
        return False, None
    if can_syn_scan():
        return True, None
    reason = (
        "SYN (-sS) requested but nmap worker is not root — using TCP connect (-sT). "
        "The dedicated nmap image should run as root (euid 0) for SYN; otherwise "
        "uncheck Prefer SYN on the LAN Discovery integration."
    )
    return False, reason
