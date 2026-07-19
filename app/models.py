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

    # LAN DNS identity (A/AAAA on Pi-hole) — apps CNAME to this name
    # e.g. rpi5-1.example.com → ip_address (or dns_ip_override)
    dns_name: Optional[str] = Field(default=None, index=True, max_length=253)
    dns_manage_a: bool = False
    dns_ip_override: Optional[str] = Field(default=None, max_length=64)

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
    # Client IP of the HTTP request that caused this event (via Caddy XFF / peer).
    # Null for scheduler / pure background work with no user request.
    client_ip: Optional[str] = Field(default=None, index=True)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None

    user: Optional[User] = Relationship(back_populates="audit_logs")
    server: Optional[Server] = Relationship(back_populates="audit_logs")


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: Optional[int] = Field(default=None, foreign_key="server.id")
    job_type: str  # backup, container_patch, os_patch, os_update_check, container_update_check, docker_stack_check, docker_stack_deploy, retention, diagnostics, herder_backup
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
    integration_down: bool = True
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


class Integration(SQLModel, table=True):
    """External product connection (Uptime Kuma, Grafana, …).

    Credentials are Fernet-encrypted JSON. last_status_json is a poll cache.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)  # uptime_kuma | grafana | pihole | …
    name: str
    base_url: str
    enabled: bool = True
    config_json: Optional[str] = None  # non-secret JSON
    credentials_encrypted: Optional[str] = None
    last_status_json: Optional[str] = None
    last_polled_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    bindings: List["IntegrationBinding"] = Relationship(back_populates="integration")


class ServiceTemplate(SQLModel, table=True):
    """Catalog entry for a deployable service template (builtin / import / git)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: Optional[str] = None
    category: str = Field(default="other", index=True)
    version: str = "1.0.0"
    source: str = Field(default="builtin", index=True)  # builtin | import | git
    enabled: bool = True
    # Full definition JSON including file_contents (see FEATURE_PLAN_TEMPLATES.md)
    definition_json: Optional[str] = None
    checksum: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    deployments: List["StackDeployment"] = Relationship(back_populates="template")


