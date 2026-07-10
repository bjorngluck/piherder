from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime
from typing import Optional, List, Any
import json


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Set on successful interactive login (password / trusted device / 2FA complete)
    last_login_at: Optional[datetime] = None

    # Profile (IAM)
    display_name: Optional[str] = None
    avatar_path: Optional[str] = None  # relative under DATA_ROOT, e.g. avatars/1.png
    updated_at: Optional[datetime] = None

    # RBAC: admin (full) | operator (run jobs, edit fleet) | viewer (read-only)
    role: str = Field(default="admin")

    # Optional TOTP 2FA
    totp_secret_encrypted: Optional[str] = None
    totp_enabled: bool = False
    totp_confirmed_at: Optional[datetime] = None

    # Admin-created users must set their own password before using the app
    must_change_password: bool = False

    audit_logs: List["AuditLog"] = Relationship(back_populates="user")
    totp_backup_codes: List["TotpBackupCode"] = Relationship(back_populates="user")
    trusted_devices: List["TrustedDevice"] = Relationship(back_populates="user")


class TotpBackupCode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    code_hash: str
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    user: Optional[User] = Relationship(back_populates="totp_backup_codes")


class TrustedDevice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(index=True)
    label: Optional[str] = None
    user_agent: Optional[str] = None
    ip: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    expires_at: datetime

    user: Optional[User] = Relationship(back_populates="trusted_devices")


