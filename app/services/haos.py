"""Home Assistant OS (HAOS) path-1 helpers: detect, facts, update check/apply via ``ha`` CLI.

v0.9: SSH-native only (no Core REST / LLAT). Used by os_patching as the apt
equivalent when ``os_type=haos`` or probes detect HAOS.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Components we check/update in 0.9 (not add-ons).
HA_COMPONENTS = ("supervisor", "core", "os")

# Preferred apply order: supervisor first (often unblocks core), then core, then OS.
APPLY_ORDER = ("supervisor", "core", "os")

UPDATE_CMDS = {
    "supervisor": "ha supervisor update",
    "core": "ha core update",
    "os": "ha os update",
}

INFO_CMDS = {
    "core": "ha core info",
    "os": "ha os info",
    "supervisor": "ha supervisor info",
}


def is_haos_server(server: Any) -> bool:
    return "haos" in (getattr(server, "os_type", None) or "").lower()


def parse_os_release(raw: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        data[k.strip().lower()] = v.strip().strip('"').strip("'")
    return data


def os_release_looks_like_haos(os_rel: dict[str, str]) -> bool:
    os_id = (os_rel.get("id") or "").lower()
    pretty = (os_rel.get("pretty_name") or os_rel.get("name") or "").lower()
    if os_id in {"hassos", "haos"}:
        return True
    if "hassos" in pretty or "home assistant os" in pretty:
        return True
    if "home assistant" in pretty and "os" in pretty:
        return True
    return False


def _normalize_ha_map(data: dict[str, Any]) -> dict[str, Any]:
    """Lower-case keys; unwrap Supervisor CLI envelope ``{result, data}``."""
    if not isinstance(data, dict):
        return {}
    # HA CLI --raw-json: {"result":"ok","data":{...actual fields...}}
    if "data" in data and isinstance(data.get("data"), dict):
        result = data.get("result")
        # Only unwrap when it looks like the Supervisor API envelope
        if result in ("ok", "error", None) or (
            "version" not in data and "disk_total" not in data and "disk_free" not in data
        ):
            inner = data["data"]
            # Prefer inner if it holds the useful fields
            if isinstance(inner, dict) and (
                any(k in inner for k in ("version", "disk_total", "disk_free", "version_latest", "hostname", "id", "children"))
                or result in ("ok", "error")
            ):
                data = inner
    return {str(k).lower().replace("-", "_"): v for k, v in data.items()}


def parse_ha_info_blob(text: str) -> dict[str, Any]:
    """Parse ``ha * info`` output (JSON or key: value lines).

    Live HA CLI ``--raw-json`` returns ``{"result":"ok","data":{...}}`` — always
    unwrap ``data`` before reading version / disk fields.
    """
    raw = (text or "").strip()
    if not raw:
        return {}
    # JSON object (raw-json / -o json) — may be full response body
    if raw.startswith("{") or raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return _normalize_ha_map(data)
        except Exception:
            pass
    # YAML-ish key: value (HA CLI default pretty mode prints *data* only)
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        # Keep indentation awareness lightly: only top-level keys (no leading space)
        if not line or line.startswith("#"):
            continue
        if line[0] in (" ", "\t", "-"):
            continue  # nested YAML list/map — skip (addons, boot_slots, …)
        line = line.strip()
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip().lower().replace("-", "_")
        if not key or " " in key:
            continue
        val = v.strip().strip('"').strip("'")
        if val == "" or val.lower() in ("null", "none", "~"):
            # key present but empty/complex value follows on next lines
            if key not in out:
                out[key] = None
            continue
        low = val.lower()
        if low in ("true", "yes", "on"):
            out[key] = True
        elif low in ("false", "no", "off"):
            out[key] = False
        elif re.fullmatch(r"-?\d+", val):
            try:
                out[key] = int(val)
            except Exception:
                out[key] = val
        elif re.fullmatch(r"-?\d+\.\d+", val):
            try:
                out[key] = float(val)
            except Exception:
                out[key] = val
        else:
            out[key] = val
    return out


def _truthy_update(data: dict[str, Any]) -> bool:
    for key in (
        "update_available",
        "update-available",
        "need_update",
        "need-update",
    ):
        if key in data:
            v = data[key]
            if isinstance(v, bool):
                return v
            if str(v).lower() in ("true", "yes", "1", "on"):
                return True
            if str(v).lower() in ("false", "no", "0", "off"):
                return False
    ver = str(data.get("version") or data.get("version_current") or "").strip()
    latest = str(
        data.get("version_latest")
        or data.get("version-latest")
        or data.get("newest_version")
        or ""
    ).strip()
    if ver and latest and ver != latest and latest.lower() not in ("", "null", "none"):
        return True
    return False


def component_fact_from_info(name: str, data: dict[str, Any]) -> dict[str, Any]:
    version = data.get("version") or data.get("version_current")
    latest = (
        data.get("version_latest")
        or data.get("version-latest")
        or data.get("newest_version")
    )
    if version is not None:
        version = str(version)
    if latest is not None:
        latest = str(latest)
    update_available = _truthy_update(data)
    return {
        "name": name,
        "version": version,
        "version_latest": latest,
        "update_available": bool(update_available),
        "machine": str(data.get("machine") or "") or None,
        "channel": str(data.get("channel") or "") or None,
        "raw_keys": sorted(data.keys())[:40],
    }


def summarize_component_sample(fact: dict[str, Any]) -> str:
    name = fact.get("name") or "?"
    ver = fact.get("version") or "?"
    latest = fact.get("version_latest") or ver
    if fact.get("update_available"):
        return f"{name} {ver} → {latest}"
    return f"{name} {ver} (current)"


def build_ha_summary(
    components: dict[str, dict[str, Any]],
    *,
    marked_by: str = "detected",
    detection_method: str = "ssh",
    ha_present: bool = True,
    os_release_name: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    samples = [
        summarize_component_sample(components[c])
        for c in HA_COMPONENTS
        if c in components and components[c].get("update_available")
    ]
    count = sum(1 for c in HA_COMPONENTS if components.get(c, {}).get("update_available"))
    return {
        "backend": "ha_cli",
        "marked_by": marked_by,
        "detection_method": detection_method,
        "ha_cli": ha_present,
        "os_release_name": os_release_name,
        "components": components,
        "packages_sample": samples[:15],
        "actionable_count": count,
        "phased_count": 0,
        "total_upgradable": count,
        "error": error,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


def parse_ha_info_for_component(name: str, text: str) -> dict[str, Any]:
    return component_fact_from_info(name, parse_ha_info_blob(text))


def _run(client, cmd: str, timeout: int = 45) -> tuple[int, str, str]:
    from .ssh import run_command

    return run_command(client, cmd, timeout=timeout)


def _ha_info_command(component: str) -> str:
    base = INFO_CMDS[component]
    # Prefer JSON when available; fall back to default text.
    return (
        f"{base} --raw-json 2>/dev/null || {base} -o json 2>/dev/null || {base} 2>/dev/null || true"
    )


def probe_haos_identity(client) -> dict[str, Any]:
    """Read-only identity probe. No package install.

    Returns dict:
      is_haos, ha_cli, os_release, os_release_name, signals[]
    """
    signals: list[str] = []
    status, out, _ = _run(client, "cat /etc/os-release 2>/dev/null || true", timeout=15)
    os_rel = parse_os_release(out or "")
    os_name = os_rel.get("pretty_name") or os_rel.get("name") or os_rel.get("id") or ""
    from_os = os_release_looks_like_haos(os_rel)
    if from_os:
        signals.append("os-release")

    st_ha, ha_path, _ = _run(
        client, "command -v ha 2>/dev/null || which ha 2>/dev/null || true", timeout=10
    )
    ha_cli = bool((ha_path or "").strip()) and (
        st_ha == 0 or (ha_path or "").strip().startswith("/")
    )
    if ha_cli:
        signals.append("ha-cli")

    # Strong confirmation: core info works
    core_ok = False
    if ha_cli:
        st, cout, _ = _run(client, _ha_info_command("core"), timeout=40)
        blob = parse_ha_info_blob(cout or "")
        if blob.get("version") or blob.get("version_latest") is not None:
            core_ok = True
            signals.append("ha-core-info")
        elif st == 0 and (cout or "").strip():
            # Partial parse still counts if output non-empty and looks HA-ish
            low = (cout or "").lower()
            if "version" in low or "home assistant" in low:
                core_ok = True
                signals.append("ha-core-info")

    is_haos = from_os or (ha_cli and core_ok)
    return {
        "is_haos": bool(is_haos),
        "ha_cli": bool(ha_cli),
        "os_release": os_rel,
        "os_release_name": os_name,
        "signals": signals,
        "core_info_ok": core_ok,
    }


def collect_ha_component_facts(client) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """Fetch core/os/supervisor info. Returns (components, error)."""
    components: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for name in HA_COMPONENTS:
        try:
            st, out, err = _run(client, _ha_info_command(name), timeout=45)
            text = (out or err or "").strip()
            if not text:
                errors.append(f"{name}: empty info")
                continue
            fact = parse_ha_info_for_component(name, text)
            if not fact.get("version") and not fact.get("version_latest"):
                # Still record empty-ish so UI can show attempt
                if st != 0:
                    errors.append(f"{name}: rc={st}")
                    continue
            components[name] = fact
        except Exception as e:
            errors.append(f"{name}: {e}")
    err = "; ".join(errors)[:400] if errors else None
    if not components and not err:
        err = "no ha component info returned"
    return components, err


def _host_info_command() -> str:
    return (
        "ha host info --raw-json 2>/dev/null || ha host info -o json 2>/dev/null "
        "|| ha host info 2>/dev/null || true"
    )


def _disks_usage_command() -> str:
    return (
        "ha host disks usage --raw-json 2>/dev/null || ha host disks usage -o json 2>/dev/null "
        "|| ha host disks usage 2>/dev/null || true"
    )


def parse_host_disk_facts(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize disk_* fields from ``ha host info`` (usually GB floats)."""
    def _num(*keys: str) -> Optional[float]:
        for k in keys:
            if k in data and data[k] is not None and str(data[k]).strip() != "":
                try:
                    return float(data[k])
                except (TypeError, ValueError):
                    # already human string
                    return None
        return None

    free = _num("disk_free", "disk-free", "diskfree")
    total = _num("disk_total", "disk-total", "disktotal")
    used = _num("disk_used", "disk-used", "diskused")
    life = data.get("disk_life_time") or data.get("disk-life-time")
    pcent = None
    if total and total > 0 and used is not None:
        pcent = round(100.0 * float(used) / float(total), 1)
    elif total and total > 0 and free is not None:
        pcent = round(100.0 * (1.0 - float(free) / float(total)), 1)

    def _gb(v: Optional[float]) -> Optional[str]:
        if v is None:
            return None
        if v >= 100:
            return f"{v:.0f}G"
        if v >= 10:
            return f"{v:.1f}G"
        return f"{v:.2f}G"

    return {
        "disk_free_gb": free,
        "disk_total_gb": total,
        "disk_used_gb": used,
        "disk_life_time": life,
        "disk_pcent": pcent,
        "disk_free_h": _gb(free),
        "disk_total_h": _gb(total),
        "disk_used_h": _gb(used),
        "chassis": data.get("chassis") or data.get("Chassis"),
        "hostname": data.get("hostname") or data.get("Hostname"),
        "operating_system": data.get("operating_system")
        or data.get("operating-system")
        or data.get("operating_system"),
        "kernel": data.get("kernel") or data.get("Kernel"),
        "deployment": data.get("deployment"),
        "virtualization": data.get("virtualization"),
    }


