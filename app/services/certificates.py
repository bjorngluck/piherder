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

LAYOUTS = frozenset(
    {"pair", "combined", "pair_and_combined", "pair_and_pfx", "pair_combined_pfx"}
)

# Human descriptions for UI
LAYOUT_HELP = {
    "pair": "Two files: fullchain + private key (Nginx, Caddy, most Docker apps)",
    "combined": "One file: private key then fullchain (some HAProxy / “snakeoil” apps)",
    "pair_and_combined": "Both pair and combined PEM in the same directory",
    "pair_and_pfx": "PEM pair plus PKCS#12 .pfx (Windows / UniFi-style consumers)",
    "pair_combined_pfx": "Pair + combined + PFX in the same directory",
}

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
        "post": (
            "sudo install -o root -g root -m 600 "
            "/home/piherder/fullchain.pem /home/piherder/privkey.pem "
            "/var/lib/docker/volumes/grafana_grafana_data/_data/ && "
            "cd /home/piherder/docker/grafana && docker compose restart"
        ),
        "help": (
            "Stages PEMs in the piherder home, then sudo-installs into the Grafana data volume "
            "and restarts compose. Requires matching sudoers — see wiki Grafana cookbook."
        ),
        "docs_anchor": "cookbook-grafana-tls-into-a-docker-named-volume",
    },
    "octopi_haproxy": {
        "id": "octopi_haproxy",
        "group": "Host TLS",
        "title": "OctoPi / HAProxy combined (snakeoil)",
        "label": "OctoPi HAProxy",
        "remote_dir": "~/certs",
        "layout": "combined",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "644",
        "owner": "",
        "group": "",
        "post": (
            "sudo install -o root -g root -m 644 "
            "/home/piherder/certs/snakeoil.pem /etc/ssl/snakeoil.pem && "
            "sudo systemctl restart haproxy"
        ),
        "help": (
            "Combined PEM (key then chain) staged under piherder home, then installed to "
            "/etc/ssl/snakeoil.pem + HAProxy restart. Needs NOPASSWD sudoers — see wiki OctoPi cookbook."
        ),
        "docs_anchor": "cookbook-octopi--haproxy-host-no-docker-least-priv-piherder",
    },
    "unifi_pfx": {
        "id": "unifi_pfx",
        "group": "PFX / Windows",
        "title": "UniFi / Windows PFX",
        "label": "UniFi controller PFX",
        "remote_dir": "~/certs",
        "layout": "pair_and_pfx",
        "fullchain": "fullchain.pem",
        "privkey": "privkey.pem",
        "combined": "snakeoil.pem",
        "pfx": "Certificate.pfx",
        "mode": "600",
        "owner": "",
        "group": "",
        "post": "",
        "help": (
            "Writes PEM pair, then builds PKCS#12 .pfx on the host via openssl. "
            "Import Certificate.pfx into UniFi / Windows as needed."
        ),
        "docs_anchor": "service-maps",
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
    """Return planned remote paths for UI preview (no I/O)."""
    base = (remote_dir or "~/certs").rstrip("/") or "~/certs"
    lay = (layout or "pair").strip()
    out: list[dict[str, str]] = []
    if lay in ("pair", "pair_and_combined", "pair_and_pfx", "pair_combined_pfx"):
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
    if lay in ("combined", "pair_and_combined", "pair_combined_pfx"):
        out.append(
            {
                "name": combined_filename or "snakeoil.pem",
                "path": f"{base}/{combined_filename or 'snakeoil.pem'}",
                "kind": "combined",
            }
        )
    if lay in ("pair_and_pfx", "pair_combined_pfx"):
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
    }


def list_targets(session: Session, certificate_id: int) -> list[CertificateTarget]:
    return list(
        session.exec(
            select(CertificateTarget).where(
                CertificateTarget.certificate_id == certificate_id
            )
        ).all()
    )


