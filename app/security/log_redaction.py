"""Strip URL credentials from log records before a handler writes them.

Uvicorn and Celery attach their own handlers and often disable propagation,
so a filter on the root logger alone does not cover them. The installer
adds the filter to handlers that already exist and to handlers created later.
"""
from __future__ import annotations

import logging
import re
import traceback

# user:password@ in redis and postgres URLs. The scheme is kept. The host is kept.
_USERINFO = re.compile(
    r"((?:rediss?|postgres(?:ql)?)(?:\+[a-z0-9]+)?://)[^\s'\"<>/@]*:[^\s'\"<>/@]*@",
    re.IGNORECASE,
)
_PASSWORD_QUERY = re.compile(r"(?i)(password=)[^\s&'\"]+")

_installed = False
_original_handler_init = None


def redact_text(text: str) -> str:
    """Remove userinfo and password query values. Other text stays."""
    cleaned = _USERINFO.sub(r"\1", text)
    return _PASSWORD_QUERY.sub(r"\1***", cleaned)


def _redact_value(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


class CredentialRedactionFilter(logging.Filter):
    """Redact msg, args, and the formatted traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.args = _redact_value(record.args)
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if record.exc_info and not record.exc_text:
                record.exc_text = "".join(traceback.format_exception(*record.exc_info))
            if isinstance(record.exc_text, str):
                record.exc_text = redact_text(record.exc_text)
        except Exception:
            return True
        return True


def _attach(handler: logging.Handler) -> None:
    if any(isinstance(item, CredentialRedactionFilter) for item in handler.filters):
        return
    handler.addFilter(CredentialRedactionFilter())


def _attach_existing() -> None:
    seen: set[int] = set()
    loggers = [logging.getLogger()]
    for name in list(logging.root.manager.loggerDict):
        logger = logging.getLogger(name)
        loggers.append(logger)
    for logger in loggers:
        for handler in list(logger.handlers):
            ident = id(handler)
            if ident in seen:
                continue
            seen.add(ident)
            _attach(handler)


def install_log_redaction() -> None:
    """Install the filter once. Later calls attach any new handlers."""
    global _installed, _original_handler_init
    if not _installed:
        _original_handler_init = logging.Handler.__init__

        def _init(self, *args, **kwargs):
            _original_handler_init(self, *args, **kwargs)
            _attach(self)

        logging.Handler.__init__ = _init  # type: ignore[method-assign]
        _installed = True
    _attach_existing()
