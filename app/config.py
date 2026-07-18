from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Core
    PIHERDER_MASTER_KEY: str
    DATABASE_URL: str = "postgresql://piherder:piherder@db:5432/piherder"
    SECRET_KEY: str = "dev-secret-change-in-prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Paths
    BACKUP_ROOT: str = "/backups"
    DEFAULT_DOCKER_BASE: str = "~/docker"
    DATA_ROOT: str = "/data"  # avatars and other app data (mount volume in compose)
    AVATAR_MAX_BYTES: int = 2 * 1024 * 1024  # 2 MiB

    # Registration: when False, public register is blocked once any user exists
    ALLOW_OPEN_REGISTRATION: bool = False

    # Optional notifications (replicates legacy webhook)
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_NUMBER: Optional[str] = None
    WEBHOOK_RECIPIENTS: Optional[str] = None  # JSON string e.g. '["+1..."]'

    # Public origin (trusted HTTPS + PWA / Web Push)
    # Hostname must match the cert SANs and Caddy site block (compose env PIHERDER_HOSTNAME)
    PIHERDER_HOSTNAME: Optional[str] = None  # e.g. piherder.example.com
    PIHERDER_PUBLIC_URL: Optional[str] = None  # e.g. https://piherder.example.com:8443
    # Auth cookies: empty = auto (Secure when PIHERDER_PUBLIC_URL is https://…); true/false to force
    COOKIE_SECURE: Optional[str] = None

    # Web Push (VAPID) — optional env override. When unset, keys are auto-generated
    # once at startup and stored encrypted in the DB (PushVapidConfig).
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_CONTACT: Optional[str] = None  # mailto:… ; defaults from PIHERDER_HOSTNAME

    # Herder self-backup (config + optional audit, compressed to host-mapped dir)
    HERDER_BACKUP_ROOT: str = "/herder_backups"
    HERDER_BACKUP_SCHEDULE: Optional[str] = None  # cron e.g. "0 3 * * *"

    # Edge TLS (this PiHerder instance → Caddy). Defaults match docker-compose mounts.
    # No extra operator config when using the stock compose stack.
    EDGE_CERTS_DIR: str = "/certs"
    CADDY_ADMIN_URL: str = "http://caddy:2019"
    CADDYFILE_PATH: str = "/caddy/Caddyfile"

    # Link to co-located Pi-hole admin/settings (common alongside PiHerder)
    PIHOLE_URL: Optional[str] = "http://pi.hole/admin/"

    # 2FA trusted device max age (days)
    TRUSTED_DEVICE_DAYS: int = 30

    # Prometheus scrape endpoint (GET /metrics)
    # If set, require Authorization: Bearer <token>. If empty, path is open like /health
    # (use only on a private network / behind Caddy allow-list).
    METRICS_TOKEN: Optional[str] = None
    # Backup considered stale when last_backup_at is older than this many hours
    METRICS_BACKUP_STALE_HOURS: int = 36

    # CORS for browser → /api/v1 from other origins (rare). Empty = disabled (recommended).
    # Server-side n8n/HA/scripts do not need CORS. Comma-separated exact origins only.
    # Example: CORS_ORIGINS=https://n8n.example.com,https://homeassistant.local:8123
    # Never use * with API tokens. Backend still enforces Bearer + scopes + IP allowlist.
    CORS_ORIGINS: Optional[str] = None

    # GitHub release check for “new version available” banner / About page
    PIHERDER_UPDATE_CHECK: bool = True
    PIHERDER_UPDATE_CHECK_TTL_HOURS: int = 12

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
