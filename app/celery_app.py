from celery import Celery
from celery.signals import worker_init, worker_ready
import os

celery = Celery(
    "piherder",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
    include=["app.tasks"]
)

# Fair multi-worker defaults: one reserved task per child process; ack after finish
# so a killed worker redelivers. Per-server mutex in app.tasks.backup_server.
# service_migrate uses the same backup mutex on both hosts; a redelivered
# running Move is failed (not re-run) — see app.tasks.service_migrate.
# exclusive_job (patch, checks, stack, templates) uses this default queue and
# does not take that mutex. A redelivered running job fails; a pending host-wait
# redelivers. nmap stays on -Q nmap.
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=7200,
    # Redis redelivers an unacked task after visibility_timeout (default 1h).
    # That must stay above task_time_limit or a still-running job is started twice.
    broker_transport_options={"visibility_timeout": 10800},
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # A redirected stderr writes one log record per traceback line.
    # Leave tracebacks on the task logger as one record with exc_info.
    worker_redirect_stdouts=False,
    # Avoid stealing tasks that another worker is about to run after lock wait
    worker_disable_rate_limits=True,
)


@worker_init.connect
def _clear_worker_metrics_dir(**_kwargs):
    """Drop multiprocess files from children that died before this start."""
    from .services.worker_metrics import prepare_multiproc_dir

    prepare_multiproc_dir()


@worker_init.connect
def _enforce_boot_secrets_on_worker(**_kwargs):
    from .security.boot_secrets import enforce_boot_secrets

    enforce_boot_secrets()


@worker_ready.connect
def _serve_worker_metrics(**_kwargs):
    from .services.worker_metrics import serve_worker_metrics

    serve_worker_metrics()