def _bytes_h(n: Any) -> str:
    try:
        b = float(n)
    except (TypeError, ValueError):
        return "?"
    units = ["B", "K", "M", "G", "T"]
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024.0
        i += 1
    if i == 0:
        return f"{int(b)}{units[i]}"
    if b >= 100:
        return f"{b:.0f}{units[i]}"
    if b >= 10:
        return f"{b:.1f}{units[i]}"
    return f"{b:.2f}{units[i]}"


def disks_usage_to_drives(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """Map ``ha host disks usage`` into diagnostics-style drive rows."""
    if not usage:
        return []
    rows: list[dict[str, Any]] = []
    total = usage.get("total_bytes")
    used = usage.get("used_bytes")
    label = usage.get("label") or usage.get("id") or "root"
    if total is not None:
        try:
            free = float(total) - float(used or 0)
            pcent = round(100.0 * float(used or 0) / float(total), 1) if float(total) else 0
            rows.append(
                {
                    "filesystem": "ha-disk",
                    "size": _bytes_h(total),
                    "used": _bytes_h(used or 0),
                    "avail": _bytes_h(free),
                    "pcent": f"{pcent}%",
                    "target": f"/{label}" if str(label).lower() != "root" else "/",
                }
            )
        except (TypeError, ValueError):
            pass
    children = usage.get("children") or []
    if isinstance(children, list):
        for ch in children:
            if not isinstance(ch, dict):
                continue
            # normalize keys
            ch_n = {str(k).lower().replace("-", "_"): v for k, v in ch.items()}
            ub = ch_n.get("used_bytes")
            lab = ch_n.get("label") or ch_n.get("id") or "?"
            rows.append(
                {
                    "filesystem": "ha-usage",
                    "size": _bytes_h(ub) if ub is not None else "?",
                    "used": _bytes_h(ub) if ub is not None else "?",
                    "avail": "—",
                    "pcent": "",
                    "target": str(lab),
                }
            )
    return rows


def collect_host_facts(client) -> tuple[dict[str, Any], Optional[str]]:
    """``ha host info`` (+ optional disks usage). Returns (facts, error)."""
    err: Optional[str] = None
    facts: dict[str, Any] = {}
    try:
        st, out, er = _run(client, _host_info_command(), timeout=40)
        text = (out or er or "").strip()
        if text:
            blob = parse_ha_info_blob(text)
            facts = parse_host_disk_facts(blob)
            facts["raw_keys"] = sorted(blob.keys())[:40]
        else:
            err = f"ha host info empty (rc={st})"
    except Exception as e:
        err = str(e)[:200]

    # Optional richer usage blob (best-effort; schema varies by Supervisor version)
    try:
        st, out, er = _run(client, _disks_usage_command(), timeout=40)
        text = (out or er or "").strip()
        if text:
            usage = parse_ha_info_blob(text)
            if usage:
                facts["disks_usage"] = usage
                facts["usage_drives"] = disks_usage_to_drives(usage)
    except Exception:
        pass
    return facts, err


def gather_system_panel(client) -> dict[str, Any]:
    """Bundle for System Info modal: components + host disk facts."""
    components, cerr = collect_ha_component_facts(client)
    host, herr = collect_host_facts(client)
    errors = [e for e in (cerr, herr) if e]
    return {
        "components": components,
        "host": host,
        "error": "; ".join(errors)[:400] if errors else None,
    }


def check_haos_updates(server: Any, client=None) -> dict[str, Any]:
    """Check-only HAOS updates via ``ha * info``. Optionally auto-uses open client."""
    from .ssh import get_ssh_client

    own_client = client is None
    if own_client:
        client = get_ssh_client(server)
    error: Optional[str] = None
    identity: dict[str, Any] = {}
    components: dict[str, dict[str, Any]] = {}
    try:
        identity = probe_haos_identity(client)
        if not identity.get("ha_cli"):
            error = "ha CLI not found — enable Terminal & SSH add-on on HAOS"
        else:
            components, ferr = collect_ha_component_facts(client)
            if ferr and not components:
                error = ferr
            elif ferr:
                error = ferr  # partial
    except Exception as e:
        error = str(e)[:400]
    finally:
        if own_client and client is not None:
            try:
                client.close()
            except Exception:
                pass

    count = sum(
        1 for c in HA_COMPONENTS if components.get(c, {}).get("update_available")
    )
    samples = [
        summarize_component_sample(components[c])
        for c in HA_COMPONENTS
        if components.get(c, {}).get("update_available")
    ]
    ha_block = {
        "marked_by": "detected" if identity.get("is_haos") else "manual",
        "detection_method": "ssh",
        "signals": identity.get("signals") or [],
        "os_release_name": identity.get("os_release_name"),
        "ha_cli": bool(identity.get("ha_cli")),
        "components": components,
    }
    return {
        "server": getattr(server, "hostname", None),
        "supported": True,
        "backend": "ha_cli",
        "updates_count": count,
        "actionable_count": count,
        "phased_count": 0,
        "total_upgradable": count,
        "reboot_pending": False,
        "packages_sample": samples[:15],
        "phased_sample": [],
        "error": error,
        "detected_os_type": "haos" if identity.get("is_haos") or is_haos_server(server) else None,
        "auto_mark_haos": bool(identity.get("is_haos")),
        "ha": ha_block,
        "identity": {
            "is_haos": identity.get("is_haos"),
            "signals": identity.get("signals"),
            "os_release_name": identity.get("os_release_name"),
        },
    }


def should_use_haos_path(server: Any, identity: Optional[dict[str, Any]] = None) -> bool:
    if is_haos_server(server):
        return True
    if identity and identity.get("is_haos"):
        return True
    return False


def run_haos_update(
    server: Any,
    *,
    selected_steps: list[str] | None = None,
    hostname: str | None = None,
    stream_log=None,
    stream_cmd=None,
) -> dict[str, Any]:
    """Apply HA updates via CLI (apt-upgrade equivalent).

    Step mapping from OS patch UI:
      - update: refresh component facts (read-only)
      - upgrade / full-upgrade: run ha updates for available components
      - autoremove: no-op (skipped)
    """
    from .ssh import get_ssh_client

    steps = list(selected_steps or ["update", "upgrade"])
    host = hostname or getattr(server, "hostname", "") or "haos"
    do_refresh = "update" in steps
    do_apply = "upgrade" in steps or "full-upgrade" in steps
    # autoremove ignored

    def log(msg: str) -> None:
        if stream_log:
            stream_log(host, msg)
        else:
            logger.info("[haos %s] %s", host, msg)

    client = get_ssh_client(server)
    results: list[dict[str, Any]] = []
    needs_reboot = False
    try:
        identity = probe_haos_identity(client)
        if not identity.get("ha_cli"):
            results.append(
                {
                    "step": "ha-cli",
                    "rc": 1,
                    "error": "ha CLI not found (SSH add-on / PATH)",
                }
            )
            log("[haos] ERROR: ha CLI not found — install/enable Terminal & SSH add-on")
            res = {
                "server": host,
                "backend": "ha_cli",
                "steps": steps,
                "results": results,
                "needs_reboot": False,
                "summary": "Failed: ha CLI not found",
                "finished_at": datetime.utcnow().isoformat() + "Z",
            }
            return res

        components: dict[str, dict[str, Any]] = {}
        if do_refresh or do_apply:
            log("[update] refreshing ha core/os/supervisor info…")
            components, ferr = collect_ha_component_facts(client)
            for name in HA_COMPONENTS:
                fact = components.get(name)
                if fact:
                    log(
                        f"[update] {name}: {fact.get('version')} "
                        f"latest={fact.get('version_latest')} "
                        f"update={fact.get('update_available')}"
                    )
            if ferr:
                log(f"[update] notes: {ferr}")
            results.append({"step": "update", "rc": 0 if components else 1})

        if do_apply:
            to_update = [
                c
                for c in APPLY_ORDER
                if components.get(c, {}).get("update_available")
            ]
            if not to_update:
                log("[upgrade] no HA components report update_available")
                results.append({"step": "upgrade", "rc": 0, "skipped": True})
            else:
                log(f"[upgrade] applying in order: {', '.join(to_update)}")
                for comp in to_update:
                    cmd = UPDATE_CMDS[comp]
                    log(f"[{comp}] $ {cmd}")
                    try:
                        if stream_cmd:
                            rc = stream_cmd(client, host, comp, cmd, timeout=1800)
                        else:
                            st, out, err = _run(client, cmd, timeout=1800)
                            if out:
                                for line in (out or "").splitlines()[:40]:
                                    log(f"[{comp}] {line}")
                            if err:
                                for line in (err or "").splitlines()[:20]:
                                    log(f"[{comp}] {line}")
                            rc = st
                        results.append({"step": comp, "rc": rc})
                        log(f"[{comp}] exit={rc}")
                        if comp == "os" and rc == 0:
                            needs_reboot = True
                        if rc != 0:
                            # Stop further applies on failure
                            log(f"[upgrade] stopping after {comp} failure")
                            break
                    except Exception as e:
                        results.append({"step": comp, "error": str(e)})
                        log(f"[{comp}] ERROR: {e}")
                        break
        elif "autoremove" in steps and not do_apply and not do_refresh:
            results.append({"step": "autoremove", "rc": 0, "skipped": True})

        if "autoremove" in steps:
            log("[autoremove] skipped on HAOS (not applicable)")
            results.append({"step": "autoremove", "rc": 0, "skipped": True})

    finally:
        try:
            client.close()
        except Exception:
            pass

    # Build summary like apt path
    parts = []
    for r in results:
        step = r.get("step") or "?"
        if r.get("error"):
            parts.append(f"{step} ✗")
        elif r.get("skipped"):
            parts.append(f"{step} skip")
        elif int(r.get("rc", 1)) != 0:
            parts.append(f"{step} rc={r.get('rc')}")
        else:
            parts.append(f"{step} ✓")
    summary = " · ".join(parts) if parts else "no steps"
    if needs_reboot:
        summary += " · OS update may require reboot"

    return {
        "server": host,
        "backend": "ha_cli",
        "steps": steps,
        "results": results,
        "needs_reboot": needs_reboot,
        "phased_deferred": False,
        "summary": summary,
        "finished_at": datetime.utcnow().isoformat() + "Z",
    }