class Server(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    hostname: str
    ip_address: Optional[str] = None
    ssh_port: int = 22
    ssh_username: str = "bjorn"
    ssh_private_key_encrypted: Optional[str] = None  # Fernet ciphertext
    ssh_public_key: Optional[str] = None
    ssh_password_encrypted: Optional[str] = None  # optional fallback

    os_type: str = "debian"
    last_seen: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Feature flags
    backup_enabled: bool = False
    os_patch_enabled: bool = False
    container_patch_enabled: bool = False

    # Backup & container config (stored as JSON strings for simplicity in v1)
    backup_paths: str = Field(default='["/home/bjorn/docker/", "/var/lib/docker/volumes/"]')
    docker_base_dir: str = "~/docker"
    excluded_projects: str = '["my-xmrig"]'
    retention_days: int = 7
    backup_schedule: Optional[str] = None  # e.g. cron "0 2 * * *" for daily at 2am

    # Configurable backup destination (per-host overrides)
    backup_dest_root: Optional[str] = None  # e.g. custom root instead of global BACKUP_ROOT
    backup_folder_name: Optional[str] = None  # e.g. custom folder instead of hostname slug

    # Backup source path policy JSON: {"allow":["/home","/var/lib/docker"],"deny":["/tmp"]}
    # Empty allow = any path not denied. Defaults always deny OS roots (/etc,/boot,…).
    backup_path_rules: Optional[str] = None

    # Manual ordering in server list (0 = use name alpha, higher/lower for custom order)
    sort_order: int = Field(default=0)

    # Populated by worker after successful backup (thin web reads this directly)
    last_backup_at: Optional[datetime] = None

    # OS update check (check-only; does not apply patches)
    os_check_enabled: bool = False
    os_check_schedule: Optional[str] = None
    last_os_check_at: Optional[datetime] = None
    os_updates_count: Optional[int] = None
    reboot_pending: bool = False
    os_updates_summary: Optional[str] = None

    # Container update check (check-only; pull+compare, no up -d)
    container_check_enabled: bool = False
    container_check_schedule: Optional[str] = None
    last_container_check_at: Optional[datetime] = None
    container_updates_count: Optional[int] = None
    container_updates_summary: Optional[str] = None

    # OS patch *apply* schedule (dangerous; default off — explicit opt-in)
    # Steps JSON e.g. '["update","upgrade","autoremove"]' (upgrade XOR full-upgrade)
    os_apply_enabled: bool = False
    os_apply_schedule: Optional[str] = None
    os_apply_steps: Optional[str] = None
    os_apply_only_if_updates: bool = True  # skip when last check shows 0 actionable

    # Container patch *apply* schedule (compose pull + conditional up -d); default off
    container_apply_enabled: bool = False
    container_apply_schedule: Optional[str] = None
    container_apply_only_if_updates: bool = True

    # Docker inventory snapshot (DB-backed; refresh in background — not live SSH on every page)
    # Payload: {v, projects, orphan_containers, meta}; status: never|ok|error|refreshing
    docker_inventory_json: Optional[str] = None
    docker_inventory_at: Optional[datetime] = None
    docker_inventory_status: str = "never"
    docker_inventory_error: Optional[str] = None

    # Host dependency probe snapshot (rsync/docker/apt for enabled features)
    # Payload: {checked_at, overall, checks[], features{}}
    host_deps_json: Optional[str] = None
    host_deps_checked_at: Optional[datetime] = None

    audit_logs: List["AuditLog"] = Relationship(back_populates="server")
    jobs: List["Job"] = Relationship(back_populates="server")
    docker_versions: List["DockerVersion"] = Relationship(back_populates="server")

    def get_backup_paths(self) -> List[str]:
        """Backward compat: return just the source paths."""
        try:
            data = json.loads(self.backup_paths)
            if isinstance(data, list):
                if data and isinstance(data[0], dict):
                    return [item.get("source", "") for item in data if item.get("source")]
                return data
            return ["/home/bjorn/docker/", "/var/lib/docker/volumes/"]
        except Exception:
            return ["/home/bjorn/docker/", "/var/lib/docker/volumes/"]

    def get_backup_sources(self) -> List[dict]:
        """
        Modern flexible sources.
        Returns list of:
        {
          "source": "/remote/path",
          "dest_name": "optional-custom-folder" or None (uses basename),
          "enabled": true
        }
        """
        try:
            data = json.loads(self.backup_paths)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                sources = []
                for item in data:
                    if isinstance(item, dict) and item.get("source"):
                        sources.append({
                            "source": item["source"],
                            "dest_name": item.get("dest_name"),
                            "enabled": item.get("enabled", True)
                        })
                return sources
            # Convert old list[str] to new format
            if isinstance(data, list):
                return [{"source": p, "dest_name": None, "enabled": True} for p in data if p]
            return [
                {"source": "/home/bjorn/docker/", "dest_name": None, "enabled": True},
                {"source": "/var/lib/docker/volumes/", "dest_name": None, "enabled": True}
            ]
        except Exception:
            return [
                {"source": "/home/bjorn/docker/", "dest_name": None, "enabled": True},
                {"source": "/var/lib/docker/volumes/", "dest_name": None, "enabled": True}
            ]

    def set_backup_sources(self, sources: List[dict]):
        """Save sources in new dict format."""
        self.backup_paths = json.dumps(sources)

    def get_excluded_projects(self) -> List[str]:
        try:
            return json.loads(self.excluded_projects)
        except Exception:
            return ["my-xmrig"]


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    server_id: Optional[int] = Field(default=None, foreign_key="server.id")
    # When action was performed via automation Bearer token (not interactive session).
    # Name is snapshotted so rename/revoke still shows a readable actor in history.
    api_token_id: Optional[int] = Field(default=None, foreign_key="apitoken.id", index=True)
    api_token_name: Optional[str] = None
    action: str  # "backup", "container_patch", "os_patch", "diagnostics", etc.
    status: str  # "success", "failed", "running"
    details: Optional[str] = None
    output_snippet: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None

    user: Optional[User] = Relationship(back_populates="audit_logs")
    server: Optional[Server] = Relationship(back_populates="audit_logs")


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: Optional[int] = Field(default=None, foreign_key="server.id")
    job_type: str  # backup, container_patch, os_patch, os_update_check, container_update_check, retention, diagnostics, herder_backup
    status: str = "pending"  # pending, running, success, failed, cancelled
    celery_task_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    details: Optional[str] = None  # JSON summary

    server: Optional[Server] = Relationship(back_populates="jobs")


class DockerVersion(SQLModel, table=True):
    """Versioned docker-compose / Dockerfile / other files for a project on a server.
    Supports drafts (local edits) and deployed versions.
    'files' is JSON: {"docker-compose.yml": "content...", "Dockerfile": "...", "extra.yml": "..."}
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id")
    project_name: str  # e.g. folder name like "myapp"
    version: int
    files: str = Field(default="{}")  # JSON dict of filename -> content
    is_draft: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deployed_at: Optional[datetime] = None

    server: Server = Relationship(back_populates="docker_versions")


class Notification(SQLModel, table=True):
    """Actionable user-facing alerts (separate from immutable AuditLog)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    server_id: Optional[int] = Field(default=None, foreign_key="server.id", index=True)
    type: str = Field(index=True)  # os_updates, container_updates, reboot_pending, backup_failed, ...
    severity: str = "warning"  # info | warning | critical
    title: str
    body: Optional[str] = None
    link_url: Optional[str] = None
    fingerprint: str = Field(index=True)
    status: str = Field(default="open", index=True)  # open | dismissed | resolved
    payload: Optional[str] = None  # JSON extras
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    dismissed_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    read_at: Optional[datetime] = None


class PushSubscription(SQLModel, table=True):
    """Browser Web Push subscription (one row per device/endpoint)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    endpoint: str = Field(unique=True, index=True)
    p256dh: str
    auth: str
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_success_at: Optional[datetime] = None
    disabled_at: Optional[datetime] = None


class PushPreference(SQLModel, table=True):
    """Per-user Web Push master switch and event-type filters."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)
    push_enabled: bool = False
    backup_failed: bool = True
    os_updates: bool = True
    reboot_pending: bool = True
    container_updates: bool = True
    herder_backup_failed: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PushVapidConfig(SQLModel, table=True):
    """Singleton-ish VAPID application server keys (auto-generated or env-seeded).

    Private key is Fernet-encrypted with PIHERDER_MASTER_KEY. Never regenerate
    casually — changing keys invalidates all browser push subscriptions.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    public_key: str
    private_key_encrypted: str
    contact: str = "mailto:piherder@localhost"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "generated"  # generated | env_import


class ApiToken(SQLModel, table=True):
    """Admin-managed automation API token (instance-wide, not personal PAT).

    Plaintext shown once at creation; only hash is stored.

    Scopes (comma-separated):
      Capability: read | jobs | edit
      Feature allowlist (optional): feature:backup | feature:os | feature:docker
      If no feature:* scopes → all features allowed (still subject to server flags).

    allowed_cidrs: optional JSON list of IPs/CIDRs; empty/null = any client IP.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    token_prefix: str = Field(index=True)  # first chars for UI (e.g. ph_abc1…)
    token_hash: str = Field(unique=True, index=True)
    scopes: str = Field(default="read,jobs")  # comma-separated
    allowed_cidrs: Optional[str] = None  # JSON list e.g. ["10.0.0.0/8","192.168.1.10"]
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    # Optional expiry (null = never)
    expires_at: Optional[datetime] = None


class AppSetting(SQLModel, table=True):
    """Singleton row for instance-wide operational settings (Settings UI).

    Stored in PostgreSQL for DR: DB dump + herder self-backup both capture it.
    Flexible JSON blob so new keys do not need a migration each time.
    Secrets (master key, DB URL) stay in env — never here.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    # Always use id=1 as the singleton; create if missing.
    data_json: str = Field(default="{}")  # JSON object
    updated_at: datetime = Field(default_factory=datetime.utcnow)