class StackDeployment(SQLModel, table=True):
    """Desired state for a template-managed stack on a host (config version Vn)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    project_name: str = Field(index=True)
    template_id: Optional[int] = Field(default=None, foreign_key="servicetemplate.id", index=True)
    template_slug: Optional[str] = Field(default=None, index=True)
    template_version: Optional[str] = None
    config_version: int = 1
    variables_json: Optional[str] = None  # non-secret key/value JSON
    secrets_encrypted: Optional[str] = None  # Fernet(JSON secrets map)
    files_json: Optional[str] = None  # rendered files for redeploy
    drift_status: str = Field(default="unknown")  # unknown | in_sync | drifted
    last_deployed_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    template: Optional[ServiceTemplate] = Relationship(back_populates="deployments")


class IntegrationBinding(SQLModel, table=True):
    """Map fleet resources to external monitors (e.g. Kuma).

    Hierarchy:
      - role=ssh_reachability → server-level SSH
      - role=service → one level lower: server → docker_project → optional docker_container
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    integration_id: int = Field(foreign_key="integration.id", index=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    role: str = Field(default="ssh_reachability", index=True)
    # Compose project name (required for role=service)
    docker_project: Optional[str] = Field(default=None, index=True)
    # Container / compose service name within the project (optional)
    docker_container: Optional[str] = Field(default=None, index=True)
    external_id: str  # e.g. Kuma monitor key (name or id)
    external_label: Optional[str] = None
    external_meta_json: Optional[str] = None
    # Relative path under DATA_ROOT, e.g. service_logos/12.png (upload or favicon fetch)
    logo_path: Optional[str] = None
    last_state: Optional[str] = None  # up | down | pending | maintenance | unknown
    last_message: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    integration: Optional[Integration] = Relationship(back_populates="bindings")


class ManagedCertificate(SQLModel, table=True):
    """TLS material stored encrypted (from NPM pull or PEM upload).

    Private key and fullchain are Fernet-encrypted; never returned to the browser.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    source: str = Field(default="upload", index=True)  # npm | upload
    source_integration_id: Optional[int] = Field(
        default=None, foreign_key="integration.id", index=True
    )
    external_id: Optional[str] = Field(default=None, index=True)  # NPM cert id
    domains_json: Optional[str] = None  # JSON list of CN + SANs
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    fingerprint_sha256: Optional[str] = Field(default=None, index=True)
    fullchain_encrypted: Optional[str] = None
    privkey_encrypted: Optional[str] = None
    issuer: Optional[str] = None
    serial: Optional[str] = None
    last_pulled_at: Optional[datetime] = None
    last_renew_requested_at: Optional[datetime] = None
    last_renew_status: Optional[str] = None
    last_error: Optional[str] = None
    auto_renew: bool = True
    renew_days_before: int = 21
    # This-instance Caddy edge mapping (not a fleet CertificateTarget)
    # When True, NPM renew re-applies PEMs to ./certs + reloads Caddy.
    edge_apply_enabled: bool = False
    last_edge_deploy_at: Optional[datetime] = None
    last_edge_deploy_status: Optional[str] = None
    last_edge_deploy_fingerprint: Optional[str] = None
    last_edge_deploy_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    targets: List["CertificateTarget"] = Relationship(back_populates="certificate")


class CertificateTarget(SQLModel, table=True):
    """Where a managed certificate is deployed on a fleet host.

    Think of this as a *service map*: one vaulted cert → host path + filenames +
    permissions + optional restart command for a specific consumer (NPM volume,
    Unifi, mail, etc.).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    certificate_id: int = Field(foreign_key="managedcertificate.id", index=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    # Human label: "NPM proxy", "Unifi controller", "HAOS reverse proxy"
    label: Optional[str] = Field(default=None, max_length=200)
    remote_dir: str = Field(default="~/certs")
    layout: str = Field(default="pair")  # pair | combined | pair_and_combined | pair_and_pfx
    # direct = SFTP into remote_dir; stage_sudo = SFTP to home stage + sudo install
    write_mode: str = Field(default="direct", max_length=32)
    fullchain_filename: str = Field(default="fullchain.pem")
    privkey_filename: str = Field(default="privkey.pem")
    combined_filename: str = Field(default="snakeoil.pem")
    pfx_filename: str = Field(default="Certificate.pfx")
    file_mode: str = Field(default="600")
    file_owner: Optional[str] = None  # e.g. root
    file_group: Optional[str] = None
    pfx_export_password_encrypted: Optional[str] = None
    post_deploy_command: Optional[str] = None
    enabled: bool = True
    last_deployed_at: Optional[datetime] = None
    last_deploy_status: Optional[str] = None
    last_deploy_fingerprint: Optional[str] = None
    last_deploy_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    certificate: Optional[ManagedCertificate] = Relationship(back_populates="targets")


class ServiceDnsRecord(SQLModel, table=True):
    """Service / app DNS identity in the fleet fabric.

    Default: CNAME → target host's dns_name (often NPM edge).
    Backend host is where the stack runs (may differ from DNS target).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    fqdn: str = Field(index=True, unique=True, max_length=253)
    record_type: str = Field(default="cname", index=True)  # cname | a
    # DNS CNAME target (edge): must have Server.dns_name
    target_server_id: int = Field(foreign_key="server.id", index=True)
    # Where the app stack runs (backend)
    backend_server_id: int = Field(foreign_key="server.id", index=True)
    stack_deployment_id: Optional[int] = Field(
        default=None, foreign_key="stackdeployment.id", index=True
    )
    docker_project: Optional[str] = Field(default=None, max_length=200, index=True)
    label: Optional[str] = Field(default=None, max_length=200)
    managed_on_pihole: bool = True
    via_proxy: bool = False  # CNAME target is NPM/proxy edge, not backend
    npm_hint: Optional[str] = Field(default=None, max_length=300)
    certificate_id: Optional[int] = Field(
        default=None, foreign_key="managedcertificate.id", index=True
    )
    # none | checklist | done
    external_dns_status: str = Field(default="checklist", max_length=32)
    notes: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None  # ok | partial | error
    last_sync_detail: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RuntimeEdge(SQLModel, table=True):
    """Confirmed / dismissed / manual runtime dependency (topology P2–P3).

    Cross-host allowed: from_server may differ from to_server (NPM edge, shared DB).
    Container fields hold compose service names when known; empty = whole project.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    from_server_id: int = Field(foreign_key="server.id", index=True)
    from_project: str = Field(max_length=200, index=True)
    from_container: Optional[str] = Field(default=None, max_length=200)
    to_server_id: int = Field(foreign_key="server.id", index=True)
    to_project: str = Field(max_length=200, index=True)
    to_container: Optional[str] = Field(default=None, max_length=200)
    # depends_on | talks_to | mounts | custom
    kind: str = Field(default="depends_on", max_length=32, index=True)
    # suggested | accepted | manual
    source: str = Field(default="manual", max_length=32, index=True)
    confidence: int = Field(default=100)
    note: Optional[str] = Field(default=None, max_length=500)
    dismissed_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: Optional[int] = Field(
        default=None, foreign_key="user.id", index=True
    )


class TopologyCategory(SQLModel, table=True):
    """Fixed vocabulary for container categories (map columns / role chips).

    Operator can add/disable/reorder; assignment is pick-from-list only (not free text).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True, max_length=64)
    label: str = Field(max_length=80)
    sort_order: int = Field(default=0, index=True)
    enabled: bool = Field(default=True, index=True)
    is_system: bool = Field(default=False)  # seed rows: cannot delete
    color_token: Optional[str] = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TopologyTag(SQLModel, table=True):
    """Fixed vocabulary for multi-select container tags (chips). Not free text."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True, max_length=64)
    label: str = Field(max_length=80)
    sort_order: int = Field(default=0, index=True)
    enabled: bool = Field(default=True, index=True)
    is_system: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class VisualServiceStack(SQLModel, table=True):
    """Operator visual group under one compose project (not a deploy boundary).

    Deploy/stop/start still target the whole compose project. Visual stacks only
    change panel/map presentation (e.g. main app vs e2e helpers in same project).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    compose_project: str = Field(max_length=200, index=True)
    name: str = Field(max_length=120)
    slug: str = Field(max_length=80, index=True)
    is_default: bool = Field(default=False, index=True)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ContainerAnnotation(SQLModel, table=True):
    """Per-container topology annotation (category, visual stack, order)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    compose_project: str = Field(max_length=200, index=True)
    # Prefer compose_service name; same identity as stack order / RuntimeEdge
    container_key: str = Field(max_length=200, index=True)
    # Null = use heuristic category (guess_container_role)
    category_key: Optional[str] = Field(default=None, max_length=64, index=True)
    # Null = implicit default visual stack ("Main")
    visual_stack_id: Optional[int] = Field(
        default=None, foreign_key="visualservicestack.id", index=True
    )
    sort_index: Optional[int] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContainerAnnotationTag(SQLModel, table=True):
    """M2M: container annotation → topology tag keys."""
    id: Optional[int] = Field(default=None, primary_key=True)
    annotation_id: int = Field(foreign_key="containerannotation.id", index=True)
    tag_key: str = Field(max_length=64, index=True)


# ---------------------------------------------------------------------------
# LAN discovery (nmap) — see docs/FEATURE_PLAN_LAN_NMAP.md
# ---------------------------------------------------------------------------


class NmapScanSchedule(SQLModel, table=True):
    """Named recurring scan (multiple allowed; all off by default)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    integration_id: int = Field(foreign_key="integration.id", index=True)
    name: str = Field(max_length=120)
    # discovery | inventory | detailed
    intensity: str = Field(default="discovery", max_length=32, index=True)
    # 5-field cron; empty/None = disabled interval-only or off
    cron: Optional[str] = Field(default=None, max_length=64)
    # Optional interval hours (alternative to cron); 0/None = use cron only
    interval_hours: Optional[int] = Field(default=None)
    enabled: bool = False
    # JSON: {"cidrs": ["192.168.1.0/24"]} or {"all_configured": true}
    scope_json: Optional[str] = None
    # JSON: {"vuln_scripts": bool, "use_syn": bool|null}
    options_json: Optional[str] = None
    last_run_at: Optional[datetime] = None
    last_job_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NmapScanRun(SQLModel, table=True):
    """One scan execution (linked to Job when enqueued)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    integration_id: int = Field(foreign_key="integration.id", index=True)
    job_id: Optional[int] = Field(default=None, foreign_key="job.id", index=True)
    schedule_id: Optional[int] = Field(
        default=None, foreign_key="nmapscanschedule.id", index=True
    )
    intensity: str = Field(default="discovery", max_length=32, index=True)
    # JSON list of targets (CIDRs / IPs)
    targets_json: Optional[str] = None
    status: str = Field(default="pending", max_length=32, index=True)
    hosts_up: int = 0
    hosts_total: int = 0
    ports_open: int = 0
    summary_json: Optional[str] = None
    artifact_path: Optional[str] = None  # relative under DATA_ROOT
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NmapDevice(SQLModel, table=True):
    """Auto-created discovered host (not a managed Server until linked/promoted)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    integration_id: int = Field(foreign_key="integration.id", index=True)
    # Stable identity key: mac:AA:BB… or ip:192.168.1.10
    identity_key: str = Field(max_length=128, index=True)
    ip_address: str = Field(max_length=64, index=True)
    hostname: Optional[str] = Field(default=None, max_length=255)
    mac_address: Optional[str] = Field(default=None, max_length=32, index=True)
    # new | known | linked | ignored | stale
    state: str = Field(default="new", max_length=32, index=True)
    linked_server_id: Optional[int] = Field(
        default=None, foreign_key="server.id", index=True
    )
    os_summary: Optional[str] = Field(default=None, max_length=255)
    # Latest ports/services snapshot JSON (bounded)
    ports_json: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_run_id: Optional[int] = Field(
        default=None, foreign_key="nmapscanrun.id", index=True
    )
    notes: Optional[str] = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NmapScriptResult(SQLModel, table=True):
    """NSE / Vulners script output for a device (latest or per-run)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="nmapdevice.id", index=True)
    run_id: Optional[int] = Field(default=None, foreign_key="nmapscanrun.id", index=True)
    script_id: str = Field(max_length=128, index=True)
    output: Optional[str] = None
    # JSON list of CVE ids when parsed
    cve_ids_json: Optional[str] = None
    # Port the script ran against (None = host-level / hostscript)
    port: Optional[int] = Field(default=None, index=True)
    protocol: Optional[str] = Field(default=None, max_length=16)
    created_at: datetime = Field(default_factory=datetime.utcnow)
