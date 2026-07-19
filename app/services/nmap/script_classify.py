"""Classify NSE / vulners script outputs for device UI.

Stock ``vuln`` category mixes real findings, clean negatives, and script errors
(e.g. probes for apps that are not present). Operators need that distinction.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence

KIND_FINDING = "finding"
KIND_CLEAR = "clear"
KIND_ERROR = "error"
KIND_INFO = "info"

_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)


def _parse_cve_ids(cve_ids_json: str | None, output: str) -> list[str]:
    ids: list[str] = []
    if cve_ids_json:
        try:
            raw = json.loads(cve_ids_json)
            if isinstance(raw, list):
                ids.extend(str(x) for x in raw if x)
        except Exception:
            pass
    for m in _CVE_RE.findall(output or ""):
        up = m.upper()
        if up not in ids:
            ids.append(up)
    return ids


def classify_script_result(
    script_id: str,
    output: str | None = None,
    *,
    cve_ids_json: str | None = None,
) -> dict[str, Any]:
    """Return classification for one script row.

    Keys: kind, label, severity (optional), cve_ids, summary.
    """
    sid = (script_id or "").strip() or "?"
    out = output or ""
    low = out.lower()
    cves = _parse_cve_ids(cve_ids_json, out)

    # --- errors (script engine / probe failed) ---
    if (
        "error: script execution failed" in low
        or "script execution failed" in low
        or low.strip().startswith("error:")
        or "failed to load" in low
    ):
        return {
            "kind": KIND_ERROR,
            "label": "Script error",
            "severity": None,
            "cve_ids": cves,
            "summary": _first_line(out) or "Script execution failed",
            "script_id": sid,
        }

    # --- explicit vulnerable ---
    if "likely vulnerable" in low:
        return {
            "kind": KIND_FINDING,
            "label": "Likely vulnerable",
            "severity": "medium",
            "cve_ids": cves,
            "summary": _first_line(out) or "LIKELY VULNERABLE",
            "script_id": sid,
        }
    if re.search(r"\bvulnerable\b", low) and "not vulnerable" not in low:
        return {
            "kind": KIND_FINDING,
            "label": "Vulnerable",
            "severity": "high",
            "cve_ids": cves,
            "summary": _first_line(out) or "VULNERABLE",
            "script_id": sid,
        }

    # --- vulners / CPE with CVE list (version match — often noisy) ---
    if cves and (
        sid.lower() in ("vulners", "vulscan")
        or "cpe:/" in low
        or "https://vulners.com" in low
    ):
        # vulscan "No findings" without CVEs handled below
        if "no findings" in low and sid.lower() == "vulscan" and not cves:
            pass
        else:
            return {
                "kind": KIND_FINDING,
                "label": "Version / CPE match",
                "severity": "medium",
                "cve_ids": cves,
                "summary": f"{len(cves)} CVE id(s) — verify against real advisories",
                "script_id": sid,
            }

    if cves and "no findings" not in low and "couldn't find" not in low:
        return {
            "kind": KIND_FINDING,
            "label": "CVE mentioned",
            "severity": "low",
            "cve_ids": cves,
            "summary": _first_line(out) or f"{len(cves)} CVE id(s)",
            "script_id": sid,
        }

    # --- clean negatives ---
    clear_phrases = (
        "couldn't find",
        "could not find",
        "no findings",
        "not vulnerable",
        "no vulnerabilities",
        "doesn't seem vulnerable",
        "does not seem vulnerable",
    )
    if any(p in low for p in clear_phrases):
        return {
            "kind": KIND_CLEAR,
            "label": "Clear",
            "severity": None,
            "cve_ids": cves,
            "summary": _first_line(out) or "No findings",
            "script_id": sid,
        }

    # --- informational ---
    return {
        "kind": KIND_INFO,
        "label": "Info",
        "severity": None,
        "cve_ids": cves,
        "summary": _first_line(out) or "(no output)",
        "script_id": sid,
    }


def classify_script_row(row: Any) -> dict[str, Any]:
    """Classify a NmapScriptResult-like object."""
    return classify_script_result(
        getattr(row, "script_id", None) or "",
        getattr(row, "output", None),
        cve_ids_json=getattr(row, "cve_ids_json", None),
    )


def classify_scripts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Classify and sort: findings first, then errors, clear, info."""
    order = {KIND_FINDING: 0, KIND_ERROR: 1, KIND_CLEAR: 2, KIND_INFO: 3}
    out: list[dict[str, Any]] = []
    for r in rows:
        c = classify_script_row(r)
        c["output"] = (getattr(r, "output", None) or "")[:2500]
        c["id"] = getattr(r, "id", None)
        out.append(c)
    out.sort(key=lambda x: (order.get(x["kind"], 9), x.get("script_id") or ""))
    return out


def script_summary_counts(classified: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {
        KIND_FINDING: 0,
        KIND_CLEAR: 0,
        KIND_ERROR: 0,
        KIND_INFO: 0,
        "total": 0,
    }
    for c in classified:
        k = c.get("kind") or KIND_INFO
        if k in counts:
            counts[k] += 1
        counts["total"] += 1
    return counts


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:200]
    return ""
