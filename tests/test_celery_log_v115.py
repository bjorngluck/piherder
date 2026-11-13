"""A raised task is one ERROR record, with the traceback attached."""
from __future__ import annotations

import logging
import traceback
from unittest.mock import MagicMock

from app.celery_app import celery


def test_raised_task_is_one_error_record(caplog):
    assert celery.conf.worker_redirect_stdouts is False

    @celery.task(name="app.tasks._log_probe_boom")
    def boom():
        raise RuntimeError("boom-marker")

    eager = celery.conf.task_always_eager
    propagate = celery.conf.task_eager_propagates
    previous_backend = getattr(celery, "_backend", None)
    backend = MagicMock()
    backend.thread_safe = False
    celery._backend = backend
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = False
    try:
        with caplog.at_level(logging.ERROR, logger="celery.app.trace"):
            boom.apply()
    finally:
        celery.conf.task_always_eager = eager
        celery.conf.task_eager_propagates = propagate
        if previous_backend is None:
            celery._backend_cache = None
            local = getattr(celery, "_local", None)
            if local is not None:
                local.backend = None
        else:
            celery._backend = previous_backend

    errors = [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert "boom-marker" in str(errors[0].exc_info[1])


def test_stderr_traceback_is_not_split_into_log_records(caplog):
    assert celery.conf.worker_redirect_stdouts is False
    try:
        raise RuntimeError("split-marker")
    except RuntimeError:
        with caplog.at_level(logging.ERROR):
            traceback.print_exc()
    assert not any("split-marker" in rec.getMessage() for rec in caplog.records)
