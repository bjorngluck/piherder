"""
Herder self-backup service.

Two modes (archives under HERDER_BACKUP_ROOT):

**Full (DR)** — complete Postgres ``pg_dump -Fc`` of **every table** + DATA_ROOT
avatars/logos. This is the real disaster-recovery artifact (no row caps).

**Config only** — lightweight JSON row snapshots of control-plane tables (no full
audit dump, no pg_dump). Fine for quick config copy; not a substitute for full.

Restore: full archives use ``pg_restore``; older/config-only JSON archives use
row upsert (v1–v5). Same ``PIHERDER_MASTER_KEY`` required for Fernet fields.

Not included (separate products):

- Per-server rsync trees (host backups)
- Nmap raw XML under DATA_ROOT/nmap (optional; paths host-local)
"""

import json
import tarfile
import tempfile
import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Type, Set
import logging
from urllib.parse import urlparse, unquote

from ..config import settings
from ..database import engine
from sqlmodel import Session, select, SQLModel
from ..models import (
    Server,
    AuditLog,
    User,
    UserFavourite,
    ApiToken,
    Job,
    DockerVersion,
    TotpBackupCode,
    TrustedDevice,
    WebAuthnCredential,
    Notification,
    PushSubscription,
    PushPreference,
    PushVapidConfig,
    Integration,
    IntegrationBinding,
    ServiceTemplate,
    StackDeployment,
    NmapScanSchedule,
    NmapScanRun,
    NmapDevice,
    NmapScriptResult,
)
from .app_settings import load_settings

logger = logging.getLogger(__name__)

# Config-only JSON payload version (full DR uses kind=pg_dump_full, version 6)
BACKUP_FORMAT_VERSION = "5"
FULL_BACKUP_FORMAT_VERSION = "6"

# Config-only JSON caps (full mode uses pg_dump — no caps)
_JOB_SNAPSHOT_LIMIT = 50_000
_NMAP_RUN_SNAPSHOT_LIMIT = 10_000
_AUDIT_SNAPSHOT_LIMIT = 50_000
_NOTIFICATION_SNAPSHOT_LIMIT = 50_000

# Relationship / non-column keys to drop from model_dump
_EXCLUDE_REL = {
    User: {"audit_logs", "totp_backup_codes", "trusted_devices", "webauthn_credentials"},
    Server: {"audit_logs", "jobs", "docker_versions"},
    DockerVersion: {"server"},
    Job: {"server"},
    TotpBackupCode: {"user"},
    TrustedDevice: {"user"},
    WebAuthnCredential: {"user"},
    AuditLog: {"user", "server"},
    ServiceTemplate: {"deployments"},
    StackDeployment: {"template"},
}

HERDER_BACKUP_DIR = Path(settings.HERDER_BACKUP_ROOT)


def _path_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".perm_test"
        test.write_text("1")
        test.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def archive_dir_candidates() -> List[Path]:
    """Preferred order for self-backup .tar.gz storage (compose mounts + data fallback)."""
    data = Path(settings.DATA_ROOT or "/data")
    return [
        Path(settings.HERDER_BACKUP_ROOT),
        Path("/herder_backups"),
        Path("/backups/piherder_backups"),
        Path("/backups"),
        data / "herder_backups",
    ]


def _ensure_dir():
    """Resolve a writable directory for self-backup archives."""
    global HERDER_BACKUP_DIR
    if _path_is_writable(HERDER_BACKUP_DIR):
        return
    for cand in archive_dir_candidates():
        if _path_is_writable(cand):
            if cand != HERDER_BACKUP_DIR:
                logger.warning(
                    "Herder backup dir %s not writable; using %s",
                    HERDER_BACKUP_DIR,
                    cand,
                )
            HERDER_BACKUP_DIR = cand
            return
    logger.error(
        "No writable herder backup directory among %s",
        [str(p) for p in archive_dir_candidates()],
    )


