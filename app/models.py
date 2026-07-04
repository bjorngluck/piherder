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

    audit_logs: List["AuditLog"] = Relationship(back_populates="user")


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

    # Manual ordering in server list (0 = use name alpha, higher/lower for custom order)
    sort_order: int = Field(default=0)

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
    job_type: str  # backup, container_patch, os_patch, retention, diagnostics, herder_backup
    status: str = "pending"  # pending, running, success, failed
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
