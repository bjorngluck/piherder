"""Managed TLS certificates — parse, store, deploy, renew orchestration.

Sources:
  - npm: pull zip from Nginx Proxy Manager
  - upload: operator-supplied PEM fullchain + privkey (cleartext form → encrypted at rest)
"""
from __future__ import annotations

import hashlib
import json
import logging
import shlex
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from sqlmodel import Session, select

from ..models import CertificateTarget, Integration, ManagedCertificate, Server
from ..security.encryption import decrypt_str, encrypt_str
from . import ssh as ssh_svc
from .integrations import npm as npm_mod
from .integrations import registry as reg

logger = logging.getLogger(__name__)

DEFAULT_RENEW_DAYS = 21
RENEW_POLL_INTERVAL_SEC = 180  # 3 minutes
RENEW_POLL_ATTEMPTS = 5

# All layouts deploy understands (includes legacy compounds)
LAYOUTS = frozenset(
    {
        "pair",
        "combined",
        "pfx",
        "pair_and_combined",
        "pair_and_pfx",
        "pair_combined_pfx",
    }
)
# New deploy-target wizard offers only one type per target
LAYOUTS_NEW_UI = frozenset({"pair", "combined", "pfx"})

# Fleet map write strategies
WRITE_MODES = frozenset({"direct", "stage_sudo"})
WRITE_MODE_HELP = {
    "direct": (
        "Write PEMs with SFTP straight into the target directory as the SSH user. "
        "Use when that user owns the path (e.g. under ~/ or a dedicated certs dir)."
    ),
    "stage_sudo": (
        "Write into a private stage dir under the SSH user’s home, then "
        "`sudo install` (mkdir/chmod/chown/mv) into the final path. "
        "Use with least-priv accounts that may not write /etc or root-owned Docker paths."
    ),
}


# --- Path helpers (sudoers snippet + deploy must share the same expansion) ---


def default_home_for_user(ssh_user: str) -> str:
    """Best-effort home when live `$HOME` is unknown (UI preview / offline snippet)."""
    user = (ssh_user or "piherder").strip() or "piherder"
    if user == "root":
        return "/root"
    return f"/home/{user}"


def resolve_ssh_home(*, ssh_user: str = "piherder", home_dir: str | None = None) -> str:
    """Absolute home for stage paths. Prefer live home_dir from SSH when provided."""
    home = (home_dir or "").strip().rstrip("/")
    if home and home != ".":
        return home
    return default_home_for_user(ssh_user).rstrip("/")


def expand_remote_dir(remote_dir: str, home: str) -> str:
    """Expand leading ``~`` against *home* (same rules as deploy over SSH)."""
    home = (home or "").rstrip("/") or "."
    rd = (remote_dir or "~/certs").strip() or "~/certs"
    if rd.startswith("~/"):
        return f"{home}/{rd[2:]}".rstrip("/") or home
    if rd == "~":
        return home
    return rd.rstrip("/") or rd


def cert_stage_base(home: str) -> str:
    """``{home}/.piherder/cert-stage`` — parent of per-map stage dirs."""
    home = (home or "").rstrip("/") or "."
    return f"{home}/.piherder/cert-stage"


def cert_stage_dir(home: str, target_id: int | str) -> str:
    return f"{cert_stage_base(home)}/{target_id}"


def cert_stage_glob(home: str) -> str:
    """Sudoers path glob for install source files (any map id under stage base)."""
    return f"{cert_stage_base(home)}/*"

# Human descriptions for UI
LAYOUT_HELP = {
    "pair": "Two files: fullchain + private key (Nginx, Caddy, most Docker apps)",
    "combined": "One file: private key then fullchain (some HAProxy / “snakeoil” apps)",
    "pfx": "One PKCS#12 .pfx file (Windows / UniFi-style). Built on the host from vault PEMs via openssl.",
    "pair_and_combined": "Legacy: both pair and combined PEM in the same directory",
    "pair_and_pfx": "Legacy: PEM pair plus PKCS#12 .pfx",
    "pair_combined_pfx": "Legacy: pair + combined + PFX in the same directory",
}


def layout_installs_pair(layout: str) -> bool:
    lay = (layout or "pair").strip()
    return lay in ("pair", "pair_and_combined", "pair_and_pfx", "pair_combined_pfx")


def layout_installs_combined(layout: str) -> bool:
    lay = (layout or "pair").strip()
    return lay in ("combined", "pair_and_combined", "pair_combined_pfx")


def layout_installs_pfx(layout: str) -> bool:
    lay = (layout or "pair").strip()
    return lay in ("pfx", "pair_and_pfx", "pair_combined_pfx")


def layout_needs_pem_for_pfx(layout: str) -> bool:
    """True when deploy must stage PEMs so openssl can build a .pfx."""
    return layout_installs_pfx(layout)


def build_post_deploy_command(
    kind: str,
    *,
    compose_file: str = "",
    compose_action: str = "restart",
    systemctl_unit: str = "",
    custom: str = "",
) -> str:
    """Build the exact SSH command run after files are written."""
    k = (kind or "none").strip().lower()
    if k in ("", "none"):
        return ""
    if k == "compose":
        path = (compose_file or "").strip() or "docker-compose.yml"
        act = (compose_action or "restart").strip().lower().replace("_", " ")
        if act in ("up -d", "up-d", "up"):
            return f"docker compose -f {path} up -d"
        return f"docker compose -f {path} restart"
    if k in ("systemctl", "systemd"):
        unit = (systemctl_unit or "").strip()
        if not unit:
            return ""
        # Allow bare unit or full command paste
        if unit.startswith("sudo ") or unit.startswith("systemctl "):
            return unit
        return f"sudo systemctl restart {unit}"
    if k == "custom":
        return (custom or "").strip()
    return (custom or "").strip()


def parse_restart_recipe(post_deploy_command: str) -> dict[str, str]:
    """Best-effort reverse of build_post_deploy_command for edit forms."""
    c = (post_deploy_command or "").strip()
    empty = {
        "kind": "none",
        "compose_file": "",
        "compose_action": "restart",
        "systemctl_unit": "",
        "custom": "",
    }
    if not c:
        return empty
    # docker compose -f PATH restart|up -d
    import re

    m = re.match(
        r"^docker\s+compose\s+-f\s+(\S+)\s+(restart|up\s+-d|up-d)\s*$",
        c,
        re.I,
    )
    if m:
        act = m.group(2).lower().replace("up-d", "up -d")
        return {
            "kind": "compose",
            "compose_file": m.group(1),
            "compose_action": "up -d" if "up" in act else "restart",
            "systemctl_unit": "",
            "custom": "",
        }
    m = re.match(
        r"^(?:sudo\s+)?systemctl\s+restart\s+(\S+)\s*$",
        c,
        re.I,
    )
    if m:
        return {
            "kind": "systemctl",
            "compose_file": "",
            "compose_action": "restart",
            "systemctl_unit": m.group(1),
            "custom": "",
        }
    return {
        "kind": "custom",
        "compose_file": "",
        "compose_action": "restart",
        "systemctl_unit": "",
        "custom": c,
    }


def layout_help_for_ui() -> dict[str, str]:
    """Help text for the three types shown in the new wizard."""
    return {k: LAYOUT_HELP[k] for k in ("pair", "combined", "pfx")}

# Service-map presets (source of truth for UI + tests). Paths are examples — edit per host.
# Keys are stable IDs used in the map form select.
MAP_PRESETS: dict[str, dict[str, Any]] = {
    "npm_pair": {
        "id": "npm_pair",
        "group": "Reverse proxy",
        "title": "Nginx Proxy Manager (custom SSL)",
        "label": "NPM custom SSL",
        "remote_dir": "/opt/stacks/npm/data/custom_ssl",
        "layout": "pair",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "root",
        "group": "root",
        "post": "docker compose -f /opt/stacks/npm/docker-compose.yml restart",
        "help": (
            "PEM pair for NPM custom certificates. Adjust path if your NPM data volume "
            "lives elsewhere (often …/data/custom_ssl or a host bind under /opt/stacks/npm)."
        ),
        "docs_anchor": "service-maps",
    },
    "caddy_pair": {
        "id": "caddy_pair",
        "group": "Reverse proxy",
        "title": "Caddy / generic Nginx PEM pair",
        "label": "Reverse proxy TLS",
        "remote_dir": "/opt/stacks/proxy/certs",
        "layout": "pair",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "root",
        "group": "root",
        "post": "docker compose -f /opt/stacks/proxy/docker-compose.yml restart",
        "help": "Two PEM files — typical for Caddy file TLS or stock Nginx ssl_certificate paths.",
        "docs_anchor": "service-maps",
    },
    "docker_bind": {
        "id": "docker_bind",
        "group": "Docker",
        "title": "Docker bind-mount certs directory",
        "label": "App stack certs",
        "remote_dir": "/opt/stacks/myservice/certs",
        "layout": "pair",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "root",
        "group": "root",
        "post": "docker compose -f /opt/stacks/myservice/docker-compose.yml restart",
        "help": (
            "Host directory you bind-mount into the container (e.g. ./certs:/certs:ro). "
            "Edit compose path and restart for your project."
        ),
        "docs_anchor": "service-maps",
    },
    "grafana_volume": {
        "id": "grafana_volume",
        "group": "Docker",
        "title": "Grafana TLS → Docker named volume",
        "label": "Grafana TLS",
        "remote_dir": "~",
        "layout": "pair",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "",
        "group": "",
        "write_mode": "direct",
        # Official grafana/grafana image runs as UID 472. root:root mode 600 → crash loop
        # "permission denied" on /var/lib/grafana/*.pem. Prefer docker volume copy + chown 472.
        "post": (
            "docker run --rm "
            "-v grafana_grafana_data:/data "
            "-v /home/piherder:/src:ro "
            "alpine:3.20 "
            "sh -c 'cp /src/fullchain.pem /src/privkey.pem /data/ && "
            "chown 472:0 /data/fullchain.pem /data/privkey.pem && "
            "chmod 644 /data/fullchain.pem && chmod 600 /data/privkey.pem' && "
            "cd /home/bjorn/docker/grafana && docker compose restart grafana"
        ),
        "help": (
            "Writes PEMs to the SSH user’s home, then a one-shot container copies them into "
            "the Grafana named volume as UID 472 (Grafana process user). "
            "Do NOT install as root:root mode 600 — Grafana cannot read those files. "
            "Adjust volume name, home path, and compose dir for your host. Needs docker group."
        ),
        "docs_anchor": "cookbook-grafana-tls-into-a-docker-named-volume",
    },
    "octopi_haproxy": {
        "id": "octopi_haproxy",
        "group": "Host TLS",
        "title": "OctoPi / HAProxy combined (snakeoil)",
        "label": "OctoPi HAProxy",
        "remote_dir": "/etc/ssl",
        "layout": "combined",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "644",
        "owner": "root",
        "group": "root",
        "write_mode": "stage_sudo",
        "post": "sudo systemctl restart haproxy",
        "help": (
            "Stage+sudo install snakeoil.pem into /etc/ssl, then restart HAProxy. "
            "Least-priv friendly — sudoers only needs install + systemctl (shown on map form)."
        ),
        "docs_anchor": "cookbook-octopi--haproxy-host-no-docker-least-priv-piherder",
    },
    "unifi_pfx": {
        "id": "unifi_pfx",
        "group": "PFX / Windows",
        "title": "UniFi / Windows PFX",
        "label": "UniFi controller PFX",
        "remote_dir": "~/certs",
        "layout": "pfx",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "",
        "group": "",
        "post": "",
        "restart_kind": "none",
        "help": (
            "Builds a single PKCS#12 .pfx on the host via openssl (PEMs stay out of the final dir). "
            "Import Certificate.pfx into UniFi / Windows as needed."
        ),
        "docs_anchor": "deploy-targets",
    },
    "combined_generic": {
        "id": "combined_generic",
        "group": "Host TLS",
        "title": "Combined single PEM (generic)",
        "label": "Combined PEM",
        "remote_dir": "~/certs",
        "layout": "combined",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "",
        "group": "",
        "post": "",
        "help": "Single file: private key first, then full chain — HAProxy and some legacy apps.",
        "docs_anchor": "service-maps",
    },
    "custom": {
        "id": "custom",
        "group": "Custom",
        "title": "Custom (blank defaults)",
        "label": "",
        "remote_dir": "~/certs",
        "layout": "pair",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "",
        "group": "",
        "post": "",
        "help": "Fill every field for your service.",
        "docs_anchor": "service-maps",
    },
}


