from celery import Celery
import os

celery = Celery(
    "piherder",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
    include=["app.tasks"]
)

# Fair multi-worker defaults: one reserved task per child process; ack after finish
# so a killed worker redelivers. Per-server mutex in app.tasks.backup_server.
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=7200,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # Avoid stealing tasks that another worker is about to run after lock wait
    worker_disable_rate_limits=True,
)