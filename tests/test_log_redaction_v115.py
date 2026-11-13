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


def test_rendered_line_exception_arg_and_a_later_handler_are_redacted():
    """A secret that appears only after formatting, or inside an exception, is removed."""
    install_log_redaction()
    secret = "s3cret-value"
    captured = {}

    class Capture(logging.Handler):
        def emit(self, record):
            captured["msg"] = record.msg
            captured["args"] = record.args
            captured["text"] = self.format(record)

    logger = logging.getLogger("piherder.redaction.later")
    saved = (logger.level, logger.propagate, list(logger.handlers))
    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    try:
        logger.error("redis://:%s@192.0.2.8:6379/0", secret)
        assert secret not in captured["text"]
        assert secret not in str(captured["msg"])
        assert captured["args"] in ((), None)
        assert "redis://192.0.2.8:6379/0" in captured["text"]

        logger.error(
            "connect failed %s",
            redis.exceptions.AuthenticationError(
                f"AUTH redis://:{secret}@192.0.2.8:6379/0 password={secret}"
            ),
        )
        assert secret not in captured["text"]
        assert secret not in str(captured["msg"])
        assert "AuthenticationError" in captured["text"]
        assert "192.0.2.8" in captured["text"]
    finally:
        logger.handlers = saved[2]
        logger.setLevel(saved[0])
        logger.propagate = saved[1]


def test_non_string_message_and_a_strict_exception_stay_redacted():
    install_log_redaction()
    secret = "s3cret-value"

    class Odd(Exception):
        def __init__(self, code):
            if not isinstance(code, int):
                raise TypeError("code")
            super().__init__(str(code))

    logger = logging.getLogger("piherder.redaction.odd")
    saved = (logger.level, logger.propagate, list(logger.handlers))
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    try:
        logger.error(
            redis.exceptions.AuthenticationError(f"redis://:{secret}@192.0.2.8:6379/0")
        )
        try:
            raise Odd(7)
        except Odd:
            logger.exception("odd failure")
        text = buf.getvalue()
        assert secret not in text
        assert "192.0.2.8" in text
        assert "Odd" in text
    finally:
        logger.handlers = saved[2]
        logger.setLevel(saved[0])
        logger.propagate = saved[1]


def test_a_broken_message_and_a_shared_handler_do_not_break_logging():
    from app.security.log_redaction import CredentialRedactionFilter

    class Boom:
        def __str__(self):
            raise RuntimeError("nope")

    record = logging.LogRecord("piherder", logging.ERROR, __file__, 1, Boom(), (), None)
    assert CredentialRedactionFilter().filter(record) is True

    shared = logging.StreamHandler(io.StringIO())
    first = logging.getLogger("piherder.redaction.shared.first")
    second = logging.getLogger("piherder.redaction.shared.second")
    saved = (
        (first, first.level, first.propagate, list(first.handlers)),
        (second, second.level, second.propagate, list(second.handlers)),
    )
    first.handlers = [shared]
    second.handlers = [shared]
    first.propagate = False
    second.propagate = False
    try:
        install_log_redaction()
        assert sum(isinstance(item, CredentialRedactionFilter) for item in shared.filters) == 1
    finally:
        for logger, level, propagate, handlers in saved:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