def prune_old_backups(keep: int):
    _ensure_dir()
    files = sorted(HERDER_BACKUP_DIR.glob("piherder-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    removed = 0
    for p in files[:-keep]:
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    if removed:
        logger.info(f"Pruned {removed} old herder backups (keep={keep})")


def _model_to_dict(row: SQLModel) -> Dict[str, Any]:
    """Serialize a table row; drop relationship attrs; datetimes via default=str later."""
    exclude = _EXCLUDE_REL.get(type(row), set())
    try:
        d = row.model_dump(exclude=exclude)
    except TypeError:
        d = row.model_dump()
        for k in exclude:
            d.pop(k, None)
    # Drop any accidental non-column keys
    for k in list(d.keys()):
        if k.startswith("_"):
            d.pop(k, None)
    return d


def _snapshot_table(model: Type[SQLModel], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    try:
        with Session(engine) as s:
            q = select(model)
            rows = s.exec(q).all()
            if limit is not None:
                rows = rows[:limit]
            return [_model_to_dict(r) for r in rows]
    except Exception as e:
        # Missing table (pre-migration) or transient DB — skip rather than fail backup
        logger.warning("Snapshot %s skipped: %s", getattr(model, "__name__", model), e)
        return []


def _snapshot_servers() -> List[Dict[str, Any]]:
    return _snapshot_table(Server)


def _snapshot_users() -> List[Dict[str, Any]]:
    """Full user rows: password hashes + encrypted TOTP secret (never plaintext)."""
    return _snapshot_table(User)


def _snapshot_docker_versions() -> List[Dict[str, Any]]:
    return _snapshot_table(DockerVersion)


def _snapshot_totp_backup_codes() -> List[Dict[str, Any]]:
    return _snapshot_table(TotpBackupCode)


def _snapshot_trusted_devices() -> List[Dict[str, Any]]:
    return _snapshot_table(TrustedDevice)


def _snapshot_webauthn_credentials() -> List[Dict[str, Any]]:
    return _snapshot_table(WebAuthnCredential)


def _snapshot_push_vapid() -> List[Dict[str, Any]]:
    return _snapshot_table(PushVapidConfig)


def _snapshot_push_subscriptions() -> List[Dict[str, Any]]:
    return _snapshot_table(PushSubscription)


def _snapshot_push_preferences() -> List[Dict[str, Any]]:
    return _snapshot_table(PushPreference)


def _snapshot_notifications(limit: int = _NOTIFICATION_SNAPSHOT_LIMIT) -> List[Dict[str, Any]]:
    with Session(engine) as s:
        rows = s.exec(
            select(Notification).order_by(Notification.created_at.desc()).limit(limit)
        ).all()
        return [_model_to_dict(r) for r in rows]


def _database_url() -> str:
    return (getattr(settings, "DATABASE_URL", None) or os.environ.get("DATABASE_URL") or "").strip()


def _pg_dump_available() -> bool:
    return bool(shutil.which("pg_dump") and shutil.which("pg_restore"))


def _pg_env_and_uri() -> tuple[dict, str]:
    """Env for libpq + connection URI for pg_dump/pg_restore."""
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    env = os.environ.copy()
    # libpq accepts postgresql://…; also set PGPASSWORD if present in URI
    try:
        parsed = urlparse(url)
        if parsed.password:
            env["PGPASSWORD"] = unquote(parsed.password)
    except Exception:
        pass
    return env, url


def create_pg_dump(dest: Path) -> Path:
    """Write a custom-format (``-Fc``) dump of the entire database to *dest*."""
    if not shutil.which("pg_dump"):
        raise RuntimeError(
            "pg_dump not found — install postgresql-client in the web image for full DR backups"
        )
    env, url = _pg_env_and_uri()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(dest),
        url,
    ]
    logger.info("Running pg_dump → %s", dest)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {err[:500]}")
    if not dest.is_file() or dest.stat().st_size < 100:
        raise RuntimeError(f"pg_dump produced empty/missing file: {dest}")
    return dest


def restore_pg_dump(dump_path: Path) -> None:
    """Restore a custom-format dump into DATABASE_URL (destructive --clean)."""
    if not shutil.which("pg_restore"):
        raise RuntimeError("pg_restore not found — install postgresql-client")
    dump_path = Path(dump_path)
    if not dump_path.is_file():
        raise FileNotFoundError(dump_path)
    env, url = _pg_env_and_uri()
    # Drop other sessions so --clean can replace objects
    try:
        from sqlalchemy import text as sa_text

        with Session(engine) as s:
            s.execute(
                sa_text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
            s.commit()
    except Exception as e:
        logger.warning("Could not terminate other DB sessions before restore: %s", e)

    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
        "--dbname",
        url,
        str(dump_path),
    ]
    logger.info("Running pg_restore ← %s", dump_path)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    # pg_restore returns non-zero on some non-fatal errors; check stderr hard fails
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # Still often usable if tables restored — log and re-check
        logger.error("pg_restore exit %s: %s", proc.returncode, err[:800])
        # Fatal if empty
        if "error:" in err.lower() and "FATAL" in err:
            raise RuntimeError(f"pg_restore failed: {err[:500]}")
        # Many "errors" are ignoreable with --clean; verify core table
        with Session(engine) as s:
            from sqlalchemy import text as sa_text

            n = s.execute(sa_text('SELECT count(*) FROM "user"')).scalar()
            if n is None:
                raise RuntimeError(f"pg_restore failed and user table missing: {err[:500]}")
        logger.warning("pg_restore completed with warnings (exit %s)", proc.returncode)


def _snapshot_integrations() -> List[Dict[str, Any]]:
    return _snapshot_table(Integration)


def _snapshot_integration_bindings() -> List[Dict[str, Any]]:
    return _snapshot_table(IntegrationBinding)


def _snapshot_managed_certificates() -> List[Dict[str, Any]]:
    from ..models import ManagedCertificate

    return _snapshot_table(ManagedCertificate)


def _snapshot_certificate_targets() -> List[Dict[str, Any]]:
    from ..models import CertificateTarget

    return _snapshot_table(CertificateTarget)


def _snapshot_service_templates() -> List[Dict[str, Any]]:
    return _snapshot_table(ServiceTemplate)


def _snapshot_stack_deployments() -> List[Dict[str, Any]]:
    return _snapshot_table(StackDeployment)


def _snapshot_service_dns_records() -> List[Dict[str, Any]]:
    from ..models import ServiceDnsRecord

    return _snapshot_table(ServiceDnsRecord)


def _snapshot_runtime_edges() -> List[Dict[str, Any]]:
    from ..models import RuntimeEdge

    return _snapshot_table(RuntimeEdge)


def _snapshot_topology_categories() -> List[Dict[str, Any]]:
    from ..models import TopologyCategory

    return _snapshot_table(TopologyCategory)


def _snapshot_topology_tags() -> List[Dict[str, Any]]:
    from ..models import TopologyTag

    return _snapshot_table(TopologyTag)


def _snapshot_visual_service_stacks() -> List[Dict[str, Any]]:
    from ..models import VisualServiceStack

    return _snapshot_table(VisualServiceStack)


def _snapshot_container_annotations() -> List[Dict[str, Any]]:
    from ..models import ContainerAnnotation

    return _snapshot_table(ContainerAnnotation)


def _snapshot_container_annotation_tags() -> List[Dict[str, Any]]:
    from ..models import ContainerAnnotationTag

    return _snapshot_table(ContainerAnnotationTag)


def _snapshot_port_annotations() -> List[Dict[str, Any]]:
    from ..models import PortAnnotation

    return _snapshot_table(PortAnnotation)


def _snapshot_user_favourites() -> List[Dict[str, Any]]:
    return _snapshot_table(UserFavourite)


def _snapshot_api_tokens() -> List[Dict[str, Any]]:
    return _snapshot_table(ApiToken)


def _snapshot_jobs(limit: int = _JOB_SNAPSHOT_LIMIT) -> List[Dict[str, Any]]:
    """All job rows (newest first, safety cap). Required for full DR of Jobs UI."""
    with Session(engine) as s:
        rows = s.exec(select(Job).order_by(Job.id.desc()).limit(limit)).all()
        # restore prefers stable id order
        rows = list(reversed(list(rows)))
        return [_model_to_dict(r) for r in rows]


def _snapshot_nmap_scan_schedules() -> List[Dict[str, Any]]:
    return _snapshot_table(NmapScanSchedule)


def _snapshot_nmap_scan_runs(limit: int = _NMAP_RUN_SNAPSHOT_LIMIT) -> List[Dict[str, Any]]:
    with Session(engine) as s:
        rows = s.exec(select(NmapScanRun).order_by(NmapScanRun.id.desc()).limit(limit)).all()
        rows = list(reversed(list(rows)))
        return [_model_to_dict(r) for r in rows]


def _snapshot_nmap_devices() -> List[Dict[str, Any]]:
    return _snapshot_table(NmapDevice)


def _snapshot_nmap_script_results() -> List[Dict[str, Any]]:
    return _snapshot_table(NmapScriptResult)


def _jobs_for_restore(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clear live Celery linkage; in-flight rows become cancelled after restore."""
    out: List[Dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["celery_task_id"] = None
        st = (row.get("status") or "").lower()
        if st in ("pending", "running"):
            row["status"] = "cancelled"
            det = row.get("details")
            note = "cancelled on herder restore (was in-flight)"
            if isinstance(det, str) and det.strip().startswith("{"):
                try:
                    import json as _json

                    d = _json.loads(det)
                    if isinstance(d, dict):
                        d["restore_note"] = note
                        row["details"] = _json.dumps(d)
                    else:
                        row["details"] = note
                except Exception:
                    row["details"] = note
            elif det:
                row["details"] = f"{det} · {note}"
            else:
                row["details"] = note
        out.append(row)
    return out


def _snapshot_audit(
    since_days: Optional[int] = None, limit: int = _AUDIT_SNAPSHOT_LIMIT
) -> List[Dict[str, Any]]:
    """Config-only path only — full DR uses pg_dump (no cap)."""
    with Session(engine) as s:
        q = select(AuditLog).order_by(AuditLog.started_at.desc())
        if since_days:
            cutoff = datetime.utcnow() - timedelta(days=since_days)
            q = q.where(AuditLog.started_at >= cutoff)
        rows = s.exec(q.limit(limit)).all()
        return [_model_to_dict(r) for r in rows]


def _avatar_files() -> List[Path]:
    """List avatar files under DATA_ROOT (relative paths preserved in tar)."""
    root = Path(settings.DATA_ROOT or "/data")
    avatars = root / "avatars"
    if not avatars.is_dir():
        return []
    out: List[Path] = []
    for p in avatars.rglob("*"):
        if p.is_file() and p.stat().st_size <= settings.AVATAR_MAX_BYTES * 2:
            out.append(p)
        if len(out) >= 500:
            break
    return out


def _service_logo_files() -> List[Path]:
    """List service logo files under DATA_ROOT/service_logos (B08)."""
    root = Path(settings.DATA_ROOT or "/data")
    logos = root / "service_logos"
    if not logos.is_dir():
        return []
    max_bytes = min(getattr(settings, "AVATAR_MAX_BYTES", 2 * 1024 * 1024), 512 * 1024) * 2
    out: List[Path] = []
    for p in logos.iterdir():
        if p.is_file() and p.stat().st_size <= max_bytes:
            out.append(p)
        if len(out) >= 500:
            break
    return out


def _build_backup_payload(
    *,
    include_audit: bool = False,
    config_only: bool = True,
    since_days: int = 90,
) -> Dict[str, Any]:
    """Assemble the JSON payload (no filesystem side effects). Used by create + tests."""
    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "config_only": config_only,
        "include_audit": include_audit,
        "version": BACKUP_FORMAT_VERSION,
        "includes": [
            "servers",
            "users",
            "user_favourites",
            "api_tokens",
            "totp_backup_codes",
            "trusted_devices",
            "webauthn_credentials",
            "docker_versions",
            "push_vapid",
            "push_subscriptions",
            "push_preferences",
            "notifications",
            "integrations",
            "integration_bindings",
            "managed_certificates",
            "certificate_targets",
            "service_templates",
            "stack_deployments",
            "service_dns_records",
            "runtime_edges",
            "topology_categories",
            "topology_tags",
            "visual_service_stacks",
            "container_annotations",
            "container_annotation_tags",
            "port_annotations",
            "jobs",
            "nmap_scan_schedules",
            "nmap_scan_runs",
            "nmap_devices",
            "nmap_script_results",
            "herder_config",
            "avatars",
            "service_logos",
        ]
        + (["audit_logs"] if include_audit else []),
        "excludes": [
            "password_reset_tokens",  # short-lived; useless after restore
            "appsetting_raw",  # covered via herder_config JSON
            "nmap_xml_artifacts",  # DATA_ROOT/nmap binaries; paths may be host-local
            "rsync_trees",  # per-server backup product, not herder self-backup
        ],
        "note": "Encrypted fields need the same PIHERDER_MASTER_KEY on restore. Jobs + nmap scan runs included (v5+).",
    }
    data: Dict[str, Any] = {
        "manifest": manifest,
        "servers": _snapshot_servers(),
        "users": _snapshot_users(),
        "user_favourites": _snapshot_user_favourites(),
        "api_tokens": _snapshot_api_tokens(),
        "jobs": _snapshot_jobs(),
        "totp_backup_codes": _snapshot_totp_backup_codes(),
        "trusted_devices": _snapshot_trusted_devices(),
        "webauthn_credentials": _snapshot_webauthn_credentials(),
        "docker_versions": _snapshot_docker_versions(),
        "push_vapid": _snapshot_push_vapid(),
        "push_subscriptions": _snapshot_push_subscriptions(),
        "push_preferences": _snapshot_push_preferences(),
        "notifications": _snapshot_notifications(),
        "integrations": _snapshot_integrations(),
        "integration_bindings": _snapshot_integration_bindings(),
        "managed_certificates": _snapshot_managed_certificates(),
        "certificate_targets": _snapshot_certificate_targets(),
        "service_templates": _snapshot_service_templates(),
        "stack_deployments": _snapshot_stack_deployments(),
        "service_dns_records": _snapshot_service_dns_records(),
        "runtime_edges": _snapshot_runtime_edges(),
        "topology_categories": _snapshot_topology_categories(),
        "topology_tags": _snapshot_topology_tags(),
        "visual_service_stacks": _snapshot_visual_service_stacks(),
        "container_annotations": _snapshot_container_annotations(),
        "container_annotation_tags": _snapshot_container_annotation_tags(),
        "port_annotations": _snapshot_port_annotations(),
        "nmap_scan_schedules": _snapshot_nmap_scan_schedules(),
        "nmap_scan_runs": _snapshot_nmap_scan_runs(),
        "nmap_devices": _snapshot_nmap_devices(),
        "nmap_script_results": _snapshot_nmap_script_results(),
        "herder_config": load_settings(),
    }
    if include_audit:
        data["audit_logs"] = _snapshot_audit(
            since_days=since_days if not config_only else 7
        )
    return data


def _add_data_files_to_tar(tar: tarfile.TarFile) -> int:
    """Add avatars + service logos under data/. Returns file count."""
    n = 0
    data_root = Path(settings.DATA_ROOT or "/data")
    for ap in _avatar_files():
        try:
            rel = ap.relative_to(data_root)
        except ValueError:
            rel = Path("avatars") / ap.name
        tar.add(ap, arcname=str(Path("data") / rel))
        n += 1
    for lp in _service_logo_files():
        try:
            rel = lp.relative_to(data_root)
        except ValueError:
            rel = Path("service_logos") / lp.name
        tar.add(lp, arcname=str(Path("data") / rel))
        n += 1
    return n


def create_herder_backup(
    include_audit: bool = False, config_only: bool = True, since_days: int = 90
) -> Path:
    """
    Create a compressed self-backup under HERDER_BACKUP_ROOT.

    - **config_only**: lightweight JSON row snapshots (not full DR).
    - **full** (``config_only=False``): complete ``pg_dump -Fc`` of the whole DB
      + DATA_ROOT files — real disaster recovery, no row caps.
    """
    global HERDER_BACKUP_DIR
    _ensure_dir()
    cfg = load_settings()
    keep = int(cfg.get("keep", 10))

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    is_full = not config_only
    suffix = "config-only" if config_only else ("full" if include_audit or is_full else "config")
    if is_full:
        suffix = "full"
    filename = f"piherder-{ts}-{suffix}.tar.gz"
    out_path = HERDER_BACKUP_DIR / filename

    tmp_paths: List[Path] = []
    try:
        if is_full:
            # ── Full DR: entire Postgres + data files ─────────────────────
            dump_path = Path(tempfile.mkstemp(prefix="piherder-pg-", suffix=".dump")[1])
            tmp_paths.append(dump_path)
            create_pg_dump(dump_path)

            manifest = {
                "created_at": datetime.utcnow().isoformat() + "Z",
                "config_only": False,
                "include_audit": True,  # whole DB includes audit
                "version": FULL_BACKUP_FORMAT_VERSION,
                "kind": "pg_dump_full",
                "pg_dump_format": "custom",
                "pg_dump_arcname": "database.dump",
                "includes": ["database.dump", "data/avatars", "data/service_logos"],
                "excludes": ["rsync_trees", "nmap_xml_artifacts"],
                "note": (
                    "Full DR archive: pg_dump -Fc of entire DATABASE_URL + DATA_ROOT files. "
                    "Restore with herder restore or pg_restore. Same PIHERDER_MASTER_KEY required."
                ),
            }
            man_path = Path(tempfile.mkstemp(prefix="piherder-man-", suffix=".json")[1])
            tmp_paths.append(man_path)
            man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            def _write_full_tar(dest: Path) -> None:
                with tarfile.open(dest, "w:gz") as tar:
                    tar.add(man_path, arcname="manifest.json")
                    tar.add(dump_path, arcname="database.dump")
                    # Keep a tiny pointer for older UI that expects JSON name
                    tar.add(man_path, arcname="piherder-backup.json")
                    _add_data_files_to_tar(tar)

            write_fn = _write_full_tar
        else:
            # ── Config-only: JSON snapshots (lighter, not complete DR) ────
            data = _build_backup_payload(
                include_audit=include_audit,
                config_only=True,
                since_days=since_days,
            )
            data["manifest"]["kind"] = "json_config"
            tmp_json_path = Path(
                tempfile.mkstemp(prefix="piherder-json-", suffix=".json")[1]
            )
            tmp_paths.append(tmp_json_path)
            tmp_json_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )

            def _write_json_tar(dest: Path) -> None:
                with tarfile.open(dest, "w:gz") as tar:
                    tar.add(tmp_json_path, arcname="piherder-backup.json")
                    _add_data_files_to_tar(tar)

            write_fn = _write_json_tar

        try:
            write_fn(out_path)
        except Exception as e:
            if (
                isinstance(e, PermissionError)
                or "Permission" in str(e)
                or not out_path.parent.exists()
                or not os.access(str(out_path.parent), os.W_OK)
            ):
                wrote = False
                last_err: Exception = e
                for fb_dir in archive_dir_candidates():
                    if fb_dir == out_path.parent:
                        continue
                    if not _path_is_writable(fb_dir):
                        continue
                    try:
                        out_path = fb_dir / filename
                        write_fn(out_path)
                        HERDER_BACKUP_DIR = fb_dir
                        logger.warning(
                            "Herder backup dir not writable, fell back to %s", out_path
                        )
                        wrote = True
                        break
                    except Exception as e2:
                        last_err = e2
                        continue
                if not wrote:
                    raise last_err
            else:
                raise

        logger.info("Herder backup created: %s (full=%s)", out_path, is_full)
        prune_old_backups(keep)
        return out_path
    finally:
        for p in tmp_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def list_backups() -> List[Dict[str, Any]]:
    _ensure_dir()
    candidates = list(HERDER_BACKUP_DIR.glob("piherder-*.tar.gz"))
    # also scan known locations (perms fixed later, or previous fallback wrote elsewhere)
    for extra in archive_dir_candidates():
        if extra != HERDER_BACKUP_DIR:
            try:
                candidates.extend(extra.glob("piherder-*.tar.gz"))
            except Exception:
                pass
    seen = set()
    out = []
    for p in sorted((c for c in candidates if c.exists()), key=lambda x: x.stat().st_mtime, reverse=True):
        if str(p) not in seen:
            seen.add(str(p))
            stat = p.stat()
            out.append({
                "path": str(p),
                "name": p.name,
                "size": stat.st_size,
                # UTC ISO so UI data-utc / app timezone conversion is correct
                "mtime": datetime.utcfromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            })
    return out


def _parse_dt(val: Any) -> Any:
    """Coerce ISO strings back to naive UTC datetime for SQLModel fields."""
    if val is None or isinstance(val, datetime):
        if isinstance(val, datetime) and val.tzinfo is not None:
            return val.replace(tzinfo=None)
        return val
    if isinstance(val, str) and val:
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except Exception:
            return val
    return val


def _column_names(model: Type[SQLModel]) -> Set[str]:
    try:
        return set(model.model_fields.keys())  # type: ignore[attr-defined]
    except Exception:
        return set(getattr(model, "__fields__", {}).keys())


def _clean_row(model: Type[SQLModel], raw: Dict[str, Any]) -> Dict[str, Any]:
    cols = _column_names(model)
    out: Dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k not in cols:
            continue
        if k in ("created_at", "updated_at", "started_at", "finished_at", "dismissed_at",
                 "resolved_at", "read_at", "used_at", "expires_at", "last_used_at",
                 "last_success_at", "disabled_at", "totp_confirmed_at", "last_seen",
                 "last_backup_at", "last_os_check_at", "last_container_check_at",
                 "docker_inventory_at"):
            out[k] = _parse_dt(v)
        else:
            out[k] = v
    return out


def _upsert_rows(
    session: Session,
    model: Type[SQLModel],
    rows: List[Dict[str, Any]],
    *,
    prefer_keep_id: bool = True,
) -> int:
    """Upsert by primary key id when present; else insert without id."""
    count = 0
    for raw in rows or []:
        data = _clean_row(model, raw)
        if not data:
            continue
        rid = data.get("id")
        existing = session.get(model, rid) if rid is not None else None
        if existing:
            for k, v in data.items():
                if k == "id":
                    continue
                if hasattr(existing, k):
                    setattr(existing, k, v)
            session.add(existing)
        else:
            if prefer_keep_id and rid is not None:
                obj = model(**data)
            else:
                data.pop("id", None)
                obj = model(**data)
            session.add(obj)
        count += 1
    return count


def _upsert_users(session: Session, rows: List[Dict[str, Any]]) -> int:
    """Upsert users by id, then by email if id missing."""
    count = 0
    for raw in rows or []:
        data = _clean_row(User, raw)
        if not data.get("email") and not data.get("id"):
            continue
        existing = None
        if data.get("id") is not None:
            existing = session.get(User, data["id"])
        if existing is None and data.get("email"):
            existing = session.exec(
                select(User).where(User.email == data["email"])
            ).first()
        if existing:
            for k, v in data.items():
                if k == "id":
                    continue
                if hasattr(existing, k):
                    setattr(existing, k, v)
            session.add(existing)
        else:
            # Ensure required fields
            if "hashed_password" not in data or not data["hashed_password"]:
                # Old v1 backups had no password — skip incomplete rows
                if "email" not in data:
                    continue
                logger.warning(
                    "Skipping user restore without hashed_password: %s",
                    data.get("email"),
                )
                continue
            if data.get("id") is None:
                data.pop("id", None)
            session.add(User(**data))
        count += 1
    return count


def _upsert_push_vapid(session: Session, rows: List[Dict[str, Any]]) -> int:
    """Restore VAPID singleton carefully (keep encrypted private key)."""
    if not rows:
        return 0
    count = 0
    for raw in rows:
        data = _clean_row(PushVapidConfig, raw)
        if not data.get("public_key") or not data.get("private_key_encrypted"):
            continue
        rid = data.get("id")
        existing = session.get(PushVapidConfig, rid) if rid is not None else None
        if existing is None:
            # Prefer single row: update first if any
            existing = session.exec(select(PushVapidConfig)).first()
        if existing:
            for k, v in data.items():
                if k == "id":
                    continue
                setattr(existing, k, v)
            session.add(existing)
        else:
            data.pop("id", None)
            session.add(PushVapidConfig(**data))
        count += 1
    return count


def _upsert_push_subscriptions(session: Session, rows: List[Dict[str, Any]]) -> int:
    """Upsert by endpoint (unique) or id."""
    count = 0
    for raw in rows or []:
        data = _clean_row(PushSubscription, raw)
        if not data.get("endpoint") or not data.get("user_id"):
            continue
        existing = None
        if data.get("id") is not None:
            existing = session.get(PushSubscription, data["id"])
        if existing is None:
            existing = session.exec(
                select(PushSubscription).where(
                    PushSubscription.endpoint == data["endpoint"]
                )
            ).first()
        if existing:
            for k, v in data.items():
                if k == "id":
                    continue
                setattr(existing, k, v)
            session.add(existing)
        else:
            data.pop("id", None)
            session.add(PushSubscription(**data))
        count += 1
    return count


def _upsert_push_preferences(session: Session, rows: List[Dict[str, Any]]) -> int:
    count = 0
    for raw in rows or []:
        data = _clean_row(PushPreference, raw)
        if data.get("user_id") is None:
            continue
        existing = session.exec(
            select(PushPreference).where(PushPreference.user_id == data["user_id"])
        ).first()
        if existing:
            for k, v in data.items():
                if k == "id":
                    continue
                setattr(existing, k, v)
            session.add(existing)
        else:
            data.pop("id", None)
            session.add(PushPreference(**data))
        count += 1
    return count


def _append_notifications(session: Session, rows: List[Dict[str, Any]]) -> int:
    """Append notifications missing the same fingerprint+status (avoid dupes)."""
    count = 0
    for raw in rows or []:
        data = _clean_row(Notification, raw)
        fp = data.get("fingerprint")
        if not fp:
            continue
        exists = session.exec(
            select(Notification).where(
                Notification.fingerprint == fp,
                Notification.status == data.get("status", "open"),
            )
        ).first()
        if exists:
            continue
        data.pop("id", None)
        session.add(Notification(**data))
        count += 1
    return count


def _append_audit(session: Session, rows: List[Dict[str, Any]]) -> int:
    count = 0
    for al in rows or []:
        data = _clean_row(AuditLog, al)
        exists = session.exec(
            select(AuditLog).where(
                AuditLog.started_at == data.get("started_at"),
                AuditLog.action == data.get("action"),
                AuditLog.server_id == data.get("server_id"),
            )
        ).first()
        if exists:
            continue
        data.pop("id", None)
        session.add(AuditLog(**data))
        count += 1
    return count


def _restore_avatars_from_tar(archive_path: Path) -> int:
    """Extract data/* members into DATA_ROOT (avatars + service logos). Returns file count."""
    data_root = Path(settings.DATA_ROOT or "/data")
    data_root.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(archive_path, "r:gz") as tar:
        for m in tar.getmembers():
            name = m.name.replace("\\", "/")
            if not name.startswith("data/") or m.isdir():
                continue
            # Prevent path traversal
            rel = name[len("data/") :]
            if ".." in rel.split("/"):
                continue
            dest = data_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            f = tar.extractfile(m)
            if not f:
                continue
            with open(dest, "wb") as out:
                shutil.copyfileobj(f, out)
            n += 1
    return n


def restore_herder_backup(
    archive_path: str, restore_audit: bool = False, dry_run: bool = False
) -> Dict[str, Any]:
    """
    Careful restore from archive (format v1–v5).

    - Users, favourites, API tokens, servers, docker versions, 2FA, trusted devices
    - Jobs (v5+; older archives may omit)
    - Push, notifications, integrations, certs, templates, topology, nmap inventory
    - Nmap scan runs + script results (v5+; older archives null last_run_id)
    - Encrypted fields travel as-is (master key must match)
    - Herder settings JSON merged into live config
    - Avatars + service logos extracted into DATA_ROOT
    - Audit optional append-only
    """
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(p)

    result: Dict[str, Any] = {
        "restored_servers": 0,
        "restored_users": 0,
        "restored_user_favourites": 0,
        "restored_api_tokens": 0,
        "restored_jobs": 0,
        "restored_docker_versions": 0,
        "restored_totp_codes": 0,
        "restored_trusted_devices": 0,
        "restored_webauthn_credentials": 0,
        "restored_push_vapid": 0,
        "restored_push_subscriptions": 0,
        "restored_push_preferences": 0,
        "restored_notifications": 0,
        "restored_integrations": 0,
        "restored_integration_bindings": 0,
        "restored_managed_certificates": 0,
        "restored_certificate_targets": 0,
        "restored_service_templates": 0,
        "restored_stack_deployments": 0,
        "restored_nmap_scan_schedules": 0,
        "restored_nmap_scan_runs": 0,
        "restored_nmap_devices": 0,
        "restored_nmap_script_results": 0,
        "restored_avatars": 0,
        "restored_herder_config": False,
        "restored_audit": 0,
        "restored_pg_dump": False,
        "dry_run": dry_run,
        "format_version": None,
        "kind": None,
    }

    # ── Full DR path: database.dump (pg_dump -Fc) ─────────────────────────
    with tarfile.open(p, "r:gz") as tar:
        names = set(tar.getnames())
        has_pg = "database.dump" in names
        man_member = None
        if "manifest.json" in names:
            man_member = tar.extractfile("manifest.json")
        elif "piherder-backup.json" in names:
            man_member = tar.extractfile("piherder-backup.json")
        manifest: Dict[str, Any] = {}
        if man_member:
            try:
                raw_man = json.loads(man_member.read())
                # v6 full: whole file is manifest; v1–5: manifest nested
                if isinstance(raw_man, dict) and (
                    raw_man.get("kind") == "pg_dump_full" or "manifest" not in raw_man
                ):
                    if raw_man.get("kind") == "pg_dump_full" or raw_man.get("pg_dump_arcname"):
                        manifest = raw_man
                    elif "manifest" in raw_man:
                        manifest = raw_man.get("manifest") or {}
                    else:
                        # ambiguous small JSON
                        manifest = raw_man if "version" in raw_man else {}
                elif isinstance(raw_man, dict):
                    manifest = raw_man.get("manifest") or {}
            except Exception:
                manifest = {}
        result["format_version"] = manifest.get("version")
        result["kind"] = manifest.get("kind") or (
            "pg_dump_full" if has_pg else "json_config"
        )

        if has_pg:
            if dry_run:
                dump_info = tar.getmember("database.dump")
                result["would_restore_pg_dump"] = True
                result["would_restore_pg_dump_bytes"] = dump_info.size
                result["would_restore_avatars"] = sum(
                    1
                    for m in tar.getmembers()
                    if m.isfile() and m.name.replace("\\", "/").startswith("data/")
                )
                return result

            dump_tmp = Path(tempfile.mkstemp(prefix="piherder-restore-", suffix=".dump")[1])
            try:
                src = tar.extractfile("database.dump")
                if not src:
                    raise ValueError("database.dump missing in archive")
                dump_tmp.write_bytes(src.read())
                restore_pg_dump(dump_tmp)
                result["restored_pg_dump"] = True
            finally:
                dump_tmp.unlink(missing_ok=True)

            # DATA_ROOT files after DB
            n_files = 0
            data_root = Path(settings.DATA_ROOT or "/data")
            for m in tar.getmembers():
                name = m.name.replace("\\", "/")
                if not m.isfile() or not name.startswith("data/"):
                    continue
                rel = name[len("data/") :]
                if not rel or ".." in rel.split("/"):
                    continue
                dest = data_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(m)
                if f:
                    dest.write_bytes(f.read())
                    n_files += 1
            result["restored_avatars"] = n_files
            logger.info("Full pg_dump restore complete from %s (%s data files)", p, n_files)
            return result

    # ── Legacy / config-only JSON path ────────────────────────────────────
    with tarfile.open(p, "r:gz") as tar:
        member = tar.extractfile("piherder-backup.json")
        if not member:
            raise ValueError("Invalid herder backup (missing piherder-backup.json)")
        payload = json.loads(member.read())

    # If the "json" was actually a v6 manifest only, fail clearly
    if payload.get("kind") == "pg_dump_full" and "servers" not in payload:
        raise ValueError(
            "Archive looks like a full pg_dump backup but database.dump is missing"
        )

    manifest = payload.get("manifest") or {}
    result["format_version"] = manifest.get("version")
    result["kind"] = manifest.get("kind") or "json_config"

    if dry_run:
        result["would_restore_servers"] = len(payload.get("servers") or [])
        result["would_restore_users"] = len(payload.get("users") or [])
        result["would_restore_docker_versions"] = len(payload.get("docker_versions") or [])
        result["would_restore_totp_codes"] = len(payload.get("totp_backup_codes") or [])
        result["would_restore_trusted_devices"] = len(payload.get("trusted_devices") or [])
        result["would_restore_webauthn_credentials"] = len(
            payload.get("webauthn_credentials") or []
        )
        result["would_restore_push_vapid"] = len(payload.get("push_vapid") or [])
        result["would_restore_push_subscriptions"] = len(
            payload.get("push_subscriptions") or []
        )
        result["would_restore_push_preferences"] = len(
            payload.get("push_preferences") or []
        )
        result["would_restore_notifications"] = len(payload.get("notifications") or [])
        result["would_restore_integrations"] = len(payload.get("integrations") or [])
        result["would_restore_integration_bindings"] = len(
            payload.get("integration_bindings") or []
        )
        result["would_restore_managed_certificates"] = len(
            payload.get("managed_certificates") or []
        )
        result["would_restore_certificate_targets"] = len(
            payload.get("certificate_targets") or []
        )
        result["would_restore_service_templates"] = len(
            payload.get("service_templates") or []
        )
        result["would_restore_stack_deployments"] = len(
            payload.get("stack_deployments") or []
        )
        result["would_restore_service_dns_records"] = len(
            payload.get("service_dns_records") or []
        )
        result["would_restore_user_favourites"] = len(
            payload.get("user_favourites") or []
        )
        result["would_restore_api_tokens"] = len(payload.get("api_tokens") or [])
        result["would_restore_jobs"] = len(payload.get("jobs") or [])
        result["would_restore_nmap_scan_schedules"] = len(
            payload.get("nmap_scan_schedules") or []
        )
        result["would_restore_nmap_scan_runs"] = len(payload.get("nmap_scan_runs") or [])
        result["would_restore_nmap_devices"] = len(payload.get("nmap_devices") or [])
        result["would_restore_nmap_script_results"] = len(
            payload.get("nmap_script_results") or []
        )
        result["would_restore_herder_config"] = bool(payload.get("herder_config"))
        with tarfile.open(p, "r:gz") as tar:
            result["would_restore_avatars"] = sum(
                1
                for m in tar.getmembers()
                if m.isfile() and m.name.replace("\\", "/").startswith("data/")
            )
        if restore_audit:
            result["would_restore_audit"] = len(payload.get("audit_logs") or [])
        return result

    with Session(engine) as s:
        # Order: users → user children → servers → integrations → nmap → …
        result["restored_users"] = _upsert_users(s, payload.get("users") or [])
        s.flush()

        result["restored_totp_codes"] = _upsert_rows(
            s, TotpBackupCode, payload.get("totp_backup_codes") or []
        )
        result["restored_trusted_devices"] = _upsert_rows(
            s, TrustedDevice, payload.get("trusted_devices") or []
        )
        result["restored_webauthn_credentials"] = _upsert_rows(
            s, WebAuthnCredential, payload.get("webauthn_credentials") or []
        )
        result["restored_api_tokens"] = _upsert_rows(
            s, ApiToken, payload.get("api_tokens") or []
        )

        result["restored_servers"] = _upsert_rows(
            s, Server, payload.get("servers") or []
        )
        s.flush()

        # Jobs after servers (server_id FK); clear live Celery ids
        jobs_rows = _jobs_for_restore(payload.get("jobs") or [])
        result["restored_jobs"] = _upsert_rows(s, Job, jobs_rows)
        s.flush()

        result["restored_user_favourites"] = _upsert_rows(
            s, UserFavourite, payload.get("user_favourites") or []
        )

        result["restored_integrations"] = _upsert_rows(
            s, Integration, payload.get("integrations") or []
        )
        s.flush()
        result["restored_integration_bindings"] = _upsert_rows(
            s, IntegrationBinding, payload.get("integration_bindings") or []
        )
        s.flush()

        # Nmap: schedules → runs → devices → script results (FK order)
        nmap_schedules = payload.get("nmap_scan_schedules") or []
        # If archive has no jobs, drop last_job_id (v4 and older)
        if not jobs_rows:
            for row in nmap_schedules:
                if isinstance(row, dict):
                    row["last_job_id"] = None
        result["restored_nmap_scan_schedules"] = _upsert_rows(
            s, NmapScanSchedule, nmap_schedules
        )
        s.flush()

        nmap_runs = payload.get("nmap_scan_runs") or []
        # Drop job_id if job missing from this archive
        job_ids = {r.get("id") for r in jobs_rows if isinstance(r, dict) and r.get("id") is not None}
        for row in nmap_runs:
            if isinstance(row, dict):
                jid = row.get("job_id")
                if jid is not None and jid not in job_ids:
                    row["job_id"] = None
        result["restored_nmap_scan_runs"] = _upsert_rows(s, NmapScanRun, nmap_runs)
        s.flush()
        run_ids = {
            r.get("id") for r in nmap_runs if isinstance(r, dict) and r.get("id") is not None
        }

        nmap_devices = payload.get("nmap_devices") or []
        for row in nmap_devices:
            if isinstance(row, dict):
                rid = row.get("last_run_id")
                if rid is not None and rid not in run_ids:
                    row["last_run_id"] = None
        result["restored_nmap_devices"] = _upsert_rows(s, NmapDevice, nmap_devices)
        s.flush()

        nmap_scripts = payload.get("nmap_script_results") or []
        for row in nmap_scripts:
            if isinstance(row, dict):
                rid = row.get("run_id")
                if rid is not None and rid not in run_ids:
                    row["run_id"] = None
        result["restored_nmap_script_results"] = _upsert_rows(
            s, NmapScriptResult, nmap_scripts
        )
        s.flush()

        from ..models import ManagedCertificate, CertificateTarget

        result["restored_managed_certificates"] = _upsert_rows(
            s, ManagedCertificate, payload.get("managed_certificates") or []
        )
        s.flush()
        result["restored_certificate_targets"] = _upsert_rows(
            s, CertificateTarget, payload.get("certificate_targets") or []
        )

        result["restored_service_templates"] = _upsert_rows(
            s, ServiceTemplate, payload.get("service_templates") or []
        )
        s.flush()
        result["restored_stack_deployments"] = _upsert_rows(
            s, StackDeployment, payload.get("stack_deployments") or []
        )
        s.flush()
        from ..models import ServiceDnsRecord

        result["restored_service_dns_records"] = _upsert_rows(
            s, ServiceDnsRecord, payload.get("service_dns_records") or []
        )
        from ..models import RuntimeEdge

        result["restored_runtime_edges"] = _upsert_rows(
            s, RuntimeEdge, payload.get("runtime_edges") or []
        )
        from ..models import (
            TopologyCategory,
            TopologyTag,
            VisualServiceStack,
            ContainerAnnotation,
            ContainerAnnotationTag,
        )

        result["restored_topology_categories"] = _upsert_rows(
            s, TopologyCategory, payload.get("topology_categories") or []
        )
        result["restored_topology_tags"] = _upsert_rows(
            s, TopologyTag, payload.get("topology_tags") or []
        )
        s.flush()
        result["restored_visual_service_stacks"] = _upsert_rows(
            s, VisualServiceStack, payload.get("visual_service_stacks") or []
        )
        s.flush()
        result["restored_container_annotations"] = _upsert_rows(
            s, ContainerAnnotation, payload.get("container_annotations") or []
        )
        s.flush()
        result["restored_container_annotation_tags"] = _upsert_rows(
            s, ContainerAnnotationTag, payload.get("container_annotation_tags") or []
        )
        from ..models import PortAnnotation

        result["restored_port_annotations"] = _upsert_rows(
            s, PortAnnotation, payload.get("port_annotations") or []
        )

        result["restored_docker_versions"] = _upsert_rows(
            s, DockerVersion, payload.get("docker_versions") or []
        )

        result["restored_push_vapid"] = _upsert_push_vapid(
            s, payload.get("push_vapid") or []
        )
        result["restored_push_subscriptions"] = _upsert_push_subscriptions(
            s, payload.get("push_subscriptions") or []
        )
        result["restored_push_preferences"] = _upsert_push_preferences(
            s, payload.get("push_preferences") or []
        )
        result["restored_notifications"] = _append_notifications(
            s, payload.get("notifications") or []
        )

        if restore_audit:
            result["restored_audit"] = _append_audit(s, payload.get("audit_logs") or [])

        s.commit()

    try:
        _fix_postgres_sequences()
    except Exception as e:
        logger.warning("Sequence fix after restore skipped: %s", e)

    # Operational settings → PostgreSQL (same as Settings UI)
    hcfg = payload.get("herder_config")
    if isinstance(hcfg, dict) and hcfg:
        try:
            from .app_settings import replace_settings, clear_cache

            clear_cache()
            replace_settings(hcfg)
            result["restored_herder_config"] = True
        except Exception as e:
            logger.warning("Could not restore herder_config: %s", e)
            result["herder_config_error"] = str(e)[:200]

    # Avatars
    try:
        result["restored_avatars"] = _restore_avatars_from_tar(p)
    except Exception as e:
        logger.warning("Avatar restore failed: %s", e)
        result["avatar_error"] = str(e)[:200]

    return result


def _fix_postgres_sequences() -> None:
    """After explicit-id inserts, bump serial sequences to max(id)."""
    from sqlalchemy import text

    tables = [
        "user",
        "userfavourite",
        "apitoken",
        "server",
        "dockerversion",
        "totpbackupcode",
        "trusteddevice",
        "pushvapidconfig",
        "pushsubscription",
        "pushpreference",
        "notification",
        "auditlog",
        "servicetemplate",
        "stackdeployment",
        "servicednsrecord",
        "runtimeedge",
        "topologycategory",
        "topologytag",
        "visualservicestack",
        "containerannotation",
        "containerannotationtag",
        "portannotation",
        "managedcertificate",
        "certificatetarget",
        "integration",
        "integrationbinding",
        "nmapscanschedule",
        "nmapdevice",
        "nmapscriptresult",
    ]
    with engine.connect() as conn:
        for table in tables:
            try:
                conn.execute(
                    text(
                        f"""
                        SELECT setval(
                            pg_get_serial_sequence('{table}', 'id'),
                            COALESCE((SELECT MAX(id) FROM "{table}"), 1),
                            true
                        )
                        """
                    )
                )
            except Exception:
                pass
        conn.commit()