def map_presets_for_ui() -> list[dict[str, Any]]:
    """Ordered preset list for map form (grouped by ``group`` in UI)."""
    order = [
        "npm_pair",
        "caddy_pair",
        "docker_bind",
        "grafana_volume",
        "octopi_haproxy",
        "unifi_pfx",
        "combined_generic",
        "custom",
    ]
    out: list[dict[str, Any]] = []
    for key in order:
        p = MAP_PRESETS.get(key)
        if p:
            out.append(dict(p))
    # Any extra presets not in order (forward-compat)
    for key, p in MAP_PRESETS.items():
        if key not in order:
            out.append(dict(p))
    return out


def get_map_preset(preset_id: str) -> dict[str, Any] | None:
    p = MAP_PRESETS.get((preset_id or "").strip())
    return dict(p) if p else None


def files_for_layout(
    layout: str,
    *,
    remote_dir: str = "~/certs",
    fullchain_filename: str = "fullchain.pem",
    privkey_filename: str = "privkey.pem",
    combined_filename: str = "snakeoil.pem",
    pfx_filename: str = "Certificate.pfx",
) -> list[dict[str, str]]:
    """Return planned *final* remote paths for UI preview (no I/O)."""
    base = (remote_dir or "~/certs").rstrip("/") or "~/certs"
    lay = (layout or "pair").strip()
    out: list[dict[str, str]] = []
    if layout_installs_pair(lay):
        out.append(
            {
                "name": fullchain_filename or "fullchain.pem",
                "path": f"{base}/{fullchain_filename or 'fullchain.pem'}",
                "kind": "fullchain",
            }
        )
        out.append(
            {
                "name": privkey_filename or "privkey.pem",
                "path": f"{base}/{privkey_filename or 'privkey.pem'}",
                "kind": "privkey",
            }
        )
    if layout_installs_combined(lay):
        out.append(
            {
                "name": combined_filename or "snakeoil.pem",
                "path": f"{base}/{combined_filename or 'snakeoil.pem'}",
                "kind": "combined",
            }
        )
    if layout_installs_pfx(lay):
        out.append(
            {
                "name": pfx_filename or "Certificate.pfx",
                "path": f"{base}/{pfx_filename or 'Certificate.pfx'}",
                "kind": "pfx",
            }
        )
    return out


def parse_pem_metadata(fullchain_pem: str) -> dict[str, Any]:
    """Extract CN, SANs, not_before/after, issuer, serial, fingerprint from leaf cert."""
    pem = (fullchain_pem or "").strip()
    if not pem:
        raise ValueError("Fullchain PEM is empty")
    # First certificate block is the leaf
    blocks = pem.split("-----END CERTIFICATE-----")
    leaf = blocks[0] + "-----END CERTIFICATE-----"
    if "BEGIN CERTIFICATE" not in leaf:
        raise ValueError("No CERTIFICATE block in fullchain PEM")
    cert = x509.load_pem_x509_certificate(leaf.encode("utf-8"), default_backend())
    # cryptography ≥41 uses timezone-aware properties
    not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    if hasattr(not_before, "tzinfo") and not_before.tzinfo:
        not_before = not_before.replace(tzinfo=None)
    if hasattr(not_after, "tzinfo") and not_after.tzinfo:
        not_after = not_after.replace(tzinfo=None)

    domains: list[str] = []
    try:
        cn = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        if cn:
            domains.append(cn[0].value)
    except Exception:
        pass
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for name in ext.value.get_values_for_type(x509.DNSName):
            if name not in domains:
                domains.append(name)
    except Exception:
        pass

    issuer = ""
    try:
        issuer = cert.issuer.rfc4514_string()
    except Exception:
        issuer = str(cert.issuer)

    serial = ""
    try:
        serial = format(cert.serial_number, "x")
    except Exception:
        serial = str(cert.serial_number)

    from cryptography.hazmat.primitives import serialization

    der = cert.public_bytes(serialization.Encoding.DER)
    fp = hashlib.sha256(der).hexdigest()

    return {
        "domains": domains,
        "not_before": not_before,
        "not_after": not_after,
        "issuer": issuer[:500],
        "serial": serial[:128],
        "fingerprint_sha256": fp,
        "cn": domains[0] if domains else "",
    }


def normalize_fingerprint(value: str | None) -> str:
    """Normalize SHA-256 fingerprints for comparison (strip colons/spaces, lower hex).

    Accepts raw hex, colon-separated, or ``openssl … -fingerprint`` lines
    (``SHA256 Fingerprint=AA:BB:…``).
    """
    if not value:
        return ""
    s = str(value).strip()
    if "=" in s:
        s = s.split("=", 1)[-1].strip()
    return "".join(c for c in s.lower() if c in "0123456789abcdef")


def fingerprint_of_pems(fullchain: str, privkey: str) -> str:
    h = hashlib.sha256()
    h.update((fullchain or "").encode("utf-8"))
    h.update(b"\0")
    h.update((privkey or "").encode("utf-8"))
    return h.hexdigest()


