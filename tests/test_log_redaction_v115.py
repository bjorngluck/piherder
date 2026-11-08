"""Handler output drops Redis and Postgres URL credentials."""
from __future__ import annotations

import io
import logging

import redis

from app.security.log_redaction import install_log_redaction


def test_root_uvicorn_and_celery_handlers_drop_url_credentials():
    install_log_redaction()
    secret = "s3cret-value"
    redis_url = f"redis://:{secret}@192.0.2.8:6379/0"
    postgres_url = f"postgresql://piherder:{secret}@192.0.2.9:5432/piherder"
    names = ("", "uvicorn.error", "celery")
    saved = []
    buffers = []
    try:
        for name in names:
            logger = logging.getLogger(name)
            saved.append((logger, logger.level, logger.propagate, list(logger.handlers)))
            buf = io.StringIO()
            handler = logging.StreamHandler(buf)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.handlers = [handler]
            logger.setLevel(logging.ERROR)
            logger.propagate = False
            buffers.append(buf)
            try:
                raise redis.exceptions.AuthenticationError(
                    f"AUTH failed {redis_url} also {postgres_url} password={secret}"
                )
            except redis.exceptions.AuthenticationError:
                logger.exception("backup status failed")
        for buf in buffers:
            text = buf.getvalue()
            assert "backup status failed" in text
            assert "AuthenticationError" in text
            assert secret not in text
            assert "redis://192.0.2.8:6379/0" in text
            assert "postgresql://192.0.2.9:5432/piherder" in text
    finally:
        for logger, level, propagate, handlers in saved:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
