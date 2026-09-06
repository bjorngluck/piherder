from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Core
    PIHERDER_MASTER_KEY: str
    DATABASE_URL: str = "postgresql://piherder:piherder@db:5432/piherder"
    SECRET_KEY: str = "dev-secret-change-in-prod"
    # Lab only: allow boot with a weak/default SECRET_KEY. Never true in production.
    PIHERDER_ALLOW_INSECURE: bool = False
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Paths
    BACKUP_ROOT: str = "/backups"
    DEFAULT_DOCKER_BASE: str = "~/docker"
    DATA_ROOT: str = "/data"  # avatars and other app data (mount volume in compose)
    AVATAR_MAX_BYTES: int = 2 * 1024 * 1024  # 2 MiB

    # B-retry (v1.2): rsync vanished files / busy sources (KI-rsync-vanished)
    # Extra attempts after code 24 (or 23 with "vanished" in stderr)
    PIHERDER_BACKUP_VANISHED_RETRIES: int = 1
    # Seconds to wait before a vanished-file retry
    PIHERDER_BACKUP_VANISHED_RETRY_DELAY_SEC: int = 5
    # If still vanished after retries, treat source as success with warning (soft OK)
    PIHERDER_BACKUP_VANISHED_SOFT_OK: bool = True

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

    # Trust CF-Connecting-IP / X-Forwarded-For / X-Real-IP only when the TCP peer
    # is in this list (comma-separated CIDRs). Empty = never trust forwarded headers
    # (use the TCP peer). Bundled Compose sets RFC1918 + loopback so Caddy is trusted.
    PIHERDER_TRUSTED_PROXY_CIDRS: Optional[str] = None

    # GitHub release check for “new version available” banner / About page
    PIHERDER_UPDATE_CHECK: bool = True
    PIHERDER_UPDATE_CHECK_TTL_HOURS: int = 12

    # LAN discovery (nmap worker) — vuln artefacts volume; empty pack = no Vulners
    PIHERDER_NMAP_VULN_ROOT: str = "/var/lib/piherder/nmap-vuln"
    PIHERDER_NMAP_TIMEOUT_SEC: int = 7200

    # Web SSH console (v1.2 Stream W) — default OFF until operators opt in
    PIHERDER_SSH_CONSOLE: bool = False
    # Ticket TTL to open the WebSocket (seconds)
    PIHERDER_SSH_CONSOLE_TICKET_SEC: int = 60
    # Idle disconnect (seconds without client→host data)
    PIHERDER_SSH_CONSOLE_IDLE_SEC: int = 900
    # Hard max session length (seconds)
    PIHERDER_SSH_CONSOLE_MAX_SEC: int = 3600
    # Concurrent open consoles per user / whole instance (multi-shell tabs each count)
    # Concurrent PTY shells (account-wide, not per host) — multi-host needs headroom
    PIHERDER_SSH_CONSOLE_MAX_PER_USER: int = 4
    PIHERDER_SSH_CONSOLE_MAX_GLOBAL: int = 20
    # Browser xterm scrollback (lines kept above viewport); client may raise further
    PIHERDER_SSH_CONSOLE_SCROLLBACK: int = 2000
    # After 2FA, grant re-open of additional shells on the same host without re-TOTP
    PIHERDER_SSH_CONSOLE_GRANT_MIN: int = 10
    # If true, every New shell requires fresh 2FA (TOTP/passkey); grant cookie ignored
    PIHERDER_SSH_CONSOLE_REQUIRE_2FA_EVERY_SHELL: bool = False
    # Console step-up: backup codes off by default (prefer passkey / TOTP app)
    PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES: bool = False
    PIHERDER_SSH_CONSOLE_PREFER_PASSKEY: bool = True
    # If true and user has passkeys enrolled, only passkey opens the console (no TOTP)
    PIHERDER_SSH_CONSOLE_REQUIRE_PASSKEY: bool = False
    # Bind console tickets to client IP / console device cookie; re-check while open
    PIHERDER_SSH_CONSOLE_BIND_IP: bool = True
    PIHERDER_SSH_CONSOLE_BIND_DEVICE: bool = True
    PIHERDER_SSH_CONSOLE_REVALIDATE_SEC: int = 10
    # After WebSocket drop (app switch / tab sleep), keep SSH PTY parked this long
    # so the browser can resume. 0 = hold until idle/max session timeout.
    PIHERDER_SSH_CONSOLE_HOLD_SEC: int = 0
    # Who may open a privileged (break-glass) console: admin | operator
    PIHERDER_SSH_CONSOLE_PRIVILEGED_ROLE: str = "admin"
    # Command audit (v1.3 W-audit): off | commands | commands_output
    PIHERDER_SSH_CONSOLE_AUDIT_MODE: str = "off"
    PIHERDER_SSH_CONSOLE_AUDIT_RETENTION_DAYS: int = 14
    # When true, every live shell records commands (Off is ignored). Demo still never stores.
    PIHERDER_SSH_CONSOLE_AUDIT_REQUIRED: bool = False

    # Host Files dest-card (v1.3 Stream F) — default OFF until operators opt in
    PIHERDER_HOST_FILES: bool = False
    # Service migration Move wizard (v1.4 Stream M) — default OFF until M is ready
    PIHERDER_SERVICE_MIGRATE: bool = False
    # Upload cap (bytes). Code default 512 MiB; env may raise up to 2 GiB.
    PIHERDER_HOST_FILES_MAX_BYTES: int = 512 * 1024 * 1024

    # Content-Security-Policy (v1.2) — default on; Report-Only for staged rollouts
    PIHERDER_CSP: bool = True
    PIHERDER_CSP_REPORT_ONLY: bool = False

    # Public demo sandbox (v1.2 Stream D) — default OFF. When true: banner, hard
    # blocks on real onboard/API tokens/outbound, canned jobs (see demo.py).
    PIHERDER_DEMO_MODE: bool = False
    # Shared demo login (seed only; only meaningful when DEMO_MODE=1)
    PIHERDER_DEMO_EMAIL: str = "demo@hacknow.info"
    PIHERDER_DEMO_PASSWORD: str = "Piherder@1"

    # Cloudflare Turnstile (bot protection on login) — empty = off
    PIHERDER_TURNSTILE_SITE_KEY: Optional[str] = None
    PIHERDER_TURNSTILE_SECRET_KEY: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
