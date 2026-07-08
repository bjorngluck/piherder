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

    # Herder self-backup (config + optional audit, compressed to host-mapped dir)
    HERDER_BACKUP_ROOT: str = "/herder_backups"
    HERDER_BACKUP_SCHEDULE: Optional[str] = None  # cron e.g. "0 3 * * *"

    # Link to co-located Pi-hole admin/settings (common alongside PiHerder)
    PIHOLE_URL: Optional[str] = "http://pi.hole/admin/"

    # 2FA trusted device max age (days)
    TRUSTED_DEVICE_DAYS: int = 30

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
