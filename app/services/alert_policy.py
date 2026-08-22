"""v1.3 Stream A — alert taxonomy, per-category policy, channel allowlists.

Storage is AppSetting JSON (no Alembic). Catalog defaults match 1.2 loudness
for existing types; new map/discovery types are documented here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import app_settings as app_cfg

logger = logging.getLogger(__name__)

SEVERITIES = ("info", "warning", "critical")

# Empty / missing allowlist = all categories. Explicit none uses this token.
NONE_SENTINEL = "_none"

# (id, operator label) — Settings rows and Alerts filter.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("host", "Hosts"),
    ("inventory", "Inventory"),
    ("integration", "Kuma services"),
    ("map_infra", "Map infra"),
    ("discovery", "LAN discovery"),
    ("stack", "PiHerder stack"),
    ("cert", "Certificates"),
    ("backup", "Backups"),
    ("updates", "Updates"),
    ("template", "Templates"),
)

CATEGORY_IDS: tuple[str, ...] = tuple(c[0] for c in CATEGORIES)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)


@dataclass(frozen=True)
class TypeSpec:
    id: str
    category: str
    label: str
    default_severity: Optional[str]  # None = emitter hint (stack_health, cert verify)
    enabled: bool = True
    debounce_minutes: int = 0
    realert_hours: int = 0


@dataclass(frozen=True)
class EffectivePolicy:
    type_id: str
    category: str
    label: str
    enabled: bool
    severity: Optional[str]  # None = use emitter hint
    debounce_minutes: int
    realert_hours: int


CATALOG: tuple[TypeSpec, ...] = (
    TypeSpec("host_down", "host", "Host down (Kuma SSH)", "critical", True, 15, 24),
    TypeSpec(
        "stack_container_down",
        "inventory",
        "Monitored container down",
        "critical",
        True,
        15,
        0,
    ),
    TypeSpec(
        "integration_monitor_down",
        "integration",
        "Kuma service down",
        "critical",
        True,
        15,
        0,
    ),
    TypeSpec("map_infra_down", "map_infra", "Gateway / WAN down", "warning", True, 15, 0),
    TypeSpec("nmap_new_device", "discovery", "New LAN device", "warning", True, 60, 0),
    TypeSpec("nmap_device_offline", "discovery", "LAN device offline", "info", True, 0, 0),
    TypeSpec("stack_health", "stack", "PiHerder stack health", None, True, 0, 0),
    TypeSpec("cert_expiring", "cert", "Certificate expiring", "warning", True, 0, 0),
    TypeSpec("cert_renew_failed", "cert", "Certificate renew failed", "critical", True, 0, 0),
    TypeSpec("cert_deploy_failed", "cert", "Certificate deploy failed", "critical", True, 0, 0),
    TypeSpec("cert_verify_failed", "cert", "Certificate verify failed", None, True, 0, 0),
    TypeSpec("backup_failed", "backup", "Host backup failed", "critical", True, 0, 0),
    TypeSpec(
        "herder_backup_failed", "backup", "PiHerder self-backup failed", "critical", True, 0, 0
    ),
    TypeSpec("os_updates", "updates", "OS updates", "warning", True, 0, 0),
    TypeSpec("reboot_pending", "updates", "Reboot pending", "warning", True, 0, 0),
    TypeSpec("container_updates", "updates", "Container image updates", "warning", True, 0, 0),
    TypeSpec("template_drift", "template", "Template config drift", "warning", True, 0, 0),
)

CATALOG_BY_ID: dict[str, TypeSpec] = {t.id: t for t in CATALOG}


def _clamp_int(raw: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def clamp_debounce(raw: Any, default: int = 0) -> int:
    return _clamp_int(raw, lo=0, hi=10080, default=default)


def clamp_realert(raw: Any, default: int = 0) -> int:
    return _clamp_int(raw, lo=0, hi=168, default=default)


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def raw_policy() -> dict[str, Any]:
    try:
        cfg = app_cfg.load_settings()
    except Exception as e:
        logger.debug("alert policy load failed: %s", e)
        return {}
    return _as_dict(cfg.get("alert_type_policy"))


def spec_for(type_id: str) -> Optional[TypeSpec]:
    return CATALOG_BY_ID.get((type_id or "").strip())


def category_of(type_id: str) -> str:
    spec = spec_for(type_id)
    return spec.category if spec else "other"


def label_of(type_id: str) -> str:
    spec = spec_for(type_id)
    if spec:
        return spec.label
    tid = (type_id or "").strip() or "other"
    return tid.replace("_", " ")


def types_in_category(category: str) -> list[str]:
    cat = (category or "").strip()
    return [t.id for t in CATALOG if t.category == cat]


def catalog_type_ids() -> frozenset[str]:
    return frozenset(CATALOG_BY_ID)


def _overlay(base: EffectivePolicy, overlay: Mapping[str, Any] | None) -> EffectivePolicy:
    if not overlay:
        return base
    enabled = base.enabled
    if "enabled" in overlay:
        enabled = bool(overlay.get("enabled"))
    sev = base.severity
    raw_sev = overlay.get("severity")
    if raw_sev is None or str(raw_sev).strip().lower() in ("", "default", "hint"):
        pass
    elif str(raw_sev).strip().lower() in SEVERITIES:
        sev = str(raw_sev).strip().lower()
    debounce = base.debounce_minutes
    if overlay.get("debounce_minutes") is not None and str(overlay.get("debounce_minutes")) != "":
        debounce = clamp_debounce(overlay.get("debounce_minutes"), default=base.debounce_minutes)
    realert = base.realert_hours
    if overlay.get("realert_hours") is not None and str(overlay.get("realert_hours")) != "":
        realert = clamp_realert(overlay.get("realert_hours"), default=base.realert_hours)
    return EffectivePolicy(
        type_id=base.type_id,
        category=base.category,
        label=base.label,
        enabled=enabled,
        severity=sev,
        debounce_minutes=debounce,
        realert_hours=realert,
    )


def effective(type_id: str) -> EffectivePolicy:
    """Merge catalog ← category overlay ← type overlay."""
    tid = (type_id or "").strip() or "other"
    spec = spec_for(tid)
    if spec is None:
        base = EffectivePolicy(
            type_id=tid,
            category="other",
            label=label_of(tid),
            enabled=True,
            severity=None,
            debounce_minutes=0,
            realert_hours=0,
        )
    else:
        base = EffectivePolicy(
            type_id=spec.id,
            category=spec.category,
            label=spec.label,
            enabled=spec.enabled,
            severity=spec.default_severity,
            debounce_minutes=spec.debounce_minutes,
            realert_hours=spec.realert_hours,
        )
    policy = raw_policy()
    cats = policy.get("categories") if isinstance(policy.get("categories"), dict) else {}
    types = policy.get("types") if isinstance(policy.get("types"), dict) else {}
    cat_ov = cats.get(base.category) if isinstance(cats.get(base.category), dict) else None
    type_ov = types.get(tid) if isinstance(types.get(tid), dict) else None
    out = _overlay(base, cat_ov)
    out = _overlay(out, type_ov)
    return out


def resolve_severity(policy: EffectivePolicy, hint: str | None) -> str:
    if policy.severity in SEVERITIES:
        return policy.severity
    h = (hint or "warning").strip().lower()
    return h if h in SEVERITIES else "warning"


def inventory_down_alerts_enabled() -> bool:
    """stack_container_down enabled, with 1.2 key as fallback when unset in policy."""
    policy = raw_policy()
    types = policy.get("types") if isinstance(policy.get("types"), dict) else {}
    cats = policy.get("categories") if isinstance(policy.get("categories"), dict) else {}
    if "stack_container_down" in types or "inventory" in cats:
        return effective("stack_container_down").enabled
    try:
        v = app_cfg.load_settings().get("stack_inventory_down_alerts")
    except Exception:
        v = True
    if v is None or v == "":
        return True
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def set_type_override(type_id: str, **fields: Any) -> dict[str, Any]:
    """Merge a per-type overlay (e.g. Network inventory-down checkbox)."""
    tid = (type_id or "").strip()
    policy = dict(raw_policy())
    types = dict(policy.get("types") or {}) if isinstance(policy.get("types"), dict) else {}
    cur = dict(types.get(tid) or {}) if isinstance(types.get(tid), dict) else {}
    if "enabled" in fields and fields["enabled"] is not None:
        cur["enabled"] = bool(fields["enabled"])
    if "severity" in fields:
        sev = fields["severity"]
        if sev in SEVERITIES:
            cur["severity"] = sev
        elif sev in (None, "", "default"):
            cur.pop("severity", None)
    types[tid] = cur
    policy["types"] = types
    extra: dict[str, Any] = {"alert_type_policy": policy}
    if tid == "stack_container_down" and "enabled" in fields and fields["enabled"] is not None:
        extra["stack_inventory_down_alerts"] = bool(fields["enabled"])
    return app_cfg.save_settings(extra)


def policy_from_form(form: Mapping[str, Any]) -> dict[str, Any]:
    """Build categories overlay from Settings form fields. Preserves type overlays."""
    categories: dict[str, Any] = {}
    for cid, _label in CATEGORIES:
        enabled = str(form.get(f"cat_enabled_{cid}") or "") in ("1", "on", "true", "yes")
        sev = str(form.get(f"cat_severity_{cid}") or "default").strip().lower()
        spec_debounce = 0
        spec_realert = 0
        for t in CATALOG:
            if t.category == cid:
                spec_debounce = max(spec_debounce, t.debounce_minutes)
                spec_realert = max(spec_realert, t.realert_hours)
        row: dict[str, Any] = {
            "enabled": enabled,
            "debounce_minutes": clamp_debounce(
                form.get(f"cat_debounce_{cid}"), default=spec_debounce
            ),
            "realert_hours": clamp_realert(
                form.get(f"cat_realert_{cid}"), default=spec_realert
            ),
        }
        if sev in SEVERITIES:
            row["severity"] = sev
        categories[cid] = row
    prev = raw_policy()
    types = prev.get("types") if isinstance(prev.get("types"), dict) else {}
    return {"categories": categories, "types": types}


def policy_audit_summary(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> str:
    b_cats = (before or {}).get("categories") if isinstance((before or {}).get("categories"), dict) else {}
    a_cats = (after or {}).get("categories") if isinstance((after or {}).get("categories"), dict) else {}
    bits: list[str] = []
    for cid, label in CATEGORIES:
        b = b_cats.get(cid) if isinstance(b_cats.get(cid), dict) else {}
        a = a_cats.get(cid) if isinstance(a_cats.get(cid), dict) else {}
        b_on = True if not b else bool(b.get("enabled", True))
        a_on = True if not a else bool(a.get("enabled", True))
        if b_on != a_on:
            bits.append(f"{label} {'on' if a_on else 'muted'}")
        b_sev = (b.get("severity") or "default") if b else "default"
        a_sev = (a.get("severity") or "default") if a else "default"
        if b_sev != a_sev:
            bits.append(f"{label} sev {a_sev}")
    return "; ".join(bits[:8]) or "policy saved"


def parse_allowlist(raw: Any) -> Optional[list[str]]:
    """None = all categories allowed. Empty list = none allowed."""
    if raw is None or raw == "":
        return None
    data: Any = raw
    if isinstance(raw, str):
        t = raw.strip()
        if not t or t in ("[]", "*"):
            return None
        try:
            data = json.loads(t)
        except Exception:
            data = [x.strip() for x in t.split(",") if x.strip()]
    if isinstance(data, list):
        if not data:
            return None
        if NONE_SENTINEL in data and len([x for x in data if x != NONE_SENTINEL]) == 0:
            return []
        cats = [str(c).strip() for c in data if str(c).strip() in CATEGORY_IDS]
        return cats if cats else None
    return None


def allowlist_from_form(form: Mapping[str, Any], *, prefix: str, submitted_key: str) -> Any:
    """Return value to store: [] all, ['_none'] none, or category ids.

    Missing submitted_key → None (do not change).
    """
    if str(form.get(submitted_key) or "") not in ("1", "on", "true", "yes"):
        return None
    checked = [
        cid
        for cid in CATEGORY_IDS
        if str(form.get(f"{prefix}{cid}") or "") in ("1", "on", "true", "yes")
    ]
    if len(checked) == len(CATEGORY_IDS):
        return []
    if not checked:
        return [NONE_SENTINEL]
    return checked


def category_allowed(category: str | None, allowlist: Optional[Sequence[str]]) -> bool:
    if allowlist is None:
        return True
    if len(allowlist) == 0:
        return False
    cat = (category or "other").strip() or "other"
    return cat in allowlist


def ui_state() -> dict[str, Any]:
    """Template context for Settings → Alerts policy card."""
    rows = []
    for cid, label in CATEGORIES:
        # Display: merge a synthetic type in this category so category overlay shows
        sample = next((t for t in CATALOG if t.category == cid), None)
        pol = effective(sample.id) if sample else EffectivePolicy(
            type_id="",
            category=cid,
            label=label,
            enabled=True,
            severity=None,
            debounce_minutes=0,
            realert_hours=0,
        )
        policy = raw_policy()
        cats = policy.get("categories") if isinstance(policy.get("categories"), dict) else {}
        stored = cats.get(cid) if isinstance(cats.get(cid), dict) else {}
        sev_ui = "default"
        if stored.get("severity") in SEVERITIES:
            sev_ui = stored["severity"]
        debounce = stored.get("debounce_minutes")
        if debounce is None or str(debounce) == "":
            debounce = max((t.debounce_minutes for t in CATALOG if t.category == cid), default=0)
        realert = stored.get("realert_hours")
        if realert is None or str(realert) == "":
            realert = max((t.realert_hours for t in CATALOG if t.category == cid), default=0)
        enabled = bool(stored["enabled"]) if "enabled" in stored else pol.enabled
        rows.append(
            {
                "id": cid,
                "label": label,
                "enabled": enabled,
                "severity_ui": sev_ui,
                "debounce_minutes": clamp_debounce(debounce),
                "realert_hours": clamp_realert(realert),
                "types": [
                    {"id": t.id, "label": t.label, "default_severity": t.default_severity or "hint"}
                    for t in CATALOG
                    if t.category == cid
                ],
            }
        )
    return {"categories": rows, "severities": list(SEVERITIES)}


def checked_categories(allowlist: Optional[Sequence[str]]) -> set[str]:
    """Which category checkboxes to tick. None/all → all ids."""
    if allowlist is None:
        return set(CATEGORY_IDS)
    return {c for c in allowlist if c in CATEGORY_IDS}
