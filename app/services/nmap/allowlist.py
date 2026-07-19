"""CIDR / target allowlist for LAN discovery (refuse out-of-scope scans)."""
from __future__ import annotations

import ipaddress
from typing import Iterable, Sequence


def _parse_network(raw: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if "/" in s:
            return ipaddress.ip_network(s, strict=False)
        # bare IP → /32 or /128
        addr = ipaddress.ip_address(s)
        if isinstance(addr, ipaddress.IPv4Address):
            return ipaddress.ip_network(f"{addr}/32", strict=False)
        return ipaddress.ip_network(f"{addr}/128", strict=False)
    except ValueError:
        return None


def validate_cidrs(cidrs: Sequence[str]) -> tuple[list[str], list[str]]:
    """Return (ok_list, error_messages)."""
    ok: list[str] = []
    errs: list[str] = []
    for raw in cidrs:
        s = (raw or "").strip()
        if not s:
            continue
        net = _parse_network(s)
        if net is None:
            errs.append(f"invalid CIDR or IP: {s!r}")
            continue
        ok.append(str(net))
    return ok, errs


def target_allowed(
    target: str,
    allowed_cidrs: Iterable[str],
    *,
    excludes: Iterable[str] | None = None,
) -> bool:
    """True if *target* (IP or CIDR) is fully inside at least one allowed network.

    Excludes win: if target IP is inside an exclude network, return False.
    For a CIDR target, every address in the target must be inside some allowed
    network (equivalently: target subnet is a subnet of an allowed network).
    """
    tgt = _parse_network(target)
    if tgt is None:
        return False

    exclude_nets = []
    for e in excludes or []:
        n = _parse_network(e)
        if n is not None:
            exclude_nets.append(n)

    # Single-host exclude check for /32 targets; for larger nets reject if
    # the target network overlaps any exclude (conservative).
    for ex in exclude_nets:
        if tgt.version != ex.version:
            continue
        if tgt.subnet_of(ex) or ex.subnet_of(tgt) or tgt.overlaps(ex):
            # exact host in exclude
            if tgt.num_addresses == 1 and tgt.network_address in ex:
                return False
            if tgt.num_addresses > 1 and tgt.overlaps(ex):
                return False

    allowed_nets = []
    for a in allowed_cidrs:
        n = _parse_network(a)
        if n is not None:
            allowed_nets.append(n)
    if not allowed_nets:
        return False

    for al in allowed_nets:
        if tgt.version != al.version:
            continue
        try:
            if tgt.subnet_of(al):
                return True
        except TypeError:
            continue
        # bare IP as host in network
        if tgt.num_addresses == 1 and tgt.network_address in al:
            return True
    return False


def filter_targets(
    targets: Sequence[str],
    allowed_cidrs: Sequence[str],
    *,
    excludes: Sequence[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split targets into (allowed, rejected)."""
    ok: list[str] = []
    bad: list[str] = []
    for t in targets:
        s = (t or "").strip()
        if not s:
            continue
        if target_allowed(s, allowed_cidrs, excludes=excludes):
            ok.append(s)
        else:
            bad.append(s)
    return ok, bad
