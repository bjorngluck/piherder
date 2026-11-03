"""Worker-process counters and the internal metrics port.

The web `/metrics` scrape only sees counters in the web process. Lock and
catch-up increments happen in the Celery worker, so the worker serves the
same series on its own port. Prefork children write through
`PROMETHEUS_MULTIPROC_DIR` and the parent adds them up.
"""
from __future__ import annotations

import logging
import os

from prometheus_client import Counter

logger = logging.getLogger(__name__)

DEFAULT_WORKER_METRICS_PORT = 9809
DEFAULT_WORKER_METRICS_URL = "http://celery-worker:9809/metrics"
WORKER_METRICS_UNAVAILABLE = "Worker metrics are unavailable."
_LOCK_REASONS = ("redis_error", "busy")
_CATCHUP_KINDS = ("backup", "container_check")
_CATCHUP_RESULTS = ("queued", "skipped", "failed")

LOCK_FAILURES = Counter(
    "piherder_lock_acquire_failures_total",
    "Host lock acquires that did not take the lock. Label reason is redis_error or busy.",
    ["reason"],
)
CATCHUP = Counter(
    "piherder_schedule_catchup_total",
    "Catch-up attempts for a missed backup or image check.",
    ["kind", "result"],
)

for _reason in _LOCK_REASONS:
    LOCK_FAILURES.labels(reason=_reason)
for _kind in _CATCHUP_KINDS:
    for _result in _CATCHUP_RESULTS:
        CATCHUP.labels(kind=_kind, result=_result)

_started = False


def worker_metrics_enabled() -> bool:
    return os.getenv("PIHERDER_WORKER_METRICS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def worker_metrics_port() -> int:
    raw = (os.getenv("PIHERDER_WORKER_METRICS_PORT") or "").strip()
    if not raw:
        return DEFAULT_WORKER_METRICS_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_WORKER_METRICS_PORT
    if port < 1 or port > 65535:
        return DEFAULT_WORKER_METRICS_PORT
    return port


def configured_worker_metrics_url() -> str:
    """Internal worker scrape URL. Callers cannot pass a request target."""
    raw = (os.getenv("PIHERDER_WORKER_METRICS_URL") or "").strip()
    return raw or DEFAULT_WORKER_METRICS_URL


def fetch_worker_metrics() -> tuple[int, bytes, str]:
    """GET the configured worker URL. A transport failure is a fixed 503."""
    url = configured_worker_metrics_url()
    try:
        import httpx

        response = httpx.get(url, timeout=2.0, follow_redirects=False)
    except Exception as exc:
        logger.warning("Worker metrics fetch failed: %s", type(exc).__name__)
        return (
            503,
            WORKER_METRICS_UNAVAILABLE.encode("utf-8"),
            "text/plain; charset=utf-8",
        )
    media = response.headers.get("content-type") or "text/plain; version=0.0.4; charset=utf-8"
    return response.status_code, response.content, media


def prepare_multiproc_dir() -> None:
    """Create the prefork directory and drop files from a previous process."""
    path = (os.environ.get("PROMETHEUS_MULTIPROC_DIR") or "").strip()
    if not path:
        return
    os.makedirs(path, exist_ok=True)
    for name in os.listdir(path):
        candidate = os.path.join(path, name)
        try:
            if os.path.isfile(candidate):
                os.unlink(candidate)
        except OSError:
            pass


def serve_worker_metrics() -> int | None:
    """Listen on the worker metrics port. No-op unless the env flag is set."""
    global _started
    if _started or not worker_metrics_enabled():
        return None
    port = worker_metrics_port()
    try:
        from prometheus_client import (
            REGISTRY,
            CollectorRegistry,
            multiprocess,
            start_http_server,
        )

        registry = REGISTRY
        if (os.environ.get("PROMETHEUS_MULTIPROC_DIR") or "").strip():
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        start_http_server(port, addr="0.0.0.0", registry=registry)
    except Exception as exc:
        logger.error("Worker metrics server did not start: %s", type(exc).__name__)
        return None
    _started = True
    logger.info("Worker metrics server started on port %s", port)
    return port
