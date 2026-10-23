"""One alert when jobs sit pending and no Celery worker answers."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from ..models import Job

logger = logging.getLogger(__name__)

STUCK_PENDING_SEC = 15 * 60
STUCK_QUEUE_FINGERPRINT = "stuck_queue"
STUCK_QUEUE_TYPE = "stuck_queue"


def oldest_pending_age_seconds(session: Session, now: Optional[datetime] = None) -> int:
    """Age of the oldest pending job, or 0 when the queue has none."""
    moment = now or datetime.utcnow()
    try:
        created = session.exec(
            select(Job.created_at)
            .where(Job.status == "pending")
            .order_by(Job.created_at.asc())
        ).first()
    except Exception:
        logger.warning("[Queue] Pending age check failed", exc_info=True)
        return 0
    if isinstance(created, tuple):
        created = created[0] if created else None
    if not isinstance(created, datetime):
        return 0
    try:
        return max(0, int((moment - created).total_seconds()))
    except Exception:
        return 0


def workers_alive() -> Optional[bool]:
    """True when a Celery worker answers. None when the check cannot tell."""
    try:
        from .jobs import celery_worker_activity

        state = celery_worker_activity()
    except Exception:
        logger.warning("[Queue] Worker heartbeat check failed", exc_info=True)
        return None
    if state is None:
        return None
    return bool(state[0])


def stuck_queue_state(oldest_seconds: int, heartbeat: Optional[bool]) -> Optional[bool]:
    """True when a pending job is older than 15 minutes and no worker answered.

    False when that is not the case. None when the pending job is old and the
    heartbeat check could not tell, so callers leave an existing alert alone.
    """
    if int(oldest_seconds or 0) <= STUCK_PENDING_SEC:
        return False
    if heartbeat is None:
        return None
    return not heartbeat


def sync_stuck_queue_alert(
    session: Session,
    *,
    oldest_seconds: Optional[int] = None,
    heartbeat: Optional[bool] = None,
) -> Optional[bool]:
    """Open one warning, or resolve it when the pending queue drains.

    Returns True when the alert is open, False when it was resolved, and None
    when a heartbeat check failed and an old pending job is still there.
    """
    from .notifications import resolve_by_fingerprint, upsert_notification

    age = oldest_pending_age_seconds(session) if oldest_seconds is None else int(oldest_seconds)
    if age > STUCK_PENDING_SEC and heartbeat is None:
        heartbeat = workers_alive()
    state = stuck_queue_state(age, heartbeat)
    if state is None:
        return None
    if not state:
        resolve_by_fingerprint(session, STUCK_QUEUE_FINGERPRINT)
        return False
    minutes = max(1, age // 60)
    upsert_notification(
        session,
        fingerprint=STUCK_QUEUE_FINGERPRINT,
        type=STUCK_QUEUE_TYPE,
        title="Job queue is stuck",
        body=(
            f"A job has been pending for {minutes} minutes and no Celery worker answered."
        ),
        link_url="/jobs",
        severity="warning",
    )
    return True


def queue_gauges(session: Session) -> tuple[int, int]:
    """Oldest pending age in seconds, and 1 when the stuck-queue condition holds."""
    age = oldest_pending_age_seconds(session)
    heartbeat = workers_alive() if age > STUCK_PENDING_SEC else True
    state = stuck_queue_state(age, heartbeat)
    return age, 1 if state is True else 0
