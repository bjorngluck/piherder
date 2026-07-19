"""CIDR / target allowlist for LAN discovery (refuse out-of-scope scans).

Host excludes are applied via nmap ``--exclude`` so a single excluded IP does
**not** reject a whole configured CIDR target.
"""
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


def target_in_scope(
    target: str,
    allowed_cidrs: Iterable[str],
) -> bool:
    """True if *target* (IP or CIDR) is fully inside at least one allowed network."""
    tgt = _parse_network(target)
    if tgt is None:
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
        if tgt.num_addresses == 1 and tgt.network_address in al:
            return True
    return False


def host_excluded(
    target: str,
    excludes: Iterable[str] | None = None,
) -> bool:
    """True if a single-host target sits inside an exclude network."""
    tgt = _parse_network(target)
    if tgt is None or not excludes:
        return False
    # Only apply host-level reject for single-address targets; multi-host
    # targets use nmap --exclude instead.
    if tgt.num_addresses != 1:
        return False
    for e in excludes:
        ex = _parse_network(e)
        if ex is None or tgt.version != ex.version:
            continue
        if tgt.network_address in ex:
            return True
    return False


def target_allowed(
    target: str,
    allowed_cidrs: Iterable[str],
    *,
    excludes: Iterable[str] | None = None,
) -> bool:
    """True if *target* is in scope and (if a single host) not excluded.

    Multi-host CIDR targets are allowed even when excludes overlap — callers
    must pass excludes to nmap via :func:`nmap_exclude_args`.
    """
    if not target_in_scope(target, allowed_cidrs):
        return False
    if host_excluded(target, excludes):
        return False
    return True


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


def nmap_exclude_args(excludes: Sequence[str] | None) -> list[str]:
    """Build ``--exclude host1,host2`` argv fragments for nmap.

    Accepts IPs and CIDRs (modern nmap). Empty when no valid excludes.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for raw in excludes or []:
        net = _parse_network(raw)
        if net is None:
            continue
        # Prefer bare IP for /32 / /128; keep CIDR otherwise
        if net.num_addresses == 1:
            token = str(net.network_address)
        else:
            token = str(net)
        if token not in seen:
            seen.add(token)
            parts.append(token)
    if not parts:
        return []
    return ["--exclude", ",".join(parts)]


def effective_excludes(
    always: Sequence[str] | None,
    *,
    intensity: str,
    excludes_port_scans: Sequence[str] | None = None,
    excludes_deep: Sequence[str] | None = None,
) -> list[str]:
    """Merge exclude lists by intensity.

    *always* — every intensity
    *excludes_port_scans* — inventory / detailed / deep (discovery still allowed)
    *excludes_deep* — deep only (inventory still allowed)
    """
    intensity = (intensity or "discovery").strip().lower()
    out: list[str] = []
    seen: set[str] = set()

    def _add(items: Sequence[str] | None) -> None:
        for raw in items or []:
            s = (raw or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)

    _add(always)
    if intensity in ("inventory", "detailed", "deep"):
        _add(excludes_port_scans)
    if intensity == "deep":
        _add(excludes_deep)
    return out