def days_until_expiry(not_after: Optional[datetime]) -> Optional[int]:
    if not not_after:
        return None
    delta = not_after - datetime.utcnow()
    return int(delta.total_seconds() // 86400)


def list_certificates(session: Session) -> list[ManagedCertificate]:
    return list(
        session.exec(
            select(ManagedCertificate).order_by(ManagedCertificate.name)
        ).all()
    )


def get_certificate(session: Session, cert_id: int) -> Optional[ManagedCertificate]:
    return session.get(ManagedCertificate, cert_id)


def decrypt_pems(cert: ManagedCertificate) -> tuple[str, str]:
    full = decrypt_str(cert.fullchain_encrypted or "")
    key = decrypt_str(cert.privkey_encrypted or "")
    return full, key


def upsert_from_pems(
    session: Session,
    *,
    name: str,
    fullchain_pem: str,
    privkey_pem: str,
    source: str = "upload",
    source_integration_id: Optional[int] = None,
    external_id: Optional[str] = None,
    auto_renew: bool = False,
    renew_days_before: int = DEFAULT_RENEW_DAYS,
    existing: Optional[ManagedCertificate] = None,
) -> ManagedCertificate:
    full = (fullchain_pem or "").strip() + "\n"
    key = (privkey_pem or "").strip() + "\n"
    if "BEGIN CERTIFICATE" not in full:
        raise ValueError("fullchain must be PEM certificate material")
    if "BEGIN" not in key or "PRIVATE" not in key.upper():
        raise ValueError("privkey must be PEM private key material")

    meta = parse_pem_metadata(full)
    material_fp = fingerprint_of_pems(full, key)
    # Prefer x509 fingerprint for renew comparisons; store both via material in updated
    fp = meta["fingerprint_sha256"] or material_fp

    row = existing
    if row is None and source_integration_id and external_id:
        row = session.exec(
            select(ManagedCertificate).where(
                ManagedCertificate.source_integration_id == source_integration_id,
                ManagedCertificate.external_id == str(external_id),
            )
        ).first()

    now = datetime.utcnow()
    if row is None:
        row = ManagedCertificate(
            name=(name or meta.get("cn") or "certificate").strip() or "certificate",
            source=source,
            source_integration_id=source_integration_id,
            external_id=str(external_id) if external_id is not None else None,
            created_at=now,
        )
    else:
        row.name = (name or row.name or meta.get("cn") or "certificate").strip()

    row.domains_json = json.dumps(meta["domains"])
    row.not_before = meta["not_before"]
    row.not_after = meta["not_after"]
    row.fingerprint_sha256 = fp
    row.fullchain_encrypted = encrypt_str(full)
    row.privkey_encrypted = encrypt_str(key)
    row.issuer = meta.get("issuer")
    row.serial = meta.get("serial")
    row.last_pulled_at = now
    row.last_error = None
    row.source = source
    if source_integration_id is not None:
        row.source_integration_id = source_integration_id
    if external_id is not None:
        row.external_id = str(external_id)
    row.auto_renew = bool(auto_renew) if source == "npm" else bool(auto_renew)
    row.renew_days_before = int(renew_days_before or DEFAULT_RENEW_DAYS)
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def pull_from_npm(
    session: Session,
    integration: Integration,
    cert_id: str | int,
    *,
    name: str = "",
    auto_renew: bool = True,
) -> ManagedCertificate:
    if integration.type != reg.TYPE_NPM:
        raise ValueError("Integration is not NPM")
    creds = reg.decrypt_credentials(integration)
    identity = creds.get("username") or ""
    password = creds.get("password") or ""
    token = npm_mod.get_token(
        integration.base_url,
        identity,
        password,
        tls_verify=reg.tls_verify(integration),
    )
    blob = npm_mod.download_certificate_zip(
        integration.base_url,
        token,
        cert_id,
        tls_verify=reg.tls_verify(integration),
    )
    parts = npm_mod.parse_certificate_zip(blob)
    # Prefer nice name from inventory cache
    display = (name or "").strip()
    if not display:
        st = reg.parse_last_status(integration)
        for c in st.get("certificates") or []:
            if str(c.get("id")) == str(cert_id):
                display = c.get("nice_name") or ", ".join(c.get("domain_names") or [])
                break
    if not display:
        display = f"NPM cert {cert_id}"
    return upsert_from_pems(
        session,
        name=display,
        fullchain_pem=parts["fullchain"],
        privkey_pem=parts["privkey"],
        source="npm",
        source_integration_id=integration.id,
        external_id=str(cert_id),
        auto_renew=auto_renew,
    )


def public_cert_dict(cert: ManagedCertificate) -> dict[str, Any]:
    domains = []
    if cert.domains_json:
        try:
            domains = json.loads(cert.domains_json)
        except Exception:
            domains = []
    days = days_until_expiry(cert.not_after)
    edge_fp = (getattr(cert, "last_edge_deploy_fingerprint", None) or "").strip()
    vault_fp = (cert.fingerprint_sha256 or "").strip()
    edge_status = getattr(cert, "last_edge_deploy_status", None)
    edge_in_sync = bool(
        edge_fp
        and vault_fp
        and edge_fp == vault_fp
        and edge_status == "success"
    )
    edge_stale = bool(
        edge_fp
        and vault_fp
        and edge_fp != vault_fp
        and edge_status == "success"
    )
    return {
        "id": cert.id,
        "name": cert.name,
        "source": cert.source,
        "source_integration_id": cert.source_integration_id,
        "external_id": cert.external_id,
        "domains": domains if isinstance(domains, list) else [],
        "not_before": cert.not_before,
        "not_after": cert.not_after,
        "days_left": days,
        "fingerprint_sha256": cert.fingerprint_sha256,
        "issuer": cert.issuer,
        "serial": cert.serial,
        "auto_renew": cert.auto_renew,
        "renew_days_before": cert.renew_days_before,
        "last_pulled_at": cert.last_pulled_at,
        "last_renew_status": cert.last_renew_status,
        "last_error": cert.last_error,
        "expiring_soon": days is not None and days <= (cert.renew_days_before or DEFAULT_RENEW_DAYS),
        "expired": days is not None and days < 0,
        "edge_apply_enabled": bool(getattr(cert, "edge_apply_enabled", False)),
        "last_edge_deploy_at": getattr(cert, "last_edge_deploy_at", None),
        "last_edge_deploy_status": edge_status,
        "last_edge_deploy_fingerprint": edge_fp or None,
        "last_edge_deploy_fingerprint_short": (
            (edge_fp[:12] + "…") if len(edge_fp) > 12 else (edge_fp or None)
        ),
        "last_edge_deploy_message": getattr(cert, "last_edge_deploy_message", None),
        "edge_in_sync": edge_in_sync,
        "edge_stale": edge_stale,
        "edge_available": edge_certs_writable(),
        # Visible "self map" if opted in or ever applied
        "edge_mapped": bool(getattr(cert, "edge_apply_enabled", False))
        or bool(edge_fp)
        or edge_status in ("success", "failed"),
    }


def edge_certs_dir() -> str:
    from ..config import settings

    return (settings.EDGE_CERTS_DIR or "/certs").rstrip("/") or "/certs"


def edge_certs_writable() -> bool:
    """True when compose mounted EDGE_CERTS_DIR and it is writable by web."""
    import os

    path = edge_certs_dir()
    try:
        return os.path.isdir(path) and os.access(path, os.W_OK)
    except Exception:
        return False


def edge_caddy_status() -> dict[str, Any]:
    """Lightweight readiness for the self-apply UI."""
    import os

    from ..config import settings

    certs = edge_certs_dir()
    caddyfile = settings.CADDYFILE_PATH or "/caddy/Caddyfile"
    return {
        "certs_dir": certs,
        "certs_writable": edge_certs_writable(),
        "caddyfile_path": caddyfile,
        "caddyfile_readable": os.path.isfile(caddyfile) and os.access(caddyfile, os.R_OK),
        "admin_url": (settings.CADDY_ADMIN_URL or "http://caddy:2019").rstrip("/"),
    }


def list_targets(session: Session, certificate_id: int) -> list[CertificateTarget]:
    return list(
        session.exec(
            select(CertificateTarget).where(
                CertificateTarget.certificate_id == certificate_id
            )
        ).all()
    )


def _normalize_write_mode(write_mode: str | None) -> str:
    wm = (write_mode or "direct").strip().lower()
    return wm if wm in WRITE_MODES else "direct"


def create_target(
    session: Session,
    *,
    certificate_id: int,
    server_id: int,
    label: str = "",
    remote_dir: str = "~/certs",
    layout: str = "pair",
    write_mode: str = "direct",
    fullchain_filename: str = "fullchain.pem",
    privkey_filename: str = "privkey.pem",
    combined_filename: str = "snakeoil.pem",
    pfx_filename: str = "Certificate.pfx",
    file_mode: str = "600",
    file_owner: str = "",
    file_group: str = "",
    pfx_export_password: str = "",
    post_deploy_command: str = "",
    verify_url: str = "",
    enabled: bool = True,
) -> CertificateTarget:
    if not session.get(ManagedCertificate, certificate_id):
        raise ValueError("Certificate not found")
    if not session.get(Server, server_id):
        raise ValueError("Server not found")
    lay = (layout or "pair").strip()
    if lay not in LAYOUTS:
        lay = "pair"
    now = datetime.utcnow()
    row = CertificateTarget(
        certificate_id=certificate_id,
        server_id=server_id,
        label=(label or "").strip()[:200] or None,
        remote_dir=(remote_dir or "~/certs").strip() or "~/certs",
        layout=lay,
        write_mode=_normalize_write_mode(write_mode),
        fullchain_filename=(fullchain_filename or "fullchain.pem").strip(),
        privkey_filename=(privkey_filename or "privkey.pem").strip(),
        combined_filename=(combined_filename or "snakeoil.pem").strip(),
        pfx_filename=(pfx_filename or "Certificate.pfx").strip(),
        file_mode=(file_mode or "600").strip() or "600",
        file_owner=(file_owner or "").strip() or None,
        file_group=(file_group or "").strip() or None,
        pfx_export_password_encrypted=encrypt_str(pfx_export_password)
        if pfx_export_password
        else None,
        post_deploy_command=(post_deploy_command or "").strip() or None,
        verify_url=_normalize_verify_url(verify_url),
        enabled=bool(enabled),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _normalize_verify_url(value: str | None) -> str | None:
    v = (value or "").strip()[:500]
    return v or None


def update_target(
    session: Session,
    target_id: int,
    *,
    server_id: int | None = None,
    label: str | None = None,
    remote_dir: str | None = None,
    layout: str | None = None,
    write_mode: str | None = None,
    fullchain_filename: str | None = None,
    privkey_filename: str | None = None,
    combined_filename: str | None = None,
    pfx_filename: str | None = None,
    file_mode: str | None = None,
    file_owner: str | None = None,
    file_group: str | None = None,
    pfx_export_password: str | None = None,
    clear_pfx_password: bool = False,
    post_deploy_command: str | None = None,
    verify_url: str | None = None,
    enabled: bool | None = None,
) -> CertificateTarget:
    row = session.get(CertificateTarget, target_id)
    if not row:
        raise ValueError("Target not found")
    if server_id is not None:
        if not session.get(Server, server_id):
            raise ValueError("Server not found")
        row.server_id = server_id
    if label is not None:
        row.label = (label or "").strip()[:200] or None
    if remote_dir is not None:
        row.remote_dir = (remote_dir or "").strip() or "~/certs"
    if layout is not None:
        lay = (layout or "pair").strip()
        row.layout = lay if lay in LAYOUTS else "pair"
    if write_mode is not None:
        row.write_mode = _normalize_write_mode(write_mode)
    if fullchain_filename is not None:
        row.fullchain_filename = (fullchain_filename or "fullchain.pem").strip()
    if privkey_filename is not None:
        row.privkey_filename = (privkey_filename or "privkey.pem").strip()
    if combined_filename is not None:
        row.combined_filename = (combined_filename or "snakeoil.pem").strip()
    if pfx_filename is not None:
        row.pfx_filename = (pfx_filename or "Certificate.pfx").strip()
    if file_mode is not None:
        row.file_mode = (file_mode or "600").strip() or "600"
    if file_owner is not None:
        row.file_owner = (file_owner or "").strip() or None
    if file_group is not None:
        row.file_group = (file_group or "").strip() or None
    if clear_pfx_password:
        row.pfx_export_password_encrypted = None
    elif pfx_export_password is not None and pfx_export_password != "":
        row.pfx_export_password_encrypted = encrypt_str(pfx_export_password)
    if post_deploy_command is not None:
        row.post_deploy_command = (post_deploy_command or "").strip() or None
    if verify_url is not None:
        row.verify_url = _normalize_verify_url(verify_url)
    if enabled is not None:
        row.enabled = bool(enabled)
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _dt_json(value: Any) -> str | None:
    """Serialize datetimes for template |tojson / API-friendly dicts."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def public_target_dict(
    target: CertificateTarget,
    *,
    server_name: str = "",
    cert_fingerprint: str | None = None,
    ssh_user: str = "piherder",
    home_dir: str | None = None,
) -> dict[str, Any]:
    files = files_for_layout(
        target.layout or "pair",
        remote_dir=target.remote_dir or "~/certs",
        fullchain_filename=target.fullchain_filename or "fullchain.pem",
        privkey_filename=target.privkey_filename or "privkey.pem",
        combined_filename=target.combined_filename or "snakeoil.pem",
        pfx_filename=target.pfx_filename or "Certificate.pfx",
    )
    fp_deployed = (target.last_deploy_fingerprint or "").strip()
    fp_cert = (cert_fingerprint or "").strip()
    in_sync = bool(
        fp_deployed
        and fp_cert
        and fp_deployed == fp_cert
        and target.last_deploy_status == "success"
    )
    stale = bool(
        fp_deployed
        and fp_cert
        and fp_deployed != fp_cert
        and target.last_deploy_status == "success"
    )
    return {
        "id": target.id,
        "server_id": target.server_id,
        "server_name": server_name or f"#{target.server_id}",
        "label": target.label or "",
        "remote_dir": target.remote_dir,
        "layout": target.layout,
        "layout_help": LAYOUT_HELP.get(target.layout or "pair", ""),
        "write_mode": getattr(target, "write_mode", None) or "direct",
        "write_mode_help": WRITE_MODE_HELP.get(
            getattr(target, "write_mode", None) or "direct", WRITE_MODE_HELP["direct"]
        ),
        "sudoers_snippet": sudoers_snippet_for_map(
            remote_dir=target.remote_dir or "~/certs",
            layout=target.layout or "pair",
            write_mode=getattr(target, "write_mode", None) or "direct",
            fullchain_filename=target.fullchain_filename or "fullchain.pem",
            privkey_filename=target.privkey_filename or "privkey.pem",
            combined_filename=target.combined_filename or "snakeoil.pem",
            pfx_filename=target.pfx_filename or "Certificate.pfx",
            file_mode=target.file_mode or "600",
            file_owner=target.file_owner or "root",
            file_group=target.file_group or "root",
            post_deploy_command=target.post_deploy_command or "",
            ssh_user=ssh_user or "piherder",
            home_dir=home_dir,
        ),
        "enabled": target.enabled,
        "file_mode": target.file_mode,
        "file_owner": target.file_owner or "",
        "file_group": target.file_group or "",
        "fullchain_filename": target.fullchain_filename,
        "privkey_filename": target.privkey_filename,
        "combined_filename": target.combined_filename,
        "pfx_filename": target.pfx_filename,
        "post_deploy_command": target.post_deploy_command or "",
        "restart": parse_restart_recipe(target.post_deploy_command or ""),
        "verify_url": getattr(target, "verify_url", None) or "",
        "has_pfx_password": bool(target.pfx_export_password_encrypted),
        "files": files,
        "ssh_user": ssh_user or "piherder",
        # ISO strings so Jinja |tojson works (datetime is not JSON-serializable)
        "last_deployed_at": _dt_json(target.last_deployed_at),
        "last_deploy_status": target.last_deploy_status,
        "last_deploy_message": target.last_deploy_message,
        "last_deploy_fingerprint": fp_deployed or None,
        "last_deploy_fingerprint_short": (fp_deployed[:12] + "…") if len(fp_deployed) > 12 else (fp_deployed or None),
        "last_verify_status": getattr(target, "last_verify_status", None),
        "last_verify_message": getattr(target, "last_verify_message", None),
        "last_verify_at": _dt_json(getattr(target, "last_verify_at", None)),
        "verified": (getattr(target, "last_verify_status", None) or "") == "success",
        "in_sync": in_sync,
        "stale_vs_vault": stale,
    }


def sudoers_snippet_for_map(
    *,
    remote_dir: str,
    layout: str,
    write_mode: str,
    fullchain_filename: str = "fullchain.pem",
    privkey_filename: str = "privkey.pem",
    combined_filename: str = "snakeoil.pem",
    pfx_filename: str = "Certificate.pfx",
    file_mode: str = "600",
    file_owner: str = "root",
    file_group: str = "root",
    post_deploy_command: str = "",
    ssh_user: str = "piherder",
    home_dir: str | None = None,
) -> str:
    """Suggested NOPASSWD drop-in for least-priv fleet maps (stage_sudo + restarts).

    Paths use the same helpers as deploy: expand ``~`` against *home_dir* (or
    default home for *ssh_user*). Stage glob is ``{home}/.piherder/cert-stage/*``.

    Known edges:
    - Pass live ``home_dir`` from SSH ``$HOME`` when known; offline UI falls back
      to ``/home/<user>`` or ``/root``.
    - Post-deploy systemctl/docker lines are best-effort; verify with ``sudo -n …``.
    - Free-form post-deploy is never fully expressible in a single drop-in.
    """
    wm = _normalize_write_mode(write_mode)
    if wm != "stage_sudo" and not (post_deploy_command or "").strip():
        return (
            "# Write mode is “direct” — no sudo needed if the SSH user owns the target path.\n"
            "# Switch to “Stage in home + sudo install” for root-owned destinations."
        )
    user = (ssh_user or "piherder").strip() or "piherder"
    home = resolve_ssh_home(ssh_user=user, home_dir=home_dir)
    final = expand_remote_dir(remote_dir or "~/certs", home)
    owner = (file_owner or "root").strip() or "root"
    group = (file_group or "root").strip() or "root"
    mode = (file_mode or "600").strip() or "600"
    stage_glob = cert_stage_glob(home)
    stage_base = cert_stage_base(home)
    names: list[str] = []
    lay = layout or "pair"
    if layout_installs_pair(lay):
        names.extend([fullchain_filename or "fullchain.pem", privkey_filename or "privkey.pem"])
    if layout_installs_combined(lay):
        names.append(combined_filename or "snakeoil.pem")
    if layout_installs_pfx(lay):
        names.append(pfx_filename or "Certificate.pfx")
    names = [n for n in names if n]
    guessed = home_dir is None or not str(home_dir).strip()
    lines = [
        f"# PiHerder cert deploy — least-priv ({user})",
        f"# Install: sudo tee /etc/sudoers.d/piherder-certs && chmod 440 … && visudo -cf …",
        f"# Runtime stage: {stage_base}/<map-id>/  (must match SSH $HOME)",
    ]
    if guessed:
        lines.append(
            f"# Home guessed as {home} — if SSH $HOME differs, re-generate with the real home."
        )
    lines.append(
        f"{user} ALL=(root) NOPASSWD: /usr/bin/install -d -o {owner} -g {group} -m 755 {final}"
    )
    for n in names:
        # Match deploy: sudo install -o -g -m src dst (exact flags)
        lines.append(
            f"{user} ALL=(root) NOPASSWD: /usr/bin/install -o {owner} -g {group} "
            f"-m {mode} {stage_glob}/{n} {final}/{n}"
        )
    post = (post_deploy_command or "").strip()
    if post:
        lines.append("# Also allow post-deploy (adjust to match your exact command):")
        if "systemctl restart haproxy" in post:
            lines.append(
                f"{user} ALL=(root) NOPASSWD: /bin/systemctl restart haproxy, "
                f"/usr/bin/systemctl restart haproxy"
            )
        elif "docker compose" in post or "docker-compose" in post:
            lines.append(
                f"# docker compose restart usually needs docker group, not sudo — "
                f"prefer adding {user} to group docker"
            )
        else:
            lines.append(f"# Review post-deploy manually: {post[:120]}")
    return "\n".join(lines) + "\n"


def delete_target(session: Session, target_id: int) -> bool:
    row = session.get(CertificateTarget, target_id)
    if not row:
        return False
    try:
        from . import notifications as notif_svc

        notif_svc.resolve_cert_target_alerts(session, int(target_id))
    except Exception:
        pass
    session.delete(row)
    session.commit()
    return True


def delete_certificate(session: Session, cert_id: int) -> bool:
    row = session.get(ManagedCertificate, cert_id)
    if not row:
        return False
    for t in list_targets(session, cert_id):
        try:
            from . import notifications as notif_svc

            if t.id is not None:
                notif_svc.resolve_cert_target_alerts(session, int(t.id))
        except Exception:
            pass
        session.delete(t)
    session.delete(row)
    session.commit()
    return True


def build_combined_pem(privkey: str, fullchain: str) -> str:
    """Order: private key then fullchain (matches operator snakeoil.pem pattern)."""
    return (privkey or "").rstrip() + "\n" + (fullchain or "").rstrip() + "\n"


def _layout_file_payloads(
    layout: str,
    full: str,
    key: str,
    target: CertificateTarget,
) -> list[tuple[str, str]]:
    """Return (filename, content) pairs installed to the *final* destination.

    Pure ``pfx`` has no PEM install payload — PEMs are staged only for openssl.
    """
    files: list[tuple[str, str]] = []
    lay = layout or "pair"
    if layout_installs_pair(lay):
        files.append((target.fullchain_filename or "fullchain.pem", full))
        files.append((target.privkey_filename or "privkey.pem", key))
    if layout_installs_combined(lay):
        files.append(
            (
                target.combined_filename or "snakeoil.pem",
                build_combined_pem(key, full),
            )
        )
    return files


def _pfx_source_pems(
    full: str,
    key: str,
    target: CertificateTarget,
) -> list[tuple[str, str]]:
    """PEM material written to stage/temp so openssl can export PKCS#12."""
    return [
        (target.fullchain_filename or "fullchain.pem", full),
        (target.privkey_filename or "privkey.pem", key),
    ]


def reload_edge_caddy() -> dict[str, Any]:
    """POST current Caddyfile to Caddy admin /load (compose-network only).

    Always force a full apply: Caddy skips identical configs by default
    (\"config is unchanged\"), which would leave previously loaded PEM material
    in memory even after we overwrite /certs/fullchain.pem + privkey.pem.
    Cache-Control: must-revalidate forces re-read of file-based TLS certs.
    """
    import urllib.error
    import urllib.request

    from ..config import settings

    admin = (settings.CADDY_ADMIN_URL or "http://caddy:2019").rstrip("/")
    caddyfile_path = settings.CADDYFILE_PATH or "/caddy/Caddyfile"
    try:
        with open(caddyfile_path, "rb") as f:
            body = f.read()
    except OSError as e:
        return {
            "ok": False,
            "error": f"Cannot read Caddyfile at {caddyfile_path}: {e}",
        }
    if not body.strip():
        return {"ok": False, "error": "Caddyfile is empty"}
    url = f"{admin}/load"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "text/caddyfile",
            # Critical: without this, /load no-ops when Caddyfile text is unchanged
            # and never reloads volume-mounted PEMs after edge apply / renew.
            "Cache-Control": "must-revalidate",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = getattr(resp, "status", 200) or 200
            if 200 <= int(code) < 300:
                return {"ok": True, "status": int(code)}
            return {"ok": False, "error": f"Caddy /load HTTP {code}"}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return {
            "ok": False,
            "error": f"Caddy /load HTTP {e.code}: {detail or e.reason}",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": (
                f"Cannot reach Caddy admin at {admin}: {e}. "
                "Stock compose enables admin on caddy:2019 (not published to host)."
            ),
        }


def deploy_to_edge_caddy(
    session: Session,
    certificate_id: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Write vault PEMs into this instance's certs volume and reload Caddy.

    No SSH, no sudoers, no extra operator config when using stock docker-compose
    (./certs mounted on web + caddy; Caddy admin on the compose network).
    """
    import os

    cert = session.get(ManagedCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}

    if (
        not force
        and cert.last_edge_deploy_fingerprint
        and cert.fingerprint_sha256
        and cert.last_edge_deploy_fingerprint == cert.fingerprint_sha256
        and cert.last_edge_deploy_status == "success"
    ):
        if not getattr(cert, "edge_apply_enabled", False):
            cert.edge_apply_enabled = True
            cert.updated_at = datetime.utcnow()
            session.add(cert)
            session.commit()
        return {
            "ok": True,
            "skipped": True,
            "fingerprint": cert.fingerprint_sha256,
            "message": "Edge already has this fingerprint",
            "edge_apply_enabled": True,
        }

    if not edge_certs_writable():
        return {
            "ok": False,
            "error": (
                f"Edge certs directory {edge_certs_dir()!r} is missing or not writable. "
                "Stock compose mounts ./certs on the web service — rebuild/restart web."
            ),
        }

    full, key = decrypt_pems(cert)
    if not full or not key:
        return {"ok": False, "error": "certificate PEMs missing"}

    certs_dir = edge_certs_dir()
    full_path = os.path.join(certs_dir, "fullchain.pem")
    key_path = os.path.join(certs_dir, "privkey.pem")
    try:
        # Atomic-ish replace
        for path, content, mode in (
            (full_path, full.rstrip() + "\n", 0o644),
            (key_path, key.rstrip() + "\n", 0o600),
        ):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        fp_side = os.path.join(certs_dir, ".piherder-cert-fp")
        with open(fp_side, "w", encoding="utf-8") as f:
            f.write((cert.fingerprint_sha256 or "") + "\n")
        try:
            os.chmod(fp_side, 0o644)
        except Exception:
            pass
    except OSError as e:
        msg = f"Write to {certs_dir} failed: {e}"
        cert.last_edge_deploy_status = "failed"
        cert.last_edge_deploy_message = msg[:500]
        cert.updated_at = datetime.utcnow()
        session.add(cert)
        session.commit()
        return {"ok": False, "error": msg}

    reload = reload_edge_caddy()
    now = datetime.utcnow()
    if not reload.get("ok"):
        msg = f"Files written but Caddy reload failed: {reload.get('error')}"
        cert.last_edge_deploy_at = now
        cert.last_edge_deploy_status = "failed"
        cert.last_edge_deploy_fingerprint = cert.fingerprint_sha256
        cert.last_edge_deploy_message = msg[:500]
        cert.updated_at = now
        session.add(cert)
        session.commit()
        return {
            "ok": False,
            "error": msg,
            "wrote": True,
            "paths": [full_path, key_path],
        }

    cert.last_edge_deploy_at = now
    cert.last_edge_deploy_status = "success"
    cert.last_edge_deploy_fingerprint = cert.fingerprint_sha256
    cert.last_edge_deploy_message = "ok"
    # Successful apply = self-managed edge mapping (renew re-apply until removed)
    cert.edge_apply_enabled = True
    cert.updated_at = now
    session.add(cert)
    session.commit()
    return {
        "ok": True,
        "fingerprint": cert.fingerprint_sha256,
        "paths": [full_path, key_path],
        "reloaded": True,
        "edge_apply_enabled": True,
    }


def _openssl_fp_cmd_for_pem(path: str) -> str:
    """Shell: SHA256 fingerprint of first cert in a PEM file (hex, no colons)."""
    p = shlex.quote(path)
    # openssl fingerprint output → strip label + colons → lower hex
    return (
        f"openssl x509 -in {p} -noout -fingerprint -sha256 2>/dev/null "
        f"| sed -e 's/^.*=//' -e 's/://g' | tr 'A-F' 'a-f'"
    )


def _openssl_fp_cmd_for_pfx(path: str, password: str = "") -> str:
    p = shlex.quote(path)
    passin = shlex.quote(f"pass:{password or ''}")
    return (
        f"openssl pkcs12 -in {p} -nokeys -clcerts -passin {passin} 2>/dev/null "
        f"| openssl x509 -noout -fingerprint -sha256 2>/dev/null "
        f"| sed -e 's/^.*=//' -e 's/://g' | tr 'A-F' 'a-f'"
    )


def verify_remote_cert_fingerprint(
    client: Any,
    target: CertificateTarget,
    *,
    remote_dir: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Read installed material on the host and compare leaf fingerprint to vault.

    Prefer openssl on the installed PEM/PFX; fall back to ``.piherder-cert-fp`` marker.
    """
    expected = normalize_fingerprint(expected_fingerprint)
    if not expected:
        return {
            "ok": False,
            "status": "skipped",
            "message": "Vault has no fingerprint to compare",
        }
    base = (remote_dir or "").rstrip("/") or remote_dir
    layout = target.layout or "pair"
    cmds: list[tuple[str, str]] = []  # (label, shell)

    if layout_installs_pair(layout):
        cmds.append(
            (
                "fullchain",
                _openssl_fp_cmd_for_pem(
                    f"{base}/{target.fullchain_filename or 'fullchain.pem'}"
                ),
            )
        )
    if layout_installs_combined(layout):
        cmds.append(
            (
                "combined",
                _openssl_fp_cmd_for_pem(
                    f"{base}/{target.combined_filename or 'snakeoil.pem'}"
                ),
            )
        )
    if layout_installs_pfx(layout) and not layout_installs_pair(layout):
        # pure pfx (or pfx-only final)
        pfx_pw = ""
        if target.pfx_export_password_encrypted:
            try:
                pfx_pw = decrypt_str(target.pfx_export_password_encrypted)
            except Exception:
                pfx_pw = ""
        cmds.append(
            (
                "pfx",
                _openssl_fp_cmd_for_pfx(
                    f"{base}/{target.pfx_filename or 'Certificate.pfx'}", pfx_pw
                ),
            )
        )
    elif layout_installs_pfx(layout) and layout_installs_pair(layout):
        # legacy pair_and_pfx — prefer fullchain
        pass

    # Marker file always useful as secondary check
    marker = f"{base}/.piherder-cert-fp"

    got_fp = ""
    source = ""
    for label, cmd in cmds:
        st, out, err = ssh_svc.run_command(client, cmd, timeout=30)
        cand = normalize_fingerprint((out or "").strip().splitlines()[0] if out else "")
        if st == 0 and cand:
            got_fp = cand
            source = label
            break

    if not got_fp:
        st, out, err = ssh_svc.run_command(
            client, f"cat {shlex.quote(marker)} 2>/dev/null", timeout=15
        )
        cand = normalize_fingerprint((out or "").strip().splitlines()[0] if out else "")
        if st == 0 and cand:
            got_fp = cand
            source = "marker"

    if not got_fp:
        return {
            "ok": False,
            "status": "failed",
            "message": "Could not read fingerprint from host (openssl or marker missing)",
            "expected": expected,
        }
    if got_fp == expected:
        return {
            "ok": True,
            "status": "success",
            "message": f"Host {source} fingerprint matches vault",
            "fingerprint": got_fp,
            "source": source,
        }
    return {
        "ok": False,
        "status": "failed",
        "message": (
            f"Fingerprint mismatch via {source}: host={got_fp[:16]}… "
            f"vault={expected[:16]}…"
        ),
        "fingerprint": got_fp,
        "expected": expected,
        "source": source,
    }


# openssl s_client -starttls <proto> values we accept
_STARTTLS_PROTOS = frozenset(
    {
        "smtp",
        "pop3",
        "imap",
        "ftp",
        "xmpp",
        "postgres",
        "postgresql",  # alias → postgres
        "mysql",
        "lmtp",
        "nntp",
        "sieve",
        "ldap",
        "redis",
    }
)

# scheme → (default_port, starttls_or_None for plain TLS)
_SCHEME_TLS: dict[str, tuple[int, str | None]] = {
    "https": (443, None),
    "tls": (443, None),
    "ssl": (443, None),
    "ldaps": (636, None),  # LDAP over TLS
    "postgres": (5432, "postgres"),
    "postgresql": (5432, "postgres"),
    "pgsql": (5432, "postgres"),
    "mysql": (3306, "mysql"),
    "mariadb": (3306, "mysql"),
    "redis": (6379, "redis"),
    "smtp": (25, "smtp"),
    "smtps": (465, None),  # implicit TLS
    "imap": (143, "imap"),
    "imaps": (993, None),
    "ldap": (389, "ldap"),
}


def parse_verify_endpoint(value: str | None) -> dict[str, Any] | None:
    """Parse a TLS probe endpoint: host:port, URL, or DB-style scheme.

    This is **port-level TLS** via ``openssl s_client``, not an HTTP GET.

    Examples:
      ``https://app.example.com/`` → :443 plain TLS + SNI
      ``app.example.com:8443`` → plain TLS on 8443 (web, custom, native TLS DB, …)
      ``127.0.0.1:5432`` → plain TLS on 5432 (DB that speaks TLS immediately)
      ``postgres://db.local:5432`` → STARTTLS postgres (common for PostgreSQL)
      ``mysql://db.local`` → STARTTLS mysql on 3306
      ``tls://10.0.0.5:636`` → plain TLS (e.g. LDAPS-style)
      ``host:5432?starttls=postgres`` → force STARTTLS
      ``10.0.0.5:443?sni=app.example.com`` → connect IP, SNI hostname
    """
    from urllib.parse import parse_qs, unquote, urlparse

    raw = (value or "").strip()
    if not raw:
        return None

    starttls: str | None = None
    servername_override: str | None = None
    host = ""
    port: int | None = None
    scheme = ""

    # Bare host:port or host:port?query (no scheme)
    if "://" not in raw:
        # Optional query on bare form: host:port?starttls=postgres&sni=…
        path_part = raw
        query = ""
        if "?" in raw:
            path_part, query = raw.split("?", 1)
        qs = parse_qs(query, keep_blank_values=False) if query else {}
        if "starttls" in qs and qs["starttls"]:
            st = (qs["starttls"][0] or "").strip().lower()
            if st in ("postgresql",):
                st = "postgres"
            if st in _STARTTLS_PROTOS:
                starttls = "postgres" if st == "postgresql" else st
        if "sni" in qs and qs["sni"]:
            servername_override = (qs["sni"][0] or "").strip() or None

        path_part = path_part.strip()
        # IPv6 in brackets: [fe80::1]:5432
        if path_part.startswith("["):
            end = path_part.find("]")
            if end > 0:
                host = path_part[1:end]
                rest = path_part[end + 1 :]
                if rest.startswith(":") and rest[1:].isdigit():
                    port = int(rest[1:])
                else:
                    port = 443
            else:
                return None
        elif path_part.count(":") == 1:
            host_part, port_s = path_part.rsplit(":", 1)
            if port_s.isdigit() and host_part:
                host = host_part.strip()
                port = int(port_s)
            else:
                host = path_part
                port = 443
        else:
            host = path_part
            port = 443
        if not host:
            return None
        sni = servername_override or host
        return {
            "host": host,
            "port": int(port or 443),
            "servername": sni,
            "starttls": starttls,
            "raw": raw,
            "scheme": "tls",
        }

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").strip()
    if not host:
        return None

    # Query overrides
    qs = parse_qs(parsed.query or "", keep_blank_values=False)
    if "starttls" in qs and qs["starttls"]:
        st = (qs["starttls"][0] or "").strip().lower()
        if st in ("postgresql",):
            st = "postgres"
        if st in _STARTTLS_PROTOS:
            starttls = "postgres" if st == "postgresql" else st
    if "sni" in qs and qs["sni"]:
        servername_override = (qs["sni"][0] or "").strip() or None

    if scheme == "http":
        # Not a TLS probe — still parse but callers may fail handshake
        port = int(parsed.port or 80)
    elif scheme in _SCHEME_TLS:
        default_port, default_starttls = _SCHEME_TLS[scheme]
        port = int(parsed.port or default_port)
        if starttls is None:
            starttls = default_starttls
    else:
        # Unknown scheme: treat as TLS with optional port, default 443
        port = int(parsed.port or 443)

    # Normalize starttls alias
    if starttls == "postgresql":
        starttls = "postgres"
    if starttls and starttls not in _STARTTLS_PROTOS:
        starttls = None
    if starttls == "postgresql":  # pragma: no cover
        starttls = "postgres"

    sni = servername_override or host
    # userinfo unused; path unused (we never HTTP GET)
    _ = unquote(parsed.path or "")

    return {
        "host": host,
        "port": int(port),
        "servername": sni,
        "starttls": starttls,
        "raw": raw,
        "scheme": scheme,
    }


def _s_client_fingerprint_shell(
    host: str,
    port: int,
    servername: str,
    *,
    starttls: str | None = None,
) -> str:
    """openssl s_client against any TLS port → leaf SHA256 fingerprint (hex).

    *starttls*: optional protocol for STARTTLS (postgres, mysql, smtp, …).
    Plain TLS (HTTPS, native TLS on a DB port, LDAPS) leaves starttls empty.
    """
    h = shlex.quote(f"{host}:{int(port)}")
    sni = shlex.quote(servername or host)
    st = (starttls or "").strip().lower()
    if st == "postgresql":
        st = "postgres"
    starttls_flag = ""
    if st and st in _STARTTLS_PROTOS:
        # openssl uses "postgres" not "postgresql"
        proto = "postgres" if st == "postgresql" else st
        starttls_flag = f"-starttls {shlex.quote(proto)} "
    return (
        f"echo | openssl s_client -connect {h} -servername {sni} "
        f"{starttls_flag}"
        f"-showcerts 2>/dev/null "
        f"| openssl x509 -noout -fingerprint -sha256 2>/dev/null "
        f"| sed -e 's/^.*=//' -e 's/://g' | tr 'A-F' 'a-f'"
    )


def verify_tls_endpoint_fingerprint(
    *,
    verify_url: str,
    expected_fingerprint: str,
    client: Any | None = None,
    timeout: int = 25,
) -> dict[str, Any]:
    """Probe a live **TLS port** (web, DB, …) and compare leaf fingerprint to vault.

    Uses ``openssl s_client -connect host:port`` (optional ``-starttls``).
    This is not an HTTP request — any service that completes a TLS handshake works.

    When *client* (SSH) is provided, the probe runs **on the fleet host** first
    (LAN / localhost DB ports). Falls back to this process (public edges).
    """
    expected = normalize_fingerprint(expected_fingerprint)
    if not expected:
        return {
            "ok": False,
            "status": "skipped",
            "message": "Vault has no fingerprint to compare",
            "kind": "tls",
        }
    ep = parse_verify_endpoint(verify_url)
    if not ep:
        return {
            "ok": False,
            "status": "skipped",
            "message": "No TLS probe endpoint configured",
            "kind": "tls",
        }
    host = ep["host"]
    port = int(ep["port"])
    sni = ep.get("servername") or host
    starttls = ep.get("starttls")
    cmd = _s_client_fingerprint_shell(host, port, sni, starttls=starttls)
    attempts: list[dict[str, Any]] = []

    def _try(label: str, runner) -> dict[str, Any] | None:
        try:
            st, out, err = runner()
        except Exception as e:
            attempts.append({"via": label, "ok": False, "detail": str(e)[:160]})
            return None
        cand = normalize_fingerprint(
            (out or "").strip().splitlines()[0] if out else ""
        )
        if st == 0 and cand:
            attempts.append({"via": label, "ok": True, "fingerprint": cand})
            st_note = f" starttls={starttls}" if starttls else ""
            if cand == expected:
                return {
                    "ok": True,
                    "status": "success",
                    "message": (
                        f"TLS port {host}:{port} (SNI {sni}{st_note}) via {label} "
                        f"matches vault"
                    ),
                    "fingerprint": cand,
                    "kind": "tls",
                    "via": label,
                    "endpoint": f"{host}:{port}",
                    "servername": sni,
                    "starttls": starttls,
                    "attempts": attempts,
                }
            return {
                "ok": False,
                "status": "failed",
                "message": (
                    f"TLS port {host}:{port}{st_note} via {label}: fingerprint mismatch "
                    f"presented={cand[:16]}… vault={expected[:16]}…"
                ),
                "fingerprint": cand,
                "expected": expected,
                "kind": "tls",
                "via": label,
                "endpoint": f"{host}:{port}",
                "servername": sni,
                "starttls": starttls,
                "attempts": attempts,
            }
        attempts.append(
            {
                "via": label,
                "ok": False,
                "detail": ((err or out or "no certificate")[:160]),
            }
        )
        return None

    # 1) From fleet host (where service often listens)
    if client is not None:
        def _ssh():
            return ssh_svc.run_command(client, cmd, timeout=timeout)

        hit = _try("host-ssh", _ssh)
        if hit is not None:
            return hit

    # 2) From PiHerder process (public / reachable endpoints)
    import subprocess

    def _local():
        proc = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    hit = _try("herder", _local)
    if hit is not None:
        return hit

    detail = "; ".join(
        f"{a['via']}: {a.get('detail') or 'fail'}" for a in attempts
    )[:300]
    st_note = f", starttls={starttls}" if starttls else ""
    return {
        "ok": False,
        "status": "failed",
        "message": (
            f"TLS port probe failed for {host}:{port} (SNI {sni}{st_note}) — "
            f"could not complete handshake / read leaf cert. {detail}"
        ),
        "kind": "tls",
        "endpoint": f"{host}:{port}",
        "servername": sni,
        "starttls": starttls,
        "attempts": attempts,
    }


def verify_deploy_target(
    client: Any,
    target: CertificateTarget,
    *,
    remote_dir: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Host file fingerprint + optional TLS URL probe; combined status.

    * **success** — host OK and TLS OK (or TLS not configured)
    * **partial** — host OK but TLS failed (or the reverse, rare)
    * **failed** — host check failed (and TLS not rescuing)
    * **skipped** — nothing to compare
    """
    host_res = verify_remote_cert_fingerprint(
        client,
        target,
        remote_dir=remote_dir,
        expected_fingerprint=expected_fingerprint,
    )
    tls_url = (getattr(target, "verify_url", None) or "").strip()
    tls_res: dict[str, Any] | None = None
    if tls_url:
        tls_res = verify_tls_endpoint_fingerprint(
            verify_url=tls_url,
            expected_fingerprint=expected_fingerprint,
            client=client,
        )

    parts: list[str] = []
    if host_res.get("message"):
        parts.append(f"host: {host_res['message']}")
    if tls_res and tls_res.get("message"):
        parts.append(f"tls: {tls_res['message']}")

    host_ok = bool(host_res.get("ok"))
    host_skip = (host_res.get("status") or "") == "skipped"
    if not tls_url:
        return {
            "ok": host_ok,
            "status": host_res.get("status") or ("success" if host_ok else "failed"),
            "message": host_res.get("message") or "",
            "host": host_res,
            "tls": None,
        }

    tls_ok = bool(tls_res and tls_res.get("ok"))
    tls_skip = bool(tls_res and (tls_res.get("status") or "") == "skipped")

    if host_ok and tls_ok:
        status, ok = "success", True
    elif host_ok and tls_skip:
        status, ok = "success", True
    elif host_skip and tls_ok:
        status, ok = "success", True
    elif host_ok and not tls_ok:
        # Files on disk good but live service not presenting vault cert yet
        status, ok = "partial", False
    elif tls_ok and not host_ok:
        status, ok = "partial", False
    else:
        status, ok = "failed", False

    return {
        "ok": ok,
        "status": status,
        "message": "; ".join(parts)[:500],
        "host": host_res,
        "tls": tls_res,
    }


def simulate_sudoers_access(
    client: Any,
    *,
    remote_dir: str,
    layout: str,
    write_mode: str,
    fullchain_filename: str = "fullchain.pem",
    privkey_filename: str = "privkey.pem",
    combined_filename: str = "snakeoil.pem",
    pfx_filename: str = "Certificate.pfx",
    file_mode: str = "600",
    file_owner: str = "root",
    file_group: str = "root",
    post_deploy_command: str = "",
    ssh_user: str = "piherder",
    home_dir: str | None = None,
) -> dict[str, Any]:
    """Best-effort NOPASSWD checks matching stage_sudo install lines.

    Does not permanently install certs. Creates a tiny probe under the stage area
    and tests ``sudo -n install`` into a temp subdir of the final destination.
    """
    wm = _normalize_write_mode(write_mode)
    checks: list[dict[str, Any]] = []
    user = (ssh_user or "piherder").strip() or "piherder"
    home = resolve_ssh_home(ssh_user=user, home_dir=home_dir)
    final = expand_remote_dir(remote_dir or "~/certs", home)
    owner = (file_owner or "root").strip() or "root"
    group = (file_group or "root").strip() or "root"
    mode = (file_mode or "600").strip() or "600"

    def _check(name: str, cmd: str) -> None:
        st, out, err = ssh_svc.run_command(client, cmd, timeout=30)
        ok = st == 0
        checks.append(
            {
                "name": name,
                "ok": ok,
                "detail": ((err or out or "")[:200] if not ok else "ok"),
            }
        )

    # Can we sudo at all without password?
    _check("sudo -n true", "sudo -n true")

    if wm == "stage_sudo":
        stage_base = cert_stage_base(home)
        probe_stage = f"{stage_base}/_sim"
        probe_final = f"{final.rstrip('/')}/.piherder-sim"
        _check(
            "mkdir stage",
            f"mkdir -p {shlex.quote(probe_stage)}",
        )
        _check(
            "sudo install -d final",
            f"sudo -n /usr/bin/install -d -o {shlex.quote(owner)} "
            f"-g {shlex.quote(group)} -m 755 {shlex.quote(final)}",
        )
        # Probe file install for each final filename of the layout
        names: list[str] = []
        if layout_installs_pair(layout):
            names.extend(
                [fullchain_filename or "fullchain.pem", privkey_filename or "privkey.pem"]
            )
        if layout_installs_combined(layout):
            names.append(combined_filename or "snakeoil.pem")
        if layout_installs_pfx(layout):
            names.append(pfx_filename or "Certificate.pfx")
        for n in names:
            if not n:
                continue
            src = f"{probe_stage}/{n}"
            dst = f"{probe_final}/{n}"
            _check(
                f"write probe {n}",
                f"printf sim > {shlex.quote(src)}",
            )
            _check(
                f"sudo install {n}",
                f"sudo -n /usr/bin/install -d -o {shlex.quote(owner)} "
                f"-g {shlex.quote(group)} -m 755 {shlex.quote(probe_final)} && "
                f"sudo -n /usr/bin/install -o {shlex.quote(owner)} "
                f"-g {shlex.quote(group)} -m {shlex.quote(mode)} "
                f"{shlex.quote(src)} {shlex.quote(dst)}",
            )
        # Cleanup probe (best effort)
        ssh_svc.run_command(
            client,
            f"rm -rf {shlex.quote(probe_stage)} {shlex.quote(probe_final)}",
            timeout=20,
        )
    else:
        _check(
            "writable final (or mkdir)",
            f"mkdir -p {shlex.quote(final)} && test -w {shlex.quote(final)}",
        )

    post = (post_deploy_command or "").strip()
    if post and "systemctl restart" in post:
        unit = post.split("systemctl restart", 1)[-1].strip().split()[0]
        if unit:
            # Non-destructive: list unit only
            _check(
                f"sudo systemctl (unit {unit})",
                f"sudo -n /usr/bin/systemctl status {shlex.quote(unit)} >/dev/null 2>&1 "
                f"|| sudo -n /bin/systemctl status {shlex.quote(unit)} >/dev/null 2>&1 "
                f"|| sudo -n true",  # status may be non-zero if inactive; fall soft
            )
            # Real check: can we run restart non-interactively? too aggressive.
            # Prefer: sudo -n -l | grep systemctl
            st, out, _ = ssh_svc.run_command(
                client, "sudo -n -l 2>/dev/null | head -c 4000", timeout=15
            )
            listed = out or ""
            if "systemctl" in listed or "ALL" in listed:
                checks.append(
                    {
                        "name": "sudoers mentions systemctl/ALL",
                        "ok": True,
                        "detail": "ok",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "sudoers mentions systemctl/ALL",
                        "ok": False,
                        "detail": "No systemctl/ALL in `sudo -l` — restart may fail",
                    }
                )

    failed = [c for c in checks if not c.get("ok")]
    ok = not failed
    return {
        "ok": ok,
        "status": "success" if ok else "failed",
        "checks": checks,
        "message": (
            "All privilege probes passed"
            if ok
            else f"{len(failed)} check(s) failed — review sudoers / write mode"
        ),
        "ssh_user": user,
        "home": home,
        "final_dir": final,
        "write_mode": wm,
    }


def deploy_target(
    session: Session,
    target_id: int,
    *,
    force: bool = False,
    progress: Optional[Any] = None,
) -> dict[str, Any]:
    """SSH deploy certificate material to a host target."""
    target = session.get(CertificateTarget, target_id)
    if not target:
        return {"ok": False, "error": "target not found"}
    cert = session.get(ManagedCertificate, target.certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}
    server = session.get(Server, target.server_id)
    if not server:
        return {"ok": False, "error": "server not found"}

    def log(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass
        logger.info("cert deploy %s: %s", target_id, msg)

    if (
        not force
        and target.last_deploy_fingerprint
        and cert.fingerprint_sha256
        and target.last_deploy_fingerprint == cert.fingerprint_sha256
        and target.last_deploy_status == "success"
    ):
        log("skip — already deployed same fingerprint")
        return {"ok": True, "skipped": True, "fingerprint": cert.fingerprint_sha256}

    full, key = decrypt_pems(cert)
    if not full or not key:
        _apply_cert_target_alerts(
            session,
            target=target,
            cert=cert,
            server=server,
            deploy_ok=False,
            deploy_error="certificate PEMs missing",
            verify=None,
        )
        return {"ok": False, "error": "certificate PEMs missing"}

    write_mode = _normalize_write_mode(getattr(target, "write_mode", None))
    layout = target.layout or "pair"
    client = None
    try:
        client = ssh_svc.get_ssh_client(server)
        st, home_raw, _ = ssh_svc.run_command(client, "printf %s \"$HOME\"", timeout=15)
        ssh_user = (getattr(server, "ssh_username", None) or "piherder").strip() or "piherder"
        home = resolve_ssh_home(ssh_user=ssh_user, home_dir=(home_raw or "").strip() or None)
        remote_dir = expand_remote_dir(target.remote_dir or "~/certs", home)

        mode = (target.file_mode or "600").strip() or "600"
        try:
            mode_int = int(mode, 8)
        except ValueError:
            mode_int = 0o600
            mode = "600"
        owner = (target.file_owner or "").strip()
        group = (target.file_group or "").strip()
        payloads = _layout_file_payloads(layout, full, key, target)

        if write_mode == "stage_sudo":
            stage_dir = cert_stage_dir(home, target_id)
            log(f"stage_sudo: stage={stage_dir} → final={remote_dir}")
            st, out, err = ssh_svc.run_command(
                client, f"mkdir -p {shlex.quote(stage_dir)}", timeout=30
            )
            if st != 0:
                raise RuntimeError(f"stage mkdir failed: {err or out}")

            sftp = client.open_sftp()
            try:
                for fname, content in payloads:
                    path = f"{stage_dir.rstrip('/')}/{fname}"
                    log(f"stage write {path}")
                    with sftp.file(path, "w") as f:
                        f.write(content)
                    try:
                        sftp.chmod(path, 0o600)
                    except Exception:
                        pass
            finally:
                sftp.close()

            # Final dir + install each file (mkdir/chmod/chown as root)
            chown_user = owner or "root"
            chown_group = group or "root"
            st, out, err = ssh_svc.run_command(
                client,
                f"sudo install -d -o {shlex.quote(chown_user)} "
                f"-g {shlex.quote(chown_group)} -m 755 {shlex.quote(remote_dir)}",
                timeout=30,
            )
            if st != 0:
                raise RuntimeError(
                    "sudo install -d failed — check sudoers allows "
                    f"`install -d … {remote_dir}` for user {ssh_user} "
                    f"(NOPASSWD, exact path). Detail: {err or out}"
                )

            for fname, _content in payloads:
                src = f"{stage_dir.rstrip('/')}/{fname}"
                dst = f"{remote_dir.rstrip('/')}/{fname}"
                log(f"sudo install {src} → {dst}")
                st, out, err = ssh_svc.run_command(
                    client,
                    f"sudo install -o {shlex.quote(chown_user)} "
                    f"-g {shlex.quote(chown_group)} -m {shlex.quote(mode)} "
                    f"{shlex.quote(src)} {shlex.quote(dst)}",
                    timeout=30,
                )
                if st != 0:
                    raise RuntimeError(
                        f"sudo install {fname} failed — check sudoers allows "
                        f"`install … {src} {dst}` for user {ssh_user} "
                        f"(NOPASSWD; stage path must match $HOME). Detail: {err or out}"
                    )

            # PFX: stage PEMs if needed (pure pfx has no PEM install payload), export, install .pfx
            if layout_installs_pfx(layout):
                pfx_pw = ""
                if target.pfx_export_password_encrypted:
                    pfx_pw = decrypt_str(target.pfx_export_password_encrypted)
                # Ensure PEMs exist in stage for openssl (pure pfx only stages these)
                if layout == "pfx":
                    sftp = client.open_sftp()
                    try:
                        for fname, content in _pfx_source_pems(full, key, target):
                            path = f"{stage_dir.rstrip('/')}/{fname}"
                            log(f"stage write (pfx source) {path}")
                            with sftp.file(path, "w") as f:
                                f.write(content)
                            try:
                                sftp.chmod(path, 0o600)
                            except Exception:
                                pass
                    finally:
                        sftp.close()
                full_p = f"{stage_dir.rstrip('/')}/{target.fullchain_filename or 'fullchain.pem'}"
                key_p = f"{stage_dir.rstrip('/')}/{target.privkey_filename or 'privkey.pem'}"
                pfx_stage = f"{stage_dir.rstrip('/')}/{target.pfx_filename or 'Certificate.pfx'}"
                passout = f"pass:{pfx_pw}"
                cmd = (
                    f"openssl pkcs12 -export -in {shlex.quote(full_p)} "
                    f"-inkey {shlex.quote(key_p)} -out {shlex.quote(pfx_stage)} "
                    f"-keypbe aes-256-cbc -certpbe aes-256-cbc "
                    f"-passout {shlex.quote(passout)}"
                )
                log("openssl pkcs12 export (stage)")
                st, out, err = ssh_svc.run_command(client, cmd, timeout=60)
                if st != 0:
                    raise RuntimeError(f"pfx export failed: {err or out}")
                pfx_dst = f"{remote_dir.rstrip('/')}/{target.pfx_filename or 'Certificate.pfx'}"
                st, out, err = ssh_svc.run_command(
                    client,
                    f"sudo install -o {shlex.quote(chown_user)} "
                    f"-g {shlex.quote(chown_group)} -m {shlex.quote(mode)} "
                    f"{shlex.quote(pfx_stage)} {shlex.quote(pfx_dst)}",
                    timeout=30,
                )
                if st != 0:
                    raise RuntimeError(f"sudo install pfx failed: {err or out}")
        else:
            # direct SFTP into remote_dir
            log(f"direct write → {remote_dir}")
            st, out, err = ssh_svc.run_command(
                client, f"mkdir -p {shlex.quote(remote_dir)}", timeout=30
            )
            if st != 0:
                raise RuntimeError(
                    f"mkdir failed: {err or out}. "
                    "For root-owned paths use write mode “Stage + sudo install”."
                )

            sftp = client.open_sftp()
            try:
                write_list = list(payloads)
                if layout == "pfx":
                    # PEMs are intermediate only; removed after pfx export
                    write_list = list(_pfx_source_pems(full, key, target))
                for fname, content in write_list:
                    path = f"{remote_dir.rstrip('/')}/{fname}"
                    log(f"write {path}")
                    with sftp.file(path, "w") as f:
                        f.write(content)
                    try:
                        sftp.chmod(path, mode_int if layout != "pfx" else 0o600)
                    except Exception:
                        ssh_svc.run_command(
                            client,
                            f"chmod {shlex.quote(mode if layout != 'pfx' else '600')} {shlex.quote(path)}",
                            timeout=15,
                        )

                fp_path = f"{remote_dir.rstrip('/')}/.piherder-cert-fp"
                with sftp.file(fp_path, "w") as f:
                    f.write((cert.fingerprint_sha256 or "") + "\n")
            finally:
                sftp.close()

            if owner and layout != "pfx":
                chown = f"{owner}:{group}" if group else owner
                for fname, _ in payloads:
                    path = f"{remote_dir.rstrip('/')}/{fname}"
                    ssh_svc.run_command(
                        client,
                        f"sudo chown {shlex.quote(chown)} {shlex.quote(path)} 2>/dev/null || "
                        f"chown {shlex.quote(chown)} {shlex.quote(path)}",
                        timeout=20,
                    )

            if layout_installs_pfx(layout):
                pfx_pw = ""
                if target.pfx_export_password_encrypted:
                    pfx_pw = decrypt_str(target.pfx_export_password_encrypted)
                full_p = f"{remote_dir.rstrip('/')}/{target.fullchain_filename or 'fullchain.pem'}"
                key_p = f"{remote_dir.rstrip('/')}/{target.privkey_filename or 'privkey.pem'}"
                pfx_p = f"{remote_dir.rstrip('/')}/{target.pfx_filename or 'Certificate.pfx'}"
                passout = f"pass:{pfx_pw}"
                cmd = (
                    f"openssl pkcs12 -export -in {shlex.quote(full_p)} "
                    f"-inkey {shlex.quote(key_p)} -out {shlex.quote(pfx_p)} "
                    f"-keypbe aes-256-cbc -certpbe aes-256-cbc "
                    f"-passout {shlex.quote(passout)}"
                )
                log("openssl pkcs12 export")
                st, out, err = ssh_svc.run_command(client, cmd, timeout=60)
                if st != 0:
                    raise RuntimeError(f"pfx export failed: {err or out}")
                try:
                    sftp = client.open_sftp()
                    try:
                        sftp.chmod(pfx_p, mode_int)
                    finally:
                        sftp.close()
                except Exception:
                    ssh_svc.run_command(
                        client,
                        f"chmod {shlex.quote(mode)} {shlex.quote(pfx_p)}",
                        timeout=15,
                    )
                if owner:
                    chown = f"{owner}:{group}" if group else owner
                    ssh_svc.run_command(
                        client,
                        f"sudo chown {shlex.quote(chown)} {shlex.quote(pfx_p)} 2>/dev/null || true",
                        timeout=15,
                    )
                # Pure pfx: remove intermediate PEMs from final dir
                if layout == "pfx":
                    for fname, _ in _pfx_source_pems(full, key, target):
                        path = f"{remote_dir.rstrip('/')}/{fname}"
                        ssh_svc.run_command(
                            client, f"rm -f {shlex.quote(path)}", timeout=15
                        )

        if target.post_deploy_command:
            log("post_deploy_command")
            st, out, err = ssh_svc.run_command(
                client, target.post_deploy_command, timeout=180
            )
            if st != 0:
                cmd_preview = (target.post_deploy_command or "").strip()
                if len(cmd_preview) > 120:
                    cmd_preview = cmd_preview[:117] + "…"
                raise RuntimeError(
                    f"Restart / post-deploy command failed (exit {st}): "
                    f"{(err or out or '').strip() or 'no output'}. "
                    f"Command: {cmd_preview}. "
                    "Fix the unit/compose path, or allow it in sudoers "
                    "(Simulate privileges in the deploy-target wizard)."
                )

        # Marker fingerprint file (direct already wrote it; stage_sudo may need sudo)
        if write_mode == "stage_sudo":
            fp_marker = f"{remote_dir.rstrip('/')}/.piherder-cert-fp"
            fp_stage = f"{cert_stage_dir(home, target_id)}/.piherder-cert-fp"
            try:
                sftp = client.open_sftp()
                try:
                    with sftp.file(fp_stage, "w") as f:
                        f.write((cert.fingerprint_sha256 or "") + "\n")
                finally:
                    sftp.close()
                chown_user = (target.file_owner or "root").strip() or "root"
                chown_group = (target.file_group or "root").strip() or "root"
                ssh_svc.run_command(
                    client,
                    f"sudo install -o {shlex.quote(chown_user)} "
                    f"-g {shlex.quote(chown_group)} -m 644 "
                    f"{shlex.quote(fp_stage)} {shlex.quote(fp_marker)}",
                    timeout=20,
                )
            except Exception as mark_err:
                log(f"marker write skipped: {mark_err}")

        log("verify host fingerprint + optional TLS URL")
        verify = verify_deploy_target(
            client,
            target,
            remote_dir=remote_dir,
            expected_fingerprint=cert.fingerprint_sha256 or "",
        )
        now = datetime.utcnow()
        target.last_deployed_at = now
        target.last_deploy_status = "success"
        target.last_deploy_fingerprint = cert.fingerprint_sha256
        vstatus = verify.get("status") or ("success" if verify.get("ok") else "failed")
        target.last_verify_status = vstatus
        target.last_verify_message = (verify.get("message") or "")[:500]
        target.last_verify_at = now
        msg = f"ok ({write_mode})"
        if vstatus == "success":
            msg += "; verified"
            if verify.get("tls") and verify["tls"].get("ok"):
                msg += "+tls"
        elif vstatus == "partial":
            msg += f"; verify partial: {target.last_verify_message}"
        elif vstatus == "failed":
            msg += f"; verify failed: {target.last_verify_message}"
        else:
            msg += f"; verify {vstatus}"
        target.last_deploy_message = msg[:500]
        target.updated_at = now
        session.add(target)
        session.commit()
        # Alerts: deploy succeeded → close deploy-failed; verify drives verify alert
        _apply_cert_target_alerts(
            session,
            target=target,
            cert=cert,
            server=server,
            deploy_ok=True,
            verify=verify,
        )
        log("done")
        return {
            "ok": True,
            "fingerprint": cert.fingerprint_sha256,
            "server_id": server.id,
            "remote_dir": remote_dir,
            "write_mode": write_mode,
            "verify": verify,
        }
    except Exception as e:
        logger.exception("cert deploy failed")
        now = datetime.utcnow()
        wm = ""
        try:
            wm = _normalize_write_mode(getattr(target, "write_mode", None))
        except Exception:
            wm = (getattr(target, "write_mode", None) or "") or ""
        ssh_u = (getattr(server, "ssh_username", None) or "piherder") if server else ""
        rdir = (getattr(target, "remote_dir", None) or "") if target else ""
        nice = humanize_deploy_error(
            e, write_mode=wm, ssh_user=ssh_u, remote_dir=rdir
        )[:500]
        target.last_deploy_status = "failed"
        target.last_deploy_message = nice
        target.updated_at = now
        session.add(target)
        session.commit()
        _apply_cert_target_alerts(
            session,
            target=target,
            cert=cert,
            server=server,
            deploy_ok=False,
            deploy_error=nice,
            verify=None,
        )
        return {"ok": False, "error": nice}
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def humanize_deploy_error(
    err: Any,
    *,
    write_mode: str = "",
    ssh_user: str = "",
    remote_dir: str = "",
) -> str:
    """Turn raw SSH/sudo/openssl failures into operator-actionable copy (A1.4)."""
    raw = str(err or "").strip() or "Deploy failed"
    low = raw.lower()
    user = (ssh_user or "piherder").strip() or "piherder"
    dest = (remote_dir or "destination").strip() or "destination"
    wm = (write_mode or "").strip()

    def _join(main: str, hint: str) -> str:
        main = main.rstrip()
        if not hint:
            return main[:500]
        if hint.lower() in main.lower():
            return main[:500]
        return f"{main} — {hint}"[:500]

    # SSH / auth
    if any(
        t in low
        for t in (
            "authentication failed",
            "auth fail",
            "permission denied (publickey",
            "no existing session",
            "unable to connect",
            "connection refused",
            "connection reset",
            "timed out",
            "timeout",
            "name or service not known",
            "no route to host",
        )
    ):
        return _join(
            raw,
            f"Check SSH for {user}: host reachability, key/password, and that the "
            "server is online in PiHerder.",
        )

    # Missing PEMs
    if "pems missing" in low or "certificate pems missing" in low:
        return _join(
            raw,
            "Re-upload fullchain + privkey (⋮ → Replace PEM) then deploy again.",
        )

    # Sudo / sudoers
    if any(
        t in low
        for t in (
            "a password is required",
            "sudo: a password is required",
            "not allowed to execute",
            "is not in the sudoers",
            "sudoers",
            "sudo install",
            "sorry, user",
        )
    ) or ("sudo" in low and ("fail" in low or "denied" in low or "error" in low)):
        return _join(
            raw,
            f"Install the deploy-target sudoers drop-in for user {user} "
            f"(exact stage + final paths), then use Simulate privileges. "
            f"Destination: {dest}.",
        )

    # Write / mkdir / permission
    if any(
        t in low
        for t in (
            "permission denied",
            "mkdir failed",
            "read-only file system",
            "operation not permitted",
        )
    ):
        if wm == "direct" or "stage + sudo" in low or "stage_sudo" in low:
            return _join(
                raw,
                f"SSH user {user} cannot write {dest}. Switch write mode to "
                "Stage + sudo install, or fix directory ownership/permissions.",
            )
        return _join(
            raw,
            f"Cannot write under {dest} as {user}. Check path exists, ownership, "
            "and write mode (direct vs stage+sudo).",
        )

    # OpenSSL / PFX
    if "pfx" in low or "pkcs12" in low or "openssl" in low:
        return _join(
            raw,
            "Need openssl on the host with pkcs12 support; check PFX password "
            "and that source PEMs staged correctly.",
        )

    # Restart / post-deploy
    if "post deploy" in low or "post-deploy" in low or "restart" in low:
        return _join(
            raw,
            "Files may already be installed — fix the restart recipe "
            "(compose path / systemd unit) or sudoers for that command, then re-deploy.",
        )

    # Stage path
    if "stage mkdir" in low or ("stage" in low and "mkdir" in low):
        return _join(
            raw,
            f"Cannot create the stage directory under {user}'s home. "
            "Confirm SSH home and disk space.",
        )

    # Generic — keep detail, add short next step
    if len(raw) < 80 and "fail" in low:
        return _join(
            raw,
            "Open deploy-target detail for paths/sudoers; run Simulate privileges, "
            "then retry Deploy.",
        )
    return raw[:500]


def _apply_cert_target_alerts(
    session: Session,
    *,
    target: CertificateTarget,
    cert: ManagedCertificate | None,
    server: Server | None,
    deploy_ok: bool,
    deploy_error: str = "",
    verify: dict[str, Any] | None = None,
) -> None:
    """Raise/resolve Notifications for deploy failure and fingerprint/TLS failure.

    Best-effort — never raise out of the deploy path.
    """
    try:
        from . import notifications as notif_svc
    except Exception:
        return
    try:
        tid = int(target.id) if target.id is not None else 0
        if not tid:
            return
        cert_id = int(cert.id) if cert and cert.id else int(target.certificate_id)
        cert_name = (cert.name if cert else None) or f"Certificate #{cert_id}"
        server_id = int(server.id) if server and server.id else int(target.server_id)
        server_name = (server.name if server else None) or f"#{server_id}"
        label = (target.label or "").strip() or f"target #{tid}"

        if deploy_ok:
            notif_svc.resolve_cert_deploy_failed(session, tid)
        else:
            notif_svc.notify_cert_deploy_failed(
                session,
                target_id=tid,
                cert_id=cert_id,
                cert_name=cert_name,
                server_id=server_id,
                server_name=server_name,
                service_label=label,
                message=deploy_error or "deploy failed",
            )
            # Deploy failed before/without a clean verify — still flag verify if stale
            # but do not open verify-only noise; leave prior verify alert alone.
            return

        # Deploy succeeded: open or close verify alert from this run's result
        if verify is None:
            return
        vstatus = (verify.get("status") or "").strip()
        if verify.get("ok") or vstatus == "success" or vstatus == "skipped":
            notif_svc.resolve_cert_verify_failed(session, tid)
        elif vstatus in ("failed", "partial") or not verify.get("ok"):
            notif_svc.notify_cert_verify_failed(
                session,
                target_id=tid,
                cert_id=cert_id,
                cert_name=cert_name,
                server_id=server_id,
                server_name=server_name,
                service_label=label,
                message=verify.get("message") or "verify failed",
                status=vstatus or "failed",
            )
    except Exception as e:
        logger.debug("cert target alerts skipped: %s", e)


def deploy_all_targets(
    session: Session,
    certificate_id: int,
    *,
    force: bool = False,
    progress: Optional[Any] = None,
) -> dict[str, Any]:
    targets = [
        t
        for t in list_targets(session, certificate_id)
        if t.enabled
    ]
    results = []
    for t in targets:
        results.append(deploy_target(session, t.id, force=force, progress=progress))
    ok = all(r.get("ok") for r in results) if results else True
    return {"ok": ok, "results": results, "count": len(results)}


def should_auto_apply_edge(cert: ManagedCertificate) -> bool:
    """True when self-managed edge mapping is enabled for this cert.

    Opt-in via successful Apply to this PiHerder; opt-out via Remove edge mapping.
    """
    return bool(getattr(cert, "edge_apply_enabled", False))


def set_edge_apply_enabled(
    session: Session, certificate_id: int, enabled: bool
) -> ManagedCertificate:
    """Enable or disable Caddy edge mapping (renew re-apply). Does not delete PEMs on disk."""
    cert = session.get(ManagedCertificate, certificate_id)
    if not cert:
        raise ValueError("certificate not found")
    cert.edge_apply_enabled = bool(enabled)
    if not enabled:
        # Keep last_* for history, but mark mapping removed in message
        cert.last_edge_deploy_message = (
            (cert.last_edge_deploy_message or "")[:400]
            + (" · " if cert.last_edge_deploy_message else "")
            + "edge mapping disabled (no auto re-apply)"
        )[:500]
    cert.updated_at = datetime.utcnow()
    session.add(cert)
    session.commit()
    session.refresh(cert)
    return cert


def redistribute_after_renew(
    session: Session,
    certificate_id: int,
    *,
    force: bool = True,
    progress: Optional[Any] = None,
) -> dict[str, Any]:
    """Fleet maps + optional edge (Caddy) re-apply after vault material changes."""

    def log(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    dist = deploy_all_targets(
        session, certificate_id, force=force, progress=progress
    )
    edge: dict[str, Any] | None = None
    cert = session.get(ManagedCertificate, certificate_id)
    if cert and should_auto_apply_edge(cert) and edge_certs_writable():
        log("re-apply to this PiHerder (Caddy edge)")
        edge = deploy_to_edge_caddy(session, certificate_id, force=True)
        if not edge.get("ok"):
            log(f"edge re-apply failed: {edge.get('error')}")
    elif cert and should_auto_apply_edge(cert) and not edge_certs_writable():
        edge = {
            "ok": False,
            "skipped": True,
            "error": "edge certs dir not writable — skip Caddy re-apply",
        }
        log(edge["error"])
    return {
        "ok": bool(dist.get("ok")) and (edge is None or bool(edge.get("ok"))),
        "fleet": dist,
        "edge": edge,
        "count": dist.get("count") or 0,
    }


def renew_npm_certificate(
    session: Session,
    cert: ManagedCertificate,
    *,
    progress: Optional[Any] = None,
    poll_interval_sec: int = RENEW_POLL_INTERVAL_SEC,
    poll_attempts: int = RENEW_POLL_ATTEMPTS,
) -> dict[str, Any]:
    """Request NPM renew, poll for new fingerprint, then distribute."""

    def log(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    if cert.source != "npm" or not cert.source_integration_id or not cert.external_id:
        return {"ok": False, "error": "certificate is not an NPM-sourced cert"}
    integration = session.get(Integration, cert.source_integration_id)
    if not integration or integration.type != reg.TYPE_NPM:
        return {"ok": False, "error": "NPM integration missing"}

    old_fp = cert.fingerprint_sha256
    old_after = cert.not_after
    creds = reg.decrypt_credentials(integration)
    tls = reg.tls_verify(integration)
    token = npm_mod.get_token(
        integration.base_url,
        creds.get("username") or "",
        creds.get("password") or "",
        tls_verify=tls,
    )

    # Re-pull first — may already be renewed by NPM's own scheduler
    log("pull before renew")
    try:
        pull_from_npm(
            session,
            integration,
            cert.external_id,
            name=cert.name,
            auto_renew=cert.auto_renew,
        )
        session.refresh(cert)
        if cert.fingerprint_sha256 and cert.fingerprint_sha256 != old_fp:
            log("already renewed — distributing")
            cert.last_renew_status = "already_new"
            cert.updated_at = datetime.utcnow()
            session.add(cert)
            session.commit()
            dist = redistribute_after_renew(
                session, cert.id, force=True, progress=progress
            )
            return {"ok": True, "renewed": True, "via": "pull", "distribute": dist}
    except Exception as e:
        log(f"pre-pull warning: {e}")

    log("request renew")
    cert.last_renew_requested_at = datetime.utcnow()
    cert.last_renew_status = "requested"
    session.add(cert)
    session.commit()
    try:
        npm_mod.renew_certificate(
            integration.base_url, token, cert.external_id, tls_verify=tls
        )
    except Exception as e:
        cert.last_renew_status = "renew_error"
        cert.last_error = str(e)[:500]
        session.add(cert)
        session.commit()
        return {"ok": False, "error": str(e)[:500]}

    for i in range(int(poll_attempts)):
        log(f"poll for new cert {i + 1}/{poll_attempts} (sleep {poll_interval_sec}s)")
        time.sleep(max(5, int(poll_interval_sec)))  # min 5s for tests to monkeypatch
        try:
            # refresh token each poll
            token = npm_mod.get_token(
                integration.base_url,
                creds.get("username") or "",
                creds.get("password") or "",
                tls_verify=tls,
            )
            pull_from_npm(
                session,
                integration,
                cert.external_id,
                name=cert.name,
                auto_renew=cert.auto_renew,
            )
            session.refresh(cert)
            new_after = cert.not_after
            if cert.fingerprint_sha256 != old_fp or (
                old_after and new_after and new_after > old_after
            ):
                log("new certificate available")
                cert.last_renew_status = "success"
                cert.last_error = None
                session.add(cert)
                session.commit()
                dist = redistribute_after_renew(
                    session, cert.id, force=True, progress=progress
                )
                return {
                    "ok": True,
                    "renewed": True,
                    "via": "renew",
                    "distribute": dist,
                }
        except Exception as e:
            log(f"poll error: {e}")

    cert.last_renew_status = "timeout"
    cert.last_error = "No new certificate after renew polls"
    session.add(cert)
    session.commit()
    return {"ok": False, "error": "renew poll exhausted without new cert"}


def check_expiring_and_renew(
    session: Session,
    *,
    progress: Optional[Any] = None,
    poll_interval_sec: int = RENEW_POLL_INTERVAL_SEC,
    poll_attempts: int = RENEW_POLL_ATTEMPTS,
) -> list[dict[str, Any]]:
    """Scheduler entry: renew NPM certs within renew_days_before window."""
    from . import notifications as notif_svc

    now = datetime.utcnow()
    results = []
    rows = list(session.exec(select(ManagedCertificate)).all())
    for cert in rows:
        if not cert.auto_renew or cert.source != "npm":
            continue
        days = days_until_expiry(cert.not_after)
        threshold = cert.renew_days_before or DEFAULT_RENEW_DAYS
        if days is None or days > threshold:
            continue
        # notify expiring
        try:
            notif_svc.upsert_notification(
                session,
                fingerprint=f"cert_expiring:{cert.id}",
                type="cert_expiring",
                title=f"Certificate expiring: {cert.name}",
                body=f"{days} day(s) left — renewing via NPM",
                link_url=f"/certificates/{cert.id}",
                severity="warning",
            )
        except Exception:
            pass
        r = renew_npm_certificate(
            session,
            cert,
            progress=progress,
            poll_interval_sec=poll_interval_sec,
            poll_attempts=poll_attempts,
        )
        results.append({"cert_id": cert.id, **r})
        if not r.get("ok"):
            try:
                notif_svc.upsert_notification(
                    session,
                    fingerprint=f"cert_renew_failed:{cert.id}",
                    type="cert_renew_failed",
                    title=f"Certificate renew failed: {cert.name}",
                    body=(r.get("error") or "unknown")[:300],
                    link_url=f"/certificates/{cert.id}",
                    severity="critical",
                )
            except Exception:
                pass
        else:
            try:
                notif_svc.resolve_by_fingerprint(
                    session, f"cert_expiring:{cert.id}"
                )
                notif_svc.resolve_by_fingerprint(
                    session, f"cert_renew_failed:{cert.id}"
                )
            except Exception:
                pass
    return results
