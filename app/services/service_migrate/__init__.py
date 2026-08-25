"""Service migration (v1.4 Stream M)."""

from .host_lock import (  # noqa: F401
    HostLockError,
    annotate_projects,
    assert_unlocked,
    compose_project_name,
    lock_state,
    migrate_enabled,
    migrate_surface_allowed,
    set_host_lock,
    unlock_host,
)
from .preflight import eligible_destinations, run_preflight  # noqa: F401
