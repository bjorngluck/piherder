"""Optional audit rows for API token reads and hosted /mcp reads.

Off unless Settings turns it on. A row stores the token id and name, and the
client IP. It never stores the token value or prefix. Read rows use their own
retention and stay off the default Audit list.
"""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from .audit_write import make_audit_log

ACTION = "api_read"
FLAG_ACTION = "audit_api_reads_changed"
FLAG_KEY = "audit_api_reads"
RETENTION_KEY = "audit_read_retention_days"
RETENTION_DEFAULT = 7
RETENTION_MIN = 1
RETENTION_MAX = 90
HOST_FACTS_ACTION = "host_facts"


def _settings() -> dict:
    from .app_settings import load_settings

    return load_settings()


def reads_enabled(cfg: dict | None = None) -> bool:
    src = cfg if cfg is not None else _settings()
    return bool(src.get(FLAG_KEY))


def retention_days(cfg: dict | None = None) -> int:
    src = cfg if cfg is not None else _settings()
    try:
        n = int(src.get(RETENTION_KEY) if src.get(RETENTION_KEY) is not None else RETENTION_DEFAULT)
    except (TypeError, ValueError):
        n = RETENTION_DEFAULT
    return max(RETENTION_MIN, min(RETENTION_MAX, n))


def public_detail(what: str) -> str:
    """A short label for the read. A token-looking string is dropped."""
    text = " ".join((what or "").split())
    if "ph_" in text.lower():
        return "read"
    return text[:200] or "read"


def is_routine_audit(action: str | None, status: str | None) -> bool:
    """Rows the default Audit list leaves out. A failed host snapshot stays."""
    act = (action or "").strip()
    if act == ACTION:
        return True
    if act == HOST_FACTS_ACTION and (status or "").strip().lower() == "success":
        return True
    return False


def is_routine_job(job_type: str | None, status: str | None) -> bool:
    """A successful host_facts snapshot. Failed, pending, and running stay listed."""
    return (job_type or "") == HOST_FACTS_ACTION and (status or "") == "success"


def record_api_read(
    session: Session,
    *,
    token_id: int | None,
    token_name: str | None,
    user_id: int | None,
    client_ip: str | None,
    what: str,
) -> None:
    """Write one read row when the flag is on. Never raises into the caller.

    The row is committed on a short-lived session for the same engine, so this
    does not commit or discard work the caller has not committed yet.
    """
    try:
        if not reads_enabled():
            return
        with Session(session.get_bind()) as audit:
            audit.add(
                make_audit_log(
                    action=ACTION,
                    status="success",
                    user_id=user_id,
                    api_token_id=token_id,
                    api_token_name=(token_name or None),
                    details=public_detail(what),
                    client_ip=client_ip,
                    finished_at=datetime.utcnow(),
                )
            )
            audit.commit()
    except Exception:
        return


def record_flag_change(
    session: Session,
    *,
    user_id: int | None,
    enabled: bool,
) -> None:
    """Always written, including the turn that switches the flag off."""
    session.add(
        make_audit_log(
            action=FLAG_ACTION,
            status="success",
            user_id=user_id,
            details="on" if enabled else "off",
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()
