"""Job queue package — module surface is ``service.py``.

We install ``service`` as this package's ``sys.modules`` entry so that:

- ``from app.services import jobs``
- ``import app.services.jobs as jobs``
- ``patch.object(jobs, "_active_job_of_type", ...)``

all refer to the **same** module object (preserves historical monolith patching).
"""
from __future__ import annotations

from . import service as _service
import sys

# Re-export for "from app.services.jobs import create_job_and_run"
from .service import *  # noqa: F403

# Critical: identity with service module for unittest.mock patch.object
sys.modules[__name__] = _service