def create_target(
    session: Session,
    *,
    certificate_id: int,
    server_id: int,
    label: str = "",
    remote_dir: str = "~/certs",
    layout: str = "pair",
    fullchain_filename: str = "fullchain.pem",
    privkey_filename: str = "privkey.pem",
    combined_filename: str = "snakeoil.pem",
    pfx_filename: str = "Certificate.pfx",
    file_mode: str = "600",
    file_owner: str = "",
    file_group: str = "",
    pfx_export_password: str = "",
    post_deploy_command: str = "",
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
        enabled=bool(enabled),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_target(
    session: Session,
    target_id: int,
    *,
    server_id: int | None = None,
    label: str | None = None,
    remote_dir: str | None = None,
    layout: str | None = None,
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
    if enabled is not None:
        row.enabled = bool(enabled)
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def public_target_dict(
    target: CertificateTarget,
    *,
    server_name: str = "",
    cert_fingerprint: str | None = None,
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
        "enabled": target.enabled,
        "file_mode": target.file_mode,
        "file_owner": target.file_owner or "",
        "file_group": target.file_group or "",
        "fullchain_filename": target.fullchain_filename,
        "privkey_filename": target.privkey_filename,
        "combined_filename": target.combined_filename,
        "pfx_filename": target.pfx_filename,
        "post_deploy_command": target.post_deploy_command or "",
        "has_pfx_password": bool(target.pfx_export_password_encrypted),
        "files": files,
        "last_deployed_at": target.last_deployed_at,
        "last_deploy_status": target.last_deploy_status,
        "last_deploy_message": target.last_deploy_message,
        "last_deploy_fingerprint": fp_deployed or None,
        "last_deploy_fingerprint_short": (fp_deployed[:12] + "…") if len(fp_deployed) > 12 else (fp_deployed or None),
        "in_sync": in_sync,
        "stale_vs_vault": stale,
    }


def delete_target(session: Session, target_id: int) -> bool:
    row = session.get(CertificateTarget, target_id)
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


def delete_certificate(session: Session, cert_id: int) -> bool:
    row = session.get(ManagedCertificate, cert_id)
    if not row:
        return False
    for t in list_targets(session, cert_id):
        session.delete(t)
    session.delete(row)
    session.commit()
    return True


def build_combined_pem(privkey: str, fullchain: str) -> str:
    """Order: private key then fullchain (matches operator snakeoil.pem pattern)."""
    return (privkey or "").rstrip() + "\n" + (fullchain or "").rstrip() + "\n"


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
        return {"ok": False, "error": "certificate PEMs missing"}

    client = None
    try:
        client = ssh_svc.get_ssh_client(server)
        remote_dir = (target.remote_dir or "~/certs").strip()
        # Expand ~ as remote user home
        st, home, _ = ssh_svc.run_command(client, "printf %s \"$HOME\"", timeout=15)
        home = (home or "").strip() or "."
        if remote_dir.startswith("~/"):
            remote_dir = home + remote_dir[1:]
        elif remote_dir == "~":
            remote_dir = home

        log(f"mkdir -p {remote_dir}")
        st, out, err = ssh_svc.run_command(
            client, f"mkdir -p {shlex.quote(remote_dir)}", timeout=30
        )
        if st != 0:
            raise RuntimeError(f"mkdir failed: {err or out}")

        sftp = client.open_sftp()
        try:
            files: list[tuple[str, str]] = []
            layout = target.layout or "pair"
            if layout in ("pair", "pair_and_combined", "pair_and_pfx", "pair_combined_pfx"):
                files.append((target.fullchain_filename, full))
                files.append((target.privkey_filename, key))
            if layout in ("combined", "pair_and_combined", "pair_combined_pfx"):
                files.append(
                    (target.combined_filename, build_combined_pem(key, full))
                )

            mode = target.file_mode or "600"
            try:
                mode_int = int(mode, 8) if mode else 0o600
            except ValueError:
                mode_int = 0o600

            for fname, content in files:
                path = f"{remote_dir.rstrip('/')}/{fname}"
                log(f"write {path}")
                with sftp.file(path, "w") as f:
                    f.write(content)
                try:
                    sftp.chmod(path, mode_int)
                except Exception:
                    ssh_svc.run_command(
                        client,
                        f"chmod {shlex.quote(mode)} {shlex.quote(path)}",
                        timeout=15,
                    )

            # fingerprint sidecar
            fp_path = f"{remote_dir.rstrip('/')}/.piherder-cert-fp"
            with sftp.file(fp_path, "w") as f:
                f.write((cert.fingerprint_sha256 or "") + "\n")
        finally:
            sftp.close()

        # owner
        owner = (target.file_owner or "").strip()
        group = (target.file_group or "").strip()
        if owner:
            chown = f"{owner}:{group}" if group else owner
            names = [target.fullchain_filename, target.privkey_filename]
            if layout in ("combined", "pair_and_combined", "pair_combined_pfx"):
                names.append(target.combined_filename)
            for fname in names:
                path = f"{remote_dir.rstrip('/')}/{fname}"
                ssh_svc.run_command(
                    client,
                    f"sudo chown {shlex.quote(chown)} {shlex.quote(path)} 2>/dev/null || "
                    f"chown {shlex.quote(chown)} {shlex.quote(path)}",
                    timeout=20,
                )

        # PFX via remote openssl
        if layout in ("pair_and_pfx", "pair_combined_pfx"):
            pfx_pw = ""
            if target.pfx_export_password_encrypted:
                pfx_pw = decrypt_str(target.pfx_export_password_encrypted)
            full_p = f"{remote_dir.rstrip('/')}/{target.fullchain_filename}"
            key_p = f"{remote_dir.rstrip('/')}/{target.privkey_filename}"
            pfx_p = f"{remote_dir.rstrip('/')}/{target.pfx_filename}"
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
            if owner:
                chown = f"{owner}:{group}" if group else owner
                ssh_svc.run_command(
                    client,
                    f"sudo chown {shlex.quote(chown)} {shlex.quote(pfx_p)} 2>/dev/null || true",
                    timeout=15,
                )

        if target.post_deploy_command:
            log("post_deploy_command")
            st, out, err = ssh_svc.run_command(
                client, target.post_deploy_command, timeout=180
            )
            if st != 0:
                raise RuntimeError(f"post deploy failed: {err or out}")

        now = datetime.utcnow()
        target.last_deployed_at = now
        target.last_deploy_status = "success"
        target.last_deploy_fingerprint = cert.fingerprint_sha256
        target.last_deploy_message = "ok"
        target.updated_at = now
        session.add(target)
        session.commit()
        log("done")
        return {
            "ok": True,
            "fingerprint": cert.fingerprint_sha256,
            "server_id": server.id,
            "remote_dir": remote_dir,
        }
    except Exception as e:
        logger.exception("cert deploy failed")
        now = datetime.utcnow()
        target.last_deploy_status = "failed"
        target.last_deploy_message = str(e)[:500]
        target.updated_at = now
        session.add(target)
        session.commit()
        return {"ok": False, "error": str(e)[:500]}
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


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
            dist = deploy_all_targets(session, cert.id, force=True, progress=progress)
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
                dist = deploy_all_targets(
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
