"""Hand-authored demo fleet seed (v1.2 Stream D).

Synthetic hosts/jobs/maps only — no real PEMs, tokens, or lab paths.
Safe to run repeatedly (idempotent unless force=True).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session, select

from ..models import (
    ApiToken,
    AuditLog,
    CertificateTarget,
    ContainerAnnotation,
    ContainerAnnotationTag,
    DockerVersion,
    Integration,
    IntegrationBinding,
    Job,
    ManagedCertificate,
    NmapDevice,
    NmapScanRun,
    NmapScanSchedule,
    NmapScriptResult,
    Notification,
    OidcIdentity,
    PasswordResetToken,
    PortAnnotation,
    PushPreference,
    PushSubscription,
    RuntimeEdge,
    Server,
    ServiceDnsRecord,
    StackDeployment,
    TotpBackupCode,
    TrustedDevice,
    User,
    UserFavourite,
    VisualServiceStack,
    WebAuthnCredential,
)
from ..security.auth import get_password_hash
from . import app_settings as app_cfg
from .demo import demo_mode

logger = logging.getLogger(__name__)

DEMO_SEED_VERSION = 1
SEED_MARKER_KEY = "demo_seed_version"

DEFAULT_DEMO_EMAIL = "demo@hacknow.info"
DEFAULT_DEMO_PASSWORD = "Piherder@1"
# Shared public login is viewer — production-like read UI; ops re-seed via CLI only
DEFAULT_DEMO_DISPLAY = "Demo Viewer"
DEFAULT_DEMO_ROLE = "viewer"


def _env_password() -> str:
    from ..config import settings as cfg

    raw = (getattr(cfg, "PIHERDER_DEMO_PASSWORD", None) or os.environ.get("PIHERDER_DEMO_PASSWORD") or "")
    return (str(raw).strip() or DEFAULT_DEMO_PASSWORD)


def _env_email() -> str:
    from ..config import settings as cfg

    raw = (getattr(cfg, "PIHERDER_DEMO_EMAIL", None) or os.environ.get("PIHERDER_DEMO_EMAIL") or "")
    return (str(raw).strip() or DEFAULT_DEMO_EMAIL)


def _inventory(
    projects: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "v": 2,
            "projects": projects,
            "orphan_containers": [],
            "meta": {"demo": True, "source": "demo_seed"},
        }
    )


def _proj(name: str, containers: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    """containers: (name, compose_service, image, ports_display)"""
    return {
        "name": name,
        "path": f"~/docker/{name}",
        "compose_file": "docker-compose.yml",
        "containers": [
            {
                "name": cname,
                "compose_service": svc,
                "image": image,
                "running": True,
                "status": "Up 3 days",
                "ports_display": ports,
            }
            for cname, svc, image, ports in containers
        ],
    }


def _host_specs() -> list[dict[str, Any]]:
    now = datetime.utcnow()
    return [
        {
            "name": "lab-core",
            "hostname": "lab-core.demo",
            "ip": "10.42.0.10",
            "dns_name": "lab-core.demo",
            "sort": 10,
            "backup": True,
            "os_patch": True,
            "container_patch": True,
            "os_updates": 3,
            "container_updates": 1,
            "last_backup": now - timedelta(hours=18),
            "inventory": _inventory(
                [
                    _proj(
                        "piherder",
                        [
                            ("piherder-web", "web", "bjorngluck/piherder:1.2.0", "8000/tcp"),
                            ("piherder-db", "db", "postgres:16-alpine", "5432/tcp"),
                            ("piherder-redis", "redis", "redis:7-alpine", "6379/tcp"),
                        ],
                    ),
                    _proj(
                        "pihole",
                        [
                            (
                                "pihole",
                                "pihole",
                                "pihole/pihole:latest",
                                "0.0.0.0:53->53/tcp, 0.0.0.0:80->80/tcp",
                            )
                        ],
                    ),
                    _proj(
                        "uptime-kuma",
                        [
                            (
                                "uptime-kuma",
                                "uptime-kuma",
                                "louislam/uptime-kuma:1",
                                "0.0.0.0:3001->3001/tcp",
                            )
                        ],
                    ),
                ]
            ),
        },
        {
            "name": "lab-edge",
            "hostname": "lab-edge.demo",
            "ip": "10.42.0.11",
            "dns_name": "lab-edge.demo",
            "sort": 20,
            "backup": True,
            "os_patch": True,
            "container_patch": True,
            "os_updates": 0,
            "container_updates": 0,
            "last_backup": now - timedelta(hours=20),
            "inventory": _inventory(
                [
                    _proj(
                        "npm",
                        [
                            (
                                "npm",
                                "app",
                                "jc21/nginx-proxy-manager:latest",
                                "0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp",
                            )
                        ],
                    ),
                    _proj(
                        "caddy",
                        [
                            (
                                "caddy",
                                "caddy",
                                "caddy:2-alpine",
                                "0.0.0.0:8443->443/tcp",
                            )
                        ],
                    ),
                ]
            ),
        },
        {
            "name": "lab-media",
            "hostname": "lab-media.demo",
            "ip": "10.42.0.12",
            "dns_name": "lab-media.demo",
            "sort": 30,
            "backup": True,
            "os_patch": True,
            "container_patch": True,
            "os_updates": 12,
            "container_updates": 2,
            "last_backup": now - timedelta(days=1, hours=2),
            "inventory": _inventory(
                [
                    _proj(
                        "frigate",
                        [
                            (
                                "frigate",
                                "frigate",
                                "ghcr.io/blakeblackshear/frigate:stable",
                                "0.0.0.0:5000->5000/tcp",
                            )
                        ],
                    ),
                    _proj(
                        "mqtt",
                        [
                            (
                                "mosquitto",
                                "mosquitto",
                                "eclipse-mosquitto:2",
                                "0.0.0.0:1883->1883/tcp",
                            )
                        ],
                    ),
                ]
            ),
        },
        {
            "name": "lab-ha",
            "hostname": "lab-ha.demo",
            "ip": "10.42.0.13",
            "dns_name": "lab-ha.demo",
            "sort": 40,
            "backup": True,
            "os_patch": True,
            "container_patch": False,
            "os_updates": 1,
            "container_updates": 0,
            "last_backup": now - timedelta(hours=6),
            "inventory": _inventory(
                [
                    _proj(
                        "homeassistant",
                        [
                            (
                                "homeassistant",
                                "homeassistant",
                                "ghcr.io/home-assistant/home-assistant:stable",
                                "0.0.0.0:8123->8123/tcp",
                            )
                        ],
                    )
                ]
            ),
        },
        {
            "name": "lab-worker",
            "hostname": "lab-worker.demo",
            "ip": "10.42.0.14",
            "dns_name": "lab-worker.demo",
            "sort": 50,
            "backup": False,
            "os_patch": True,
            "container_patch": True,
            "os_updates": 0,
            "container_updates": 0,
            "last_backup": None,
            "inventory": _inventory(
                [
                    _proj(
                        "n8n",
                        [
                            (
                                "n8n",
                                "n8n",
                                "n8nio/n8n:latest",
                                "0.0.0.0:5678->5678/tcp",
                            )
                        ],
                    ),
                    _proj(
                        "grafana",
                        [
                            (
                                "grafana",
                                "grafana",
                                "grafana/grafana:latest",
                                "0.0.0.0:3000->3000/tcp",
                            )
                        ],
                    ),
                ]
            ),
        },
        {
            "name": "lab-spare",
            "hostname": "lab-spare.demo",
            "ip": "10.42.0.15",
            "dns_name": "lab-spare.demo",
            "sort": 60,
            "backup": False,
            "os_patch": False,
            "container_patch": False,
            "os_updates": 0,
            "container_updates": 0,
            "last_backup": None,
            "inventory": _inventory([]),
        },
    ]


def _delete_all(session: Session, model) -> None:
    try:
        rows = list(session.exec(select(model)).all())
    except Exception:
        session.rollback()
        return
    for row in rows:
        session.delete(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("wipe failed for %s", getattr(model, "__name__", model))
        raise


# Postgres tables wiped on --force (order irrelevant with CASCADE).
# Leaves: alembic_version, appsetting, servicetemplate, pushvapidconfig, topology*.
_PG_WIPE_TABLES = (
    "nmapscriptresult",
    "nmapdevice",
    "nmapscanrun",
    "nmapscanschedule",
    "portannotation",
    "containerannotationtag",
    "containerannotation",
    "visualservicestack",
    "runtimeedge",
    "servicednsrecord",
    "certificatetarget",
    "managedcertificate",
    "integrationbinding",
    "stackdeployment",
    "dockerversion",
    "notification",
    "job",
    "auditlog",
    "pushsubscription",
    "pushpreference",
    "apitoken",
    "userfavourite",
    "totpbackupcode",
    "trusteddevice",
    "webauthncredential",
    "oidcidentity",
    "passwordresettoken",
    "integration",
    "server",
    "user",  # quoted below — reserved word in PostgreSQL
)


def wipe_demo_fleet(session: Session) -> None:
    """Remove fleet + users so re-seed is clean (FK-safe).

    Postgres: TRUNCATE … CASCADE (handles notification, push, etc.).
    SQLite / other: ordered ORM deletes (tests).
    """
    bind = session.get_bind()
    dialect = (bind.dialect.name if bind is not None else "") or ""
    if dialect == "postgresql":
        # Quote identifiers; "user" is reserved in PostgreSQL
        tables = ", ".join(f'"{t}"' for t in _PG_WIPE_TABLES)
        session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        session.commit()
        session.expire_all()
    else:
        # Children before parents
        for model in (
            NmapScriptResult,
            NmapDevice,
            NmapScanRun,
            NmapScanSchedule,
            PortAnnotation,
            ContainerAnnotationTag,
            ContainerAnnotation,
            VisualServiceStack,
            RuntimeEdge,
            ServiceDnsRecord,
            CertificateTarget,
            ManagedCertificate,
            IntegrationBinding,
            StackDeployment,
            DockerVersion,
            Notification,
            Job,
            AuditLog,
            PushSubscription,
            PushPreference,
            ApiToken,
            Integration,
            Server,
            UserFavourite,
            TotpBackupCode,
            TrustedDevice,
            WebAuthnCredential,
            OidcIdentity,
            PasswordResetToken,
            User,
        ):
            _delete_all(session, model)
    try:
        app_cfg.save_settings({SEED_MARKER_KEY: 0})
    except Exception:
        logger.exception("clear seed marker failed")


def _ensure_demo_user(session: Session, *, password: str, email: str) -> User:
    user = session.exec(select(User).where(User.email == email)).first()
    if user:
        user.hashed_password = get_password_hash(password)
        user.role = DEFAULT_DEMO_ROLE
        user.is_active = True
        user.must_change_password = False
        user.display_name = DEFAULT_DEMO_DISPLAY
        user.totp_enabled = False
        user.totp_secret_encrypted = None
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    user = User(
        email=email,
        hashed_password=get_password_hash(password),
        role=DEFAULT_DEMO_ROLE,
        is_active=True,
        must_change_password=False,
        display_name=DEFAULT_DEMO_DISPLAY,
        totp_enabled=False,
        password_login_enabled=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seed_settings() -> None:
    app_cfg.save_settings(
        {
            "force_2fa": False,
            "template_require_2fa": False,
            "timezone": "UTC",
            "dns_base_domain": "demo.invalid",
            "network_lan_subnet": "10.42.0.0/24",
            "network_gateway_ip": "10.42.0.1",
            "network_public_ip": "203.0.113.50",
            "os_check_global_enabled": False,
            "container_check_global_enabled": False,
            "webhook_enabled": False,
            "smtp_enabled": False,
            "smtp_alert_enabled": False,
            SEED_MARKER_KEY: DEMO_SEED_VERSION,
        }
    )


def seed_demo_fleet(
    session: Session,
    *,
    force: bool = False,
    password: Optional[str] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    """Populate synthetic fleet. Returns counts summary.

    **Safety:** refuses to run (including ``force`` wipe) unless
    ``PIHERDER_DEMO_MODE`` is true — never touch a production DB by accident.
    """
    if not demo_mode():
        raise RuntimeError(
            "Refusing demo seed: PIHERDER_DEMO_MODE is not enabled. "
            "This protects production fleets from wipe/seed. "
            "Only run on a dedicated demo instance with DEMO_MODE=true."
        )

    pw = (password or _env_password()).strip() or DEFAULT_DEMO_PASSWORD
    em = (email or _env_email()).strip() or DEFAULT_DEMO_EMAIL

    if force:
        wipe_demo_fleet(session)
    else:
        existing_servers = list(session.exec(select(Server)).all())
        if existing_servers:
            user = _ensure_demo_user(session, password=pw, email=em)
            return {
                "skipped": True,
                "reason": "servers_exist",
                "user_email": user.email,
                "servers": len(existing_servers),
            }

    user = _ensure_demo_user(session, password=pw, email=em)
    _seed_settings()

    now = datetime.utcnow()
    servers_by_key: dict[str, Server] = {}

    for spec in _host_specs():
        srv = Server(
            name=spec["name"],
            hostname=spec["hostname"],
            ip_address=spec["ip"],
            ssh_port=22,
            ssh_username="demo",
            ssh_public_key="ssh-ed25519 AAAADEMO_PLACEHOLDER_NOT_A_REAL_KEY demo@seed",
            os_type="debian",
            last_seen=now - timedelta(minutes=5),
            backup_enabled=spec["backup"],
            os_patch_enabled=spec["os_patch"],
            container_patch_enabled=spec["container_patch"],
            os_check_enabled=spec["os_patch"],
            container_check_enabled=spec["container_patch"],
            backup_paths=json.dumps(
                [
                    {
                        "source": "/home/demo/docker/",
                        "dest_name": None,
                        "enabled": True,
                    }
                ]
            ),
            docker_base_dir="~/docker",
            excluded_projects="[]",
            retention_days=7,
            sort_order=spec["sort"],
            last_backup_at=spec["last_backup"],
            last_os_check_at=now - timedelta(hours=12),
            os_updates_count=spec["os_updates"],
            reboot_pending=spec["name"] == "lab-media",
            os_updates_summary=f"{spec['os_updates']} packages (demo)",
            last_container_check_at=now - timedelta(hours=8),
            container_updates_count=spec["container_updates"],
            container_updates_summary=(
                f"{spec['container_updates']} image(s) (demo)"
                if spec["container_updates"]
                else "up to date (demo)"
            ),
            docker_inventory_json=spec["inventory"],
            docker_inventory_at=now - timedelta(hours=1),
            docker_inventory_status="ok" if spec["inventory"] else "never",
            host_deps_json=json.dumps(
                {
                    "checked_at": now.isoformat(),
                    "overall": "ok",
                    "checks": [
                        {"name": "ssh", "ok": True, "detail": "demo"},
                        {"name": "docker", "ok": True, "detail": "demo"},
                    ],
                    "features": {},
                }
            ),
            host_deps_checked_at=now - timedelta(hours=1),
            dns_name=spec["dns_name"],
            dns_manage_a=True,
        )
        session.add(srv)
        session.commit()
        session.refresh(srv)
        try:
            from . import ssh_identities as ident_svc

            ident_svc.ensure_fleet_identity(session, srv)
            session.commit()
        except Exception:
            pass
        servers_by_key[spec["name"]] = srv

    # Integrations (no real secrets)
    nmap = Integration(
        type="nmap",
        name="Demo LAN",
        base_url="",
        enabled=True,
        config_json=json.dumps(
            {
                "cidrs": ["10.42.0.0/24"],
                "excludes": [],
                "use_syn": False,
                "vuln_enabled": False,
            }
        ),
        last_status_json=json.dumps({"demo": True, "devices": 8}),
        last_polled_at=now - timedelta(hours=2),
    )
    session.add(nmap)
    session.add(
        Integration(
            type="pihole",
            name="Demo Pi-hole",
            base_url="http://10.42.0.10/admin/",
            enabled=True,
            config_json=json.dumps({"demo": True}),
            last_status_json=json.dumps({"status": "enabled", "demo": True}),
            last_polled_at=now - timedelta(hours=1),
        )
    )
    session.add(
        Integration(
            type="uptime_kuma",
            name="Demo Kuma",
            base_url="http://10.42.0.10:3001/",
            enabled=True,
            config_json=json.dumps({"demo": True}),
            last_status_json=json.dumps({"monitors_up": 12, "demo": True}),
            last_polled_at=now - timedelta(minutes=30),
        )
    )
    session.add(
        Integration(
            type="npm",
            name="Demo NPM",
            base_url="http://10.42.0.11:81/",
            enabled=True,
            config_json=json.dumps({"demo": True}),
            last_status_json=json.dumps({"proxy_hosts": 6, "demo": True}),
            last_polled_at=now - timedelta(hours=3),
        )
    )
    session.commit()
    session.refresh(nmap)

    # Nmap scan run + devices
    run = NmapScanRun(
        integration_id=nmap.id,
        intensity="discovery",
        status="success",
        hosts_up=8,
        hosts_total=10,
        ports_open=24,
        summary_json=json.dumps({"demo": True}),
        started_at=now - timedelta(hours=2, minutes=5),
        finished_at=now - timedelta(hours=2),
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    device_specs = [
        ("10.42.0.1", "gateway.demo", "gateway", "gateway", None, "Router"),
        ("10.42.0.10", "lab-core.demo", "linked", None, "lab-core", "lab-core"),
        ("10.42.0.11", "lab-edge.demo", "linked", None, "lab-edge", "lab-edge"),
        ("10.42.0.12", "lab-media.demo", "linked", None, "lab-media", "lab-media"),
        ("10.42.0.13", "lab-ha.demo", "linked", None, "lab-ha", "lab-ha"),
        ("10.42.0.20", "phone-alice", "known", None, None, "Phone"),
        ("10.42.0.21", "laptop-bob", "known", None, None, "Laptop"),
        ("10.42.0.30", "printer", "new", None, None, "Printer"),
    ]
    for ip, host, state, map_role, link_key, display in device_specs:
        linked = servers_by_key[link_key].id if link_key and link_key in servers_by_key else None
        session.add(
            NmapDevice(
                integration_id=nmap.id,
                identity_key=f"ip:{ip}",
                ip_address=ip,
                hostname=host,
                display_name=display,
                map_role=map_role,
                state=state,
                linked_server_id=linked,
                os_summary="Linux (demo)" if state == "linked" else None,
                ports_json=json.dumps(
                    [{"port": 22, "proto": "tcp", "state": "open", "service": "ssh"}]
                ),
                last_seen_at=now - timedelta(hours=2),
                last_run_id=run.id,
            )
        )
    session.commit()

    # DNS fabric samples
    core = servers_by_key["lab-core"]
    edge = servers_by_key["lab-edge"]
    for fqdn, backend, project, label in (
        ("pihole.demo.invalid", core, "pihole", "Pi-hole"),
        ("kuma.demo.invalid", core, "uptime-kuma", "Uptime Kuma"),
        ("ha.demo.invalid", servers_by_key["lab-ha"], "homeassistant", "Home Assistant"),
        ("grafana.demo.invalid", servers_by_key["lab-worker"], "grafana", "Grafana"),
        ("frigate.demo.invalid", servers_by_key["lab-media"], "frigate", "Frigate"),
    ):
        session.add(
            ServiceDnsRecord(
                fqdn=fqdn,
                record_type="cname",
                target_server_id=edge.id,
                backend_server_id=backend.id,
                docker_project=project,
                label=label,
                via_proxy=True,
                managed_on_pihole=False,
                external_dns_status="checklist",
            )
        )
    session.commit()

    # Runtime edges (stack dependencies)
    edges = [
        ("lab-media", "frigate", "lab-media", "mqtt", "depends_on"),
        ("lab-ha", "homeassistant", "lab-media", "mqtt", "talks_to"),
        ("lab-core", "piherder", "lab-core", "pihole", "talks_to"),
        ("lab-edge", "npm", "lab-core", "pihole", "talks_to"),
    ]
    for fk, fp, tk, tp, kind in edges:
        session.add(
            RuntimeEdge(
                from_server_id=servers_by_key[fk].id,
                from_project=fp,
                to_server_id=servers_by_key[tk].id,
                to_project=tp,
                kind=kind,
                source="manual",
                confidence=90,
                note="demo seed",
            )
        )
    session.commit()

    # Jobs + audit history
    job_plan = [
        ("lab-core", "backup", "success", 2, "Backup complete (demo)"),
        ("lab-core", "os_update_check", "success", 12, "3 packages (demo)"),
        ("lab-edge", "backup", "success", 20, "Backup complete (demo)"),
        ("lab-media", "backup", "failed", 26, "Demo simulated failure"),
        ("lab-media", "container_update_check", "success", 8, "2 images (demo)"),
        ("lab-ha", "backup", "success", 6, "Backup complete (demo)"),
        ("lab-worker", "docker_stack_restart", "success", 4, "Stack restarted (demo)"),
        ("lab-core", "os_patch", "success", 48, "OS patch simulated (demo)"),
    ]
    for host_key, jtype, status, hours_ago, summary in job_plan:
        srv = servers_by_key[host_key]
        started = now - timedelta(hours=hours_ago)
        finished = started + timedelta(minutes=3)
        details = {
            "current": status,
            "status": status,
            "summary": summary,
            "done": True,
            "demo": True,
            "log_lines": [summary, "Demo seed history"],
        }
        job = Job(
            server_id=srv.id,
            job_type=jtype,
            status=status,
            created_at=started,
            started_at=started,
            finished_at=finished,
            details=json.dumps(details),
        )
        session.add(job)
        session.add(
            AuditLog(
                user_id=user.id,
                server_id=srv.id,
                action=jtype,
                status=status,
                details=f"Demo seed · {summary}",
                started_at=started,
                finished_at=finished,
                client_ip="10.42.0.99",
            )
        )
    session.add(
        AuditLog(
            user_id=user.id,
            action="demo_seed",
            status="success",
            details=f"Demo fleet seeded v{DEMO_SEED_VERSION}",
            started_at=now,
            finished_at=now,
        )
    )
    session.commit()

    summary = {
        "skipped": False,
        "user_email": em,
        "servers": len(servers_by_key),
        "integrations": 4,
        "jobs": len(job_plan),
        "seed_version": DEMO_SEED_VERSION,
    }
    logger.info("demo seed complete: %s", summary)
    return summary


def ensure_demo_seeded(session: Session) -> Optional[dict[str, Any]]:
    """Lifespan hook: if demo mode and empty fleet, seed once."""
    if not demo_mode():
        return None
    if session.exec(select(Server)).first():
        # Still refresh demo password/email if user exists
        try:
            _ensure_demo_user(session, password=_env_password(), email=_env_email())
        except Exception:
            logger.exception("demo user refresh failed")
        return None
    logger.info("Demo mode with empty fleet — running seed")
    return seed_demo_fleet(session, force=False)
